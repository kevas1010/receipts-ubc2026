"""Match a slip leg (moneyline, spread, total, player prop) to the Gemini contract that settles it.

Every pick becomes a position on one Gemini contract: YES or NO. Examples from real Gemini markets:

    Bucks +9.5            -> NO  on "Portland wins by more than 9.5 points"   (NBA-...-POR-MIL-S)
    Pistons @ Spurs o218.5 -> YES on "More than 218.5 points scored"            (NBA-...-DET-SAS-T)
    Hezonja over 5.5 reb  -> YES on "Mario Hezonja 6+ Rebounds"                 (NBA-...-DAL-CLE-PPREB)
    Detroit ML            -> YES on "Detroit"                                   (NBA-...-TOR-DET-M)

Only exact lines count. If Gemini has no contract at the analyst's line (props come in 2+/4+/6+ ladders),
the leg is reported as "no Gemini market at that line" rather than graded on a different line.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .gemini import ALIASES, Game, Side, _norm
from .slips import Leg

MAX_LEAD = timedelta(hours=48)
STAT_METRIC = {"points": "points", "rebounds": "rebounds", "assists": "assists", "pass_yds": "passing_yards",
               "rush_yds": "rushing_yards", "rec_yds": "receiving_yards", "pass_td": "passing_touchdowns",
               "td": "touchdowns"}
SPORT_LEAGUES = {"NBA": {"NBA"}, "CBB": {"NCAAM", "NCAAB", "CBB"}, "NFL": {"NFL"}, "CFB": {"NCAAF", "CFB"},
                 "NHL": {"NHL"}}


@dataclass
class Match:
    game: Game
    side: Side
    position: str            # yes | no
    leg: Leg

    @property
    def label(self) -> str:
        return f"{self.position.upper()} · {self.side.label}"


@dataclass
class NoMatch:
    reason: str


def lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def player_match(q: str, name: str) -> bool:
    strip = lambda s: re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", _norm(s)).replace(".", " ").split()
    qa, na = strip(q), strip(name)
    if not qa or not na:
        return False
    qs, ns = "".join(qa), "".join(na)
    if qs == ns or lev(qs, ns) <= 2:
        return True
    return (qa[-1] == na[-1] or (len(qa[-1]) > 4 and lev(qa[-1], na[-1]) <= 1)) and qa[0][0] == na[0][0]


def team_keys(p: dict) -> set[str]:
    full = _norm(p.get("name", ""))
    w = full.split()
    keys = {full, _norm(p.get("abbreviation", ""))}
    if len(w) > 1:
        keys |= {w[-1], " ".join(w[-2:]), " ".join(w[:-1])}       # nickname, 2-word nickname, location
    if len(w) > 2:
        keys.add(" ".join(w[:-2]))                                  # "portland" from "portland trail blazers"
    return {k for k in keys if k}


def team_hit(q: str, p: dict) -> int:
    q = _norm(q)
    if not q:
        return 0
    qs = {q, *(_norm(a) for a in ALIASES.get(q, []))}
    keys = team_keys(p)
    if qs & keys:
        return 3
    full = _norm(p.get("name", ""))
    if len(q) >= 4 and (full.startswith(q) or q.startswith(full) or set(q.split()) <= set(full.split())):
        return 2
    return 0


def _window(games: list[Game], posted_at: datetime) -> list[Game]:
    return [g for g in games if g.start_time and posted_at < g.start_time <= posted_at + MAX_LEAD]


def _league_ok(g: Game, sport: str | None) -> bool:
    return not sport or sport not in SPORT_LEAGUES or g.league in SPORT_LEAGUES[sport]


def _games_for(teams: list[str], mtype: str, games: list[Game], sport: str | None) -> list[tuple[Game, int, int, str]]:
    """(game, participant index of teams[0], match score, matched team name) for full-game markets with all `teams`."""
    out = []
    for g in games:
        if g.market_type != mtype or g.scope != "full_contest" or not g.participants:
            continue
        idx, score = [], 0
        for t in teams:
            best = max(range(len(g.participants)), key=lambda i: team_hit(t, g.participants[i]))
            hit = team_hit(t, g.participants[best])
            if not hit:
                break
            idx.append(best)
            score += hit
        else:
            out.append((g, idx[0], score, g.participants[idx[0]]["name"]))
    hinted = [x for x in out if _league_ok(x[0], sport)]
    return hinted or out


def _one(found: list[tuple[Game, int, int, str]], what: str) -> tuple[Game, int] | NoMatch:
    """The team's next game. Ambiguous only if the name fits different teams equally well."""
    if not found:
        return NoMatch(f"No Gemini {what} near that post (Gemini's settled history covers about a week).")
    top = max(x[2] for x in found)
    best = [x for x in found if x[2] == top]
    names = {_norm(x[3]) for x in best}
    if len(names) > 1:
        return NoMatch("Fits several teams: " + ", ".join(sorted({x[3] for x in best})[:3]))
    g, i, _, _ = min(best, key=lambda x: x[0].start_time)
    return g, i


def nums(label: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", label)]


def match_leg(leg: Leg, posted_at: datetime, games: list[Game]) -> Match | NoMatch:
    pool = _window(games, posted_at)
    if leg.kind == "moneyline":
        f = _one(_games_for([leg.team or ""], "moneyline", pool, leg.sport), f"moneyline for \"{leg.team}\"")
        if isinstance(f, NoMatch):
            return f
        g, i = f
        p = g.participants[i]
        side = next((s for s in g.sides if _norm(s.abbreviation) == _norm(p["abbreviation"]) or team_hit(s.label, p) >= 2), None)
        return Match(g, side, "yes", leg) if side else NoMatch("Gemini moneyline has no contract for that team.")

    if leg.kind == "spread":
        if not leg.line:
            return NoMatch("Spread has no line.")
        f = _one(_games_for([leg.team or ""], "spread", pool, leg.sport), f"spread for \"{leg.team}\"")
        if isinstance(f, NoMatch):
            return f
        g, i = f
        fav = g.participants[i] if leg.line < 0 else g.participants[1 - i]
        n = abs(leg.line)
        cands = [s for s in g.sides if team_hit(re.split(r"\s+wins\b", s.label)[0], fav) >= 2 or _norm(s.abbreviation) in team_keys(fav)]
        side = next((s for s in cands if n in nums(s.label)), None)
        if not side:
            have = sorted({x for s in cands for x in nums(s.label)})
            return NoMatch(f"Gemini has no {fav['name']} {-n:g} line (has {', '.join(f'{x:g}' for x in have[:6]) or 'none'}).")
        return Match(g, side, "yes" if leg.line < 0 else "no", leg)

    if leg.kind == "total":
        teams = list(leg.matchup) if leg.matchup else ([leg.team] if leg.team else [])
        if not teams:
            return NoMatch("Total doesn't say which game.")
        f = _one(_games_for(teams, "total", pool, leg.sport), f"total for {' @ '.join(teams)}")
        if isinstance(f, NoMatch):
            return f
        g, _ = f
        side = next((s for s in g.sides if leg.line in nums(s.label)), None)
        if not side:
            return NoMatch(f"Gemini has no {leg.line:g} total for {g.title}.")
        under_label = bool(re.search(r"\b(fewer|less|under)\b", side.label, re.I))
        yes = (leg.side != "under") != under_label
        return Match(g, side, "yes" if yes else "no", leg)

    if leg.kind == "prop":
        metric = STAT_METRIC.get(leg.stat or "")
        if not metric:
            return NoMatch(f"{leg.stat_label} props aren't on Gemini.")
        if leg.line is None or not leg.player:
            return NoMatch("Couldn't read the prop line from the slip.")
        target = math.floor(leg.line) + 1   # over 5.5 == 6+
        seen_player = False
        for g in sorted(pool, key=lambda g: g.start_time):
            if g.market_type != "prop" or g.metric != metric:
                continue
            if leg.team and not any(_norm(p["abbreviation"]) == _norm(leg.team) for p in g.participants):
                continue
            mine = [s for s in g.sides if player_match(leg.player, re.sub(r"\s+\d+\+.*$", "", s.label))]
            if not mine:
                continue
            seen_player = True
            side = next((s for s in mine if nums(s.label)[:1] == [float(target)]), None)
            if side:
                return Match(g, side, "no" if leg.side == "under" else "yes", leg)
            have = sorted({int(nums(s.label)[0]) for s in mine if nums(s.label)})
            return NoMatch(f"Gemini lists {leg.player} at {', '.join(f'{x}+' for x in have)}, not {target}+ {leg.stat_label}.")
        return NoMatch(f"No Gemini {leg.stat_label.lower()} market for {leg.player} near that post."
                       if not seen_player else "No matching Gemini line.")
    return NoMatch("Unknown bet type.")


def position_price(game: Game, side: Side, position: str) -> float | None:
    """What a tailer pays for this position now. YES = bestAsk/buy.yes; NO = buy.no, else 1 - YES."""
    if position == "yes":
        return side.price
    c = next((c for c in game.raw.get("contracts") or [] if c.get("instrumentSymbol") == side.instrument_symbol), {})
    no = ((c.get("prices") or {}).get("buy") or {}).get("no")
    if no not in (None, ""):
        try:
            return float(no)
        except ValueError:
            pass
    return round(1 - side.price, 4) if side.price is not None else None
