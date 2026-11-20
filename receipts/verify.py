"""The badge rewards a complete, untouched month of moneyline picks, not a winning streak."""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from . import db
from .config import settings
from .gemini import parse_ts


@dataclass
class Rule:
    key: str
    label: str
    required: str
    actual: str
    passed: bool


@dataclass
class Record:
    capper_id: str
    tier: str  # unverified | verified | sharp
    wins: int = 0
    losses: int = 0
    voids: int = 0
    pending: int = 0
    units: float = 0.0
    staked: float = 0.0
    roi: float = 0.0
    avg_clv: float | None = None
    edits: int = 0
    deletes: int = 0
    active_days: int = 0
    first_pick: str | None = None
    rules: list[Rule] = field(default_factory=list)

    @property
    def graded(self) -> int:
        return self.wins + self.losses

    def to_dict(self) -> dict:
        d = asdict(self)
        d["graded"] = self.graded
        d["verified"] = self.tier in ("verified", "sharp")
        d["win_rate"] = round(self.wins / self.graded, 3) if self.graded else None
        return d


def profit(result: str, units: float, price: float | None) -> float:
    """Units won at the Gemini price: a YES at p pays (1-p)/p per unit risked."""
    if result == "win" and price and 0 < price < 1:
        return units * (1 - price) / price
    if result == "loss":
        return -units
    return 0.0


def compute(capper_id: str, conn: sqlite3.Connection | None = None, at: datetime | None = None) -> Record:
    conn = conn or db.connect()
    at = at or datetime.now(timezone.utc)
    window_start = at - timedelta(days=settings.window_days)
    picks = [dict(r) for r in conn.execute("SELECT * FROM picks WHERE capper_id=? ORDER BY posted_at", (capper_id,))]
    edit_rows = conn.execute(
        "SELECT e.kind FROM pick_events e JOIN picks p ON p.id=e.pick_id WHERE p.capper_id=? AND e.at>=?",
        (capper_id, window_start.strftime("%Y-%m-%dT%H:%M:%SZ"))).fetchall()
    rec = Record(capper_id=capper_id, tier="unverified")
    rec.edits = sum(1 for e in edit_rows if e["kind"] == "edit")
    rec.deletes = sum(1 for e in edit_rows if e["kind"] == "delete")

    in_window = [p for p in picks if parse_ts(p["posted_at"]) >= window_start]
    days = set()
    clvs = []
    late = 0
    for p in in_window:
        days.add(parse_ts(p["posted_at"]).date())
        if parse_ts(p["posted_at"]) >= parse_ts(p["start_time"]):
            late += 1
        r = p["result"]
        if r == "win":
            rec.wins += 1
        elif r == "loss":
            rec.losses += 1
        elif r == "void":
            rec.voids += 1
        else:
            rec.pending += 1
        if r in ("win", "loss"):
            rec.units += profit(r, p["units"], p["lock_price"])
            rec.staked += p["units"]
        if p["lock_price"] and p["close_price"]:
            clvs.append(p["close_price"] - p["lock_price"])
    rec.units = round(rec.units, 2)
    rec.roi = round(rec.units / rec.staked, 4) if rec.staked else 0.0
    rec.avg_clv = round(sum(clvs) / len(clvs), 4) if clvs else None
    rec.active_days = len(days)
    rec.first_pick = picks[0]["posted_at"] if picks else None
    history_days = (at - parse_ts(picks[0]["posted_at"])).days if picks else 0

    rec.rules = [
        Rule("history", "Days since first logged pick", f"{settings.window_days}", f"{history_days}",
             history_days >= settings.window_days),
        Rule("graded", "Graded moneyline picks in the window", f"{settings.min_picks}", f"{rec.graded}",
             rec.graded >= settings.min_picks),
        Rule("days", "Days with at least one pick", f"{settings.min_active_days}", f"{rec.active_days}",
             rec.active_days >= settings.min_active_days),
        Rule("untouched", "Edits or deletes after lock", f"{settings.max_edits}", f"{rec.edits + rec.deletes}",
             rec.edits + rec.deletes <= settings.max_edits),
        Rule("pregame", "Every pick posted before game start", "all", "all" if not late else f"{late} late",
             late == 0),
    ]
    if all(r.passed for r in rec.rules):
        rec.tier = "verified"
        if rec.units > 0 and (rec.avg_clv is None or rec.avg_clv >= 0):
            rec.tier = "sharp"
    return rec


def leaderboard(conn: sqlite3.Connection | None = None, at: datetime | None = None) -> list[dict]:
    conn = conn or db.connect()
    rows = conn.execute("SELECT * FROM cappers").fetchall()
    out = []
    for c in rows:
        rec = compute(c["id"], conn, at).to_dict()
        out.append({**dict(c), **rec})
    rank = {"sharp": 0, "verified": 1, "unverified": 2}
    out.sort(key=lambda r: (rank[r["tier"]], -r["units"]))
    return out
