// PickProof content script for discord.com.
//
// Reads only the messages Discord has already rendered for the signed-in user (no Discord API calls, no
// tokens; it only scrolls when you press "Load 30 days"), sends them to PickProof for Gemini grading, and puts a small record badge
// next to each analyst's name. The DOM shape lives in one place, readMessages(), so it is easy to adjust.
(() => {
  if (window.__receiptsLoaded) {
    return;
  }
  window.__receiptsLoaded = true;

  const SENT = new Map(); // message_id -> text already reported (re-sent if the text changes)
  const BADGES = new Map(); // capper id -> badge data
  let lastAuthor = null;

  // ---- DOM reading ------------------------------------------------------------------------
  function context() {
    const m = location.pathname.match(/^\/channels\/(@me|\d+)\/(\d+)/);
    const title = document.title || '';
    const name = (title.match(/#\s*([^|\s]+)/) || [])[1] || null;
    return {guildId: m ? m[1] : null, channelId: m ? m[2] : null, channelName: name};
  }

  function capperId(authorId, name) {
    return authorId && /^\d+$/.test(authorId) ? 'discord:' + authorId : 'discordname:' + String(name || '').toLowerCase();
  }

  function messageText(li) {
    const content = li.querySelector('[id^="message-content-"]');
    if (!content) {
      return {text: '', edited: false};
    }
    const clone = content.cloneNode(true);
    let edited = false;
    clone.querySelectorAll('[class*="edited"], time').forEach(el => {
      if (/edited/i.test(el.textContent || '') || /edited/i.test(el.className || '')) {
        edited = true;
        el.remove();
      }
    });
    return {text: (clone.innerText || clone.textContent || '').trim(), edited};
  }

  /** Attached images (bet slip screenshots). Prefer the original CDN link over the resized preview. */
  function messageImages(li) {
    const urls = new Set();
    li.querySelectorAll('a[href*="/attachments/"]').forEach(a => {
      if (/\.(png|jpe?g|webp)(\?|$)/i.test(a.getAttribute('href') || '')) {
        urls.add(a.href);
      }
    });
    if (!urls.size) {
      li.querySelectorAll('img[src*="/attachments/"]').forEach(img => urls.add(img.src));
    }
    return [...urls].slice(0, 4);
  }

  /** Discord message rows: <li id="chat-messages-{channelId}-{messageId}">. Continuation rows have no
   * header, so the author carries over from the row above. */
  function readMessages() {
    const ctx = context();
    const out = [];
    lastAuthor = null;
    document.querySelectorAll('li[id^="chat-messages-"]').forEach(li => {
      const ids = li.id.match(/chat-messages-(\d+)-(\d+)/);
      if (!ids) {
        return;
      }
      const nameEl = li.querySelector('[id^="message-username-"] [class*="username"], [id^="message-username-"]');
      const avatar = li.querySelector('img[class*="avatar"]');
      if (nameEl) {
        const src = avatar ? avatar.getAttribute('src') || '' : '';
        const idMatch = src.match(/\/avatars\/(\d+)\//);
        lastAuthor = {
          name: (nameEl.textContent || '').trim(),
          id: idMatch ? idMatch[1] : null,
          avatar: src && !src.includes('/embed/avatars/') ? src : null,
          nameEl,
        };
      }
      if (!lastAuthor) {
        return;
      }
      const {text, edited} = messageText(li);
      const images = messageImages(li);
      if (!text && !images.length) {
        return;
      }
      out.push({
        message_id: ids[2],
        channel_id: ids[1],
        guild_id: ctx.guildId,
        channel_name: ctx.channelName,
        author_id: lastAuthor.id,
        author_name: lastAuthor.name,
        avatar_url: lastAuthor.avatar,
        text,
        edited,
        images,
        _nameEl: nameEl || null,
      });
    });
    return out;
  }

  // ---- reporting ----------------------------------------------------------------------------
  function send(type, payload) {
    return new Promise(resolve => {
      try {
        chrome.runtime.sendMessage({type, ...payload}, r => resolve(chrome.runtime.lastError ? {error: chrome.runtime.lastError.message} : r));
      } catch (e) {
        resolve({error: String(e)}); // extension reloaded: this old script is orphaned
      }
    });
  }

  let inFlight = null;
  let again = false;
  // One scan at a time: slip OCR on the server can take a few seconds, and Discord mutates constantly.
  async function scan(force) {
    if (inFlight) {
      again = true;
      return inFlight;
    }
    inFlight = scanOnce(force).finally(() => {
      inFlight = null;
      if (again) {
        again = false;
        schedule();
      }
    });
    return inFlight;
  }

  async function scanOnce(force) {
    const msgs = readMessages();
    const key = m => m.text + '|' + m.images.length;
    const fresh = msgs.filter(m => force || SENT.get(m.message_id) !== key(m));
    let result = null;
    if (fresh.length) {
      const batch = fresh.slice(0, 200).map(({_nameEl, ...m}) => m);
      result = await send('OBSERVE', {messages: batch});
      if (result && !result.error && !result.skipped) {
        batch.forEach(m => SENT.set(m.message_id, key(m)));
      }
    }
    await paintBadges(msgs);
    return {context: context(), visible: msgs.length, sent: fresh.length, result};
  }

  // ---- badges -------------------------------------------------------------------------------
  async function paintBadges(msgs) {
    const heads = msgs.filter(m => m._nameEl);
    const ids = [...new Set(heads.map(m => capperId(m.author_id, m.author_name)))];
    const missing = ids.filter(id => !BADGES.has(id));
    if (missing.length) {
      const r = await send('LOOKUP', {ids: missing});
      if (r && !r.error) {
        missing.forEach(id => BADGES.set(id, r[id] || null));
      }
    }
    heads.forEach(m => {
      const data = BADGES.get(capperId(m.author_id, m.author_name));
      const host = m._nameEl.closest('[id^="message-username-"]') || m._nameEl;
      let el = host.parentElement && host.parentElement.querySelector(':scope > .rcpt-badge');
      if (!data || (!data.wins && !data.losses && !data.pending && data.tier === 'unverified')) {
        if (el) {
          el.remove();
        }
        return;
      }
      if (!el) {
        el = document.createElement('button');
        el.type = 'button';
        el.className = 'rcpt-badge';
        el.addEventListener('click', e => {
          e.preventDefault();
          e.stopPropagation();
          send('OPEN_ANALYST', {handle: el.dataset.handle});
        });
        host.insertAdjacentElement('afterend', el);
      }
      const wr = data.win_rate == null ? '–' : Math.round(data.win_rate * 100) + '%';
      const verified = data.tier !== 'unverified';
      const label = verified
        ? `${data.tier === 'sharp' ? '✓ Sharp' : '✓ Verified'} · ${wr} · ${data.wins}-${data.losses}`
        : `Observed · ${wr} · ${data.wins}-${data.losses}`;
      // Only touch the DOM when something changed, or our own edits would retrigger the observer forever.
      if (el.textContent !== label || el.dataset.tier !== data.tier) {
        el.dataset.handle = data.handle;
        el.dataset.tier = data.tier;
        el.textContent = label;
        el.title = verified
          ? 'PickProof Verified: every pick since verification, graded on Gemini Predictions. Click for details.'
          : 'PickProof: graded from posts tailers have seen. May be incomplete. Click for details.';
      }
    });
  }

  // ---- triggers -----------------------------------------------------------------------------
  let timer = null;
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(() => scan(false), 1200);
  };
  new MutationObserver(schedule).observe(document.body, {childList: true, subtree: true});
  setInterval(() => BADGES.clear(), 60_000); // refresh records once a minute
  schedule();

  // ---- history: user-requested scroll back through the open channel ------------------------
  function scroller() {
    let el = document.querySelector('ol[data-list-id="chat-messages"]');
    while (el && !(el.scrollHeight > el.clientHeight + 10 && /auto|scroll/.test(getComputedStyle(el).overflowY))) {
      el = el.parentElement;
    }
    return el;
  }
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  async function loadHistory(days) {
    const sc = scroller();
    if (!sc) {
      return {running: false, messages: 0, oldest: null, error: 'Open a text channel first.'};
    }
    const cutoff = Date.now() - days * 864e5;
    const seen = new Set();
    let oldest = null;
    let stall = 0;
    for (let i = 0; i < 300; i++) {
      const before = seen.size;
      readMessages().forEach(m => {
        seen.add(m.message_id);
        const t = Number((BigInt(m.message_id) >> 22n) + 1420070400000n); // snowflake time
        if (!oldest || t < oldest) {
          oldest = t;
        }
      });
      await scan(false); // grade what's on screen before moving on
      if (/This is the start of the/.test(sc.innerText.slice(0, 4000)) || (oldest && oldest < cutoff)) {
        break;
      }
      stall = seen.size === before ? stall + 1 : 0;
      if (stall > 8) {
        break;
      }
      sc.scrollTop = 0;
      sc.dispatchEvent(new Event('scroll', {bubbles: true}));
      await sleep(1200 + Math.random() * 600); // Discord loads ~50 messages per page
    }
    // walk back down so messages Discord unloaded on the way up are read too
    for (let i = 0; i < 200 && sc.scrollTop + sc.clientHeight < sc.scrollHeight - 5; i++) {
      sc.scrollTop += Math.round(sc.clientHeight * 0.8);
      await sleep(350);
      readMessages().forEach(m => seen.add(m.message_id));
      await scan(false);
    }
    return {running: false, done: true, messages: seen.size, oldest: oldest ? new Date(oldest).toISOString() : null};
  }

  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg.type === 'LOAD_HISTORY') {
      loadHistory(msg.days || 30).then(reply, e => reply({running: false, messages: 0, oldest: null, error: String(e)}));
      return true;
    }
    if (msg.type === 'SCAN') {
      scan(true).then(reply);
      return true;
    }
    if (msg.type === 'CONTEXT') {
      reply({context: context(), visible: readMessages().length});
    }
    return false;
  });
})();
