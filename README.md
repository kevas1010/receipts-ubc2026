# Receipts

Tamper-proof moneyline records for sports cappers, graded on **Gemini Predictions**.

Cappers keep posting picks in Discord the way they already do. Receipts locks each moneyline with the
live Gemini price, timestamps it from the message ID (which can't be faked), commits a keccak hash to
**Arc testnet**, catches edits and deletes, and grades the pick from Gemini settlement. The badge
rewards a complete, untouched month, not a hot streak.

Everything here runs on free services: Gemini, Kalshi and Polymarket public market data (no keys),
a free Discord bot, Arc testnet (free faucet USDC for gas), X's public oEmbed, SQLite, and Render's free tier.

## Gemini Prediction Markets usage

Gemini is the only market that touches a pick's record.

| Job | Endpoint | Field |
|---|---|---|
| Find eligible games | `GET /v1/prediction-markets/events?status=active&category=sports` | tickers ending `-M` (moneylines); `-S`/`-T` skipped |
| Match the team | same | `contracts[].label`, `participants[].name`, `abbreviatedName` |
| Lock-time check | same | `startTime` vs. the post ID's timestamp |
| Price at lock | same / `GET /v1/prediction-markets/events/{ticker}` | `prices.bestAsk`, falling back to `prices.buy.yes` |
| Closing line | event by ticker, ~10 min before start | `prices.bestAsk` |
| Closing line fallback, demo seed, profile sparklines | `GET /v2/candles/{instrumentSymbol}/1hr` | hourly close |
| Grade | event by ticker | `status=settled`, `contracts[].resolutionSide` (`yes` = win) |
| Disputed games | `status=under_review` | stays open, void after 4 days unsettled |

Gemini shows up for users as the price on every lock receipt, "Graded on Gemini Predictions" on every
embed and page, Gemini listed first ("grades this pick") in the price comparison, a "Tail on Gemini"
link, and per-pick Gemini price charts on profiles. Kalshi and Polymarket appear only in the comparison
for verified cappers, compared on ask price. Gemini wins ties, and the star always goes to the venue that is
actually cheapest. Set `SHOW_OTHER_VENUES=false` to hide them.

## Layout

| Path | What it does |
|---|---|
| `receipts/parse.py` | Moneyline-only parser; rejects spreads, totals, props, parlays, stakes over `MAX_UNITS` |
| `receipts/gemini.py` | Gemini client: discovery, matching (soonest game per team, asks when the name fits two teams), prices, settlement, candles, retries |
| `receipts/service.py` | Lock, flag edits and deletes, closing-line snapshot, grading, re-anchoring |
| `receipts/verify.py` | Rolling-window rules, record, units at Gemini prices, closing line value, tiers |
| `receipts/odds.py` | Gemini-first comparison with Kalshi and Polymarket |
| `receipts/chain.py`, `contracts/PickRegistry.sol` | keccak commits, Arc testnet anchoring (no web3 dependency) |
| `receipts/bot.py` | Discord bot: auto-lock, edit/delete callouts, `/record`, `/leaderboard`, `/import_x`, role sync |
| `receipts/web.py` | JSON API, embeddable SVG badge, serves the UI |
| `receipts/seed_demo.py` | Demo cappers from real settled Gemini games and real Gemini candle prices |
| `web/` | React UI built with [Astryx](https://astryx.atmeta.com/) (Meta's design system): leaderboard, profiles, "Try a pick" |
| `tests/` | 36 offline tests |

## Run it locally

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
cp .env.example .env
cd web && npm install && npm run build && cd ..
python -m receipts.seed_demo                        # optional demo data
uvicorn receipts.web:app --port 8000                # http://localhost:8000
python -m receipts.bot                              # needs DISCORD_TOKEN
python -m pytest -q tests
```

For UI work, run `npm run dev` in `web/`; it proxies `/api` and `/badge` to port 8000.

### Discord (free)

1. Create an application at https://discord.com/developers/applications and add a bot.
2. Turn on the **Message Content** and **Server Members** intents.
3. Invite it with the `bot` and `applications.commands` scopes and permissions to read and send messages,
   read message history, embed links and manage roles.
4. Create a channel whose name contains `moneyline` or `ml-picks`, and a role named `Verified Capper`
   placed below the bot's role.
5. Put the token in `.env` as `DISCORD_TOKEN`.

### Arc testnet (free)

1. Get testnet USDC (gas) from https://faucet.circle.com for a fresh wallet.
2. Deploy with Foundry (free):
   `forge create contracts/PickRegistry.sol:PickRegistry --rpc-url https://rpc.testnet.arc.network --private-key $ARC_PRIVATE_KEY --broadcast`
3. Put `ARC_PRIVATE_KEY` and `PICK_REGISTRY_ADDRESS` in `.env`.

Without these, picks lock "unanchored": the hash is stored locally and anchored by the bot's 5-minute
loop once a key is set. `chain.recent_commits()` reads `PickCommitted` logs from the public RPC, the free
replacement for a paid event stream.

### Deploy (free)

`render.yaml` deploys the web app on Render's free tier. The free disk is wiped on restart, so the
start command reseeds the demo from live Gemini data. Run the bot on your own machine, or on any
always-on free host.

## Verification rules

All are `.env` settings and computed over a rolling window.

| Rule | Default | Demo |
|---|---|---|
| Days since first logged pick | 30 | 4 |
| Graded moneyline picks in the window | 15 | 8 |
| Days with at least one pick | 10 | 3 |
| Edits or deletes after lock | 0 | 0 |
| Every pick posted before game start | all | all |

**Verified** = every rule passes. **Verified · Sharp** = verified, positive units at Gemini prices,
and non-negative average closing line value.

The demo uses the compressed window (`DEMO_MODE=true`) and says so on every page. Rerun
`python -m receipts.seed_demo` right before demoing so the rolling window covers the seeded games.

## 90-second demo

1. The problem: a capper's "7-0 week" screenshot with nothing behind it.
2. In `#moneyline`, post a live game's pick (use `/try` to find one), e.g. `Detroit ML 2u`. The bot replies
   "Locked" with the Gemini price, start time and Arc commit.
3. Edit the message to the other team. The bot posts the original, as committed.
4. Post a pick for a game that already started. The bot refuses it.
5. Open the leaderboard: Deleter Dan has the best record but isn't verified because he deleted a loss.
6. Post as a verified capper: the receipt shows Gemini first, then Kalshi and Polymarket, with the star
   on the venue that's actually cheapest.
7. Close on a profile: every pick, its Gemini price, the Gemini price chart, result and hash.

## Known limits

- `GEMINI_MARKET_URL` is a placeholder (`https://www.gemini.com/predictions/{slug}`). Confirm the format
  with the Gemini team.
- Kalshi and Polymarket matching was checked live on NBA and NHL games. NFL and MLB use the same code but were not tested. College games usually show Gemini only.
- X imports are accepted only before the game starts. Re-checking imported X posts for deletion after
  the game is not built yet.
- Telegram is not built.
