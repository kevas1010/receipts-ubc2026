"""Moneyline-only pick parser.

Accepts posts like "Arizona ML 2u", "Take UConn moneyline (1.5 units)", "LAL ML -150 3u".
Rejects spreads, totals, props, parlays and stakes above MAX_UNITS. Rejections carry a reason
so the bot can stay silent on chatter but explain when something looks like a pick.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import settings


@dataclass
class ParsedPick:
    team: str
    units: float
    american_odds: int | None = None


@dataclass
class Rejection:
    reason: str


ML_TOKEN = re.compile(r"\b(?:ml|moneyline|money\s*line|to\s+win\s+outright)\b", re.I)
UNITS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:u|units?)\b", re.I)
AMERICAN = re.compile(r"(?<![\w.])([+-]\d{3,4})(?![\d.])")
SPREAD = re.compile(r"(?<![\w.])[+-]\d{1,2}(?:\.5)?(?![\d.])")
TOTALS = re.compile(r"\b(?:over|under|o/u|total|o\d+(?:\.\d)?|u\d+(?:\.\d)?)\b", re.I)
PROPS = re.compile(r"\b(?:pts|points|rebounds?|reb|assists?|ast|yards|yds|td|touchdowns?|strikeouts?|ks|goals?\s+scorer|anytime|sog|shots|hits|prop|player|1h|2h|first\s+half|1st\s+half|quarter|q[1-4]|period|p[1-3])\b", re.I)
PARLAY = re.compile(r"\b(?:parlay|sgp|teaser|round\s*robin|\d+\s*-?\s*leg)\b", re.I)
FILLER = re.compile(r"\b(?:take|taking|play|playing|lock|pick|potd|bet|hammer|love|like|on|the|today|tonight|game|my|units?|u|for|at|vs\.?|@|max|play)\b", re.I)


def parse_pick(text: str, max_units: float | None = None) -> ParsedPick | Rejection | None:
    """Return a ParsedPick, a Rejection (looks like a bet but not a straight moneyline), or None (not a pick)."""
    max_units = settings.max_units if max_units is None else max_units
    raw = " ".join(text.strip().split())
    if not raw:
        return None
    if len(ML_TOKEN.findall(raw)) == 0:
        if SPREAD.search(raw) or TOTALS.search(raw) or PARLAY.search(raw):
            return Rejection("Only straight moneylines count. Spreads, totals, props and parlays are ignored.")
        return None
    if PARLAY.search(raw) or len(ML_TOKEN.findall(raw)) > 1 or re.search(r"\s(?:&|\+|and)\s.*\bml\b", raw, re.I):
        return Rejection("Parlays don't count. Post one moneyline per message.")
    if TOTALS.search(raw) or PROPS.search(raw):
        return Rejection("Only full-game moneylines count. Totals, props and period bets are ignored.")

    units = 1.0
    um = UNITS.search(raw)
    if um:
        units = float(um.group(1))
    if units <= 0 or units > max_units:
        return Rejection(f"Stake must be between 0 and {max_units:g} units.")

    odds = None
    am = AMERICAN.search(raw)
    if am:
        odds = int(am.group(1))

    # Team = text before the ML token, minus filler, odds and stake.
    before = raw[: ML_TOKEN.search(raw).start()]
    cleaned = UNITS.sub(" ", before)
    cleaned = AMERICAN.sub(" ", cleaned)
    cleaned = re.sub(r"[^\w.&' -]", " ", cleaned)
    cleaned = FILLER.sub(" ", cleaned)
    team = " ".join(cleaned.split()).strip(" .-")
    if not team:
        after = raw[ML_TOKEN.search(raw).end():]
        after = FILLER.sub(" ", AMERICAN.sub(" ", UNITS.sub(" ", re.sub(r"[^\w.&' -]", " ", after))))
        team = " ".join(after.split()[:3]).strip(" .-")
    if not team:
        return Rejection("Couldn't find a team. Try \"Arizona ML 2u\".")
    if SPREAD.search(before.replace(am.group(1), "") if am else before):
        return Rejection("That looks like a spread. Only straight moneylines count.")
    return ParsedPick(team=team, units=units, american_odds=odds)


def is_pick_channel(name: str) -> bool:
    n = (name or "").lower()
    return any(k in n for k in settings.channel_keywords)
