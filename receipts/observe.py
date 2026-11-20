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

from . import analysts, chain, db, ocr, service
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
    """Active + settled Gemini moneylines, cached. Settled games reach back about five days."""

    def __init__(self, gemini: GeminiClient, ttl: int = 600):
        self.gemini, self.ttl = gemini, ttl
        self._settled: tuple[float, list[Game]] | None = None
        self._lock = threading.Lock()

    def games(self) -> list[Game]:
        with self._lock:
            if not self._settled or time.time() - self._settled[0] > self.ttl:
                self._settled = (time.time(), self.gemini.settled_moneylines())
            settled = self._settled[1]
        active = self.gemini.moneylines()
        seen = {g.ticker for g in active}
        return active + [g for g in settled if g.ticker not in seen]


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
    out = []
    for raw in messages:
        try:
            msg = Message.from_dict(raw)
            posted_at = discord_time(msg.message_id)
        except (KeyError, ValueError):
            out.append({"message_id": raw.get("message_id"), "outcome": "invalid"})
            continue
        mid = msg.message_id

        capper = _touch_capper(msg, conn, now)
        claimed = analysts.confirm_from_message(msg.capper_id, msg.text, conn) if analysts.CODE_RE.search(msg.text) else None
        if claimed:
            out.append({"message_id": mid, "outcome": "claimed", "capper_id": msg.capper_id})
            continue

        existing = [dict(r) for r in conn.execute(
            "SELECT * FROM picks WHERE source='discord' AND (post_id=? OR post_id LIKE ?)", (mid, f"{mid}:slip%"))]
        if existing:
            text_pick = next((e for e in existing if e["post_id"] == mid and e.get("note") != SLIP_NOTE), None)
            outcome = _recheck(text_pick, msg, conn) if text_pick else "seen"
            out.append({"message_id": mid, "outcome": outcome, "pick_id": existing[0]["id"]})
            continue

        candidates: list[tuple[str, ParsedPick, str, str | None]] = []  # post_id, parsed, text, note
        rejection = None
        parsed = parse_pick(msg.text)
        if isinstance(parsed, ParsedPick):
            candidates.append((mid, parsed, msg.text, None))
        elif isinstance(parsed, Rejection):
            rejection = parsed.reason
        if not candidates and msg.images:
            for url in msg.images[:3]:
                slip_text = read_image(url)
                if not slip_text:
                    continue
                slip = ocr.read_slip(slip_text)
                rejection = rejection or slip.rejected
                for pick_text in slip.picks:
                    pp = parse_pick(pick_text)
                    if isinstance(pp, ParsedPick):
                        candidates.append((f"{mid}:slip{len(candidates) + 1}", pp, pick_text, SLIP_NOTE))
        if not candidates:
            if rejection:
                out.append({"message_id": mid, "outcome": "rejected", "reason": rejection})
            else:
                out.append({"message_id": mid, "outcome": "not_a_pick"})
            continue

        verified_at = parse_ts(capper["verified_at"]) if capper.get("verified_at") else None
        if verified_at and posted_at < verified_at:
            out.append({"message_id": mid, "outcome": "before_verification"})
            continue

        if games is None:
            games = (pool or GamePool(gemini)).games()
        for post_id, parsed, text, note in candidates:
            source = {"message_id": mid, "post_id": post_id, "from_slip": note == SLIP_NOTE}
            matches = match_for_post(parsed.team, posted_at, games)
            if not matches:
                out.append({**source, "outcome": "no_game",
                            "reason": f"No Gemini moneyline for \"{parsed.team}\" near that post (Gemini history covers about five days)."})
                continue
            if len(matches) > 1:
                out.append({**source, "outcome": "ambiguous",
                            "reason": f"\"{parsed.team}\" fits {len(matches)} games: " + ", ".join(g.title for g, _ in matches[:3])})
                continue
            game, side = matches[0]
            pick_id = _record(msg, parsed, game, side, posted_at, now, mode="live" if verified_at else "scraped",
                              gemini=gemini, conn=conn, post_id=post_id, text=text, extra_note=note)
            out.append({**source, "outcome": "tracked" if verified_at else "scraped", "pick_id": pick_id})
    return out


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


def _record(msg: Message, parsed: ParsedPick, game: Game, side: Side, posted_at: datetime, now: datetime,
            mode: str, gemini: GeminiClient, conn: sqlite3.Connection, post_id: str | None = None,
            text: str | None = None, extra_note: str | None = None) -> int:
    post_id = post_id or msg.message_id
    text = text if text is not None else msg.text
    start = game.start_time
    note = None
    if now < start and side.price is not None:
        lock_price = side.price  # seen before the game: lock at the live Gemini price
    else:
        lock_price = _safe_price_at(gemini, side.instrument_symbol, posted_at)
        note = "Price from the Gemini candle at post time."
        if lock_price is None:  # market history starts after the post: use Gemini's first price
            lock_price = _first_price(gemini, side.instrument_symbol)
            note = "Price is Gemini's earliest available price (its history starts after the post)."
    close_price = None
    if now >= start - timedelta(minutes=settings.close_snapshot_minutes):
        close_price = _safe_price_at(gemini, side.instrument_symbol, start - timedelta(minutes=settings.close_snapshot_minutes))
    result = "pending"
    if game.status == "settled" and side.resolution in ("yes", "no"):
        result = "win" if side.resolution == "yes" else "loss"

    note = " ".join(n for n in (extra_note, note) if n) or None
    payload = chain.commit_payload(capper_id=msg.capper_id, source="discord", post_id=post_id, text=text,
                                   event_ticker=game.ticker, side_label=side.label, units=parsed.units,
                                   lock_price=lock_price, posted_at=service.iso(posted_at))
    h = chain.commit_hash(payload)
    with db.tx(conn):
        cur = conn.execute(
            """INSERT INTO picks(capper_id, source, post_id, channel_id, guild_id, channel_name, text, team_query, units,
               event_ticker, event_title, event_slug, league, side_label, side_abbr, instrument_symbol, start_time,
               posted_at, locked_at, observed_at, lock_price, close_price, result, graded_at, commit_hash, mode,
               edited_flag, note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (msg.capper_id, "discord", post_id, msg.channel_id, msg.guild_id, msg.channel_name, text,
             parsed.team, parsed.units, game.ticker, game.title, game.slug, game.league, side.label, side.abbreviation,
             side.instrument_symbol, service.iso(start), service.iso(posted_at), service.iso(now), service.iso(now),
             lock_price, close_price, result, service.iso(now) if result != "pending" else None, h, mode,
             int(msg.edited), note))
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
