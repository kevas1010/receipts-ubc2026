"""Offline tests: no network, no Discord, no chain."""
from datetime import datetime, timedelta, timezone

import pytest

from receipts import chain, db, service, verify
from receipts.config import settings
from receipts.gemini import GeminiClient, match_team, to_game
from receipts.odds import Quote
from receipts.parse import ParsedPick, Rejection, is_pick_channel, parse_pick
from receipts.snowflake import discord_id_for, discord_time, parse_x_url, x_time
from receipts.web import badge_svg

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def ev(ticker, away, home, start, status="active", res=None, prices=(0.4, 0.6)):
    a_abbr, h_abbr = away[:3].upper(), home[:3].upper()
    return {
        "ticker": ticker, "title": f"{away} vs {home}", "slug": ticker.lower(), "status": status,
        "startTime": start.isoformat().replace("+00:00", "Z"), "sportsMarket": {"type": "moneyline", "sport": "basketball"},
        "participants": [{"name": f"{away} Wildcats", "abbreviation": a_abbr, "side": "away"},
                         {"name": f"{home} Huskies", "abbreviation": h_abbr, "side": "home"}],
        "contracts": [
            {"label": away, "abbreviatedName": a_abbr, "instrumentSymbol": f"GEMI-{ticker}-{a_abbr}",
             "prices": {"bestAsk": str(prices[0])}, "resolutionSide": (res[0] if res else None), "sportsSide": "away"},
            {"label": home, "abbreviatedName": h_abbr, "instrumentSymbol": f"GEMI-{ticker}-{h_abbr}",
             "prices": {"buy": {"yes": str(prices[1])}}, "resolutionSide": (res[1] if res else None), "sportsSide": "home"},
        ],
    }


class FakeGemini(GeminiClient):
    def __init__(self, events):
        self.events = {e["ticker"]: e for e in events}
        self._cache = None

    def moneylines(self, refresh=False):
        return [to_game(e) for e in self.events.values() if e["status"] == "active"]

    def event(self, ticker):
        return to_game(self.events[ticker])

    def candles(self, sym, interval="1hr"):
        return [{"t": 0, "open": .5, "high": .5, "low": .5, "close": .55, "volume": 0}]


@pytest.fixture
def conn():
    return db.reset_for_tests()


# ---- parser ---------------------------------------------------------------
@pytest.mark.parametrize("text,team,units", [
    ("Arizona ML 2u", "Arizona", 2),
    ("UConn moneyline (1.5 units)", "UConn", 1.5),
    ("Take the Lakers ML -150 3u", "Lakers", 3),
    ("St. Louis ML", "St. Louis", 1),
    ("ML: Ohio State 2u", "Ohio State", 2),
])
def test_parse_moneylines(text, team, units):
    p = parse_pick(text)
    assert isinstance(p, ParsedPick) and p.team == team and p.units == units


@pytest.mark.parametrize("text", [
    "Arizona -3.5 2u", "Lakers/Celtics over 220.5", "LeBron over 25.5 pts ML", "3-leg parlay Arizona ML",
    "Arizona ML & Duke ML", "Arizona ML 25u", "Lakers 1H ML",
])
def test_parse_rejects_non_moneylines(text):
    assert isinstance(parse_pick(text), Rejection)


def test_parse_ignores_chatter():
    assert parse_pick("good morning degenerates") is None


def test_pick_channels():
    assert is_pick_channel("💰-moneyline-picks") and is_pick_channel("nba-ml-picks") and not is_pick_channel("general")


# ---- snowflakes -----------------------------------------------------------
def test_discord_snowflake_roundtrip():
    t = datetime(2026, 11, 18, 23, 15, 7, tzinfo=timezone.utc)
    assert discord_time(discord_id_for(t)) == t


def test_known_discord_id():
    assert discord_time(175928847299117063).year == 2016


def test_x_ids():
    assert x_time("1585841080431321088").year == 2022
    assert parse_x_url("https://x.com/capper/status/1585841080431321088?s=20") == ("capper", "1585841080431321088")
    assert parse_x_url("https://example.com") is None


# ---- matching -------------------------------------------------------------
def test_match_by_label_nickname_abbr():
    games = [to_game(ev("NCAAM-1-ARI-UCO-M", "Arizona", "UConn", NOW + timedelta(hours=3)))]
    assert match_team("arizona", games, NOW)[0][1].label == "Arizona"
    assert match_team("Huskies", games, NOW)[0][1].label == "UConn"
    assert match_team("uconn", games, NOW)[0][1].label == "UConn"


def test_match_skips_started():
    games = [to_game(ev("X-M", "Arizona", "UConn", NOW - timedelta(minutes=1)))]
    assert match_team("Arizona", games, NOW) == []


def test_price_fallback_to_buy_yes():
    g = to_game(ev("X-M", "Arizona", "UConn", NOW))
    assert g.side("Arizona").price == 0.4 and g.side("UConn").price == 0.6


# ---- service --------------------------------------------------------------
def lock(gem, text, posted, post_id="1", capper="discord:1", conn=None):
    service.ensure_capper(capper, "Cap", conn=conn)
    return service.lock_pick(capper_id=capper, source="discord", post_id=post_id, text=text, posted_at=posted,
                             gemini=gem, conn=conn, anchor=False)


def test_lock_records_gemini_price_and_hash(conn):
    gem = FakeGemini([ev("A-M", "Arizona", "UConn", NOW + timedelta(hours=2))])
    res = lock(gem, "Arizona ML 2u", NOW, conn=conn)
    assert res.ok and res.pick["lock_price"] == 0.4 and res.pick["commit_hash"].startswith("0x")
    assert res.pick["tx_hash"] is None  # unanchored without a key


def test_refuses_started_game(conn):
    gem = FakeGemini([ev("A-M", "Arizona", "UConn", NOW - timedelta(minutes=5))])
    res = lock(gem, "Arizona ML", NOW, conn=conn)
    assert not res.ok and "before the game starts" in res.message


def test_ambiguous_team_asks(conn):
    gem = FakeGemini([ev("A-M", "Arizona", "UConn", NOW + timedelta(hours=1)),
                      ev("B-M", "Duke", "Washington", NOW + timedelta(hours=2))])
    res = lock(gem, "Huskies ML", NOW, conn=conn)
    assert not res.ok and len(res.choices) == 2


def test_edit_and_delete_are_flagged(conn):
    gem = FakeGemini([ev("A-M", "Arizona", "UConn", NOW + timedelta(hours=2))])
    lock(gem, "Arizona ML 2u", NOW, conn=conn)
    assert service.flag("discord", "1", "edit", "Arizona ML 2u", conn=conn) is None  # no content change
    p = service.flag("discord", "1", "edit", "UConn ML 2u", conn=conn)
    assert p["status"] == "edited" and p["text"] == "Arizona ML 2u"
    assert service.flag("discord", "1", "delete", conn=conn)["status"] == "deleted"


def test_grade_from_resolution_side(conn):
    e = ev("A-M", "Arizona", "UConn", NOW + timedelta(hours=2))
    gem = FakeGemini([e])
    lock(gem, "Arizona ML 2u", NOW, conn=conn)
    e.update(status="settled")
    e["contracts"][0]["resolutionSide"], e["contracts"][1]["resolutionSide"] = "yes", "no"
    graded = service.grade_pending(gem, conn=conn, at=NOW + timedelta(hours=5))
    assert graded[0]["result"] == "win"


def test_under_review_stays_open_then_voids(conn):
    e = ev("A-M", "Arizona", "UConn", NOW + timedelta(hours=2))
    gem = FakeGemini([e])
    lock(gem, "Arizona ML", NOW, conn=conn)
    e.update(status="under_review")
    assert service.grade_pending(gem, conn=conn, at=NOW + timedelta(days=1)) == []
    assert service.grade_pending(gem, conn=conn, at=NOW + timedelta(days=5))[0]["result"] == "void"


def test_close_snapshot_uses_live_price_before_start(conn):
    gem = FakeGemini([ev("A-M", "Arizona", "UConn", NOW + timedelta(minutes=5))])
    lock(gem, "Arizona ML", NOW, conn=conn)
    assert service.snapshot_closes(gem, conn=conn, at=NOW) == 1
    assert conn.execute("SELECT close_price FROM picks").fetchone()[0] == 0.4


# ---- verification ---------------------------------------------------------
def test_profit_at_gemini_price():
    assert verify.profit("win", 1, 0.5) == pytest.approx(1.0)
    assert verify.profit("win", 2, 0.25) == pytest.approx(6.0)
    assert verify.profit("loss", 2, 0.25) == -2
    assert verify.profit("void", 2, 0.25) == 0


def _month(conn, gem_events, deleted=False, verified=True):
    service.ensure_capper("discord:9", "Nine", conn=conn)
    if verified:
        conn.execute("UPDATE cappers SET verified_at=? WHERE id='discord:9'", (service.iso(NOW - timedelta(days=41)),))
    start = NOW - timedelta(days=settings.window_days - 1)
    for i in range(settings.min_picks + 1):
        posted = start + timedelta(days=i % (settings.min_active_days + 2), hours=i)
        conn.execute("""INSERT INTO picks(capper_id, source, post_id, text, team_query, units, event_ticker, event_title,
            side_label, instrument_symbol, start_time, posted_at, locked_at, lock_price, close_price, result, commit_hash)
            VALUES ('discord:9','discord',?,?,?,1,'T','T','Arizona','S',?,?,?,0.5,0.55,?, '0x')""",
                     (str(i), "Arizona ML", "Arizona", service.iso(posted + timedelta(hours=2)), service.iso(posted),
                      service.iso(posted), "win" if i % 3 else "loss"))
    conn.execute("INSERT INTO picks(capper_id, source, post_id, text, team_query, units, event_ticker, event_title, side_label, instrument_symbol, start_time, posted_at, locked_at, commit_hash) VALUES ('discord:9','discord','old','x','x',1,'T','T','A','S',?,?,?,'0x')",
                 (service.iso(NOW - timedelta(days=40)),) * 3)
    if deleted:
        conn.execute("INSERT INTO pick_events(pick_id, kind, at) VALUES (1, 'delete', ?)", (service.iso(NOW - timedelta(days=1)),))
    conn.commit()


def test_clean_month_is_verified_sharp(conn):
    _month(conn, [])
    rec = verify.compute("discord:9", conn, NOW)
    assert all(r.passed for r in rec.rules), rec.rules
    assert rec.tier == "sharp" and rec.avg_clv == pytest.approx(0.05) and rec.record_kind == "tracked"


def test_delete_keeps_verified_but_blocks_sharp(conn):
    _month(conn, [], deleted=True)
    rec = verify.compute("discord:9", conn, NOW)
    assert rec.tier == "verified"
    assert not next(r for r in rec.rules if r.key == "untouched").passed


def test_unregistered_is_unverified_with_observed_record(conn):
    _month(conn, [], verified=False)
    rec = verify.compute("discord:9", conn, NOW)
    assert rec.tier == "unverified" and rec.record_kind == "observed" and rec.rules == []
    assert rec.graded > 0


def test_new_capper_not_verified(conn):
    service.ensure_capper("discord:2", "New", conn=conn)
    assert verify.compute("discord:2", conn, NOW).tier == "unverified"


# ---- accounts, claim, observation -----------------------------------------
from receipts import analysts, observe  # noqa: E402


def test_login_roles(conn):
    t = analysts.login("tailer", "Tess", conn)
    assert t["role"] == "tailer" and analysts.user_by_token(t["token"], conn)["id"] == t["id"]
    with pytest.raises(ValueError):
        analysts.login("admin", "x", conn)


def test_tailer_cannot_claim(conn):
    t = analysts.login("tailer", "Tess", conn)
    with pytest.raises(PermissionError):
        analysts.start_claim(t, "tess", conn)


def msg(mid, text, author="555", name="Capper Carl", **kw):
    return {"message_id": str(mid), "channel_id": "c1", "guild_id": "g1", "channel_name": "ml-picks",
            "author_id": author, "author_name": name, "text": text, **kw}


class SettledPool:
    def __init__(self, games):
        self._g = games

    def games(self):
        return self._g

    def all_markets(self):
        return self._g


def settled_game(start, winner=0):
    e = ev("NBA-X-M", "Arizona", "UConn", start, status="settled",
           res=("yes", "no") if winner == 0 else ("no", "yes"))
    return to_game(e)


def test_claim_code_posted_in_discord_verifies_author(conn):
    a = analysts.login("analyst", "Carl", conn)
    code = analysts.start_claim(a, "Capper Carl", conn)
    res = observe.ingest([msg(discord_id_for(NOW), f"verifying {code}")], FakeGemini([]), SettledPool([]), conn=conn, now=NOW)
    assert res[0]["outcome"] == "claimed"
    me = analysts.user_by_token(a["token"], conn)
    assert me["capper_id"] == "discord:555" and me["claim_code"] is None
    assert verify.compute("discord:555", conn, NOW).tier == "verified"


def test_scraped_past_pick_is_graded_from_gemini(conn):
    posted = NOW - timedelta(days=2)
    game = settled_game(posted + timedelta(hours=3), winner=0)
    res = observe.ingest([msg(discord_id_for(posted), "Arizona ML 2u")], FakeGemini([]), SettledPool([game]), conn=conn, now=NOW)
    assert res[0]["outcome"] == "scraped"
    p = conn.execute("SELECT * FROM picks").fetchone()
    assert p["mode"] == "scraped" and p["result"] == "win" and p["lock_price"] == 0.55  # candle at post time
    assert p["posted_at"] == service.iso(posted)  # from the message ID, not the clock
    rec = verify.compute("discord:555", conn, NOW)
    assert rec.tier == "unverified" and rec.wins == 1


def test_verified_analyst_is_no_longer_scraped(conn):
    posted_before = NOW - timedelta(days=2)
    service.ensure_capper("discord:555", "Capper Carl", conn=conn)
    conn.execute("UPDATE cappers SET verified_at=? WHERE id='discord:555'", (service.iso(NOW - timedelta(days=1)),))
    conn.commit()
    game = settled_game(posted_before + timedelta(hours=3))
    res = observe.ingest([msg(discord_id_for(posted_before), "Arizona ML 2u")], FakeGemini([]), SettledPool([game]), conn=conn, now=NOW)
    assert res[0]["outcome"] == "before_verification"
    assert conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0


def test_verified_analyst_is_not_scraped_by_default(conn):
    """Verified analysts post through PickProof (hash-committed); the consumer scraper leaves them alone."""
    service.ensure_capper("discord:555", "Capper Carl", conn=conn)
    conn.execute("UPDATE cappers SET verified_at=? WHERE id='discord:555'", (service.iso(NOW - timedelta(days=1)),))
    conn.commit()
    gem = FakeGemini([ev("NBA-Y-M", "Arizona", "UConn", NOW + timedelta(hours=3))])
    res = observe.ingest([msg(discord_id_for(NOW - timedelta(minutes=5)), "Arizona ML 1u")], gem,
                         SettledPool(gem.moneylines()), conn=conn, now=NOW)
    assert res[0]["outcome"] == "verified_source"
    assert conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 0


def test_verified_new_post_is_tracked_live(conn, monkeypatch):
    monkeypatch.setattr(observe.settings, "scrape_verified", True)
    service.ensure_capper("discord:555", "Capper Carl", conn=conn)
    conn.execute("UPDATE cappers SET verified_at=? WHERE id='discord:555'", (service.iso(NOW - timedelta(days=1)),))
    conn.commit()
    gem = FakeGemini([ev("NBA-Y-M", "Arizona", "UConn", NOW + timedelta(hours=3))])
    res = observe.ingest([msg(discord_id_for(NOW - timedelta(minutes=5)), "Arizona ML 1u")], gem,
                         SettledPool(gem.moneylines()), conn=conn, now=NOW)
    assert res[0]["outcome"] == "tracked"
    p = conn.execute("SELECT * FROM picks").fetchone()
    assert p["mode"] == "live" and p["lock_price"] == 0.4 and p["result"] == "pending"


def test_observation_edit_flags_and_dedupes(conn):
    posted = NOW - timedelta(days=2)
    pool = SettledPool([settled_game(posted + timedelta(hours=3))])
    m = msg(discord_id_for(posted), "Arizona ML 2u")
    observe.ingest([m], FakeGemini([]), pool, conn=conn, now=NOW)
    assert observe.ingest([m], FakeGemini([]), pool, conn=conn, now=NOW)[0]["outcome"] == "seen"
    assert observe.ingest([{**m, "text": "UConn ML 2u"}], FakeGemini([]), pool, conn=conn, now=NOW)[0]["outcome"] == "edited"
    row = conn.execute("SELECT edited_flag, text FROM picks").fetchone()
    assert row["edited_flag"] == 1 and row["text"] == "Arizona ML 2u"


def test_observation_skips_games_far_from_post(conn):
    posted = NOW - timedelta(days=4)
    game = settled_game(posted + timedelta(days=3))
    res = observe.ingest([msg(discord_id_for(posted), "Arizona ML")], FakeGemini([]), SettledPool([game]), conn=conn, now=NOW)
    assert res[0]["outcome"] == "no_game"


# ---- chain + misc ---------------------------------------------------------
def test_commit_hash_is_deterministic_and_sensitive():
    p = chain.commit_payload(capper_id="a", source="discord", post_id="1", text="Arizona ML", event_ticker="T",
                             side_label="Arizona", units=1, lock_price=0.4, posted_at="2026-11-19T00:00:00Z")
    assert chain.commit_hash(p) == chain.commit_hash(dict(p))
    assert chain.commit_hash(p) != chain.commit_hash({**p, "text": "UConn ML"})


def test_calldata_encoding():
    data = chain._encode_commit("0x" + "11" * 32, "discord:123")
    assert data[:4] == chain.COMMIT_SELECTOR and len(data) == 4 + 32 * 4


def test_badge_svg_shows_win_rate_and_escapes():
    svg = badge_svg("<Dan>", {"tier": "verified", "wins": 9, "losses": 3, "win_rate": 0.75, "units": 3.1})
    assert "&lt;Dan&gt;" in svg and "PickProof Verified" in svg and "75%" in svg


def test_quote_implied():
    assert Quote("Gemini", 0.42, None).implied == "42%"


def test_same_team_future_games_pick_soonest():
    games = [to_game(ev("NBA-1-M", "Arizona", "UConn", NOW + timedelta(days=2))),
             to_game(ev("NBA-2-M", "Duke", "Arizona", NOW + timedelta(hours=3)))]
    games[1].sides[1].full_name = games[0].sides[0].full_name  # same Arizona team
    m = match_team("Arizona", games, NOW)
    assert len(m) == 1 and m[0][0].ticker == "NBA-2-M"


# ---- bet slip OCR ---------------------------------------------------------
from receipts import ocr  # noqa: E402


def test_read_slip_text_layouts():
    assert ocr.read_slip("STRAIGHT\nDetroit Pistons\nMoneyline\n-280\nWager $50.00").picks == ["Detroit Pistons ML"]
    assert ocr.read_slip("Calgary Flames +135 Moneyline\nStake $20").picks == ["Calgary Flames ML"]
    assert ocr.read_slip("2 Leg Parlay\nArizona\nMoneyline\nUConn\nMoneyline").rejected
    assert ocr.read_slip("Lakers -4.5\nSpread\n-110").rejected
    assert ocr.read_slip("lol nice hit").picks == []


def test_only_discord_image_hosts_are_fetched():
    assert ocr.allowed_url("https://cdn.discordapp.com/attachments/1/2/slip.png?ex=abc")
    assert ocr.allowed_url("https://media.discordapp.net/attachments/1/2/slip.png")
    assert not ocr.allowed_url("http://cdn.discordapp.com/x.png")
    assert not ocr.allowed_url("https://169.254.169.254/latest")
    assert not ocr.allowed_url("https://evil.example/cdn.discordapp.com/x.png")


def test_slip_image_becomes_graded_pick(conn):
    posted = NOW - timedelta(days=2)
    game = settled_game(posted + timedelta(hours=3), winner=0)
    m = msg(discord_id_for(posted), "", images=["https://cdn.discordapp.com/attachments/1/2/slip.png"])
    res = observe.ingest([m], FakeGemini([]), SettledPool([game]), conn=conn, now=NOW,
                         read_image=lambda url: "STRAIGHT\nArizona Wildcats\nMoneyline\n+120\nWager $20")
    assert res[0]["outcome"] == "scraped" and res[0]["from_slip"]
    p = conn.execute("SELECT post_id, text, note, posted_at FROM picks").fetchone()
    assert p["post_id"].endswith(":slip1") and p["text"] == "Arizona Wildcats ML"
    assert "OCR" in p["note"] and p["posted_at"] == service.iso(posted)
    # seen again: no duplicate
    again = observe.ingest([m], FakeGemini([]), SettledPool([game]), conn=conn, now=NOW, read_image=lambda url: "x")
    assert again[0]["outcome"] == "seen" and conn.execute("SELECT COUNT(*) FROM picks").fetchone()[0] == 1


def test_parlay_slip_legs_are_matched_one_by_one(conn):
    """A parlay is split into its legs; each needs its own Gemini market (none here)."""
    m = msg(discord_id_for(NOW - timedelta(hours=1)), "lock it in", images=["https://cdn.discordapp.com/a/b/c.png"])
    res = observe.ingest([m], FakeGemini([]), SettledPool([]), conn=conn, now=NOW,
                         read_image=lambda url: "3 Leg Parlay\nArizona\nMoneyline")
    assert res[0]["outcome"] == "no_game" and "Arizona ML" in res[0]["reason"]


@pytest.mark.skipif(not ocr.available(), reason="Tesseract not installed")
def test_tesseract_reads_rendered_slips():
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
    from slips import render

    assert ocr.read_slip(ocr.image_text(render("straight_ml"))).picks == ["Detroit Pistons ML"]
    assert ocr.read_slip(ocr.image_text(render("light_ml"))).picks == ["Calgary Flames ML"]
    assert ocr.read_slip(ocr.image_text(render("parlay"))).rejected
    assert ocr.read_slip(ocr.image_text(render("spread"))).rejected
