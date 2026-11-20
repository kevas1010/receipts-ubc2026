"""Two statuses and one achievement.

* Unverified: an analyst who never claimed their account. Receipts grades the past posts tailers' extensions
  observe ("scraped"), and labels the record observed and possibly incomplete.
* Verified: the analyst claimed their Discord account. From that moment they are no longer scraped; their
  record is every pick they post after verification ("tracked").
* Verified Sharp: an optional achievement on top of Verified, computed over tracked picks in a rolling window.
"""
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
    record_kind: str = "observed"  # observed (scraped, maybe partial) | tracked (every pick since verification)
    verified_at: str | None = None
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
    channels: int = 0
    first_pick: str | None = None
    rules: list[Rule] = field(default_factory=list)

    @property
    def graded(self) -> int:
        return self.wins + self.losses

    def to_dict(self) -> dict:
        d = asdict(self)
        d["graded"] = self.graded
        d["verified"] = self.tier in ("verified", "sharp")
        d["sharp"] = self.tier == "sharp"
        d["win_rate"] = round(self.wins / self.graded, 3) if self.graded else None
        return d


def profit(result: str, units: float, price: float | None) -> float:
    """Units won at the Gemini price: a YES at p pays (1-p)/p per unit risked."""
    if result == "win" and price and 0 < price < 1:
        return units * (1 - price) / price
    if result == "loss":
        return -units
    return 0.0


def _z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_picks(capper: dict, conn: sqlite3.Connection) -> list[dict]:
    """The picks that make up the public record: tracked picks for Verified, observed picks otherwise."""
    if capper.get("verified_at"):
        rows = conn.execute("SELECT * FROM picks WHERE capper_id=? AND mode='live' AND posted_at>=? ORDER BY posted_at",
                            (capper["id"], capper["verified_at"]))
    else:
        rows = conn.execute("SELECT * FROM picks WHERE capper_id=? ORDER BY posted_at", (capper["id"],))
    return [dict(r) for r in rows]


def compute(capper_id: str, conn: sqlite3.Connection | None = None, at: datetime | None = None) -> Record:
    conn = conn or db.connect()
    at = at or datetime.now(timezone.utc)
    row = conn.execute("SELECT * FROM cappers WHERE id=?", (capper_id,)).fetchone()
    capper = dict(row) if row else {"id": capper_id}
    verified = bool(capper.get("verified_at"))
    picks = record_picks(capper, conn)
    rec = Record(capper_id=capper_id, tier="verified" if verified else "unverified",
                 record_kind="tracked" if verified else "observed", verified_at=capper.get("verified_at"))

    ids = [p["id"] for p in picks]
    if ids:
        q = ",".join("?" * len(ids))
        kinds = [r["kind"] for r in conn.execute(f"SELECT kind FROM pick_events WHERE pick_id IN ({q})", ids)]
        rec.edits = sum(1 for k in kinds if k == "edit") + sum(1 for p in picks if p.get("edited_flag") and p["status"] == "locked")
        rec.deletes = sum(1 for k in kinds if k == "delete")

    clvs, days, channels = [], set(), set()
    for p in picks:
        days.add(parse_ts(p["posted_at"]).date())
        channels.add(p.get("channel_id"))
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
    rec.channels = len(channels - {None})
    rec.first_pick = picks[0]["posted_at"] if picks else None

    if verified:
        rec.rules = sharp_rules(picks, rec, conn, at)
        if all(r.passed for r in rec.rules):
            rec.tier = "sharp"
    return rec


def sharp_rules(picks: list[dict], rec: Record, conn: sqlite3.Connection, at: datetime) -> list[Rule]:
    """The original one-month rules, now the bar for the optional Verified Sharp achievement."""
    window_start = at - timedelta(days=settings.window_days)
    in_window = [p for p in picks if parse_ts(p["posted_at"]) >= window_start]
    graded = sum(1 for p in in_window if p["result"] in ("win", "loss"))
    days = len({parse_ts(p["posted_at"]).date() for p in in_window})
    touched = conn.execute(
        "SELECT COUNT(*) FROM pick_events e JOIN picks p ON p.id=e.pick_id WHERE p.capper_id=? AND e.at>=?",
        (rec.capper_id, _z(window_start))).fetchone()[0]
    late = sum(1 for p in in_window if parse_ts(p["posted_at"]) >= parse_ts(p["start_time"]))
    history = (at - parse_ts(picks[0]["posted_at"])).days if picks else 0
    units = sum(profit(p["result"], p["units"], p["lock_price"]) for p in in_window)
    clvs = [p["close_price"] - p["lock_price"] for p in in_window if p["lock_price"] and p["close_price"]]
    clv = sum(clvs) / len(clvs) if clvs else None
    return [
        Rule("history", "Days since first tracked pick", f"{settings.window_days}", f"{history}", history >= settings.window_days),
        Rule("graded", "Graded moneyline picks in the window", f"{settings.min_picks}", f"{graded}", graded >= settings.min_picks),
        Rule("days", "Days with at least one pick", f"{settings.min_active_days}", f"{days}", days >= settings.min_active_days),
        Rule("untouched", "Edits or deletes after lock", f"{settings.max_edits}", f"{touched}", touched <= settings.max_edits),
        Rule("pregame", "Every pick posted before game start", "all", "all" if not late else f"{late} late", late == 0),
        Rule("units", "Net units at Gemini prices", "> 0", f"{units:+.2f}", units > 0),
        Rule("clv", "Average closing line value", ">= 0", "n/a" if clv is None else f"{clv * 100:+.1f} pts",
             clv is None or clv >= 0),
    ]


def leaderboard(conn: sqlite3.Connection | None = None, at: datetime | None = None,
                capper_ids: list[str] | None = None) -> list[dict]:
    conn = conn or db.connect()
    if capper_ids is not None:
        if not capper_ids:
            return []
        q = ",".join("?" * len(capper_ids))
        rows = conn.execute(f"SELECT * FROM cappers WHERE id IN ({q})", capper_ids).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cappers").fetchall()
    out = []
    for c in rows:
        rec = compute(c["id"], conn, at).to_dict()
        out.append({**dict(c), **rec})
    rank = {"sharp": 0, "verified": 1, "unverified": 2}
    out.sort(key=lambda r: (rank[r["tier"]], -(r["win_rate"] or 0), -r["graded"]))
    return out
