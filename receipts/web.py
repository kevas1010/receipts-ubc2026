"""JSON API + embeddable SVG badge + the built Astryx UI.  Run with:  uvicorn receipts.web:app"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import chain, db, odds, service, verify
from .config import settings
from .gemini import GeminiClient
from .parse import ParsedPick, Rejection, parse_pick

app = FastAPI(title="Receipts")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
gemini = GeminiClient()
DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def pick_out(p: dict, events: list[dict]) -> dict:
    out = {k: p[k] for k in ("id", "source", "post_id", "text", "units", "event_ticker", "event_title", "event_slug",
                             "league", "side_label", "side_abbr", "instrument_symbol", "start_time", "posted_at",
                             "locked_at", "lock_price", "close_price", "result", "commit_hash", "tx_hash", "status")}
    out["profit"] = round(verify.profit(p["result"], p["units"], p["lock_price"]), 2)
    out["clv"] = round(p["close_price"] - p["lock_price"], 4) if p["close_price"] and p["lock_price"] else None
    out["tx_url"] = chain.tx_url(p["tx_hash"])
    out["gemini_url"] = settings.gemini_market_url.format(slug=p["event_slug"] or "", ticker=p["event_ticker"])
    out["venues"] = service.venues(p)
    out["events"] = [e for e in events if e["pick_id"] == p["id"]]
    return out


@app.get("/api/config")
def config():
    return {"window_days": settings.window_days, "min_picks": settings.min_picks,
            "min_active_days": settings.min_active_days, "max_edits": settings.max_edits,
            "demo_mode": settings.demo_mode, "show_other_venues": settings.show_other_venues,
            "chain_enabled": settings.chain_enabled, "contract": settings.arc_contract,
            "explorer": settings.arc_explorer, "gemini_market_url": settings.gemini_market_url}


@app.get("/api/leaderboard")
def leaderboard():
    return verify.leaderboard()


@app.get("/api/cappers/{handle}")
def capper(handle: str):
    conn = db.connect()
    c = conn.execute("SELECT * FROM cappers WHERE handle=?", (handle,)).fetchone()
    if not c:
        raise HTTPException(404, "No such capper")
    rec = verify.compute(c["id"], conn).to_dict()
    picks = [dict(r) for r in conn.execute("SELECT * FROM picks WHERE capper_id=? ORDER BY posted_at DESC", (c["id"],))]
    events = [dict(r) for r in conn.execute(
        "SELECT e.* FROM pick_events e JOIN picks p ON p.id=e.pick_id WHERE p.capper_id=? ORDER BY e.at", (c["id"],))]
    return {"capper": dict(c), "record": rec, "picks": [pick_out(p, events) for p in picks]}


@app.get("/api/picks/{pick_id}/candles")
def candles(pick_id: int):
    p = db.connect().execute("SELECT instrument_symbol, posted_at, start_time FROM picks WHERE id=?", (pick_id,)).fetchone()
    if not p:
        raise HTTPException(404)
    try:
        rows = gemini.candles(p["instrument_symbol"])
    except Exception as e:
        raise HTTPException(502, f"Gemini candles unavailable: {e}")
    start = datetime.fromisoformat(p["start_time"].replace("Z", "+00:00")).timestamp() * 1000
    return {"candles": [c for c in rows if c["t"] <= start][-72:],
            "posted_at": p["posted_at"], "start_time": p["start_time"]}


@app.get("/api/preview")
def preview(text: str):
    """Dry run for the web demo: parse a post and match it to a live Gemini moneyline. Stores nothing."""
    parsed = parse_pick(text)
    if parsed is None:
        return {"ok": False, "message": "Not a pick. Try \"Arizona ML 2u\"."}
    if isinstance(parsed, Rejection):
        return {"ok": False, "message": parsed.reason}
    assert isinstance(parsed, ParsedPick)
    matches = gemini.match(parsed.team)
    if not matches:
        return {"ok": False, "message": f"No upcoming Gemini moneyline for \"{parsed.team}\"."}
    out = []
    for g, s in matches[:5]:
        quotes = odds.compare(g, s) if len(matches) == 1 else [odds.Quote("Gemini", s.price, g.market_url, primary=True, cheapest=True, note="grades this pick")]
        out.append({"ticker": g.ticker, "title": g.title, "start_time": g.start_time.isoformat() if g.start_time else None,
                    "side": s.label, "price": s.price, "gemini_url": g.market_url,
                    "venues": [q.__dict__ for q in quotes]})
    return {"ok": len(out) == 1, "team": parsed.team, "units": parsed.units,
            "message": "Would lock" if len(out) == 1 else "Matches more than one game; the bot would ask which.",
            "matches": out}


@app.get("/api/markets")
def markets():
    now = datetime.now(timezone.utc)
    games = [g for g in gemini.moneylines() if g.start_time and g.start_time > now]
    games.sort(key=lambda g: g.start_time)
    return [{"ticker": g.ticker, "title": g.title, "league": g.league, "start_time": g.start_time.isoformat(),
             "gemini_url": g.market_url, "sides": [{"label": s.label, "price": s.price} for s in g.sides]}
            for g in games[:40]]


def badge_svg(name: str, tier: str, wins: int, losses: int, units: float) -> str:
    label = {"sharp": "Gemini-graded · Verified · Sharp", "verified": "Gemini-graded · Verified",
             "unverified": "Gemini-graded · Not verified"}[tier]
    color = {"sharp": "#0A7A4B", "verified": "#1F6FEB", "unverified": "#6B7280"}[tier]
    stat = f"{wins}-{losses} · {units:+.1f}u"
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    w = max(260, 7 * len(label) + 40)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="56" role="img" aria-label="{esc(label)}">
<rect width="{w}" height="56" rx="10" fill="{color}"/>
<text x="16" y="23" font-family="system-ui,Segoe UI,sans-serif" font-size="13" font-weight="600" fill="#fff">{esc(label)}</text>
<text x="16" y="42" font-family="system-ui,Segoe UI,sans-serif" font-size="12" fill="#fff" opacity=".9">{esc(name)} · {esc(stat)} · Receipts</text>
</svg>"""


@app.get("/badge/{handle}.svg")
def badge(handle: str):
    conn = db.connect()
    c = conn.execute("SELECT * FROM cappers WHERE handle=?", (handle,)).fetchone()
    if not c:
        raise HTTPException(404)
    r = verify.compute(c["id"], conn)
    return Response(badge_svg(c["display_name"], r.tier, r.wins, r.losses, r.units), media_type="image/svg+xml",
                    headers={"Cache-Control": "max-age=300"})


# ---- built React UI (web/dist) -------------------------------------------
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    f = (DIST / path).resolve()
    if path and f.is_file() and DIST.resolve() in f.parents:
        return FileResponse(f)
    index = DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return Response("UI not built. Run: cd web && npm run build", media_type="text/plain")
