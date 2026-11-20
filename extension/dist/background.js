// Receipts background service worker: the only part of the extension that talks to the Receipts API.
// The content script and side panel send it messages; it adds the signed-in token and forwards them.

const DEFAULT_API = 'http://localhost:8000';

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true}).catch(() => {});
});

async function session() {
  const s = await chrome.storage.local.get(['apiBase', 'token', 'role', 'capture']);
  return {apiBase: (s.apiBase || DEFAULT_API).replace(/\/$/, ''), token: s.token || null, role: s.role || null,
          capture: s.capture !== false};
}

async function call(path, {method = 'GET', body} = {}) {
  const s = await session();
  const res = await fetch(s.apiBase + path, {
    method,
    headers: {'Content-Type': 'application/json', ...(s.token ? {Authorization: 'Bearer ' + s.token} : {})},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${await res.text()}`);
  }
  return res.json();
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  // sidePanel.open must run synchronously inside the click's user gesture, before any await.
  if (msg.type === 'OPEN_ANALYST' && sender.tab && sender.tab.id != null) {
    chrome.sidePanel.open({tabId: sender.tab.id}).catch(() => {});
  }
  (async () => {
    const s = await session();
    switch (msg.type) {
      case 'STATUS':
        return {signedIn: !!s.token, role: s.role, capture: s.capture};
      case 'OBSERVE': {
        // Only signed-in users with capture on contribute observations.
        if (!s.token || !s.capture) {
          return {skipped: true};
        }
        const out = await call('/api/observations', {method: 'POST', body: {messages: msg.messages}});
        const counts = (await chrome.storage.session.get('counts')).counts || {};
        for (const [k, v] of Object.entries(out.counts || {})) {
          counts[k] = (counts[k] || 0) + v;
        }
        await chrome.storage.session.set({counts, lastObservedAt: Date.now()});
        return out;
      }
      case 'LOOKUP':
        return call('/api/badges?ids=' + encodeURIComponent(msg.ids.join(',')));
      case 'OPEN_ANALYST': {
        await chrome.storage.session.set({focusAnalyst: msg.handle, focusAt: Date.now()});
        return {ok: true};
      }
      default:
        return {error: 'unknown message'};
    }
  })()
    .then(reply)
    .catch(e => reply({error: String(e && e.message ? e.message : e)}));
  return true; // async reply
});
