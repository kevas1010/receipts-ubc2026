"""API for the Receipts Chrome extension, plus the shareable SVG badge.  Run with:  uvicorn receipts.web:app

There is no standalone site: every screen lives in the extension's side panel.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import slips, markets as market_match
from . import analysts, chain, db, observe, odds, service, verify
from .config import settings
from .gemini import GeminiClient
from .parse import ParsedPick, Rejection, parse_pick

app = FastAPI(title="Receipts API")
# The extension calls from a chrome-extension:// origin; tokens, not cookies, carry identity.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])
gemini = GeminiClient()
pool = observe.GamePool(gemini)


# ---- auth -----------------------------------------------------------------
def current_user(authorization: str | None = Header(default=None)) -> dict:
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = analysts.user_by_token(token)
    if not user:
        raise HTTPException(401, "Sign in from the Receipts extension")
    return user


def analyst_user(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "analyst":
        raise HTTPException(403, "Analyst accounts only")
    return user


class LoginIn(BaseModel):
    role: str
    name: str = Field(max_length=40)


@app.post("/api/auth/login")
def login(body: LoginIn):
    try:
        user = analysts.login(body.role, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"token": user["token"], "user": public_user(user)}


def public_user(user: dict) -> dict:
    return {k: user[k] for k in ("id", "role", "name", "capper_id", "claim_code", "claim_handle")}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    out = {"user": public_user(user), "capper": None, "record": None}
    if user.get("capper_id"):
        c = db.connect().execute("SELECT * FROM cappers WHERE id=?", (user["capper_id"],)).fetchone()
        out["capper"] = dict(c) if c else None
        out["record"] = verify.compute(user["capper_id"]).to_dict()
    return out


# ---- analyst --------------------------------------------------------------
class ClaimIn(BaseModel):
    handle: str = Field(max_length=40)


@app.post("/api/analyst/claim")
def claim(body: ClaimIn, user: dict = Depends(analyst_user)):
    code = analysts.start_claim(user, body.handle)
    return {"code": code, "instructions": f"Post {code} as a message in any Discord channel while the extension is on."}


class StorefrontIn(BaseModel):
    url: str = Field(max_length=300)


@app.post("/api/analyst/storefront")
def storefront(body: StorefrontIn, user: dict = Depends(analyst_user)):
    try:
        analysts.set_storefront(user, body.url.strip())
    except (PermissionError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class PickIn(BaseModel):
    text: str = Field(max_length=200)
    ticker: str | None = None


@app.post("/api/analyst/picks")
def post_pick(body: PickIn, user: dict = Depends(analyst_user)):
    """A Verified analyst posts straight from the extension. The server clock is the authoritative time."""
    if not user.get("capper_id"):
        raise HTTPException(400, "Claim your Discord account first; picks count once you're Verified.")
    res = service.lock_pick(capper_id=user["capper_id"], source="receipts", post_id=secrets.token_hex(8),
                            text=body.text, posted_at=datetime.now(timezone.utc), gemini=gemini,
                            choose_ticker=body.ticker)
    if res.choices:
        return {"ok": False, "message": res.message,
                "choices": [{"ticker": g.ticker, "title": g.title, "side": s.label,
                             "start_time": g.start_time.isoformat()} for g, s in res.choices[:5]]}
    if not res.ok:
        return {"ok": False, "message": res.message or "Not a pick Gemini lists (moneyline, spread, total or player prop)."}
    pick = res.pick
    if res.game and res.side and (pick.get("market_type") or "moneyline") == "moneyline":  # venue compare is moneyline-only
        quotes = odds.compare(res.game, res.side)
        service.save_venues(pick["id"], odds.to_json(quotes))
        pick = dict(db.connect().execute("SELECT * FROM picks WHERE id=?", (pick["id"],)).fetchone())
    return {"ok": True, "pick": pick_out(pick, [])}


# ---- tailer / observation -------------------------------------------------
class ObservationsIn(BaseModel):
    messages: list[dict] = Field(max_length=200)


@app.post("/api/observations")
def observations(body: ObservationsIn, user: dict = Depends(current_user)):
    results = observe.ingest(body.messages, gemini, pool)
    counts: dict[str, int] = {}
    for r in results:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    return {"results": results, "counts": counts}


@app.get("/api/badges")
def badge_lookup(ids: str = ""):
    wanted = [i for i in ids.split(",") if i][:100]
    return observe.badges(wanted)


@app.get("/api/analysts")
def analysts_list(guild_id: str | None = None, channel_id: str | None = None):
    conn = db.connect()
    if guild_id or channel_id:
        col, val = ("channel_id", channel_id) if channel_id else ("guild_id", guild_id)
        ids = [r[0] for r in conn.execute(f"SELECT DISTINCT capper_id FROM picks WHERE {col}=?", (val,))]
        return verify.leaderboard(conn, capper_ids=ids)
    return verify.leaderboard(conn)


# ---- shared read endpoints -----------------------------------------------
def pick_out(p: dict, events: list[dict]) -> dict:
    keys = ("id", "source", "post_id", "text", "units", "event_ticker", "event_title", "event_slug", "league",
            "side_label", "side_abbr", "instrument_symbol", "start_time", "posted_at", "locked_at", "lock_price",
            "close_price", "result", "commit_hash", "tx_hash", "status", "mode", "channel_name", "edited_flag", "note",
            "position", "market_type", "bet_text")
    out = {k: p.get(k) for k in keys}
    out["position"] = out.get("position") or "yes"
    out["market_type"] = out.get("market_type") or "moneyline"
    out["bet_text"] = out.get("bet_text") or (f"{p.get('side_label')} ML" if out["market_type"] == "moneyline" else p.get("team_query"))
    out["profit"] = round(verify.profit(p["result"], p["units"], p["lock_price"]), 2)
    out["clv"] = round(p["close_price"] - p["lock_price"], 4) if p.get("close_price") and p.get("lock_price") else None
    out["tx_url"] = chain.tx_url(p.get("tx_hash"))
    out["gemini_url"] = settings.gemini_market_url.format(slug=p.get("event_slug") or "", ticker=p["event_ticker"])
    out["venues"] = service.venues(p)
    out["events"] = [e for e in events if e["pick_id"] == p["id"]]
    return out


@app.get("/api/config")
def config():
    return {"window_days": settings.window_days, "min_picks": settings.min_picks,
            "min_active_days": settings.min_active_days, "max_edits": settings.max_edits,
            "demo_mode": settings.demo_mode, "show_other_venues": settings.show_other_venues,
            "chain_enabled": settings.chain_enabled, "contract": settings.arc_contract,
            "explorer": settings.arc_explorer}


@app.get("/api/analysts/{handle}")
def analyst_profile(handle: str):
    conn = db.connect()
    c = conn.execute("SELECT * FROM cappers WHERE handle=?", (handle,)).fetchone()
    if not c:
        raise HTTPException(404, "No such analyst")
    capper = dict(c)
    rec = verify.compute(capper["id"], conn).to_dict()
    picks = verify.record_picks(capper, conn)
    picks.reverse()
    earlier = []
    if capper.get("verified_at"):  # observed history from before verification, shown separately
        earlier = [dict(r) for r in conn.execute(
            "SELECT * FROM picks WHERE capper_id=? AND (mode='scraped' OR posted_at<?) ORDER BY posted_at DESC",
            (capper["id"], capper["verified_at"]))]
    events = [dict(r) for r in conn.execute(
        "SELECT e.* FROM pick_events e JOIN picks p ON p.id=e.pick_id WHERE p.capper_id=? ORDER BY e.at", (capper["id"],))]
    return {"capper": capper, "record": rec, "picks": [pick_out(p, events) for p in picks],
            "earlier": [pick_out(p, events) for p in earlier]}


@app.get("/api/picks/{pick_id}/candles")
def candles(pick_id: int):
    p = db.connect().execute("SELECT instrument_symbol, posted_at, start_time, position FROM picks WHERE id=?", (pick_id,)).fetchone()
    if not p:
        raise HTTPException(404)
    try:
        rows = gemini.candles(p["instrument_symbol"])
    except Exception as e:
        raise HTTPException(502, f"Gemini candles unavailable: {e}")
    start = datetime.fromisoformat(p["start_time"].replace("Z", "+00:00")).timestamp() * 1000
    if (p["position"] or "yes") == "no":  # candles are YES prices; a NO position is 1 - YES
        rows = [{**c, **{k: round(1 - c[k], 4) for k in ("open", "high", "low", "close")}} for c in rows]
    return {"candles": [c for c in rows if c["t"] <= start][-72:], "posted_at": p["posted_at"], "start_time": p["start_time"]}


@app.get("/api/preview")
def preview(text: str):
    """Dry run: parse a post and match it to a live Gemini moneyline. Stores nothing."""
    parsed = parse_pick(text)
    if not isinstance(parsed, ParsedPick):
        leg = slips.parse_typed(text)
        if leg is None:
            return {"ok": False, "message": parsed.reason if isinstance(parsed, Rejection)
                    else "Not a pick. Try \"Arizona ML 2u\", \"Pistons -5.5 2u\" or \"Tyrese Maxey over 5.5 assists\"."}
        m = market_match.match_leg(leg, datetime.now(timezone.utc), gemini.markets())
        if isinstance(m, market_match.NoMatch):
            return {"ok": False, "message": f"{leg.describe()}: {m.reason}"}
        price = market_match.position_price(m.game, m.side, m.position)
        return {"ok": True, "team": leg.describe(), "units": slips.message_meta(text)[0] or 1.0, "message": "Would lock",
                "matches": [{"ticker": m.game.ticker, "title": m.game.title, "start_time": m.game.start_time.isoformat(),
                             "side": f"{m.position.upper()} · {m.side.label}", "price": price, "gemini_url": m.game.market_url,
                             "venues": [{"venue": "Gemini", "price": price, "url": m.game.market_url, "primary": True,
                                         "cheapest": True, "note": "grades this pick"}]}]}
    matches = gemini.match(parsed.team)
    if not matches:
        return {"ok": False, "message": f"No upcoming Gemini moneyline for \"{parsed.team}\"."}
    out = []
    for g, s in matches[:5]:
        quotes = odds.compare(g, s) if len(matches) == 1 else [odds.Quote("Gemini", s.price, g.market_url, primary=True, cheapest=True, note="grades this pick")]
        out.append({"ticker": g.ticker, "title": g.title, "start_time": g.start_time.isoformat() if g.start_time else None,
                    "side": s.label, "price": s.price, "gemini_url": g.market_url, "venues": [q.__dict__ for q in quotes]})
    return {"ok": len(out) == 1, "team": parsed.team, "units": parsed.units,
            "message": "Would lock" if len(out) == 1 else "Matches more than one game; pick one.", "matches": out}


@app.get("/api/markets")
def markets():
    now = datetime.now(timezone.utc)
    games = sorted((g for g in gemini.moneylines() if g.start_time and g.start_time > now), key=lambda g: g.start_time)
    return [{"ticker": g.ticker, "title": g.title, "league": g.league, "start_time": g.start_time.isoformat(),
             "gemini_url": g.market_url, "sides": [{"label": s.label, "price": s.price} for s in g.sides]}
            for g in games[:60]]


def badge_svg(name: str, rec: dict) -> str:
    tier = rec["tier"]
    label = {"sharp": "Receipts Verified · Sharp", "verified": "Receipts Verified",
             "unverified": "Receipts · Unverified (observed)"}[tier]
    color = {"sharp": "#0A7A4B", "verified": "#1F6FEB", "unverified": "#6B7280"}[tier]
    wr = f"{rec['win_rate'] * 100:.0f}%" if rec.get("win_rate") is not None else "no graded picks"
    stat = f"{rec['wins']}-{rec['losses']} · {wr} · {rec['units']:+.1f}u · Gemini-graded"
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    w = max(300, 7 * max(len(label), len(name) + len(stat) + 3) + 32)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="56" role="img" aria-label="{esc(label)}: {esc(stat)}">
<rect width="{w}" height="56" rx="10" fill="{color}"/>
<text x="16" y="23" font-family="system-ui,Segoe UI,sans-serif" font-size="13" font-weight="600" fill="#fff">{esc(label)}</text>
<text x="16" y="42" font-family="system-ui,Segoe UI,sans-serif" font-size="12" fill="#fff" opacity=".92">{esc(name)} · {esc(stat)}</text>
</svg>"""


@app.get("/badge/{handle}.svg")
def badge(handle: str):
    conn = db.connect()
    c = conn.execute("SELECT * FROM cappers WHERE handle=?", (handle,)).fetchone()
    if not c:
        raise HTTPException(404)
    rec = verify.compute(c["id"], conn).to_dict()
    return Response(badge_svg(c["display_name"], rec), media_type="image/svg+xml", headers={"Cache-Control": "max-age=300"})


@app.get("/")
def root():
    return {"name": "Receipts API", "client": "Chrome extension", "docs": "/docs"}
