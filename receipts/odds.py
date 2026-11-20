"""Gemini-first price comparison. Kalshi and Polymarket are shown only to verified cappers and
never touch a record. Gemini always comes first; the star goes to whichever is actually cheapest."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

import httpx

from .config import settings
from .gemini import Game, Side, _norm

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

KALSHI_SERIES = {"NBA": "KXNBAGAME", "NHL": "KXNHLGAME", "NFL": "KXNFLGAME", "MLB": "KXMLBGAME",
                 "NCAAM": "KXNCAAMBGAME", "NCAAF": "KXNCAAFGAME", "WNBA": "KXWNBAGAME"}
POLY_LEAGUE = {"NBA": "nba", "NHL": "nhl", "NFL": "nfl", "MLB": "mlb", "NCAAM": "cbb", "NCAAF": "cfb", "WNBA": "wnba"}


@dataclass
class Quote:
    venue: str
    price: float | None
    url: str | None
    primary: bool = False
    cheapest: bool = False
    note: str | None = None

    @property
    def implied(self) -> str:
        return f"{self.price * 100:.0f}%" if self.price else "n/a"


def _et_date(game: Game):
    return game.start_time.astimezone(ET).date() if game.start_time else None


def kalshi_quote(game: Game, side: Side, http: httpx.Client) -> Quote | None:
    """Kalshi lists one market per team per game; find the event on the same ET date whose
    markets name both teams, then take our team's yes ask."""
    series = KALSHI_SERIES.get(game.league)
    d = _et_date(game)
    if not series or not d:
        return None
    stamp = d.strftime("%y%b%d").upper()  # 26NOV19
    r = http.get(f"{settings.kalshi_base}/markets", params={"series_ticker": series, "status": "open", "limit": 1000})
    r.raise_for_status()
    by_event: dict[str, list[dict]] = {}
    for m in r.json().get("markets", []):
        if stamp in m.get("event_ticker", ""):
            by_event.setdefault(m["event_ticker"], []).append(m)

    def names(s: Side) -> set[str]:
        return {_norm(s.label), _norm(s.full_name), _norm(s.abbreviation)} - {""}

    other = next((s for s in game.sides if s is not side), None)
    for ev, markets in by_event.items():
        subs = {_norm(m.get("yes_sub_title") or ""): m for m in markets}
        mine = next((m for k, m in subs.items() if k in names(side)), None)
        theirs = other is None or any(k in names(other) for k in subs)
        if mine and theirs:
            price = mine.get("yes_ask_dollars") or (mine["yes_ask"] / 100 if mine.get("yes_ask") else None)
            return Quote("Kalshi", float(price) if price else None,
                         f"https://kalshi.com/markets/{series.lower()}/{ev.lower()}")
    return None


def polymarket_quote(game: Game, side: Side, http: httpx.Client) -> Quote | None:
    league = POLY_LEAGUE.get(game.league)
    d = _et_date(game)
    if not league or not d:
        return None
    away = next((s for s in game.sides if s.home_away == "away"), None)
    home = next((s for s in game.sides if s.home_away == "home"), None)
    if not away or not home:
        return None
    for day in (d, d - timedelta(days=1), d + timedelta(days=1)):
        slug = f"{league}-{away.abbreviation.lower()}-{home.abbreviation.lower()}-{day.isoformat()}"
        r = http.get(f"{settings.polymarket_base}/events", params={"slug": slug})
        r.raise_for_status()
        for e in r.json():
            for m in e.get("markets", []):
                if m.get("sportsMarketType") not in (None, "moneyline"):
                    continue
                outcomes = json.loads(m.get("outcomes") or "[]")
                prices = json.loads(m.get("outcomePrices") or "[]")
                full = _norm(side.full_name)
                for i, name in enumerate(outcomes):
                    n = _norm(name)
                    if n and (n in full or n == _norm(side.label)):
                        # Compare what a bettor pays: the ask. Outcome 0's ask is bestAsk; buying
                        # outcome 1 means hitting outcome 0's bid, so its ask is 1 - bestBid.
                        if i == 0 and m.get("bestAsk") is not None:
                            price = float(m["bestAsk"])
                        elif i == 1 and m.get("bestBid") is not None:
                            price = round(1 - float(m["bestBid"]), 4)
                        else:
                            price = float(prices[i])
                        return Quote("Polymarket", price, f"https://polymarket.com/event/{slug}")
    return None


def compare(game: Game, side: Side, http: httpx.Client | None = None) -> list[Quote]:
    quotes = [Quote("Gemini", side.price, game.market_url, primary=True, note="grades this pick")]
    if settings.show_other_venues:
        http = http or httpx.Client(timeout=10, headers={"User-Agent": "receipts-bot/1.0"})
        for fn in (kalshi_quote, polymarket_quote):
            try:
                q = fn(game, side, http)
                if q:
                    quotes.append(q)
            except Exception as e:
                log.info("%s lookup failed: %s", fn.__name__, e)
    priced = [q for q in quotes if q.price]
    if priced:
        best = min(q.price for q in priced)
        for q in priced:
            # Ties go to Gemini (it is listed first), but the star never lies.
            if q.price == best:
                q.cheapest = True
                break
    return quotes


def to_json(quotes: list[Quote]) -> str:
    return json.dumps([asdict(q) for q in quotes])
