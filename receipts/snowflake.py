"""Post IDs on Discord and X are snowflakes: the creation time is baked into the ID and cannot be edited later."""
from __future__ import annotations

import re
from datetime import datetime, timezone

DISCORD_EPOCH_MS = 1420070400000  # 2015-01-01
X_EPOCH_MS = 1288834974657  # Twitter snowflake epoch (Nov 2010)


def _to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def discord_time(message_id: int | str) -> datetime:
    return _to_dt((int(message_id) >> 22) + DISCORD_EPOCH_MS)


def x_time(post_id: int | str) -> datetime:
    return _to_dt((int(post_id) >> 22) + X_EPOCH_MS)


def discord_id_for(dt: datetime, seq: int = 0) -> int:
    """Synthesise a valid snowflake for a time (used only for demo seed rows)."""
    ms = int(dt.timestamp() * 1000) - DISCORD_EPOCH_MS
    return (ms << 22) | (seq & 0x3FFFFF)


_X_URL = re.compile(r"(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d+)")


def parse_x_url(url: str) -> tuple[str, str] | None:
    """Return (handle, post_id) for an x.com / twitter.com status URL."""
    m = _X_URL.search(url)
    return (m.group(1), m.group(2)) if m else None
