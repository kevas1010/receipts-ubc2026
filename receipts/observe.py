"""Turn Discord messages that a tailer's extension can already see into graded picks.

* Unverified authors are "scraped": every moneyline they posted is matched to the Gemini game it was for,
  priced from Gemini's hourly candle at the moment it was posted, and graded from Gemini settlement.
* Verified authors are not scraped. Only picks posted after verification count, and they are tracked as live.
* The post time always comes from the Discord message ID, never from the page or the observer's clock.

Only what the observer's own Discord client has rendered is read. Nothing here calls Discord's API, uses a
Discord token, or reaches channels the observer can't open.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import analysts, chain, db, markets, ocr, service, slips
from .config import settings
from .gemini import Game, GeminiClient, Side, match_team, parse_ts
from .parse import ParsedPick, Rejection, parse_pick
from .snowflake import discord_time

log = logging.getLogger(__name__)
MAX_LEAD = timedelta(hours=48)  # a pick is for a game starting within two days of the post


@dataclass
class Message:
    message_id: str
    channel_id: str | None
    guild_id: str | None
    channel_name: str | None
    author_id: str | None
    author_name: str
    avatar_url: str | None
    text: str
    edited: bool = False
    images: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(message_id=str(d["message_id"]), channel_id=d.get("channel_id"), guild_id=d.get("guild_id"),
                   channel_name=d.get("channel_name"), author_id=d.get("author_id"),
                   author_name=(d.get("author_name") or "unknown")[:60], avatar_url=d.get("avatar_url"),
                   text=(d.get("text") or "")[:2000], edited=bool(d.get("edited")),
                   images=tuple(u for u in (d.get("images") or []) if isinstance(u, str))[:4])

    @property
    def capper_id(self) -> str:
        if self.author_id and self.author_id.isdigit():
            return f"discord:{self.author_id}"
        return f"discordname:{self.author_name.lower()}"


class GamePool:
    """Active + settled Gemini sports markets (moneylines, spreads, totals, props), cached.
    Settled markets reach back about a week."""

    def __init__(self, gemini: GeminiClient, ttl: int = 600):
        self.gemini, self.ttl = gemini, ttl
        self._settled: tuple[float, list[Game]] | None = None
        self._lock = threading.Lock()

    def all_markets(self) -> list[Game]:
        with self._lock:
            if not self._settled or time.time() - self._settled[0] > self.ttl:
                self._settled = (time.time(), self.gemini.settled_markets())
            settled = self._settled[1]
        active = self.gemini.markets()
        seen = {g.ticker for g in active}
        return active + [g for g in settled if g.ticker not in seen]

    def games(self) -> list[Game]:
        """Moneylines only (typed "Team ML" picks)."""
        return [g for g in self.all_markets() if g.market_type == "moneyline" or g.ticker.endswith("-M")]


def match_for_post(team: str, posted_at: datetime, games: list[Game]) -> list[tuple[Game, Side]]:
    found = match_team(team, games, at=posted_at)
    return [(g, s) for g, s in found if g.start_time and g.start_time - posted_at <= MAX_LEAD]


SLIP_NOTE = "Read from a bet slip image with OCR."


def ingest(messages: list[dict], gemini: GeminiClient, pool: GamePool | None = None,
           conn: sqlite3.Connection | None = None, now: datetime | None = None,
           read_image=None) -> list[dict]:
    """Process a batch of observed messages. Returns one outcome per pick found (or per message without one).

    A pick can come from the message text or, when the text has none, from bet-slip screenshots attached to it
    (Tesseract OCR). Slip picks get post IDs like "<message_id>:slip1"; their time is still the message's.
    """
    conn = conn or db.connect()
    now = now or datetime.now(timezone.utc)
    read_image = read_image or ocr.fetch_and_read
    games: list[Game] | None = None
    all_games: list[Game] | None = None
    out = []
    revealed: set[str] = set()
    for raw in messages:
        try:
            msg = Message.from_dict(raw)
            posted_at = discord_time(msg.message_id)
        except (KeyError, ValueError):
            out.append({"message_id": raw.get("message_id"), "outcome": "invalid"})
            continue
        mid = msg.message_id

        capper = _touch_capper(msg, conn, now)
        if capper.get("demo_staged"):  # pre-imported for the demo: pressing Load reveals the checked record
            with db.tx(conn):
                conn.execute("UPDATE cappers SET demo_staged=0 WHERE id=?", (msg.capper_id,))
            capper["demo_staged"] = 0
            revealed.add(msg.capper_id)
        claimed = analysts.confirm_from_message(msg.capper_id, msg.text, conn) if analysts.CODE_RE.search(msg.text) else None
        if claimed:
            out.append({"message_id": mid, "outcome": "claimed", "capper_id": msg.capper_id})
            continue

        existing = [dict(r) for r in conn.execute(
            "SELECT * FROM picks WHERE source='discord' AND (post_id=? OR post_id LIKE ?)", (mid, f"{mid}:slip%"))]
        if existing:
            text_pick = next((e for e in existing if e["post_id"] == mid and not (e.get("note") or "").startswith(SLIP_NOTE)), None)
            outcome = _recheck(text_pick, msg, conn) if text_pick else "seen"
            if msg.capper_id in revealed and outcome == "seen":
                for e in existing:
                    out.append({"message_id": mid, "post_id": e["post_id"], "outcome": "scraped", "pick_id": e["id"],
                                "bet": e.get("bet_text") or e.get("team_query"), "from_slip": "slip" in e["post_id"]})
                continue
            out.append({"message_id": mid, "outcome": outcome, "pick_id": existing[0]["id"]})
            continue

        if capper.get("verified_at") and not settings.scrape_verified and posted_at >= parse_ts(capper["verified_at"]):
            # Verified analysts post through PickProof (hash-committed); the consumer scraper skips them.
            out.append({"message_id": mid, "outcome": "verified_source"})
            continue

        cands: list[Cand] = []
        rejections: list[str] = []
        parsed = parse_pick(msg.text)
        if isinstance(parsed, ParsedPick):
            cands.append(Cand(mid, parsed.team, parsed.units, msg.text, None, None))
        elif isinstance(parsed, Rejection) and not msg.images and len(msg.text) <= 120:
            leg = slips.parse_typed(msg.text)  # "Pistons -5.5 2u", "Maxey over 5.5 assists"
            if leg:
                units = slips.message_meta(msg.text)[0] or 1.0
                cands.append(Cand(mid, leg.describe(), units, msg.text, None, leg))
            else:
                rejections.append(parsed.reason)
        if not cands and msg.images:
            for url in msg.images[:3]:
                slip_text = read_image(url)
                if not slip_text:
                    continue
                slip = slips.parse_slip(slip_text, msg.text)
                legs = [l for l in slip.legs if not l.needs_review]
                rejections += [f"{l.describe()}: couldn't read this leg clearly." for l in slip.legs if l.needs_review]
                if not legs:
                    continue
                stake = (slip.units or 1.0) / len(legs)
                for k, leg in enumerate(legs, 1):
                    note = SLIP_NOTE if len(legs) == 1 else f"{SLIP_NOTE} Leg {k} of {len(legs)}; the slip's stake is split evenly."
                    cands.append(Cand(f"{mid}:slip{len(cands) + 1}", leg.describe(), round(stake, 3), leg.describe(), note, leg))
        if not cands:
            if rejections:
                out.append({"message_id": mid, "outcome": "rejected", "reason": " ".join(dict.fromkeys(rejections))})
            else:
                out.append({"message_id": mid, "outcome": "not_a_pick"})
            continue

        verified_at = parse_ts(capper["verified_at"]) if capper.get("verified_at") else None
        if verified_at and posted_at < verified_at:
            out.append({"message_id": mid, "outcome": "before_verification"})
            continue

        pool = pool or GamePool(gemini)
        for c in cands:
            source = {"message_id": mid, "post_id": c.post_id, "from_slip": bool(c.note), "bet": c.team_query}
            if c.leg is None:  # typed moneyline: the original matching rules
                games = games if games is not None else pool.games()
                found = match_for_post(c.team_query, posted_at, games)
                if not found:
                    out.append({**source, "outcome": "no_game",
                                "reason": f"No Gemini moneyline for \"{c.team_query}\" near that post (Gemini history covers about a week)."})
                    continue
                if len(found) > 1:
                    out.append({**source, "outcome": "ambiguous",
                                "reason": f"\"{c.team_query}\" fits {len(found)} games: " + ", ".join(g.title for g, _ in found[:3])})
                    continue
                game, side = found[0]
                position, mtype = "yes", "moneyline"
            else:
                all_games = all_games if all_games is not None else pool.all_markets()
                m = markets.match_leg(c.leg, posted_at, all_games)
                if isinstance(m, markets.NoMatch):
                    out.append({**source, "outcome": "no_game", "reason": f"{c.team_query}: {m.reason}"})
                    continue
                game, side, position, mtype = m.game, m.side, m.position, c.leg.kind
            if conn.execute("SELECT 1 FROM picks WHERE source='discord' AND post_id=?", (c.post_id,)).fetchone():
                continue
            pick_id = _record(msg, c.team_query, c.units, game, side, posted_at, now,
                              mode="live" if verified_at else "scraped", gemini=gemini, conn=conn,
                              post_id=c.post_id, text=c.text, extra_note=c.note, position=position, market_type=mtype)
            out.append({**source, "outcome": "tracked" if verified_at else "scraped", "pick_id": pick_id})
    return out


@dataclass
class Cand:
    post_id: str
    team_query: str          # the bet in words ("Bucks +9.5", "Mario Hezonja over 5.5 Rebounds")
    units: float
    text: str
    note: str | None
    leg: slips.Leg | None    # None = typed moneyline handled by the original parser


def _touch_capper(msg: Message, conn: sqlite3.Connection, now: datetime) -> dict:
    capper = service.ensure_capper(msg.capper_id, msg.author_name, msg.avatar_url, conn=conn)
    stamp = service.iso(now)
    with db.tx(conn):
        conn.execute("UPDATE cappers SET discord_id=COALESCE(discord_id, ?), first_seen=COALESCE(first_seen, ?), last_seen=? WHERE id=?",
                     (msg.author_id, stamp, stamp, msg.capper_id))
    return dict(conn.execute("SELECT * FROM cappers WHERE id=?", (msg.capper_id,)).fetchone())


def _recheck(pick: dict, msg: Message, conn: sqlite3.Connection) -> str:
    """Seen before. If the text changed, a live pick is flagged as edited; the committed text stays."""
    if msg.text.strip() == pick["text"].strip():
        return "seen"
    if pick["mode"] == "live":
        service.flag("discord", msg.message_id, "edit", msg.text, conn=conn)
    else:
        with db.tx(conn):
            conn.execute("UPDATE picks SET edited_flag=1 WHERE id=?", (pick["id"],))
    return "edited"


def _record(msg: Message, bet: str, units: float, game: Game, side: Side, posted_at: datetime, now: datetime,
            mode: str, gemini: GeminiClient, conn: sqlite3.Connection, post_id: str | None = None,
            text: str | None = None, extra_note: str | None = None, position: str = "yes",
            market_type: str = "moneyline") -> int:
    post_id = post_id or msg.message_id
    text = text if text is not None else msg.text
    start = game.start_time
    note = None
    flip = (lambda p: p) if position == "yes" else (lambda p: None if p is None else round(1 - p, 4))
    live = markets.position_price(game, side, position)
    if now < start and live is not None:
        lock_price = live  # seen before the game: lock at the live Gemini price for this side
    else:
        lock_price = flip(_safe_price_at(gemini, side.instrument_symbol, posted_at))
        note = "Price from the Gemini candle at post time."
        if lock_price is None:  # market history starts after the post: use Gemini's first price
            lock_price = flip(_first_price(gemini, side.instrument_symbol))
            note = "Price is Gemini's earliest available price (its history starts after the post)."
    close_price = None
    if now >= start - timedelta(minutes=settings.close_snapshot_minutes):
        close_price = flip(_safe_price_at(gemini, side.instrument_symbol, start - timedelta(minutes=settings.close_snapshot_minutes)))
    result = "pending"
    if game.status == "settled" and side.resolution in ("yes", "no"):
        result = "win" if side.resolution == position else "loss"

    note = " ".join(n for n in (extra_note, note) if n) or None
    committed_side = side.label if position == "yes" and market_type == "moneyline" else f"{position.upper()}: {side.label}"
    payload = chain.commit_payload(capper_id=msg.capper_id, source="discord", post_id=post_id, text=text,
                                   event_ticker=game.ticker, side_label=committed_side, units=units,
                                   lock_price=lock_price, posted_at=service.iso(posted_at))
    h = chain.commit_hash(payload)
    with db.tx(conn):
        cur = conn.execute(
            """INSERT INTO picks(capper_id, source, post_id, channel_id, guild_id, channel_name, text, team_query, units,
               event_ticker, event_title, event_slug, league, side_label, side_abbr, instrument_symbol, start_time,
               posted_at, locked_at, observed_at, lock_price, close_price, result, graded_at, commit_hash, mode,
               edited_flag, note, position, market_type, bet_text)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (msg.capper_id, "discord", post_id, msg.channel_id, msg.guild_id, msg.channel_name, text,
             bet, units, game.ticker, game.title, game.slug, game.league, side.label, side.abbreviation,
             side.instrument_symbol, service.iso(start), service.iso(posted_at), service.iso(now), service.iso(now),
             lock_price, close_price, result, service.iso(now) if result != "pending" else None, h, mode,
             int(msg.edited), note, position, market_type, bet))
        pick_id = cur.lastrowid
    if mode == "live":
        tx_hash = chain.anchor(h, f"discord:{post_id}")
        if tx_hash:
            with db.tx(conn):
                conn.execute("UPDATE picks SET tx_hash=? WHERE id=?", (tx_hash, pick_id))
    return pick_id


def _safe_price_at(gemini: GeminiClient, symbol: str, when: datetime) -> float | None:
    try:
        return gemini.price_at(symbol, when)
    except Exception as e:
        log.info("candle lookup failed for %s: %s", symbol, e)
        return None


def _first_price(gemini: GeminiClient, symbol: str) -> float | None:
    try:
        candles = gemini.candles(symbol)
    except Exception as e:
        log.info("candles failed for %s: %s", symbol, e)
        return None
    return candles[0]["close"] if candles else None


def badges(capper_ids: list[str], conn: sqlite3.Connection | None = None) -> dict[str, dict]:
    """Compact per-author stats for the inline Discord badges."""
    from . import verify

    conn = conn or db.connect()
    out = {}
    for row in verify.leaderboard(conn, capper_ids=capper_ids):
        out[row["id"]] = {"tier": row["tier"], "wins": row["wins"], "losses": row["losses"],
                          "win_rate": row["win_rate"], "units": row["units"], "pending": row["pending"],
                          "record_kind": row["record_kind"], "handle": row["handle"]}
    return out
