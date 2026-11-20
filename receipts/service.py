"""Lock picks, flag edits and deletes, snapshot the closing line, grade from Gemini settlement."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import chain, db
from .config import settings
from .gemini import Game, GeminiClient, Side, parse_ts
from .parse import ParsedPick, Rejection, parse_pick

log = logging.getLogger(__name__)


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "capper"


@dataclass
class LockResult:
    ok: bool
    message: str
    pick: dict | None = None
    choices: list[tuple[Game, Side]] = field(default_factory=list)
    game: Game | None = None
    side: Side | None = None


# ---- cappers -------------------------------------------------------------
def ensure_capper(capper_id: str, display_name: str, avatar_url: str | None = None, demo: bool = False,
                  handle: str | None = None, conn: sqlite3.Connection | None = None) -> dict:
    conn = conn or db.connect()
    row = conn.execute("SELECT * FROM cappers WHERE id=?", (capper_id,)).fetchone()
    if row:
        if avatar_url and row["avatar_url"] != avatar_url:
            conn.execute("UPDATE cappers SET avatar_url=?, display_name=? WHERE id=?", (avatar_url, display_name, capper_id))
            conn.commit()
        return dict(row)
    base = handle or slugify(display_name)
    h, n = base, 1
    while conn.execute("SELECT 1 FROM cappers WHERE handle=?", (h,)).fetchone():
        n += 1
        h = f"{base}-{n}"
    with db.tx(conn):
        conn.execute("INSERT INTO cappers(id, handle, display_name, avatar_url, demo, created_at) VALUES (?,?,?,?,?,?)",
                     (capper_id, h, display_name, avatar_url, int(demo), iso(now())))
    return dict(conn.execute("SELECT * FROM cappers WHERE id=?", (capper_id,)).fetchone())


# ---- locking -------------------------------------------------------------
def lock_pick(*, capper_id: str, source: str, post_id: str, text: str, posted_at: datetime,
              gemini: GeminiClient, channel_id: str | None = None, guild_id: str | None = None,
              choose_ticker: str | None = None, games: list[Game] | None = None,
              conn: sqlite3.Connection | None = None, anchor: bool = True) -> LockResult:
    """posted_at must come from the post ID (Discord or X snowflake), never from the receive time."""
    conn = conn or db.connect()
    existing = conn.execute("SELECT * FROM picks WHERE source=? AND post_id=?", (source, str(post_id))).fetchone()
    if existing:
        return LockResult(True, "Already locked.", dict(existing))

    parsed = parse_pick(text)
    if parsed is None:
        return LockResult(False, "")
    if isinstance(parsed, Rejection):
        return LockResult(False, parsed.reason)
    assert isinstance(parsed, ParsedPick)

    # Match against every Gemini moneyline, including started ones, so we can say *why* we refuse.
    pool = games if games is not None else gemini.moneylines()
    all_matches = gemini.match(parsed.team, pool, at=datetime.min.replace(tzinfo=timezone.utc))
    if choose_ticker:
        all_matches = [(g, s) for g, s in all_matches if g.ticker == choose_ticker]
    if not all_matches:
        return LockResult(False, f"No Gemini moneyline found for \"{parsed.team}\". Use the full team name.")
    open_matches = [(g, s) for g, s in all_matches if g.start_time and g.start_time > posted_at]
    if not open_matches:
        g = all_matches[0][0]
        return LockResult(False, f"{g.title} started at {g.start_time:%H:%M} UTC, before this was posted. Picks must be posted before the game starts.", game=g)
    tickers = {g.ticker for g, _ in open_matches}
    if len(tickers) > 1 or len(open_matches) > 1:
        return LockResult(False, f"\"{parsed.team}\" matches more than one game. Which one?", choices=open_matches)

    game, side = open_matches[0]
    lock_price = side.price
    payload = chain.commit_payload(capper_id=capper_id, source=source, post_id=str(post_id), text=text,
                                   event_ticker=game.ticker, side_label=side.label, units=parsed.units,
                                   lock_price=lock_price, posted_at=iso(posted_at))
    h = chain.commit_hash(payload)
    with db.tx(conn):
        cur = conn.execute(
            """INSERT INTO picks(capper_id, source, post_id, channel_id, guild_id, text, team_query, units,
               event_ticker, event_title, event_slug, league, side_label, side_abbr, instrument_symbol, start_time,
               posted_at, locked_at, lock_price, commit_hash)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (capper_id, source, str(post_id), channel_id, guild_id, text, parsed.team, parsed.units,
             game.ticker, game.title, game.slug, game.league, side.label, side.abbreviation, side.instrument_symbol,
             iso(game.start_time), iso(posted_at), iso(now()), lock_price, h))
        pick_id = cur.lastrowid
    if anchor:
        tx_hash = chain.anchor(h, f"{source}:{post_id}")
        if tx_hash:
            with db.tx(conn):
                conn.execute("UPDATE picks SET tx_hash=? WHERE id=?", (tx_hash, pick_id))
    pick = dict(conn.execute("SELECT * FROM picks WHERE id=?", (pick_id,)).fetchone())
    return LockResult(True, "Locked", pick, game=game, side=side)


def set_reply(pick_id: int, reply_id: str, conn=None):
    conn = conn or db.connect()
    with db.tx(conn):
        conn.execute("UPDATE picks SET reply_id=? WHERE id=?", (reply_id, pick_id))


def save_venues(pick_id: int, venues_json: str, conn=None):
    conn = conn or db.connect()
    with db.tx(conn):
        conn.execute("UPDATE picks SET venues_json=? WHERE id=?", (venues_json, pick_id))


# ---- edits and deletes ---------------------------------------------------
def flag(source: str, post_id: str, kind: str, new_text: str | None = None, conn=None) -> dict | None:
    """Record an edit or delete of a locked pick. The original stays exactly as committed."""
    conn = conn or db.connect()
    row = conn.execute("SELECT * FROM picks WHERE source=? AND post_id=?", (source, str(post_id))).fetchone()
    if not row:
        return None
    if kind == "edit" and new_text is not None and new_text == row["text"]:
        return None  # embeds unfurling etc. fire edit events without a content change
    with db.tx(conn):
        conn.execute("INSERT INTO pick_events(pick_id, kind, at, new_text) VALUES (?,?,?,?)",
                     (row["id"], kind, iso(now()), new_text))
        status = "deleted" if kind == "delete" else ("deleted" if row["status"] == "deleted" else "edited")
        conn.execute("UPDATE picks SET status=? WHERE id=?", (status, row["id"]))
    return dict(conn.execute("SELECT * FROM picks WHERE id=?", (row["id"],)).fetchone())


# ---- closing line + grading ---------------------------------------------
def snapshot_closes(gemini: GeminiClient, conn=None, at: datetime | None = None) -> int:
    """About CLOSE_SNAPSHOT_MINUTES before start, record the Gemini price as the closing line.
    Missed the window? Fall back to the hourly candle at start."""
    conn = conn or db.connect()
    at = at or now()
    n = 0
    rows = conn.execute("SELECT * FROM picks WHERE close_price IS NULL").fetchall()
    for r in rows:
        start = parse_ts(r["start_time"])
        if at < start - timedelta(minutes=settings.close_snapshot_minutes):
            continue
        price = None
        try:
            if at < start:
                g = gemini.event(r["event_ticker"])
                s = g.side(r["side_label"])
                price = s.price if s else None
            if price is None:
                price = gemini.price_at(r["instrument_symbol"], start - timedelta(minutes=settings.close_snapshot_minutes))
        except Exception as e:
            log.info("close snapshot failed for %s: %s", r["event_ticker"], e)
        if price is not None:
            with db.tx(conn):
                conn.execute("UPDATE picks SET close_price=? WHERE id=?", (price, r["id"]))
            n += 1
    return n


def grade_pending(gemini: GeminiClient, conn=None, at: datetime | None = None) -> list[dict]:
    """Grade from Gemini resolutionSide. under_review stays open; void after VOID_AFTER_DAYS unsettled."""
    conn = conn or db.connect()
    at = at or now()
    graded = []
    rows = conn.execute("SELECT * FROM picks WHERE result='pending'").fetchall()
    cache: dict[str, Game] = {}
    for r in rows:
        start = parse_ts(r["start_time"])
        if at < start:
            continue
        try:
            g = cache.get(r["event_ticker"]) or gemini.event(r["event_ticker"])
            cache[r["event_ticker"]] = g
        except Exception as e:
            log.info("grade fetch failed for %s: %s", r["event_ticker"], e)
            continue
        side = g.side(r["side_label"])
        result = None
        if g.status == "settled" and side and side.resolution in ("yes", "no"):
            result = "win" if side.resolution == "yes" else "loss"
        elif at - start > timedelta(days=settings.void_after_days):
            result = "void"
        if result:
            with db.tx(conn):
                conn.execute("UPDATE picks SET result=?, graded_at=? WHERE id=?", (result, iso(at), r["id"]))
            graded.append({**dict(r), "result": result})
    return graded


def anchor_pending(conn=None, limit: int = 20) -> int:
    conn = conn or db.connect()
    if not settings.chain_enabled:
        return 0
    n = 0
    for r in conn.execute("SELECT id, commit_hash, source, post_id FROM picks WHERE tx_hash IS NULL LIMIT ?", (limit,)):
        tx_hash = chain.anchor(r["commit_hash"], f"{r['source']}:{r['post_id']}")
        if tx_hash:
            with db.tx(conn):
                conn.execute("UPDATE picks SET tx_hash=? WHERE id=?", (tx_hash, r["id"]))
            n += 1
    return n


def venues(pick: dict) -> list[dict]:
    return json.loads(pick["venues_json"]) if pick.get("venues_json") else []
