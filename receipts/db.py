"""SQLite storage (stdlib only). One file, zero cost."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS cappers (
    id TEXT PRIMARY KEY,            -- e.g. discord:1234 or demo:sharp-sam
    handle TEXT UNIQUE NOT NULL,    -- url slug
    display_name TEXT NOT NULL,
    avatar_url TEXT,
    x_handle TEXT,
    demo INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capper_id TEXT NOT NULL REFERENCES cappers(id),
    source TEXT NOT NULL,           -- discord | x | demo
    post_id TEXT NOT NULL,
    channel_id TEXT,
    guild_id TEXT,
    reply_id TEXT,
    text TEXT NOT NULL,
    team_query TEXT NOT NULL,
    units REAL NOT NULL,
    event_ticker TEXT NOT NULL,
    event_title TEXT NOT NULL,
    event_slug TEXT,
    league TEXT,
    side_label TEXT NOT NULL,
    side_abbr TEXT,
    instrument_symbol TEXT NOT NULL,
    start_time TEXT NOT NULL,
    posted_at TEXT NOT NULL,        -- from the post ID, not the clock
    locked_at TEXT NOT NULL,
    lock_price REAL,
    close_price REAL,
    result TEXT NOT NULL DEFAULT 'pending',  -- pending | win | loss | void
    graded_at TEXT,
    commit_hash TEXT NOT NULL,
    tx_hash TEXT,
    status TEXT NOT NULL DEFAULT 'locked',   -- locked | edited | deleted
    venues_json TEXT,
    UNIQUE(source, post_id)
);
CREATE TABLE IF NOT EXISTS pick_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pick_id INTEGER NOT NULL REFERENCES picks(id),
    kind TEXT NOT NULL,             -- edit | delete
    at TEXT NOT NULL,
    new_text TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,             -- tailer | analyst
    name TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    capper_id TEXT REFERENCES cappers(id),   -- analysts: the Discord identity they claimed
    claim_code TEXT,                -- one-time code the analyst posts in Discord to prove ownership
    claim_handle TEXT,              -- the Discord username they say is theirs
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS picks_capper ON picks(capper_id);
CREATE INDEX IF NOT EXISTS picks_result ON picks(result);
"""

# Columns added after the first schema. Each is added if missing, so old databases keep working.
MIGRATIONS = {
    "cappers": {
        "discord_id": "TEXT",
        "verified_at": "TEXT",          # set when an analyst claims the account: Verified from then on
        "storefront_url": "TEXT",
        "first_seen": "TEXT",
        "last_seen": "TEXT",
        "demo_staged": "INTEGER NOT NULL DEFAULT 0",  # imported but hidden until someone presses Load (demo)
        "credential": "TEXT",                          # PickProof credential hash, issued at verification
    },
    "picks": {
        "mode": "TEXT NOT NULL DEFAULT 'live'",   # live = locked/tracked; scraped = graded from past posts
        "channel_name": "TEXT",
        "observed_at": "TEXT",
        "edited_flag": "INTEGER NOT NULL DEFAULT 0",
        "note": "TEXT",
        "position": "TEXT NOT NULL DEFAULT 'yes'",          # yes | no on the Gemini contract
        "market_type": "TEXT NOT NULL DEFAULT 'moneyline'",  # moneyline | spread | total | prop
        "bet_text": "TEXT",                                   # the bet as the analyst made it ("Bucks +9.5")
    },
}


def migrate(conn: sqlite3.Connection) -> None:
    for table, cols in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, decl in cols.items():
            if col not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()


_local = threading.local()


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or settings.db_path
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != path:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        migrate(conn)
        _local.conn, _local.path = conn, path
    return conn


@contextmanager
def tx(conn: sqlite3.Connection | None = None):
    conn = conn or connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def reset_for_tests(path: str = ":memory:") -> sqlite3.Connection:
    _local.conn = None
    return connect(path)
