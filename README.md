# Receipts: every pick has a receipt

A **Chrome extension** that grades sports pick sellers ("analysts") from what they actually posted in Discord,
using **Gemini Predictions** as the source of truth for prices and results, with tamper-evident commits on
**Arc testnet**. No standalone site: everything happens in a side panel next to Discord, plus a small badge
next to each analyst's name in the chat.

You sign in as one of two roles:

| | Tailer (consumer) | Analyst (posts picks) |
|---|---|---|
| Goal | Know which sellers are actually good before paying | Prove a real record to buyers |
| What Receipts does | Reads the picks analysts posted in channels you can already see, matches each to the Gemini game, prices it at the Gemini price when it was posted, grades it from Gemini settlement | Claim your Discord account, become **Verified** immediately, and from then on every pick you post is tracked live |
| Sees | Per-channel and global analyst scorecards, badges in Discord, each pick with its Gemini price, close, chart and result | Verified status, a badge with your tracked win rate, share text, storefront link, a "post a pick" box |

### Verified vs Unverified

* **Unverified** (never claimed): Receipts keeps scraping their past posts as tailers scroll. The record is
  labelled **observed** and may be incomplete. Not registering never implies dishonesty.
* **Verified** (claimed the Discord account): free, can't be bought, no waiting period. From the moment of
  verification Receipts **stops scraping** them. Their public record is every pick they post after
  verifying, tracked live. Older scraped picks are shown separately as "before verification".
* **Verified Sharp** (optional achievement): the original one-month rules over tracked picks, all in `.env`:
  30 days of history, 15 graded picks, 10 active days, zero edits or deletes, every pick pregame, positive
  units at Gemini prices, non-negative closing line value. The demo uses a 4-day window.

### How claiming works

The analyst gets a one-time code (`RCPT-XXXXXX`) in the side panel and posts it in any Discord channel. The
first Receipts extension that sees that message (theirs or any tailer's) reports it, and the message's author
becomes the verified account. Only the person logged into that Discord account can post as it, so that's the
ownership proof.

### What the extension reads, and what it doesn't

* Only messages Discord has already rendered on your screen, in channels you can already open.
* No Discord API calls, no Discord token, no auto-scrolling, nothing posted. There's a switch to turn reading off.
* Post times come from Discord message IDs (snowflakes), which encode creation time and can't be edited.

## Gemini Prediction Markets usage

| Job | Endpoint | Field |
|---|---|---|
| Find games | `GET /v1/prediction-markets/events?status=active&category=sports` and `status=settled` | tickers ending `-M` (moneylines) |
| Match a post to a game | same | contract labels, participant names, abbreviations, `startTime` (game must start after the post and within 48 h) |
| Price at lock (live) | event list / `GET /v1/prediction-markets/events/{ticker}` | `prices.bestAsk`, falling back to `prices.buy.yes` |
| Price at post time (scraped picks) and closing line | `GET /v2/candles/{instrumentSymbol}/1hr` | hourly close at the post time / 10 min before start |
| Grade | event by ticker | `status=settled`, `contracts[].resolutionSide` |
| Disputed | `under_review` | stays open, void after 4 days |

Gemini is listed first in every price comparison ("grades this pick"). Kalshi and Polymarket are shown for
verified analysts' new picks, compared on ask price; the star goes to whichever venue is actually cheapest.

**Limit:** Gemini's settled-events API reaches back about five days, so older scraped posts come back as
"no Gemini game" instead of being guessed.

## Layout

| Path | What it does |
|---|---|
| `extension/` | The Chrome extension (Manifest V3). Side panel UI in React with [Astryx](https://astryx.atmeta.com/); `public/content.js` reads Discord and paints badges; `public/background.js` talks to the API |
| `extension/dev/discord-fixture.html` | A Discord-shaped test page with real settled games, for testing the content script without Discord |
| `receipts/observe.py` | Grades observed Discord messages: scrape unverified authors, track verified ones, catch claim codes and edits |
| `receipts/analysts.py` | Tailer and analyst sign-in, the claim code flow, storefront links |
| `receipts/verify.py` | Verified / Unverified records and the Verified Sharp rules |
| `receipts/web.py` | The API the extension uses, plus the shareable SVG badge |
| `receipts/gemini.py`, `parse.py`, `service.py`, `odds.py`, `chain.py`, `contracts/PickRegistry.sol` | Gemini client, moneyline parser, locking and grading, venue comparison, Arc commits |
| `receipts/bot.py` | Optional Discord bot (auto-lock in `#moneyline` channels, `/record`, `/leaderboard`, `/import_x`) |
| `receipts/seed_demo.py` | Demo analysts from real settled Gemini games |
| `tests/` | 45 offline tests |

## Run it

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # macOS/Linux: .venv/bin/pip
cp .env.example .env                                  # uncomment the demo block for the 4-day Sharp window
python -m receipts.seed_demo                          # three labelled demo analysts
uvicorn receipts.web:app --port 8000                  # the API the extension talks to
cd extension && npm install && npm run build          # builds extension/dist
```

Then in Chrome: `chrome://extensions` → turn on **Developer mode** → **Load unpacked** → pick `extension/dist`.
Click the Receipts icon to open the side panel, sign in as a Tailer or an Analyst, and open a Discord channel
on discord.com. If Discord was already open, reload that tab once.

The side panel's "Receipts server" field points the extension at a hosted API instead of localhost. Hosts on
`*.onrender.com` are already allowed in the manifest. `render.yaml` deploys the API to Render's free tier.

Testing without Discord: run `npx vite` in `extension/` and open
`http://localhost:5173/sidepanel.html` (the side panel) and `http://localhost:5173/dev/discord-fixture.html`
(a Discord-shaped page that runs the real content script). Add `?post=RCPT-XXXXXX&as=frank` to post a claim code.

## Demo analysts

| Analyst | Status | Why |
|---|---|---|
| Sharp Sam | Verified · Sharp | Claimed, positive units and closing line value over the demo window |
| Deleter Dan | Verified, not Sharp | Best record, but deleted a losing pick after lock |
| Steady Steph | Unverified | Never claimed; record observed from scraped posts |

All demo rows are labelled. Their games, prices and results are real Gemini data.

## Known limits

* The Discord reader depends on Discord's message markup (`li#chat-messages-CHANNEL-MESSAGE`, `message-username-*`,
  `message-content-*`). All selectors live in `readMessages()` in `extension/public/content.js`.
* Analysts with Discord's default avatar have no user ID in the page, so they're keyed by username until they claim.
* A team name that fits several games (e.g. "Portland": Trail Blazers or the Portland Pilots) is reported as
  ambiguous and not graded.
* Deleted messages are only caught by the Discord bot, not by the extension.
* Sign-in is a named session token for the hackathon: no passwords.
* Not built: Telegram, X scraping in the extension (the bot's `/import_x` still works), payments.
