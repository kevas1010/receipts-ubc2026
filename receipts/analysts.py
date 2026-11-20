"""Extension accounts (tailer or analyst) and the Discord ownership claim.

Claiming: an analyst asks for a one-time code, posts it in any Discord channel, and the first extension that
sees the message (theirs or any tailer's) reports it. The message's author is, by construction, the account
that posted it, so the analyst's Receipts login is bound to that Discord identity and becomes Verified.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timezone

from . import db

CODE_RE = re.compile(r"\bRCPT-([A-Z0-9]{6})\b")
ROLES = ("tailer", "analyst")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def login(role: str, name: str, conn: sqlite3.Connection | None = None) -> dict:
    """Hackathon-grade sign-in: a named session token kept in the extension. No passwords stored."""
    if role not in ROLES:
        raise ValueError("role must be tailer or analyst")
    name = " ".join(name.split())[:40] or role.title()
    conn = conn or db.connect()
    token = secrets.token_urlsafe(24)
    with db.tx(conn):
        conn.execute("INSERT INTO users(role, name, token, created_at) VALUES (?,?,?,?)", (role, name, token, _now()))
    return user_by_token(token, conn)


def user_by_token(token: str | None, conn: sqlite3.Connection | None = None) -> dict | None:
    if not token:
        return None
    conn = conn or db.connect()
    row = conn.execute("SELECT * FROM users WHERE token=?", (token,)).fetchone()
    return dict(row) if row else None


def start_claim(user: dict, handle: str, conn: sqlite3.Connection | None = None) -> str:
    if user["role"] != "analyst":
        raise PermissionError("Only analyst accounts can claim a Discord identity")
    conn = conn or db.connect()
    code = "RCPT-" + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
    with db.tx(conn):
        conn.execute("UPDATE users SET claim_code=?, claim_handle=? WHERE id=?", (code, handle.strip()[:40], user["id"]))
    return code


def confirm_from_message(capper_id: str, text: str, conn: sqlite3.Connection | None = None) -> dict | None:
    """If the message carries a pending claim code, bind that analyst login to the message's author."""
    m = CODE_RE.search(text or "")
    if not m:
        return None
    conn = conn or db.connect()
    user = conn.execute("SELECT * FROM users WHERE claim_code=?", (m.group(0),)).fetchone()
    if not user:
        return None
    now = _now()
    with db.tx(conn):
        conn.execute("UPDATE users SET capper_id=?, claim_code=NULL WHERE id=?", (capper_id, user["id"]))
        conn.execute("UPDATE cappers SET verified_at=COALESCE(verified_at, ?) WHERE id=?", (now, capper_id))
    return dict(conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone())


def set_storefront(user: dict, url: str, conn: sqlite3.Connection | None = None) -> None:
    if not user.get("capper_id"):
        raise PermissionError("Claim your Discord account first")
    if url and not re.match(r"^https://", url):
        raise ValueError("Storefront link must start with https://")
    conn = conn or db.connect()
    with db.tx(conn):
        conn.execute("UPDATE cappers SET storefront_url=? WHERE id=?", (url or None, user["capper_id"]))
