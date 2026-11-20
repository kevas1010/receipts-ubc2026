"""Read pick'em and sportsbook slips (OCR text) into legs: moneylines, spreads, totals and player props.

Tuned on real analyst slips from Discord: Chalkboard ("↑ 19.5 / P + R + A"), Underdog/PrizePicks-style
cards ("NYK • G • #3 ... 4.5 Rebounds") and team cards ("Milwaukee Bucks / +9.5 Spread",
"Total Points / Higher 240.5"). Tesseract regularly drops decimals ("35" for 3.5) and reads the up-arrow as
"1" or "t" ("135" for ↑3.5), so lines are resolved by cross-checking a full-image pass against a second pass
over the right-hand column, where pick'em apps print the line and stat.

Whether a leg can be tracked is decided later by finding its Gemini market (see markets.py); this module only
reads what the slip says.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

POS = r"(?i:PG|SG|SF|PF|C|G|F|G-F|F-C|C-F|F-G|QB|RB|WR|TE|K|S|CB|LB|DE|DT)"  # OCR often lowercases these

# canonical stat -> pattern. Combos first so "Pts + Reb" isn't read as plain points.
STATS: list[tuple[str, re.Pattern]] = [(k, re.compile(p, re.I)) for k, p in [
    ("pra", r"\b(p\s*\+\s*r\s*\+\s*a|pts\s*\+\s*rebs?\s*\+\s*asts?|pra)\b"),
    ("pts_reb", r"\b(pts|points)\s*\+\s*(rebs?|rebounds)\b"),
    ("pts_ast", r"\b(pts|points)\s*\+\s*(asts?|assists)\b"),
    ("reb_ast", r"\b(asts?|assists)\s*\+\s*(rebs?|rebounds)\b|\b(rebs?|rebounds)\s*\+\s*(asts?|assists)\b"),
    ("tackles_ast", r"tackles\s*\+\s*ast"),
    ("fantasy", r"fantasy"),
    ("threes", r"(3[- ]?pt|3-pointers|threes|3pm)"),
    ("pass_att", r"pass(ing)?\s*att"),
    ("pass_yds", r"pass(ing)?\s*yds?|passing yards"),
    ("rush_yds", r"rush(ing)?\s*yds?|rushing yards"),
    ("rec_yds", r"rec(eiving)?\s*yds?|receiving yards"),
    ("receptions", r"\breceptions?\b|^rec$|\brec\b(?!\w)"),
    ("pass_td", r"pass(ing)?\s*tds?|passing touchdowns"),
    ("td", r"\b(anytime\s*)?(td|touchdowns?)\b"),
    ("rebounds", r"\brebounds?\b|\brebs?\b|rebound\)"),
    ("assists", r"\bassists?\b|\basts?\b|acwsts|assts"),
    ("steals", r"\bsteals?\b|soest"),
    ("blocks", r"\bblocks?\b|\bblks?\b"),
    ("turnovers", r"turnovers?"),
    ("points", r"\bpoints?\b|\bpts\b"),
]]
STAT_LABEL = {
    "pra": "Pts+Reb+Ast", "pts_reb": "Pts+Reb", "pts_ast": "Pts+Ast", "reb_ast": "Reb+Ast", "tackles_ast": "Tackles+Ast",
    "fantasy": "Fantasy", "threes": "3PM", "pass_att": "Pass Att", "pass_yds": "Pass Yds", "rush_yds": "Rush Yds",
    "rec_yds": "Rec Yds", "receptions": "Receptions", "pass_td": "Pass TD", "td": "TD", "rebounds": "Rebounds",
    "assists": "Assists", "steals": "Steals", "blocks": "Blocks", "turnovers": "Turnovers", "points": "Points",
}
FOOTBALL_STATS = {"pass_att", "pass_yds", "rush_yds", "rec_yds", "receptions", "pass_td", "td", "tackles_ast"}
SPORT_TAGS = {"NBA": "NBA", "NFL": "NFL", "NHL": "NHL", "CBB": "CBB", "NCAAB": "CBB", "NCAAM": "CBB", "CFB": "CFB",
              "NCAAF": "CFB", "MNF": "NFL", "TNF": "NFL", "SNF": "NFL", "MLB": "MLB", "EPL": "SOCCER", "UCL": "SOCCER"}

# Right column of the slip, the second OCR pass, is separated from the full pass by this marker in the cache.
RIGHT_MARK = "\n<<RIGHT-COLUMN>>\n"


@dataclass
class Leg:
    kind: str                       # moneyline | spread | total | prop
    line: float | None = None
    side: str | None = None         # over | under (totals and props)
    team: str | None = None         # team name (moneyline, spread) or team abbreviation (prop)
    player: str | None = None
    stat: str | None = None
    sport: str | None = None
    matchup: tuple[str, str] | None = None   # (away, home) as printed on the slip
    side_guessed: bool = False
    needs_review: bool = False
    _cands: list[float] = field(default_factory=list, repr=False)

    @property
    def stat_label(self) -> str:
        return STAT_LABEL.get(self.stat or "", "?")

    def describe(self) -> str:
        ou = {"over": "over", "under": "under"}.get(self.side or "", "")
        if self.kind == "prop":
            return f"{self.player} {ou} {fmt(self.line)} {self.stat_label}".replace("  ", " ")
        if self.kind == "spread":
            return f"{self.team} {self.line:+g}" if self.line is not None else f"{self.team} spread"
        if self.kind == "total":
            game = " @ ".join(self.matchup) if self.matchup else (self.team or "game")
            return f"{game} {ou} {fmt(self.line)}"
        return f"{self.team} ML"


@dataclass
class Slip:
    legs: list[Leg]
    units: float | None = None
    multiplier: float | None = None
    pick_count: int | None = None
    sport: str | None = None


def fmt(x: float | None) -> str:
    return "?" if x is None else f"{x:g}"


def find_stat(s: str) -> str | None:
    for k, rx in STATS:
        if rx.search(s or ""):
            return k
    return None


def arrow_before_number(s: str) -> str | None:
    """Tesseract renders ↑ as t/*/¢/4 and ↓ as L/Lv/J/|/v right before the line number."""
    m = re.search(r"(\S{1,2})\s*\d{1,3}(?:\.\d)?\s*$", s or "")
    if not m:
        return None
    tok = m.group(1)
    if re.fullmatch(r"(?:L|Lv|l|J|\||v|V|lv)", tok):
        return "under"
    if re.fullmatch(r"(?:t|\*|¢|4|f|\?|T|1|\+|ft)", tok):
        return "over"
    return None


def direction(s: str) -> str | None:
    if re.search(r"\b(higher|over|more)\b|↑", s or "", re.I):
        return "over"
    if re.search(r"\b(lower|under|less|fewer)\b|↓", s or "", re.I):
        return "under"
    return None


def candidates(raw: str | None) -> list[float]:
    """OCR drops the decimal ("35" for 3.5) and reads the up-arrow as "1"/"7" ("135" for ↑3.5)."""
    if raw is None:
        return []
    def fix(t: str) -> str:
        return t[:-1] + ".5" if "." not in t and len(t) >= 2 and t.endswith("5") else t
    out = [float(fix(raw))]
    if re.match(r"^[17]", raw) and len(raw.replace(".", "")) >= 3:
        out.append(float(fix(raw[1:])))
    return list(dict.fromkeys(out))


def last_num(s: str | None) -> str | None:
    m = re.search(r"(\d{1,3}(?:\.\d)?)(?!.*\d)", s or "")
    return m.group(1) if m else None


def clean(s: str) -> str:
    s = re.sub(r"[^\w\s.'&\-]", " ", s or "")
    s = " ".join(s.split())
    toks = s.split()
    while len(toks) > 1 and len(toks[-1]) <= 2 and toks[-1] not in ("Jr", "Jr.", "Sr", "II", "IV", "FC"):
        toks.pop()  # trailing OCR junk: "Milwaukee Bucks O"
    return " ".join(toks)


def strip_junk_prefix(s: str) -> str:
    toks = (s or "").strip().split()
    while len(toks) > 2 and (re.fullmatch(r"[^A-Za-z]*", toks[0]) or
                             (re.fullmatch(r"[A-Za-z&]{1,2}[^A-Za-z]*", toks[0]) and not re.fullmatch(r"[A-Z]\.", toks[0]))):
        toks.pop(0)
    if toks:
        toks[0] = re.sub(r"^[^A-Za-z]+", "", toks[0])
    return " ".join(toks)


def message_meta(text: str) -> tuple[float | None, float | None, str | None]:
    """Units, multiplier and sport tag from the Discord message that carried the slip ("NBA 2U Straight 1.62x")."""
    t = text or ""
    units = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*(?:u|units?)\b", t, re.I)
    if m:
        units = (float(m.group(1)) + float(m.group(2))) / 2 if m.group(2) else float(m.group(1))
    mult = None
    mm = re.search(r"\b(\d+(?:\.\d+)?)\s*x\b", t, re.I)
    if mm and 1 < float(mm.group(1)) < 100:
        mult = float(mm.group(1))
    sport = None
    sm = re.search(r"\b(" + "|".join(SPORT_TAGS) + r")\b", t)
    if sm:
        sport = SPORT_TAGS[sm.group(1)]
    return units, mult, sport


def split_cached(text: str) -> tuple[str, str]:
    full, _, right = (text or "").partition(RIGHT_MARK)
    return full, right


def parse_right_column(text: str) -> list[dict]:
    out = []
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    i = 0
    while i < len(lines):
        m = re.search(r"(?:^|\s)(\d{1,3}(?:\.\d)?)\s*$", lines[i])
        stat = find_stat(lines[i + 1]) if i + 1 < len(lines) else None
        if m and stat and not re.search(r"\$|picks|cash", lines[i], re.I):
            out.append({"cands": candidates(m.group(1)), "stat": stat, "side": direction(lines[i])})
            i += 2
            continue
        i += 1
    return out


MATCHUP = re.compile(r"^([A-Z][\w.'& ]+?)\s+(@|vs\.?|at)\s+([A-Z][\w.'& ]+?)$")
TEAM_LINE = re.compile(r"\b([A-Z]{2,4})\s*(?:vs\.?|at|@|[•·+\-=,«»]|\s)\s*([A-Z]{2,4})?\b")


def parse_slip(ocr_text: str, msg_text: str = "", right_text: str | None = None) -> Slip:
    if right_text is None:
        ocr_text, right_text = split_cached(ocr_text)
    units, mult, msg_sport = message_meta(msg_text)
    lines = [l.strip() for l in (ocr_text or "").splitlines() if l.strip()]
    legs: list[Leg] = []
    pick_count = None
    mx = re.search(r"max cash\s*(\d+(?:\.\d+)?)\s*x", ocr_text, re.I)
    tw = re.search(r"\$([\d,]+(?:\.\d+)?)\s*to win\s*\$([\d,]+(?:\.\d+)?)", ocr_text, re.I)
    if mx:
        mult = float(mx.group(1))
    elif tw:
        mult = float(tw.group(2).replace(",", "")) / float(tw.group(1).replace(",", ""))
    pc = re.search(r"(\d+)\s*picks?\b", ocr_text, re.I)
    if pc:
        pick_count = int(pc.group(1))

    matchup = None
    section_sport = None
    pos_rx = re.compile(rf"^(.*?[A-Z][A-Za-z'.\-]+(?:\s+(?:Jr\.?|Sr\.?|II|III|IV))?)\s+{POS}[a-z]?\b(.*)$")
    for i, L in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        nxt2 = lines[i + 2] if i + 2 < len(lines) else ""
        sec = re.match(r"^(NBA|NFL|NHL|CBB|CFB|NCAAB|NCAAF)\b", L)
        if sec:
            section_sport = SPORT_TAGS[sec.group(1)]
        mu = MATCHUP.match(L)
        if mu and not re.search(r"\d", L):
            matchup = (mu.group(1).strip(), mu.group(3).strip())
        sport = section_sport or msg_sport

        sp = re.search(r"([+-]\s?\d{1,2}(?:\.5)?)\s*spread", L, re.I)
        if sp:
            legs.append(Leg("spread", line=float(sp.group(1).replace(" ", "")),
                            team=clean(strip_junk_prefix(lines[i - 1] if i else "")), sport=sport, matchup=matchup))
            continue
        if re.search(r"\b(moneyline|money line|ML)\b", L, re.I) and "$" not in L:
            inline = re.sub(r"\b(moneyline|money line|ML)\b.*$", "", L, flags=re.I).strip()
            inline = re.sub(r"[+-]\d{3,4}", "", inline).strip()
            team = clean(strip_junk_prefix(inline or (lines[i - 1] if i else "")))
            if team:
                legs.append(Leg("moneyline", team=team, sport=sport, matchup=matchup))
            continue
        if re.search(r"total\s*(points|goals|runs)", L, re.I):
            look = " ".join((L, nxt, nxt2))
            d = direction(look)
            v = re.search(r"(\d{1,3}\.5|\d{2,3})", re.sub(r"total\s*\w+", "", look, flags=re.I))
            if v:
                legs.append(Leg("total", line=float(v.group(1)), side=d or "over", side_guessed=not d,
                                sport=sport, matchup=matchup))
                continue

        pm = pos_rx.match(L)
        is_hdr = bool(pm and re.search(r"[A-Z][a-z]", pm.group(1)))
        underdog = (not pm and re.search(r"^[^\d$]*[A-Z][a-z]+\s+[A-Z][a-z'.\-]+", L)
                    and re.search(r"\b[A-Z]{2,4}\s*[•·+\-]\s*(?:[A-Z]{1,3}(?:-[A-Z])?)\s*[•·+\-]", nxt))
        if (is_hdr or underdog) and not re.search(r"spread|moneyline|total", nxt, re.I):
            name = clean(strip_junk_prefix(pm.group(1) if is_hdr else re.sub(r"\s+\S*\d\S*\s*$", "", L)))
            name = re.sub(r"\s+(?:er\w*|ort|Tt|OTr\w*)$", "", name, flags=re.I).strip()
            stat = find_stat(" ".join((nxt, nxt2, pm.group(2) if is_hdr else "")))
            raw = last_num(pm.group(2) if is_hdr else re.sub(r"^.*?[A-Z][a-z]+\s+[A-Z][\w'.\-]+", "", L))
            c = candidates(raw)
            d = direction(" ".join((L, nxt, nxt2))) or arrow_before_number(pm.group(2) if is_hdr else L)
            tl = TEAM_LINE.search(nxt) or TEAM_LINE.search(nxt2)
            if not c and not stat:
                continue  # a team card header ("Chattanooga Mocs fe)"), not a player
            legs.append(Leg("prop", player=name, stat=stat, line=c[0] if c else None, side=d or "over",
                            side_guessed=not d, team=tl.group(1) if tl else None,
                            sport=sport or ("NFL" if stat in FOOTBALL_STATS else None), _cands=c))

    if right_text:
        col = parse_right_column(right_text)
        props = [l for l in legs if l.kind == "prop"]
        if len(col) == len(props):
            for leg, c in zip(props, col):
                both = next((x for x in leg._cands if x in c["cands"]), None)
                if both is not None:
                    leg.line = both
                elif leg.line is None or len(leg._cands) > 1:
                    leg.line = c["cands"][1] if len(c["cands"]) > 1 else c["cands"][0]
                if not leg.stat:
                    leg.stat = c["stat"]
                if c["side"] and leg.side_guessed:
                    leg.side, leg.side_guessed = c["side"], False
    for leg in legs:
        if leg.kind == "prop":
            if not leg.sport and leg.stat in FOOTBALL_STATS:
                leg.sport = "NFL"
            leg.needs_review = leg.line is None or not leg.stat or not leg.player
    return Slip(legs=legs, units=units, multiplier=mult, pick_count=pick_count, sport=msg_sport)


# ---- typed picks (an analyst posting from the side panel, or a short Discord message) --------
TYPED_PROP = re.compile(
    r"^(?P<player>[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){1,3})\s+(?P<side>over|under|o|u)\s*(?P<line>\d{1,3}(?:\.5)?)\s+(?P<stat>[a-z+ ]{2,30}?)(?:\s+\d+(?:\.\d+)?\s*u(?:nits?)?)?\s*$",
    re.I)
TYPED_TOTAL = re.compile(
    r"^(?P<a>[A-Za-z.'& ]+?)\s*(?:/|vs\.?|@|at|-)\s*(?P<b>[A-Za-z.'& ]+?)\s+(?P<side>over|under|o|u)\s*(?P<line>\d{2,3}(?:\.5)?)\b", re.I)
TYPED_SPREAD = re.compile(r"^(?P<team>[A-Za-z.'& ]{3,40}?)\s+(?P<line>[+-]\d{1,2}(?:\.5)?)(?![\d.])", re.I)


def parse_typed(text: str) -> Leg | None:
    """"Pistons -5.5 2u", "Pistons/Spurs over 218.5", "Tyrese Maxey over 5.5 assists". Moneylines stay in parse.py."""
    t = " ".join((text or "").split())
    t = re.sub(r"\b\d+(?:\.\d+)?\s*(?:u|units?)\b", "", t, flags=re.I).strip(" ,.")
    side = lambda s: "over" if s.lower() in ("over", "o") else "under"
    m = TYPED_PROP.match(t)
    if m:
        stat = find_stat(m.group("stat"))
        if stat:
            return Leg("prop", player=m.group("player").strip(), side=side(m.group("side")), line=float(m.group("line")),
                       stat=stat, sport="NFL" if stat in FOOTBALL_STATS else None)
    m = TYPED_TOTAL.match(t)
    if m:
        return Leg("total", matchup=(m.group("a").strip(), m.group("b").strip()), side=side(m.group("side")),
                   line=float(m.group("line")))
    m = TYPED_SPREAD.match(t)
    if m and not re.search(r"[+-]\d{3}", t):
        return Leg("spread", team=m.group("team").strip(), line=float(m.group("line")))
    return None
