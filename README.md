# PickProof: proof before you tail

A **Chrome extension** that grades sports pick sellers ("analysts") from what they actually posted in Discord,
using **Gemini Predictions** as the source of truth for prices and results, with tamper-evident commits on
**Arc testnet**. No standalone site: everything happens in a side panel next to Discord, plus a small badge
next to each analyst's name in the chat.

The side panel opens on a start page with two paths, **For customers** and **For analysts**:

| | For customers (tailers) | For analysts (post picks) |
|---|---|---|
| Goal | Know which sellers are actually good before paying | Prove a real record to buyers |
| What PickProof does | Reads the picks analysts posted in channels you can already see (typed or bet slip screenshots), matches each leg to the Gemini contract that settles it, prices it at the Gemini price when it was posted, grades it from Gemini settlement | Three-step verification: claim your Discord account, get a **PickProof credential** (a public keccak256 hash), then post hash-committed picks through PickProof |
| Sees | Per-channel and global analyst scorecards, badges in Discord, each pick with its Gemini price, close, chart and result | Verification steps, the credential, a "post a pick" box, a simulator on last week's settled Gemini markets, a badge and share text |

### Verified vs Unverified

* **Unverified** (never claimed): PickProof keeps scraping their past posts as tailers scroll. The record is
  labelled **observed** and may be incomplete. Not registering never implies dishonesty.
* **Verified** (claimed the Discord account): free, can't be bought, no waiting period. They get a PickProof
  credential, `keccak256("pickproof:v1:<analyst id>:<verified at>")`, anchored on Arc when a key is set. From
  the moment of verification the customer scraper **stops reading** their Discord posts (`SCRAPE_VERIFIED=false`,
  the default): their public record is every pick they post through PickProof, each with a hash commitment.
  Older scraped picks are shown separately as "before verification".
* **Verified Sharp** (optional achievement): the original one-month rules over tracked picks, all in `.env`:
  30 days of history, 15 graded picks, 10 active days, zero edits or deletes, every pick pregame, positive
  units at Gemini prices, non-negative closing line value. The demo uses a 4-day window.

### How claiming works

The analyst gets a one-time code (`RCPT-XXXXXX`) in the side panel and posts it in any Discord channel. The
first PickProof extension that sees that message (theirs or any tailer's) reports it, and the message's author
becomes the verified account. Only the person logged into that Discord account can post as it, so that's the
ownership proof.

### Bet slip screenshots (Tesseract OCR)

Most analysts post a screenshot of the slip instead of typing the pick, usually from pick'em apps (Chalkboard,
Underdog/PrizePicks-style cards) rather than sportsbooks. When a message has image attachments, the server
downloads the image (only from Discord's own image hosts, size-capped), cleans it up (grayscale, upscale,
dark-mode inversion) and reads it with **Tesseract** twice: once for the whole slip and once for the right-hand
column, where pick'em apps print the line and stat. Tesseract often drops decimals ("35" for 3.5) or reads the
↑ arrow as a "1" ("135" for ↑3.5); the two passes are cross-checked to recover the real line.

`receipts/slips.py` turns the text into legs, and `receipts/markets.py` matches each leg to the Gemini contract
that settles it. Every pick is a YES or NO position on one Gemini contract:

| Slip says | Gemini contract | Position |
|---|---|---|
| Detroit Pistons · Moneyline | `Detroit` (…-TOR-DET-M) | YES |
| Milwaukee Bucks +9.5 Spread | `Portland wins by more than 9.5 points` (…-POR-MIL-S) | NO |
| Total Points · Over 218.5 | `More than 218.5 points scored` (…-DET-SAS-T) | YES (Under = NO) |
| Mario Hezonja ↑ 5.5 Rebounds | `Mario Hezonja 6+ Rebounds` (…-DAL-CLE-PPREB) | YES (↓ = NO) |

Rules: only exact lines count (a +6.5 slip is not graded on Gemini's +5.5); props Gemini doesn't list (points +
rebounds + assists, steals, tackles, pass attempts) and esports are reported as "not on Gemini" with the reason;
a multi-leg slip is split into its legs, each tracked as its own pick with the slip's stake split evenly. Units
come from the message ("NBA 2U Straight", "4-6U" = 5u). The post time is still the message's, and the pick is
labelled "Read from a bet slip image with OCR". OCR results are cached per attachment.

Verified analysts can type the same bets in the side panel: `Pistons -5.5 2u`, `Pistons/Spurs over 218.5`,
`Tyrese Maxey over 5.5 assists`.

Install Tesseract locally (Windows: the UB Mannheim build; macOS: `brew install tesseract`; Linux:
`apt install tesseract-ocr`). The Dockerfile used for deployment installs it automatically.

### What the extension reads, and what it doesn't

* Only messages Discord has already rendered on your screen, in channels you can already open.
* No Discord API calls, no Discord token, nothing posted. There's a switch to turn reading off.
* It never scrolls on its own. **Load 30 days** in the side panel scrolls the open channel back for you (at
  human pace, about 50 messages a page) so a tailer doesn't have to; it stops at the channel's first message.
* Post times come from Discord message IDs (snowflakes), which encode creation time and can't be edited.

## Gemini Prediction Markets usage

| Job | Endpoint | Field |
|---|---|---|
| Find markets | `GET /v1/prediction-markets/events?status=active&category=sports` and `status=settled` | `sportsMarket.type` (moneyline, spread, total, prop), `scope`, `metric`, `participants` |
| Match a post to a game | same | contract labels, participant names, abbreviations, `startTime` (game must start after the post and within 48 h) |
| Price at lock (live) | event list / `GET /v1/prediction-markets/events/{ticker}` | `prices.bestAsk`, falling back to `prices.buy.yes` |
| Price at post time (scraped picks) and closing line | `GET /v2/candles/{instrumentSymbol}/1hr` | hourly close at the post time / 10 min before start |
| Grade | event by ticker | `status=settled`, `contracts[].resolutionSide` compared with the pick's YES/NO position |
| Disputed | `under_review` | stays open, void after 4 days |

Gemini is listed first in every price comparison ("grades this pick"). Kalshi and Polymarket are shown for
verified analysts' new picks, compared on ask price; the star goes to whichever venue is actually cheapest.

**Limit:** Gemini's settled-events API returns about 1,000 events (roughly a week of games), so older scraped
posts come back as "no Gemini market" instead of being guessed. NO prices are Gemini's `buy.no` quote live, and
1 − the YES candle for history.

## Layout

| Path | What it does |
|---|---|
| `extension/` | The Chrome extension (Manifest V3). Side panel UI in React with [Astryx](https://astryx.atmeta.com/); `public/content.js` reads Discord and paints badges; `public/background.js` talks to the API |
| `extension/dev/discord-fixture.html` | A Discord-shaped test page with real settled games, for testing the content script without Discord |
| `receipts/observe.py` | Grades observed Discord messages: scrape unverified authors, track verified ones, catch claim codes and edits |
| `receipts/ocr.py` | Tesseract OCR for bet-slip images (full pass + right-column pass, cached) |
| `receipts/slips.py` | Slip text → legs (moneyline, spread, total, player prop), units and payout from the message |
| `receipts/markets.py` | Leg → Gemini contract + YES/NO position, exact lines only |
| `receipts/import_export.py` | Import channel exports, stage analysts for the demo, list what's loaded |
| `receipts/analysts.py` | Tailer and analyst sign-in, the claim code flow, storefront links |
| `receipts/verify.py` | Verified / Unverified records and the Verified Sharp rules |
| `receipts/web.py` | The API the extension uses, plus the shareable SVG badge |
| `receipts/gemini.py`, `parse.py`, `service.py`, `odds.py`, `chain.py`, `contracts/PickRegistry.sol` | Gemini client, moneyline parser, locking and grading, venue comparison, Arc commits |
| `receipts/bot.py` | Optional Discord bot (auto-lock in `#moneyline` channels, `/record`, `/leaderboard`, `/import_x`) |
| `receipts/seed_demo.py` | Demo analysts from real settled Gemini games |
| `tests/` | 80 offline tests (the Tesseract one is skipped if Tesseract isn't installed); `tests/test_slips.py` uses OCR text from 11 real analyst slips (`tests/fixtures/real_slips/`) and real Gemini contract wording |

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
Click the PickProof icon to open the side panel, sign in as a Tailer or an Analyst, and open a Discord channel
on discord.com. If Discord was already open, reload that tab once.

The side panel's "PickProof server" field points the extension at a hosted API instead of localhost. Hosts on
`*.onrender.com` are already allowed in the manifest. `render.yaml` deploys the API (Docker, with Tesseract)
to Render's free tier.

Testing without Discord: run `npx vite` in `extension/` and open
`http://localhost:5173/sidepanel.html` (the side panel) and `http://localhost:5173/dev/discord-fixture.html`
(a Discord-shaped page that runs the real content script, including bet slip screenshots). Add
`?post=RCPT-XXXXXX&as=frank` to post a claim code. Set `OCR_ALLOW_LOCAL=1` in `.env` so the server may read the
test page's slip images from localhost.

## Demo with real Discord data

1. **Export the analyst channels.** Each export is a DiscordChatExporter-format JSON file of the messages the
   signed-in user can see (`pickproof-export-X.json`, ...). They were made in the browser by scrolling each
   channel; no Discord token or API. Image links in an export expire after about a day, so import the same day.
2. **Import them** (OCR, Gemini matching and grading; slow the first time, cached after):
   ```bash
   python -m receipts.import_export exports/*.json
   ```
3. **Stage one analyst for the live demo.** A staged analyst is fully imported and graded but hidden. When you
   open their channel and press **Load 30 days**, the scan reveals the already-checked record in seconds:
   ```bash
   python -m receipts.import_export --stage Bronco
   python -m receipts.import_export --list          # check what's loaded and staged
   ```
4. **Snapshot it.** Stop the server and copy `receipts.db` to `demo.db`; set `RECEIPTS_DB` to a copy of `demo.db`
   to run the demo, so it can be reset. Don't commit either file: they hold real usernames and picks.
5. Set `DEMO_MODE=true` in `.env` to show the **Verify instantly (demo)** button on the analyst path, so the
   verification flow can be shown without posting a claim code in Discord. Demo-verified analysts are labelled
   demo.

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
* Over/under arrows on pick'em slips are read from OCR glyphs (↑ often becomes "t", ↓ becomes "Lv"); when the
  arrow is unreadable the leg is assumed to be an over, which is what nearly all of these slips are.
* Gemini prop ladders mostly step by 2 (4+, 6+, 8+), so many pick'em lines (over 10.5 = 11+) have no exact
  Gemini contract and are reported, not graded.
* Not built: Telegram, X scraping in the extension (the bot's `/import_x` still works), payments, Gemini combos
  (parlay contracts aren't enabled in Gemini's production API yet).
