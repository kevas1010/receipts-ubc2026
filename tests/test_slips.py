"""Real analyst slips (OCR text captured from Discord screenshots) and real Gemini contract wording."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from receipts import db, markets, observe, service, slips
from receipts.gemini import GeminiClient, to_game
from receipts.snowflake import discord_id_for

FIX = Path(__file__).parent / "fixtures" / "real_slips"
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def slip(name, msg=""):
    return slips.parse_slip((FIX / f"{name}.txt").read_text(), msg)


# ---- reading real slips --------------------------------------------------------------------
@pytest.mark.parametrize("name,legs", [
    ("spread_bucks", ["Milwaukee Bucks +9.5"]),
    ("spread_chattanooga", ["Chattanooga Mocs +6.5"]),
    ("total_over_pistons", ["Pistons @ Spurs over 218.5"]),
    ("total_over_grizzlies", ["Grizzlies @ Wizards over 228.5"]),
    ("total_lower_famu", ["Florida A&M @ UTEP under 141.5"]),
    ("chalk_zubac_3", ["Ivica Zubac over 19.5 Pts+Reb+Ast", "Brandin Podziemski over 3.5 Rebounds",
                       "Domantas Sabonis over 23.5 Pts+Reb"]),
    ("chalk_hezonja_3", ["Mario Hezonja over 5.5 Rebounds", "Giannis Antetokounmpo over 15.5 Reb+Ast",
                         "Danny Wolf over 3.5 Rebounds"]),
    ("chalk_allen_porter", ["Jarrett Allen over 10.5 Reb+Ast", "Michael Porter Jr. over 29.5 Pts+Reb+Ast"]),
    ("chalk_robinson_brooks", ["Mitchell Robinson over 7.5 Pts+Reb", "Dillon Brooks over 19.5 Pts+Reb+Ast"]),
    ("chalk_nfl_3", ["Rashid Shaheed over 1.5 Receptions", "Nick Emmanwori over 3.5 Tackles+Ast",
                     "Matthew Stafford under 31.5 Pass Att"]),
])
def test_real_slips_read_every_leg(name, legs):
    assert [l.describe() for l in slip(name).legs] == legs


def test_slip_header_and_message_meta():
    s = slip("chalk_allen_porter", "@everyone 5U Sharp Value on both lines")
    assert s.multiplier == pytest.approx(2.6) and s.units == 5 and [l.team for l in s.legs] == ["CLE", "BKN"]
    assert slips.message_meta("NBA 2U Straight 1.62x MEMPHIS")[:3] == (2.0, 1.62, "NBA")
    assert slips.message_meta("CFB 4-6U FIU's defense")[0] == 5.0


@pytest.mark.parametrize("raw,expect", [("35", [3.5]), ("135", [13.5, 3.5]), ("19.5", [19.5, 9.5]), ("15", [1.5])])
def test_ocr_line_candidates(raw, expect):
    assert slips.candidates(raw) == expect


@pytest.mark.parametrize("text,desc", [
    ("Pistons -5.5 2u", "Pistons -5.5"),
    ("Pistons/Spurs over 218.5", "Pistons @ Spurs over 218.5"),
    ("Tyrese Maxey over 5.5 assists 1u", "Tyrese Maxey over 5.5 Assists"),
])
def test_typed_picks(text, desc):
    assert slips.parse_typed(text).describe() == desc


# ---- Gemini markets (real wording, from the settled API on 2026-11-20) ---------------------
def gev(ticker, mtype, parts, contracts, start, status="settled", metric=None):
    return {"ticker": ticker, "title": ticker, "slug": ticker.lower(), "status": status,
            "startTime": start.isoformat().replace("+00:00", "Z"),
            "sportsMarket": {"type": mtype, "sport": "basketball", "scope": {"type": "full_contest"}, "metric": metric},
            "participants": [{"name": n, "abbreviation": a, "side": s} for n, a, s in parts],
            "contracts": [{"label": l, "abbreviatedName": ab, "instrumentSymbol": f"GEMI-{ticker}-{i}",
                           "prices": {"buy": {"yes": str(p), "no": str(round(1 - p, 2))}}, "resolutionSide": r}
                          for i, (l, ab, p, r) in enumerate(contracts)]}


POR_MIL = [("Milwaukee Bucks", "MIL", "home"), ("Portland Trail Blazers", "POR", "away")]
DET_SAS = [("San Antonio Spurs", "SAS", "home"), ("Detroit Pistons", "DET", "away")]
DAL_CLE = [("Cleveland Cavaliers", "CLE", "home"), ("Dallas Mavericks", "DAL", "away")]


def market_games(start):
    return [to_game(e) for e in [
        gev("NBA-2611200100-POR-MIL-S", "spread", POR_MIL, [
            ("Portland wins by more than 9.5 points", "Portland", .45, "yes"),
            ("Portland wins by more than 19.5 points", "Portland", .1, "no"),
            ("Milwaukee wins by more than 1.5 points", "Milwaukee", .5, "no")], start),
        gev("NBA-2611200100-POR-MIL-M", "moneyline", POR_MIL, [
            ("Portland", "POR", .6, "yes"), ("Milwaukee", "MIL", .4, "no")], start),
        gev("NBA-2611180100-DET-SAS-T", "total", DET_SAS, [
            ("More than 218.5 points scored", None, .52, "no"), ("More than 224.5 points scored", None, .4, "no")], start),
        gev("NBA-2611150030-DAL-CLE-PPREB", "prop", DAL_CLE, [
            ("Mario Hezonja 4+ Rebounds", None, .8, "yes"), ("Mario Hezonja 6+ Rebounds", None, .55, "yes"),
            ("Jarrett Allen 8+ Rebounds", None, .6, "no"), ("Jarrett Allen 10+ Rebounds", None, .4, "no")],
            start, metric="rebounds"),
        gev("NBA-2611150100-BKN-MEM-PPREB", "prop", [("Memphis Grizzlies", "MEM", "home"), ("Brooklyn Nets", "BKN", "away")], [
            ("Danny Wolf 4+ Rebounds", None, .5, "no"), ("Danny Wolf 6+ Rebounds", None, .3, "no")],
            start, metric="rebounds"),
    ]]


def test_spread_dog_is_no_on_the_favorite():
    posted = NOW - timedelta(hours=5)
    m = markets.match_leg(slips.Leg("spread", team="Milwaukee Bucks", line=9.5), posted, market_games(posted + timedelta(hours=2)))
    assert m.side.label == "Portland wins by more than 9.5 points" and m.position == "no"


def test_spread_favorite_is_yes_and_missing_line_is_explained():
    posted = NOW - timedelta(hours=5)
    games = market_games(posted + timedelta(hours=2))
    m = markets.match_leg(slips.Leg("spread", team="Portland Trail Blazers", line=-19.5), posted, games)
    assert m.position == "yes" and "19.5" in m.side.label
    miss = markets.match_leg(slips.Leg("spread", team="Bucks", line=6.5), posted, games)
    assert isinstance(miss, markets.NoMatch) and "9.5" in miss.reason


def test_total_and_prop_ladders():
    posted = NOW - timedelta(hours=5)
    games = market_games(posted + timedelta(hours=2))
    t = markets.match_leg(slips.Leg("total", line=218.5, side="over", matchup=("Pistons", "Spurs")), posted, games)
    assert t.position == "yes" and t.side.label.startswith("More than 218.5")
    u = markets.match_leg(slips.Leg("total", line=218.5, side="under", matchup=("Pistons", "Spurs")), posted, games)
    assert u.position == "no"
    p = markets.match_leg(slips.Leg("prop", player="Mario Hezonja", stat="rebounds", line=5.5, side="over", team="CLE"), posted, games)
    assert p.side.label == "Mario Hezonja 6+ Rebounds" and p.position == "yes"
    off = markets.match_leg(slips.Leg("prop", player="A JarrettAllen", stat="rebounds", line=10.5, side="over", team="CLE"), posted, games)
    assert isinstance(off, markets.NoMatch) and "8+, 10+" in off.reason   # no 11+ contract: not graded
    combo = markets.match_leg(slips.Leg("prop", player="Giannis", stat="reb_ast", line=15.5), posted, games)
    assert isinstance(combo, markets.NoMatch) and "aren't on Gemini" in combo.reason


def test_games_must_start_after_the_post():
    posted = NOW - timedelta(hours=5)
    m = markets.match_leg(slips.Leg("spread", team="Bucks", line=9.5), posted, market_games(posted - timedelta(hours=1)))
    assert isinstance(m, markets.NoMatch)


# ---- end to end: a real Chalkboard slip in a Discord message -------------------------------
class Pool:
    def __init__(self, games):
        self.g = games

    def games(self):
        return [g for g in self.g if g.market_type == "moneyline"]

    def all_markets(self):
        return self.g


class Fake(GeminiClient):
    def __init__(self, games):
        self.by = {g.ticker: g for g in games}
        self._cache = None

    def event(self, ticker):
        return self.by[ticker]

    def candles(self, sym, interval="1hr"):
        return [{"t": 0, "open": .6, "high": .6, "low": .6, "close": .6, "volume": 0}]


@pytest.fixture
def conn():
    return db.reset_for_tests()


def discord_msg(posted, text, images=()):
    return {"message_id": discord_id_for(posted), "channel_id": "1514832820604768377", "guild_id": "1064595437715214457",
            "channel_name": "X", "author_id": "999", "author_name": "X", "text": text, "images": list(images)}


def test_real_pickem_slip_is_split_priced_and_graded(conn):
    posted = NOW - timedelta(days=5)
    games = market_games(posted + timedelta(hours=8))
    m = discord_msg(posted, "@everyone NBA LNE 2-5U Bad beats Let's sweep",
                    ["https://media.discordapp.net/attachments/1/2/IMG_1.png"])
    res = observe.ingest([m], Fake(games), Pool(games), conn=conn, now=NOW,
                         read_image=lambda url: (FIX / "chalk_hezonja_3.txt").read_text())
    by = {r["bet"]: r for r in res}
    assert by["Mario Hezonja over 5.5 Rebounds"]["outcome"] == "scraped"
    assert by["Danny Wolf over 3.5 Rebounds"]["outcome"] == "scraped"
    assert "aren't on Gemini" in by["Giannis Antetokounmpo over 15.5 Reb+Ast"]["reason"]
    rows = {r["bet_text"]: dict(r) for r in conn.execute("SELECT * FROM picks")}
    hez, wolf = rows["Mario Hezonja over 5.5 Rebounds"], rows["Danny Wolf over 3.5 Rebounds"]
    assert hez["result"] == "win" and wolf["result"] == "loss"         # Gemini resolutionSide
    assert hez["units"] == pytest.approx(3.5 / 3, abs=1e-3) and "Leg 1 of 3" in hez["note"]
    assert hez["market_type"] == "prop" and hez["position"] == "yes" and hez["posted_at"] == service.iso(posted)


def test_dog_spread_from_slip_locks_no_price_and_grades(conn):
    posted = NOW - timedelta(hours=30)
    games = market_games(posted + timedelta(hours=6))
    m = discord_msg(posted, "@everyone NBA 2U", ["https://media.discordapp.net/attachments/1/2/IMG_4580.png"])
    observe.ingest([m], Fake(games), Pool(games), conn=conn, now=NOW,
                   read_image=lambda url: (FIX / "spread_bucks.txt").read_text())
    p = dict(conn.execute("SELECT * FROM picks").fetchone())
    assert p["position"] == "no" and p["units"] == 2 and p["result"] == "loss"
    assert p["lock_price"] == pytest.approx(0.4)   # NO = 1 - YES candle (0.6)


def test_grade_pending_uses_position(conn):
    posted = NOW - timedelta(hours=30)
    live = [to_game(gev("NBA-2611200100-POR-MIL-S", "spread", POR_MIL, [
        ("Portland wins by more than 9.5 points", "Portland", .45, None)], posted + timedelta(hours=6), status="active"))]
    m = discord_msg(posted, "NBA 2U", ["https://media.discordapp.net/attachments/1/2/a.png"])
    observe.ingest([m], Fake(live), Pool(live), conn=conn, now=posted + timedelta(hours=1),
                   read_image=lambda url: "Milwaukee Bucks\n+9.5 Spread")
    assert conn.execute("SELECT result FROM picks").fetchone()[0] == "pending"
    settled = [to_game(gev("NBA-2611200100-POR-MIL-S", "spread", POR_MIL, [
        ("Portland wins by more than 9.5 points", "Portland", .45, "no")], posted + timedelta(hours=6)))]
    service.grade_pending(Fake(settled), conn=conn, at=NOW)
    assert conn.execute("SELECT result FROM picks").fetchone()[0] == "win"   # Portland didn't cover: Bucks +9.5 wins


def test_typed_spread_message_is_tracked(conn):
    posted = NOW - timedelta(hours=30)
    games = market_games(posted + timedelta(hours=6))
    res = observe.ingest([discord_msg(posted, "Portland -19.5 2u")], Fake(games), Pool(games), conn=conn, now=NOW)
    assert res[0]["outcome"] == "scraped"
    assert conn.execute("SELECT position, result FROM picks").fetchone()[:] == ("yes", "loss")


# ---- analyst direction: a verified analyst posts a spread / prop from the side panel --------
class LiveFake(Fake):
    def markets(self, refresh=False):
        return list(self.by.values())


def test_analyst_locks_spread_and_prop(conn):
    start = NOW + timedelta(hours=6)
    live = [to_game({**e, "status": "active"}) for e in [
        gev("NBA-2611200100-POR-MIL-S", "spread", POR_MIL, [("Portland wins by more than 9.5 points", "Portland", .45, None)], start),
        gev("NBA-2611200100-POR-MIL-PPAST", "prop", POR_MIL, [("Damian Lillard 6+ Assists", None, .62, None)], start, metric="assists")]]
    service.ensure_capper("discord:1", "X", conn=conn)
    r = service.lock_pick(capper_id="discord:1", source="receipts", post_id="a1", text="Bucks +9.5 2u",
                          posted_at=NOW, gemini=LiveFake(live), conn=conn, anchor=False)
    assert r.ok and r.pick["position"] == "no" and r.pick["lock_price"] == pytest.approx(0.55) and r.pick["units"] == 2
    r2 = service.lock_pick(capper_id="discord:1", source="receipts", post_id="a2", text="Damian Lillard over 5.5 assists",
                           posted_at=NOW, gemini=LiveFake(live), conn=conn, anchor=False)
    assert r2.ok and r2.pick["market_type"] == "prop" and r2.pick["side_label"] == "Damian Lillard 6+ Assists"
    bad = service.lock_pick(capper_id="discord:1", source="receipts", post_id="a3", text="Bucks +6.5",
                            posted_at=NOW, gemini=LiveFake(live), conn=conn, anchor=False)
    assert not bad.ok and "9.5" in bad.message
