"""Seed three demo cappers from real settled Gemini moneylines, with real Gemini candle prices.

A month can't happen in 48 hours, so run the demo with the compressed window, e.g.
    DEMO_MODE=true VERIFY_WINDOW_DAYS=4 VERIFY_MIN_PICKS=8 VERIFY_MIN_DAYS=3
Every row is labelled demo. Run:  python -m receipts.seed_demo [--anchor]
"""
from __future__ import annotations

import random
from itertools import zip_longest
from concurrent.futures import ThreadPoolExecutor
import sys
from datetime import datetime, timedelta, timezone

from . import chain, db, service
from .config import settings
from .gemini import Game, GeminiClient, Side
from .snowflake import discord_id_for

CAPPERS = [
    # id, name, wins, losses, wants_positive_clv, verified (claimed account)
    ("demo:sharp-sam", "Sharp Sam", 8, 3, True, True),            # Verified Sharp
    ("demo:deleter-dan", "Deleter Dan", 9, 2, False, True),       # Verified, not Sharp: deleted a loss
    ("demo:steady-steph", "Steady Steph", 6, 4, False, False),    # Unverified: graded from scraped posts
]


def candidates(gem: GeminiClient, now: datetime, days: float) -> list[dict]:
    lo, hi = now - timedelta(days=days), now - timedelta(hours=6)
    games = [g for g in gem.settled_moneylines() if g.start_time and lo <= g.start_time <= hi]
    games.sort(key=lambda g: g.start_time)
    rng = random.Random(7)
    jobs = []
    for g in games:
        for side in g.sides:
            if side.resolution in ("yes", "no"):
                posted = g.start_time - timedelta(hours=rng.choice([2, 3, 4, 5]), minutes=rng.randint(1, 50))
                jobs.append((g, side, posted))

    def price(job):
        g, side, posted = job
        try:
            candles = gem.candles(side.instrument_symbol)
        except Exception:
            return None
        ms_post = posted.timestamp() * 1000
        ms_close = (g.start_time - timedelta(minutes=settings.close_snapshot_minutes)).timestamp() * 1000
        lock = next((c["close"] for c in reversed(candles) if c["t"] <= ms_post), None)
        close = next((c["close"] for c in reversed(candles) if c["t"] <= ms_close), None)
        if not lock or not close or not (0.15 <= lock <= 0.85):
            return None
        return {"game": g, "side": side, "posted": posted, "lock": lock, "close": close, "win": side.resolution == "yes"}

    with ThreadPoolExecutor(max_workers=6) as pool:
        out = [r for r in pool.map(price, jobs) if r]
    return out


def insert(conn, capper_id: str, c: dict, units: float, seq: int, anchor: bool, mode: str = "live") -> int:
    g: Game = c["game"]
    s: Side = c["side"]
    post_id = str(discord_id_for(c["posted"], seq))
    text = f"{s.label} ML {units:g}u"
    payload = chain.commit_payload(capper_id=capper_id, source="demo", post_id=post_id, text=text,
                                   event_ticker=g.ticker, side_label=s.label, units=units,
                                   lock_price=c["lock"], posted_at=service.iso(c["posted"]))
    h = chain.commit_hash(payload)
    tx = chain.anchor(h, f"demo:{post_id}") if anchor and mode == "live" else None
    cur = conn.execute(
        """INSERT INTO picks(capper_id, source, post_id, text, team_query, units, event_ticker, event_title, event_slug,
           league, side_label, side_abbr, instrument_symbol, start_time, posted_at, locked_at, lock_price, close_price,
           result, graded_at, commit_hash, tx_hash, mode, channel_id, channel_name)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (capper_id, "demo", post_id, text, s.label, units, g.ticker, g.title, g.slug, g.league, s.label,
         s.abbreviation, s.instrument_symbol, service.iso(g.start_time), service.iso(c["posted"]),
         service.iso(c["posted"]), c["lock"], c["close"], "win" if c["win"] else "loss",
         service.iso(g.start_time + timedelta(hours=3)), h, tx, mode, "demo-channel", "moneyline-picks (demo)"))
    return cur.lastrowid


def seed(anchor: bool = False, days: float | None = None) -> None:
    now = datetime.now(timezone.utc)
    days = days or max(settings.window_days + 1, 5)
    gem = GeminiClient()
    pool = candidates(gem, now, days)
    if len(pool) < 30:
        raise SystemExit(f"Only {len(pool)} usable settled games found; widen the window.")
    conn = db.connect()
    with db.tx(conn):
        ids = [c[0] for c in CAPPERS]
        q = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM pick_events WHERE pick_id IN (SELECT id FROM picks WHERE capper_id IN ({q}))", ids)
        conn.execute(f"DELETE FROM picks WHERE capper_id IN ({q})", ids)
        conn.execute(f"DELETE FROM cappers WHERE id IN ({q})", ids)
    rng = random.Random(42)
    used: set[str] = set()
    seq = 0
    for capper_id, name, wins, losses, positive_clv, verified in CAPPERS:
        service.ensure_capper(capper_id, name, demo=True, handle=capper_id.split(":")[1], conn=conn)
        window_start = now - timedelta(days=settings.window_days) + timedelta(hours=2)
        mine = [c for c in pool if c["game"].ticker not in used and c["posted"] >= window_start]
        if positive_clv:
            mine.sort(key=lambda c: c["lock"] - c["close"])  # line moved toward the pick
        else:
            rng.shuffle(mine)
        # Round-robin across days so every capper posts on several different days.
        by_day: dict = {}
        for c in mine:
            by_day.setdefault(c["posted"].date(), []).append(c)
        mine = [c for group in zip_longest(*by_day.values()) for c in group if c]
        # Everyone opens with one of the earliest unused games so the history rule can pass.
        first = min(pool, key=lambda c: c["posted"])  # openers may share a game
        mine = [c for c in mine if c["game"].ticker != first["game"].ticker]
        need_w, need_l = wins - first["win"], losses - (not first["win"])
        chosen = [first] + [c for c in mine if c["win"]][:need_w] + [c for c in mine if not c["win"]][:need_l]
        # One side per game per capper.
        seen, unique = set(), []
        for c in chosen:
            if c["game"].ticker not in seen:
                seen.add(c["game"].ticker)
                unique.append(c)
        chosen = unique
        mode = "live" if verified else "scraped"
        with db.tx(conn):
            if verified:
                first_post = min(c["posted"] for c in chosen) - timedelta(hours=1)
                conn.execute("UPDATE cappers SET verified_at=?, storefront_url=? WHERE id=?",
                             (service.iso(first_post),
                              "https://example.com/receipts-demo-storefront" if positive_clv else None, capper_id))
            pick_ids = []
            for c in sorted(chosen, key=lambda c: c["posted"]):
                used.add(c["game"].ticker)
                seq += 1
                units = rng.choice([1, 1, 1.5, 2, 2, 3])
                pick_ids.append((insert(conn, capper_id, c, units, seq, anchor, mode), c))
            if capper_id == "demo:deleter-dan":
                loss = max((pid for pid, c in pick_ids if not c["win"]), default=None)
                if loss is None:
                    raise SystemExit("No losing pick for Deleter Dan; rerun later.")
                at = service.iso(datetime.fromisoformat(
                    conn.execute("SELECT start_time FROM picks WHERE id=?", (loss,)).fetchone()[0].replace("Z", "+00:00"))
                    + timedelta(hours=3))
                conn.execute("INSERT INTO pick_events(pick_id, kind, at) VALUES (?, 'delete', ?)", (loss, at))
                conn.execute("UPDATE picks SET status='deleted' WHERE id=?", (loss,))
        print(f"seeded {name}: {len(chosen)} picks ({'verified' if verified else 'unverified, scraped'})")


if __name__ == "__main__":
    seed(anchor="--anchor" in sys.argv)
