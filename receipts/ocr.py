"""Read sportsbook bet-slip screenshots with Tesseract OCR and pull out straight moneylines.

Analysts often post a screenshot of the slip instead of typing the pick. The image URL comes from the
message the extension saw; the post time still comes from the message ID. Only Discord's own image hosts
are fetched, with a size cap, so the server can't be pointed at arbitrary URLs.
"""
from __future__ import annotations

import io
import logging
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from . import db

log = logging.getLogger(__name__)

ALLOWED_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
MAX_BYTES = 8 * 1024 * 1024
WINDOWS_PATHS = (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                 os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"))

SCHEMA = """CREATE TABLE IF NOT EXISTS ocr_cache (key TEXT PRIMARY KEY, text TEXT NOT NULL)"""


def tesseract_cmd() -> str | None:
    cmd = os.getenv("TESSERACT_CMD") or shutil.which("tesseract")
    if cmd:
        return cmd
    return next((p for p in WINDOWS_PATHS if os.path.exists(p)), None)


def available() -> bool:
    return tesseract_cmd() is not None


def allowed_url(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if os.getenv("OCR_ALLOW_LOCAL") == "1" and host in ("localhost", "127.0.0.1"):
        return True  # dev only: the Discord-shaped test page serves its slips from localhost
    return u.scheme == "https" and (host in ALLOWED_HOSTS or (host.startswith("images-ext-") and host.endswith(".discordapp.net")))


def cache_key(url: str) -> str:
    """Discord signs attachment URLs with expiring query params; the path identifies the file."""
    u = urlparse(url)
    return f"{u.hostname}{u.path}"


# ---- OCR --------------------------------------------------------------------------------
def image_text(data: bytes) -> str:
    import pytesseract
    from PIL import Image, ImageOps, ImageStat

    cmd = tesseract_cmd()
    if not cmd:
        raise RuntimeError("Tesseract is not installed (set TESSERACT_CMD)")
    pytesseract.pytesseract.tesseract_cmd = cmd
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("L")
    if img.width < 1400:  # phone screenshots OCR far better upscaled
        scale = 1400 / img.width
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    # Sportsbook apps are mostly dark mode: make text dark on light.
    if ImageStat.Stat(img).mean[0] < 110:
        img = ImageOps.invert(img)
    img = ImageOps.autocontrast(img)
    return pytesseract.image_to_string(img, config="--psm 6")


def fetch_and_read(url: str, conn: sqlite3.Connection | None = None, http: httpx.Client | None = None) -> str | None:
    if not allowed_url(url):
        return None
    conn = conn or db.connect()
    conn.execute(SCHEMA)
    key = cache_key(url)
    hit = conn.execute("SELECT text FROM ocr_cache WHERE key=?", (key,)).fetchone()
    if hit:
        return hit[0]
    try:
        client = http or httpx.Client(timeout=15, follow_redirects=False)
        with client.stream("GET", url) as r:
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image/"):
                return None
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    return None
        text = image_text(bytes(buf))
    except Exception as e:
        log.info("OCR failed for %s: %s", key, e)
        return None
    with db.tx(conn):
        conn.execute("INSERT OR REPLACE INTO ocr_cache(key, text) VALUES (?,?)", (key, text))
    return text


# ---- slip text -> picks -------------------------------------------------------------------
@dataclass
class SlipRead:
    picks: list[str]          # normalized pick texts, e.g. "Detroit Pistons ML"
    rejected: str | None      # why the slip isn't a straight moneyline, if it isn't
    raw: str


ML_LINE = re.compile(r"\b(?:money\s*line|moneyline|ml)\b", re.I)
PARLAY = re.compile(r"\b(?:parlay|same\s*game|sgp|teaser|round\s*robin|\d+\s*-?\s*(?:leg|pick)s?\b|legs?\b)", re.I)
OTHER_MARKET = re.compile(r"\b(?:spread|run\s*line|puck\s*line|total|over|under|alt(?:ernate)?|props?|points|rebounds|assists|yards|1st|2nd|first\s+half|quarter|period)\b", re.I)
ODDS_ONLY = re.compile(r"^[\s+\-−–]*\d{3,4}\s*$")
NOISE = re.compile(r"\b(?:wager|stake|risk|to\s*win|payout|to\s*pay|potential|bet\s*id|id[:#]|placed|cash\s*out|odds|single|straight|bet\s*slip|share|receipt|\$\s*[\d,.]+)\b", re.I)


def _clean(line: str) -> str:
    line = line.replace("−", "-").replace("–", "-")
    line = re.sub(r"[|@©®™•*_~=<>{}\[\]]", " ", line)
    line = re.sub(r"(?<![\w.])[+-]\d{3,4}(?![\d.])", " ", line)  # odds
    line = re.sub(r"\$\s*[\d,.]+", " ", line)
    return " ".join(line.split()).strip(" .:-")


def read_slip(text: str) -> SlipRead:
    lines = [l for l in (s.strip() for s in text.splitlines()) if l]
    joined = " ".join(lines)
    if PARLAY.search(joined):
        return SlipRead([], "Bet slip is a parlay. Only straight moneylines count.", text)
    picks: list[str] = []
    for i, line in enumerate(lines):
        if not ML_LINE.search(line):
            continue
        same = _clean(ML_LINE.split(line)[0])
        team = same if re.search(r"[A-Za-z]{3,}", same or "") and not NOISE.search(same) else ""
        j = i - 1
        while not team and j >= 0 and i - j <= 3:  # selection usually sits just above the market line
            cand = lines[j]
            if not ODDS_ONLY.match(cand) and not NOISE.search(cand) and not OTHER_MARKET.search(cand):
                cleaned = _clean(cand)
                if re.search(r"[A-Za-z]{3,}", cleaned):
                    team = cleaned
            j -= 1
        if team:
            team = re.sub(r"\b(?:vs\.?|@|at)\b.*$", "", team, flags=re.I).strip()
            pick = f"{team} ML"
            if pick not in picks:
                picks.append(pick)
    if not picks:
        if OTHER_MARKET.search(joined):
            return SlipRead([], "Bet slip isn't a moneyline (spread, total or prop).", text)
        return SlipRead([], None, text)
    return SlipRead(picks, None, text)
