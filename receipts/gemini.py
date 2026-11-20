"""Gemini Prediction Markets client: the single source of truth for eligibility, price and result.

All endpoints are public and need no API key.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from .config import settings

# Nicknames and short forms people actually type. Keys are lowercase.
ALIASES: dict[str, list[str]] = {
    "uconn": ["connecticut"],
    "zona": ["arizona"],
    "bama": ["alabama"],
    "cuse": ["syracuse"],
    "nova": ["villanova"],
    "lsu": ["louisiana state"],
    "ole miss": ["mississippi"],
    "unc": ["north carolina"],
    "habs": ["montreal"],
    "leafs": ["toronto"],
    "sixers": ["philadelphia 76ers", "76ers"],
    "cavs": ["cleveland cavaliers", "cavaliers"],
    "mavs": ["dallas mavericks", "mavericks"],
    "wolves": ["minnesota timberwolves", "timberwolves"],
    "blues": ["st. louis blues"],
    "caps": ["washington capitals", "capitals"],
    "bolts": ["tampa bay lightning", "lightning"],
    "pens": ["pittsburgh penguins", "penguins"],
    "knights": ["vegas golden knights", "golden knights"],
}


def _norm(s: str) -> str:
    s = s.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9. ]", " ", s)
    return " ".join(s.split())


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class Side:
    label: str
    abbreviation: str
    full_name: str
    instrument_symbol: str
    price: float | None
    resolution: str | None = None
    home_away: str | None = None


@dataclass
class Game:
    ticker: str
    title: str
    slug: str
    status: str
    start_time: datetime | None
    sport: str
    league: str
    sides: list[Side] = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)
    market_type: str = "moneyline"   # moneyline | spread | total | prop | ...
    scope: str = "full_contest"
    metric: str | None = None
    participants: list[dict] = field(default_factory=list)

    @property
    def market_url(self) -> str:
        return settings.gemini_market_url.format(slug=self.slug, ticker=self.ticker)

    def side(self, label: str) -> Side | None:
        return next((s for s in self.sides if s.label == label), None)


def contract_price(c: dict) -> float | None:
    """What a bettor pays for YES right now: bestAsk, else the buy-yes quote, else last trade."""
    p = c.get("prices") or {}
    for v in (p.get("bestAsk"), (p.get("buy") or {}).get("yes"), p.get("lastTradePrice")):
        if v not in (None, ""):
            try:
                return float(v)
            except ValueError:
                pass
    return None


def to_game(e: dict) -> Game:
    parts = {p.get("abbreviation"): p for p in e.get("participants") or []}
    sides = []
    for c in e.get("contracts") or []:
        abbr = c.get("abbreviatedName") or c.get("ticker") or ""
        part = parts.get(abbr) or {}
        sides.append(Side(
            label=c.get("label", ""),
            abbreviation=abbr,
            full_name=part.get("name") or c.get("label", ""),
            instrument_symbol=c.get("instrumentSymbol", ""),
            price=contract_price(c),
            resolution=c.get("resolutionSide"),
            home_away=c.get("sportsSide") or part.get("side"),
        ))
    league = e.get("ticker", "").split("-")[0]
    sm = e.get("sportsMarket") or {}
    mtype = sm.get("type") or ("moneyline" if e.get("ticker", "").endswith("-M") else "")
    return Game(
        ticker=e["ticker"], title=e.get("title", ""), slug=e.get("slug", ""), status=e.get("status", ""),
        start_time=parse_ts(e.get("startTime")), sport=sm.get("sport", ""),
        league=league, sides=sides, raw=e, market_type=mtype,
        scope=((sm.get("scope") or {}).get("type") or "full_contest"), metric=sm.get("metric"),
        participants=[{"name": p.get("name", ""), "abbreviation": p.get("abbreviation", ""), "side": p.get("side")}
                      for p in e.get("participants") or []],
    )


def is_moneyline(e: dict) -> bool:
    sm = e.get("sportsMarket") or {}
    if sm.get("type") and sm.get("type") != "moneyline":
        return False
    return e.get("ticker", "").endswith("-M")


class _RetryClient:
    """httpx client wrapper that retries transient network errors and 429/5xx with backoff."""

    def __init__(self, http: httpx.Client, tries: int = 6):
        self.http, self.tries = http, tries

    def get(self, url, **kw):
        for i in range(self.tries):
            try:
                r = self.http.get(url, **kw)
                if r.status_code in (429, 500, 502, 503, 504) and i < self.tries - 1:
                    raise httpx.TransportError(f"HTTP {r.status_code}")
                return r
            except httpx.TransportError:
                if i == self.tries - 1:
                    raise
                time.sleep(0.8 * 2 ** i)


class GeminiClient:
    def __init__(self, base: str | None = None, http: httpx.Client | None = None, cache_seconds: int = 60):
        self.base = (base or settings.gemini_base).rstrip("/")
        self.http = _RetryClient(http or httpx.Client(timeout=20, headers={"User-Agent": "receipts-bot/1.0"}))
        self.cache_seconds = cache_seconds
        self._cache: tuple[float, list[Game]] | None = None

    # ---- discovery -------------------------------------------------------
    def _events(self, status: str, limit: int = 100, max_pages: int = 20) -> list[dict]:
        out: list[dict] = []
        for page in range(max_pages):
            r = self.http.get(f"{self.base}/v1/prediction-markets/events",
                              params={"status": status, "category": "sports", "limit": limit, "offset": page * limit})
            r.raise_for_status()
            body = r.json()
            data = body.get("data") or []
            out.extend(data)
            total = (body.get("pagination") or {}).get("total", 0)
            if len(data) < limit or (page + 1) * limit >= total:
                break
        return out

    def moneylines(self, refresh: bool = False) -> list[Game]:
        """Active moneyline games (tickers ending -M). Spreads (-S) and totals (-T) are skipped."""
        if not refresh and self._cache and time.time() - self._cache[0] < self.cache_seconds:
            return self._cache[1]
        games = [to_game(e) for e in self._events("active") if is_moneyline(e)]
        self._cache = (time.time(), games)
        return games

    def markets(self, refresh: bool = False) -> list[Game]:
        """Every active sports market: moneylines, spreads, totals and player props."""
        hit = getattr(self, "_mcache", None)
        if not refresh and hit and time.time() - hit[0] < self.cache_seconds:
            return hit[1]
        games = [to_game(e) for e in self._events("active")]
        self._mcache = (time.time(), games)
        return games

    def settled_markets(self, max_pages: int = 20) -> list[Game]:
        return [to_game(e) for e in self._events("settled", max_pages=max_pages)]

    def settled_moneylines(self, max_pages: int = 20) -> list[Game]:
        return [to_game(e) for e in self._events("settled", max_pages=max_pages) if is_moneyline(e)]

    def event(self, ticker: str) -> Game:
        r = self.http.get(f"{self.base}/v1/prediction-markets/events/{ticker}")
        r.raise_for_status()
        body = r.json()
        return to_game(body.get("data", body) if isinstance(body, dict) and "ticker" not in body else body)

    def candles(self, instrument_symbol: str, interval: str = "1hr") -> list[dict]:
        """Hourly candles, oldest first: {t, open, high, low, close, volume}. Works on settled contracts."""
        r = self.http.get(f"{self.base}/v2/candles/{instrument_symbol}/{interval}")
        r.raise_for_status()
        rows = r.json() or []
        out = [{"t": int(row[0]), "open": row[1], "high": row[2], "low": row[3], "close": row[4], "volume": row[5]}
               for row in rows if isinstance(row, list) and len(row) >= 6]
        return sorted(out, key=lambda c: c["t"])

    def price_at(self, instrument_symbol: str, when: datetime) -> float | None:
        """Close of the last hourly candle at or before `when` (closing-line fallback, demo seed)."""
        ms = when.timestamp() * 1000
        best = None
        for c in self.candles(instrument_symbol):
            if c["t"] <= ms:
                best = c["close"]
        return best

    # ---- matching --------------------------------------------------------
    def match(self, team: str, games: list[Game] | None = None, at: datetime | None = None) -> list[tuple[Game, Side]]:
        """Find (game, side) pairs whose team matches. Only games that haven't started at `at` qualify."""
        games = self.moneylines() if games is None else games
        return match_team(team, games, at)


def _keys_for(side: Side) -> set[str]:
    full = _norm(side.full_name)
    label = _norm(side.label)
    keys = {label, full, _norm(side.abbreviation)}
    words = full.split()
    if len(words) > 1:
        keys.add(words[-1])  # nickname: "Washington State Cougars" -> "cougars"
        keys.add(" ".join(words[-2:]))  # "golden knights", "maple leafs"
    return {k for k in keys if k}


def match_team(team: str, games: list[Game], at: datetime | None = None) -> list[tuple[Game, Side]]:
    q = _norm(team)
    if not q:
        return []
    queries = {q, *(_norm(a) for a in ALIASES.get(q, []))}
    at = at or datetime.now(timezone.utc)
    exact: list[tuple[Game, Side]] = []
    loose: list[tuple[Game, Side]] = []
    for g in games:
        if g.start_time and g.start_time <= at:
            continue
        for s in g.sides:
            keys = _keys_for(s)
            if queries & keys:
                exact.append((g, s))
            elif len(q) >= 4 and any(k.startswith(q) or q in k.split() for k in keys):
                loose.append((g, s))
    found = exact or loose
    # A team's next game is the one a capper means: keep only the soonest game per team.
    soonest: dict[str, tuple[Game, Side]] = {}
    for g, s in sorted(found, key=lambda gs: gs[0].start_time or at):
        soonest.setdefault(f"{g.league}:{_norm(s.full_name)}", (g, s))
    return list(soonest.values())
