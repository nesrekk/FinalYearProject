"""
ask_lib.py
==========
"Ask in English everywhere" (round 10, part B, step 10-7): one typed sentence
becomes the one thing the app already does for it.

    typed text --(Gemini, structured output)--> an answer in intent_schema() form
               --(to_action, this file)------> one checked action:
                    open_page       a PAGES id (+ an Analytics tab) and the link inputs that page reads
                    build_board     a Workbench board: player / team sets by id, table / chart / tool / finder blocks
                    run_finder      the Player Finder's spec (the Finder's own English box reads the sentence)
                    open_live_game  today's game of one team, or today's list
                    ask             a name two players on file share: which one? (never guessed)
                    refuse          with one of REASONS
               --(the box, step 10-8)---------> a preview of what will happen; one click runs it

The model never answers the question, never writes SQL and never writes a URL.
It gets the typed sentence and a fixed description of the app built from
api/ask_pages.py (every page, its link inputs and their allowed values), the
Workbench catalogue (datasets, stats, charts, tools), today's team codes, the
seasons on file and the reading rules, and must answer with JSON matching
intent_schema(), whose enums are those same keys. to_action() then treats the
answer as untrusted input: every page, key, value, stat, team, season and date
is looked up again, what isn't allowed is dropped and said, names become ids
through the app's own search (routers.workbench.workbench_entities), a shared
name becomes an `ask`, a season outside the data becomes a `no_data` refusal
(never the nearest season), and the result carries what the box will show.

The Player Finder keeps its own engine (workbench_parse_lib): a run_finder
answer, or a finder block on a board, sends the sentence through that box's
parse() and takes its spec, so the two boxes can never disagree.

Shared by routers/ask.py (the endpoint), scripts/ask_eval.py (the measured
score on api/ask_sentences.json) and api/tests/test_ask.py.
"""

import datetime
import hashlib
import json
import math
import re
import threading
import time
import unicodedata
from collections import deque
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import ask_pages as AP
import current_season
import workbench_catalogue as WC
import workbench_parse_lib as P
from routers.workbench import _dataset_meta, _season_label, workbench_entities

ParseError = P.ParseError
MODEL = P.MODEL
MAX_TEXT = P.MAX_TEXT
clean_text = P.clean_text


def api_key():
    return P.api_key()

ACTIONS = ("open_page", "build_board", "run_finder", "open_live_game", "refuse")
CHARTS = ("scatter", "line", "bar", "histogram", "box", "heatmap")
TOOLS = {"card": "player", "shots": "player", "quality": "player", "shotmix": "player", "gamelog": "player",
         "tracker": "player", "rapm": "player", "projections": "player", "rotation": "team", "assists": "team"}
TOOL_LABELS = {"card": "Player card", "shots": "Shot chart", "quality": "Shot quality map", "shotmix": "Shot mix history",
               "gamelog": "Game log", "tracker": "Rating Tracker", "rapm": "RAPM", "projections": "Projection",
               "rotation": "Rotation chart", "assists": "Assist network"}
BOARD_DATASETS = ("player_season", "player_game", "team_season", "team_game")
# The refusal reasons, as api/ask_sentences.json defines them (a test checks the keys match).
REASONS = {
    "off_topic": "That isn't a question about basketball data in this app.",
    "opinion": "That's a judgement with no stat behind it; the app shows numbers, it doesn't rank greatness.",
    "other_league": "Only the NBA is on file (no WNBA, EuroLeague, G League or international play).",
    "no_data": "The app has that kind of thing, but not for that season, player or span.",
    "unknown_player": "Nobody of that name is on file.",
    "needs_a_computed_answer": "That answer is a number no page shows; this box only opens what the app has.",
    "would_change_data": "This box only reads; it can't add, change or delete anything.",
    "betting_advice": "The app shows markets against its own model; it never gives betting advice.",
}
DEFAULT_COLUMNS = {
    "player_season": ["gp", "min", "pts", "reb", "ast", "ts_pct", "bpm"],
    "player_game": ["min", "pts", "reb", "ast", "fg3m", "ts_pct", "plus_minus"],
    "team_season": ["w", "l", "w_pct", "mov", "o_rtg", "d_rtg", "n_rtg", "pace"],
    "team_game": ["games", "wins", "win_pct", "margin", "off_rating", "def_rating", "net_rating"],
}
LIMITS = {"params": 16, "players": 12, "teams": 12, "blocks": 6, "columns": 20, "notes": 8}
NBA_TZ = ZoneInfo("America/New_York")
_SEASON_LABEL = re.compile(r"^(\d{4})-(\d{2})$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class _Drop(Exception):
    """A value that can't be used; the message is shown and the key left out."""


class _NoData(Exception):
    """An ask outside the data's edges: the whole action becomes a `no_data` refusal."""


class _Default(Exception):
    """The page's own default value: not written to the link (the page shows it anyway), nothing to say."""


# ─── today ──────────────────────────────────────────────────────────────────

def today_nba(now=None):
    """The NBA's calendar date (US Eastern), as the frontend's nbaDateIso()."""
    now = now or datetime.datetime.now(NBA_TZ)
    return now.astimezone(NBA_TZ).date() if now.tzinfo else now.date()


def seasons_today():
    """(current season, 'last season') as api/current_season.py says today (R10-009)."""
    st = current_season.status()
    return int(st["current"]), int(st["latest_complete"])


# ─── what the model may answer with ─────────────────────────────────────────

def _teams():
    return dict(P._teams())


def intent_schema():
    """JSON Schema for the model's answer. Every enum is a key the app has: page ids, tab ids, link keys, team codes,
    datasets, chart and tool kinds, refusal reasons. Values of link inputs are strings, checked by to_action()
    against the page's own rules (ask_pages.py). No maxItems on nested arrays beside enums (Gemini refuses that)."""
    nullable_enum = lambda values: {"type": ["string", "null"], "enum": list(values) + [None]}   # noqa: E731
    block = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": ["table", "chart", "tool", "finder"]},
            "dataset": nullable_enum(BOARD_DATASETS),
            "columns": {"type": "array", "items": {"type": "string"}},
            "chart": nullable_enum(CHARTS),
            "x": {"type": ["string", "null"]},
            "y": {"type": ["string", "null"]},
            "season_from": {"type": ["integer", "null"]},
            "season_to": {"type": ["integer", "null"]},
            "tool": nullable_enum(TOOLS),
            "member": {"type": ["string", "null"]},
            "text": {"type": ["string", "null"]},
        },
        "required": ["type", "dataset", "columns", "chart", "x", "y", "season_from", "season_to", "tool", "member", "text"],
    }
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "page": nullable_enum(AP.PAGES),
            "tab": nullable_enum(AP.TABS),
            "params": {"type": "array", "items": {
                "type": "object",
                "properties": {"key": {"type": "string", "enum": list(AP.all_keys())}, "value": {"type": "string"}},
                "required": ["key", "value"]}},
            "board": {"type": "object", "properties": {
                "player_names": {"type": "array", "items": {"type": "string"}},
                "teams": {"type": "array", "items": {"type": "string", "enum": list(AP.CODES)}},
                "blocks": {"type": "array", "items": block}},
                "required": ["player_names", "teams", "blocks"]},
            "live_team": nullable_enum(AP.CODES),
            "refuse_reason": nullable_enum(REASONS),
            "guessed": {"type": "array", "items": {"type": "string"}},
            "not_understood": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["action", "page", "tab", "params", "board", "live_team", "refuse_reason", "guessed", "not_understood"],
    }


# ─── what the model is told ─────────────────────────────────────────────────

def _key_line(key, k):
    if k.type == "enum":
        vals = ", ".join(f"{v} ({k.labels[v]})" if k.labels.get(v) and k.labels[v] != v else v for v in k.values)
        return f"{key}: {k.what}; one of: {vals}"
    if k.type == "list":
        return f"{key}: {k.what}" + (f"; stats: {', '.join(k.stats)}" if k.stats else "")
    if k.type in ("int", "num"):
        return f"{key}: {k.what} ({k.lo:g} to {k.hi:g})"
    return f"{key}: {k.what}"


def _page_lines(pages):
    out = []
    for pid, p in pages.items():
        line = f"- {pid}: {p.label}. {p.what}"
        if p.first_season:
            line += f" Data from {_season_label(p.first_season)}."
        if p.bare_hint:
            line += f" ({p.bare_hint}.)"
        out.append(line)
        for key, k in p.keys.items():
            out.append(f"    {_key_line(key, k)}")
    return "\n".join(out)


def _columns_text(ds_key):
    ds = WC.DATASETS[ds_key]
    return ", ".join(f"{c.key} ({c.label})" for c in ds.columns.values() if c.status == "verified")


def system_prompt(today=None, seasons=None):
    today = today or today_nba()
    current, last = seasons or seasons_today()
    yesterday = today - datetime.timedelta(days=1)
    teams = ", ".join(f"{code} {name}" for code, name in sorted(_teams().items()))
    meta = _dataset_meta()
    reasons = "\n".join(f"- {k}: {v}" for k, v in REASONS.items())
    return f"""You turn one sentence typed into an NBA statistics app into the ONE thing the app should do for it.
You never answer the question, never invent numbers, never write SQL and never write a URL: you fill the JSON form.
Everything the app can do is listed below; nothing else exists. Fill only what the sentence says: no view, sort,
floor, limit or extra input the person didn't ask for (the page's own defaults fill the rest).

ACTIONS (action)
- open_page: one page with its link inputs (page, tab for analytics, params as key/value strings). For one player,
  one team or one tool with inputs.
- build_board: a Workbench board (board: player_names, teams, blocks). For three or more players, two or more teams,
  a named chart, or named stats to lay side by side. Two players compared in a season = open_page compare (not a
  board); two players' CAREERS, or over several seasons = a board (a player_season table, no seasons); two teams
  compared = a board (Team Comparison reads nothing from a link).
- run_finder: a LIST OF PLAYERS meeting conditions on stats ("players who averaged 25 and 10 since 2010", "who
  scored 30+ in 10 straight games", "centers who average 3 blocks"). Set only action: the Finder's own box reads
  the sentence afterwards. The subject decides: PLAYERS with a number of games or a run ("players with 10 or more
  40-point games in a season", "who has the most 50-point games", "who scored 30+ in 10 straight games") =
  run_finder; the GAMES themselves ("Curry's 50-point games", "all 60-point games", "games with 20 assists",
  "longest streak of 25-point games" = mode streaks) = the gamefinder page; "seasons like 27/7/7" is the statline page;
  a stat's LEADERS in a season or range is the builder page ("who led", "best", "most", "top 10"). The Finder has
  no team box: whenever a TEAM is named with a season average ("for the Knicks", "Lakers players who averaged 20",
  "who leads the Lakers in scoring") it is the builder page with stat, from, to and team, never run_finder.
- open_live_game: a score or game "right now", "tonight", "live", "is there a game on", "what's the score"
  (live_team: the team's code, or null for today's list: "live games", "games on now", "is there a game on" =
  open_live_game with live_team null, not the scores page, which is a day's finished scoreboard). "How are the Celtics doing right now" = open_live_game
  BOS; "how are the Lakers doing this season" = the team page; "last night" or a past date is the scores page with
  that date (today's scores need no date).
- refuse: with refuse_reason (below).

A player's game log, next-season projection, on/off, RAPM, Rating Tracker, clutch, contract, shot zones or awards
with no page named = open_page player (his profile has those blocks). A team's on/off, pairs and lineups are
Analytics tabs (page analytics, tab onoff / pairs / lineups) with team and season; a team's rotation, assist
network, possessions and coaching decisions have their own pages.

PAGES (page) and the link inputs each reads (params). A page with no inputs opens bare.
{_page_lines(AP.PAGES)}

ANALYTICS TABS (page analytics + tab)
{_page_lines(AP.TABS)}

BOARDS (build_board). player_names: full names of the players in the player set (never a team); teams: codes of
the team set (a team's nickname or city is a team, never a player);
blocks: each with type, and for table/chart a dataset (player_season default for players, team_season for teams;
player_game / team_game for single games, 2020-21 on), columns (the stats to show; [] = the page's defaults),
chart (scatter needs x and y; line / bar / histogram / box need y), season_from / season_to (end years; both null
= every season on file, i.e. careers; "since 2015-16" = 2016 to {current}), tool (an app tool for one member: {', '.join(f'{k} = {v}' for k, v in TOOL_LABELS.items())};
member = the player's name or team code), text (a finder block's condition sentence). No blocks = a table.
Stats by dataset:
  player_season ({_season_label(meta['player_season']['from'])} on): {_columns_text('player_season')}
  team_season ({_season_label(meta['team_season']['from'])} on): {_columns_text('team_season')}
  player_game ({_season_label(meta['player_game']['from'])} on): {_columns_text('player_game')}
  team_game ({_season_label(meta['team_game']['from'])} on): {_columns_text('team_game')}

STATS: "from three", "three-point shooting", "shooting" = a percentage (fg3_pct, fg_pct, ft_pct, ts_pct); "threes",
"threes made" = fg3m; "scoring" = pts; "boards" = reb; "dimes" = ast.

SEASONS are end years: 2022-23 = 2023; a bare year Y is the season ending in Y ("in 2010" = 2009-10 = 2010).
Today is {today.isoformat()} (the NBA's date, US Eastern); yesterday was {yesterday.isoformat()}.
"this season" / "this year" = {_season_label(current)} = {current}; "last season" = {_season_label(last)} = {last}.
On a page with from and to (builder, gamefinder, plays, regression, statline) one season, "this season" or "last
season" fills BOTH from and to with it; on a board it fills season_from and season_to; on shotcharts, team, era,
hotstreaks, rapm, splits and the Analytics tabs it is the season input. A sentence with no season leaves every
season input out.
On the builder, n only for an explicit "top N" ("who led", "best", "most" = no n). "ever" / "of all time" on the
builder = from 1950 to {current}; a decade ("the 1990s") = the seasons ending in its
years (1990 to 1999); on a page, "since 2015-16" = from 2016 with no 'to'; "as a rookie" = the player's first NBA season (use
what you know of his debut; say so in guessed); "next season" / "next year" on a projection = the projections page
(it has one target season). On shotcharts write the season as its label ("2015-16") or end year.

THE DATA'S EDGES (an ask outside them = refuse no_data, never the nearest season the data has): player seasons from
1949-50; team seasons from 1946-47; shot locations (shotcharts, shot zones, quality) from 1996-97; game logs and
every play-by-play tool (gamefinder, plays, rotations, assists, possessions, coaching, bestgames, rapm, hotstreaks,
splits, a profile's game log / on-off / clutch, analytics onoff / pairs / lineups / replay / wpa) from 2020-21; a
question whose answer needs every game ever (how many players ever scored 70 in a game) is no_data too; salaries
and contracts end with {_season_label(AP.LAST_SALARY_SEASON)} (a salary for a later season = no_data); nothing after
{_season_label(current)} exists.

PLAYERS: write a player's full name as NBA.com spells it (Stephen Curry, LeBron James, Nikola Jokić, Luka Dončić,
Giannis Antetokounmpo, Shai Gilgeous-Alexander, Victor Wembanyama, Kevin Durant, Damian Lillard, Shaquille O'Neal,
Michael Jordan, Dirk Nowitzki, Kobe Bryant, Magic Johnson, Wilt Chamberlain, Anthony Edwards, Devin Booker,
Jayson Tatum, Joel Embiid, Hakeem Olajuwon). Nicknames and misspellings go to the one player they commonly mean
(Steph, Klay, Bron, KD, Luka, Giannis, Wemby, Shai / SGA, Dame, Shaq, MJ, Dirk, Kobe, Magic, Wilt, Ant, Book,
Joker). The server looks every name up and asks the person when two players share it; you never pick between
namesakes and never invent a player: a name you don't know as an NBA player = refuse unknown_player.
TEAMS: today's codes, also for a franchise's past seasons (the 2007-08 Celtics = BOS; the Sonics = OKC; the Nets
= BKN): {teams}. Nicknames and cities: Dubs = GSW, Sixers / Philly = PHI, Blazers = POR, Wolves = MIN, Cavs = CLE,
Mavs = DAL, Knicks = NYK, Heat = MIA, Pistons = DET, Pacers = IND, Thunder = OKC, Spurs = SAS, Jazz = UTA.

REFUSALS (refuse_reason). Comparing two players or asking who led a stat is NOT an opinion (open the page);
"who is the GOAT", "best ever" with no stat is. A count or average no page shows ("the average age of MVP
winners") = needs_a_computed_answer, unless the data can't cover the span asked (then no_data).
{reasons}

OUTPUT: params = [{{"key", "value"}}] with every value a string ("2016", "LAL", "Stephen Curry", "pts:gte:50",
"2026-03-15"); only keys the chosen page lists, and only for what the sentence says: never a page's default
(era all, view team, version single, mode games, order high, v games / off / team, dir hot / up, m net, pos all,
tab live) unless the sentence asks for it by name; for a date, leave today out. board only for build_board (else
empty arrays); live_team only for open_live_game; refuse_reason only for refuse. guessed: usually []; only a
real assumption the sentence didn't state (a season from basketball knowledge: "unanimous MVP year = 2015-16"; a
rookie season; which of two readings you took), in a few words each. Reading "this season", "last season", a
season label, a year, a nickname listed above or a team's nickname is NOT a guess. not_understood: words of the
sentence no input could hold, in a few words each; [] when everything fit."""


def prompt_version(today=None, seasons=None):
    """A short hash of what the model is told (prompt + schema + model), recorded with every evaluation."""
    blob = json.dumps([MODEL, system_prompt(today, seasons), intent_schema()], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# ─── names → ids (the app's own search) ─────────────────────────────────────

def fold(text):
    """Lower-case, accents off, punctuation inside names (hyphens, periods, apostrophes) out, spaces collapsed."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[-.'’`]", " ", text.lower())
    return " ".join(text.split())


def search_players(name):
    """The app's search (GET /workbench/entities) for a typed name, then the names that are this name: equal after
    folding, or this name followed by a suffix (Jimmy Butler → Jimmy Butler III). → [{id, name, from, to, team}]."""
    q = " ".join(str(name or "").split())
    if len(q) < 2:
        return []
    rows = workbench_entities(kind="player", q=q)["results"]
    want = fold(q)
    if not rows and " " in want:
        # The database spells it with a hyphen or an apostrophe ("Shai Gilgeous Alexander"): search the last word.
        rows = workbench_entities(kind="player", q=q.split()[-1])["results"]
    hits = [r for r in rows if fold(r["name"]) == want]
    if not hits:
        hits = [r for r in rows if fold(r["name"]).startswith(want + " ")]
    return hits


def resolve_player(name, choices=None):
    """→ ("one", row) | ("many", rows) | ("none", None). `choices` {typed name: id} answers an earlier `ask`."""
    hits = search_players(name)
    chosen = (choices or {}).get(fold(name)) if choices else None
    if chosen is not None:
        picked = [r for r in hits if r["id"] == chosen]
        if picked:
            return "one", picked[0]
    if not hits:
        return "none", None
    if len(hits) == 1:
        return "one", hits[0]
    return "many", hits


# ─── the model's answer → one checked action ────────────────────────────────

class _Ctx:
    """What one to_action() call collects: notes (dropped and why), the namesake to ask about, resolved names."""

    def __init__(self, today, seasons, choices, finder):
        self.today = today
        self.current, self.last = seasons
        self.choices = {fold(k): v for k, v in (choices or {}).items()}
        self.finder = finder
        self.notes = []
        self.pending = None          # (name, rows) when a shared name must be asked about
        self.names = {}              # id → name, for previews and board members

    def note(self, text):
        text = " ".join(str(text).split())
        if text and text not in self.notes and len(self.notes) < LIMITS["notes"]:
            self.notes.append(text)

    def player(self, raw, where):
        """A name → {id, name, ...}; a namesake → the placeholder "<chosen>" and an `ask`; nobody → unknown_player."""
        name = " ".join(str(raw or "").split())
        if not name:
            raise _Drop(f"{where}: no name given.")
        status, hit = resolve_player(name, self.choices)
        if status == "none":
            raise _Unknown(name)
        if status == "many":
            if self.pending is None:
                self.pending = (name, hit)
            return {"id": "<chosen>", "name": name}
        self.names[hit["id"]] = hit["name"]
        return hit


class _Unknown(Exception):
    """A name nobody on file has: the whole action becomes an `unknown_player` refusal."""


def _int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and math.isfinite(v) and float(v).is_integer():
        return int(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        if re.fullmatch(r"-?\d+\.0+", s):
            return int(float(s))
    return None


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    if isinstance(v, str):
        try:
            f = float(v.strip().replace(",", "").rstrip("%"))
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _season_value(raw, ctx):
    """An end year from "2016", 2016, "2015-16", or the tokens this season / last season."""
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("current", "this season", "this year"):
            return ctx.current
        if s in ("last", "last season", "last year"):
            return ctx.last
        m = _SEASON_LABEL.match(s)
        if m:
            start = int(m.group(1))
            end = start // 100 * 100 + int(m.group(2))
            if end < start:
                end += 100
            if end == start + 1:
                return end
            return None
    n = _int(raw)
    return n if n is not None and 1900 <= n <= 2200 else None


def _check_season_range(season, page, key, ctx):
    lo = page.first_season or AP.FIRST_TEAM_SEASON
    if season < lo:
        raise _NoData(f"{page.label} has data from {_season_label(lo)}; {_season_label(season)} was asked for.")
    if season > ctx.current:
        raise _NoData(f"Nothing after {_season_label(ctx.current)} is on file; {_season_label(season)} was asked for.")
    return season


def _team_value(raw, where):
    s = str(raw or "").strip()
    code = s.upper()
    teams = _teams()
    if code in teams:
        return code
    want = fold(s)
    by_name = [c for c, n in teams.items() if want and (fold(n) == want or fold(n).endswith(" " + want) or fold(n).startswith(want + " "))]
    if len(by_name) == 1:
        return by_name[0]
    raise _Drop(f"{where}: {s!r} isn't a team the app knows.")


def _list_value(k, raw, where, ctx):
    """A comma-separated input: each item checked; a bad item is left out and said, the rest kept."""
    items = [x.strip() for x in str(raw or "").split(",") if x.strip()]
    if not items:
        raise _Drop(f"{where}: nothing given.")
    out = []
    for item in items[:12]:
        try:
            out.append(_list_item(k, item))
        except _Drop as e:
            ctx.note(f"{where}: {e}")
    if not out:
        raise _Drop(f"{where}: nothing usable was given.")
    seen = []
    for x in out:
        if x not in seen:
            seen.append(x)
    return ",".join(seen)


def _list_item(k, item):
    if k.item == "cond":
        parts = item.split(":")
        if len(parts) != 3 or parts[0] not in k.stats or parts[1] not in AP.GAME_FINDER_OPS or _num(parts[2]) is None:
            raise _Drop(f"{item!r} isn't stat:op:value with a Game Finder stat; left out.")
        v = _num(parts[2])
        if parts[0] in AP.PCT_STATS and v > 1:
            v = v / 100
        return f"{parts[0]}:{parts[1]}:{_clean(v)}"
    if k.item == "line":
        parts = item.split(":")
        if len(parts) != 2 or parts[0] not in k.stats or _num(parts[1]) is None:
            raise _Drop(f"{item!r} isn't stat:value with a Stat Line Finder stat; left out.")
        v = _num(parts[1])
        if parts[0] in AP.PCT_STATS and v > 1:
            v = v / 100
        return f"{parts[0]}:{_clean(v)}"
    if k.item == "stat":
        if item not in k.stats:
            raise _Drop(f"{item!r} isn't a stat this page knows; left out.")
        return item
    if k.item == "period":
        if item.lower() not in AP.PERIODS:
            raise _Drop(f"{item!r} isn't a period (1, 2, 3, 4, ot); left out.")
        return item.lower()
    raise _Drop(f"{item!r} can't be read.")


def _clean(v):
    v = float(v)
    return int(v) if v.is_integer() else round(v, 6)


def _read_value(key, k, raw, page, ctx):
    where = f"{key}"
    if k.type == "player":
        return ctx.player(raw, where)["id"]
    if k.type == "team":
        return _team_value(raw, where)
    if k.type in ("season", "season_label"):
        season = _season_value(raw, ctx)
        if season is None:
            raise _Drop(f"{where}: {raw!r} isn't a season.")
        _check_season_range(season, page, key, ctx)
        return _season_label(season) if k.type == "season_label" else season
    if k.type == "date":
        s = str(raw or "").strip().lower()
        if s in ("today", ctx.today.isoformat()):
            raise _Drop("Today is the page's default; no date filled.")
        if s == "yesterday":
            return (ctx.today - datetime.timedelta(days=1)).isoformat()
        if not _DATE.match(s):
            raise _Drop(f"{where}: {raw!r} isn't a date (YYYY-MM-DD).")
        try:
            d = datetime.date.fromisoformat(s)
        except ValueError:
            raise _Drop(f"{where}: {raw!r} isn't a real date.") from None
        if d > ctx.today:
            raise _NoData(f"{d.isoformat()} hasn't been played yet.")
        return d.isoformat()
    if k.type == "int":
        n = _int(raw)
        if n is None or not (k.lo <= n <= k.hi):
            raise _Drop(f"{where}: {raw!r} isn't a whole number between {k.lo:g} and {k.hi:g}.")
        return n
    if k.type == "num":
        n = _num(raw)
        if n is None or not (k.lo <= n <= k.hi):
            raise _Drop(f"{where}: {raw!r} isn't a number between {k.lo:g} and {k.hi:g}.")
        return _clean(n)
    if k.type == "enum":
        s = str(raw or "").strip()
        match = next((v for v in k.values if v.lower() == s.lower()), None)
        if match is None:
            # Allow the label ("heat map" → heatmap).
            match = next((v for v, lab in k.labels.items() if fold(lab) == fold(s)), None)
        if match is None:
            raise _Drop(f"{where}: {s!r} isn't one of {', '.join(k.values)}.")
        if k.default is not None and match == k.default:
            raise _Default()
        return match
    if k.type == "list":
        return _list_value(k, raw, where, ctx)
    raise _Drop(f"{where}: unknown input type.")


def _open_page(intent, ctx):
    page_id = intent.get("page")
    if page_id not in AP.PAGES:
        raise ParseError("The model named a page the app doesn't have.", status=502)
    tab = intent.get("tab")
    if page_id == "analytics":
        if tab not in AP.TABS:
            raise ParseError("Which Analytics tool? The sentence didn't say.", status=422, dropped=ctx.notes)
    elif tab is not None:
        ctx.note(f"An Analytics tab ({tab}) was named for the {AP.PAGES[page_id].label} page and left out.")
        tab = None
    page = AP.page_for(page_id, tab)
    params = {}
    raw = intent.get("params") if isinstance(intent.get("params"), list) else []
    if len(raw) > LIMITS["params"]:
        ctx.note(f"Only the first {LIMITS['params']} inputs were kept.")
    for item in raw[:LIMITS["params"]]:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            continue
        key = item["key"]
        k = page.keys.get(key)
        if k is None:
            if page.keys:
                ctx.note(f"{page.label} has no '{key}' input; it was left out.")
            else:
                ctx.note(f"{page.label} reads nothing from a link; '{key}' was left out.")
            continue
        if key in params:
            continue
        try:
            params[key] = _read_value(key, k, item.get("value"), page, ctx)
        except _Drop as e:
            ctx.note(str(e))
        except _Default:
            pass
    label = page.label if page_id != "analytics" else f"Analytics › {page.label}"
    return {"action": "open_page", "page": page_id, "hash": tab, "params": params,
            "href": _href(page_id, tab, params), "label": label,
            "preview": _page_preview(label, page, params, ctx)}


def _href(page, tab, params):
    q = urlencode({"page": page, **{k: v for k, v in params.items() if v is not None}}, safe=",:")
    return f"?{q}" + (f"#{tab}" if tab else "")


def _page_preview(label, page, params, ctx):
    parts = [f"Open {label}"]
    if ("from" in params or "to" in params) and "from" in page.keys and "to" in page.keys:
        lo, hi = params.get("from"), params.get("to")
        parts.append(_season_label(lo) if lo == hi or hi is None and lo else
                     f"{_season_label(lo) if lo else '…'} to {_season_label(hi) if hi else 'now'}")
    for key, v in params.items():
        k = page.keys[key]
        if key in ("from", "to") and "from" in page.keys and "to" in page.keys:
            continue
        if k.type == "player":
            parts.append(ctx.names.get(v, "which player?" if v == "<chosen>" else str(v)))
        elif k.type == "team":
            parts.append(_teams().get(v, v))
        elif k.type == "season":
            parts.append(_season_label(v))
        elif k.type == "enum":
            parts.append(k.labels.get(v) or v)
        elif k.type in ("int", "num"):
            parts.append(f"{key} {v}")
        elif k.type == "list" and k.item == "cond":
            ops = {"gte": "≥", "gt": ">", "lte": "≤", "lt": "<", "eq": "="}
            parts.append(", ".join(f"{AP.STAT_LABELS.get(a, a)} {ops[o]} {c}" for a, o, c in
                                   (x.split(":") for x in v.split(","))))
        elif k.type == "list" and k.item == "line":
            parts.append(", ".join(f"{AP.STAT_LABELS.get(a, a)} {c}" for a, c in (x.split(":") for x in v.split(","))))
        else:
            parts.append(str(v))
    if page is AP.PAGES.get("shotcharts") and "games" not in params:
        parts.append("regular season")
    return " · ".join(parts)


def _board_action(intent, ctx):
    raw = intent.get("board") if isinstance(intent.get("board"), dict) else {}
    names = [n for n in (raw.get("player_names") or []) if isinstance(n, str) and n.strip()]
    codes = [c for c in (raw.get("teams") or []) if isinstance(c, str)]
    if len(names) > LIMITS["players"]:
        ctx.note(f"Only the first {LIMITS['players']} players were kept.")
    if len(codes) > LIMITS["teams"]:
        ctx.note(f"Only the first {LIMITS['teams']} teams were kept.")
    players = []
    for n in names[:LIMITS["players"]]:
        try:
            hit = ctx.player(n, "Board")
        except _Drop as e:
            ctx.note(str(e))
            continue
        except _Unknown:
            # A team written as a player ("Celtics") joins the team set; anyone else is nobody on file.
            try:
                codes.append(_team_value(n, "Board"))
                continue
            except _Drop:
                raise _Unknown(n) from None
        if hit["id"] not in [p["id"] for p in players]:
            players.append(hit)
    teams = []
    for c in codes[:LIMITS["teams"]]:
        try:
            code = _team_value(c, "Board")
        except _Drop as e:
            ctx.note(str(e))
            continue
        if code not in teams:
            teams.append(code)
    if not players and not teams:
        raise ParseError("No players or teams for a board were named.", status=422, dropped=ctx.notes)
    sets, members = [], {}
    if players:
        sets.append({"id": "s0", "name": " / ".join(ctx.names.get(p["id"], p["name"]) for p in players[:4]) or "Players",
                     "kind": "player",
                     "members": [{"id": p["id"], "name": ctx.names.get(p["id"], p["name"]), "color": i % 8}
                                 for i, p in enumerate(players)]})
        members["player"] = "s0"
    if teams:
        sid = f"s{len(sets)}"
        sets.append({"id": sid, "name": " / ".join(_teams()[t] for t in teams[:4]), "kind": "team",
                     "members": [{"id": t, "name": _teams()[t], "color": i % 8} for i, t in enumerate(teams)]})
        members["team"] = sid
    blocks_in = raw.get("blocks") if isinstance(raw.get("blocks"), list) else []
    if len(blocks_in) > LIMITS["blocks"]:
        ctx.note(f"Only the first {LIMITS['blocks']} blocks were kept.")
    blocks, scored = [], []
    for b in blocks_in[:LIMITS["blocks"]]:
        try:
            block, shape = _block(b, members, players, teams, ctx)
        except _Drop as e:
            ctx.note(str(e))
            continue
        blocks.append(block)
        scored.append(shape)
    if not blocks:
        # No usable block: a table on each set (the page's default columns).
        for kind, sid in members.items():
            ds = "player_season" if kind == "player" else "team_season"
            block, shape = _table_block(ds, sid, [], None, None, ctx)
            blocks.append(block)
            scored.append(shape)
    layout = [{"type": "set", "w": 4, "h": 7, "settings": {"setId": s["id"]}} for s in sets]
    board = {"name": _board_name(sets, scored), "sets": sets, "blocks": layout + blocks}
    return {"action": "build_board",
            "sets": [{"kind": s["kind"], ("ids" if s["kind"] == "player" else "codes"): [m["id"] for m in s["members"]]}
                     for s in sets],
            "blocks": scored, "board": board,
            "preview": "New board: " + " · ".join([board["name"]] + [_block_words(s) for s in scored])}


def _season_pair(b, ctx):
    lo, hi = _season_value(b.get("season_from"), ctx) if b.get("season_from") is not None else None, \
        _season_value(b.get("season_to"), ctx) if b.get("season_to") is not None else None
    if b.get("season_from") is not None and lo is None:
        ctx.note(f"Block: {b.get('season_from')!r} isn't a season; the range was left open.")
    if b.get("season_to") is not None and hi is None:
        ctx.note(f"Block: {b.get('season_to')!r} isn't a season; the range was left open.")
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    for s in (lo, hi):
        if s is not None and (s < AP.FIRST_TEAM_SEASON or s > ctx.current):
            ctx.note(f"Block: {_season_label(s)} is outside the data; the range was left open.")
            return None, None
    if lo is not None and hi is None:
        hi = ctx.current           # "since 2015-16": up to the current season
    return lo, hi


def _columns(ds_key, cols, ctx, where):
    ds = WC.DATASETS[ds_key]
    out = []
    for c in (cols if isinstance(cols, list) else [])[:LIMITS["columns"]]:
        col = ds.columns.get(c) if isinstance(c, str) else None
        if col is None or col.status != "verified":
            ctx.note(f"{where}: {c!r} isn't a stat of {ds.label.lower()}; left out.")
        elif c not in out:
            out.append(c)
    return out


def _block(b, members, players, teams, ctx):
    if not isinstance(b, dict) or b.get("type") not in ("table", "chart", "tool", "finder"):
        raise _Drop("A block of a kind the Workbench doesn't have was left out.")
    kind = b["type"]
    if kind == "tool":
        tool = b.get("tool")
        if tool not in TOOLS:
            raise _Drop(f"Tool block: {b.get('tool')!r} isn't an app tool.")
        entity = TOOLS[tool]
        sid = members.get(entity)
        if sid is None:
            raise _Drop(f"{TOOL_LABELS[tool]} needs a {entity} set; none was named.")
        member = None
        if b.get("member"):
            if entity == "player":
                try:
                    hit = ctx.player(b["member"], f"{TOOL_LABELS[tool]} block")
                    member = hit["id"] if any(p["id"] == hit["id"] for p in players) else None
                except (_Drop, _Unknown):
                    member = None
            else:
                try:
                    code = _team_value(b["member"], f"{TOOL_LABELS[tool]} block")
                    member = code if code in teams else None
                except _Drop:
                    member = None
        if member is None:
            member = players[0]["id"] if entity == "player" else teams[0]
        season = _season_value(b.get("season_from"), ctx) if b.get("season_from") is not None else None
        settings = {"tool": tool, "setId": sid, "member": member, "season": season, "view": None, "games": None}
        return ({"type": "tool", "w": 6, "h": 12, "title": TOOL_LABELS[tool], "settings": settings},
                {"type": "tool", "tool": tool, "member": member, "dataset": None, "columns": None, "chart": None,
                 "x": None, "y": None, "seasonFrom": season, "seasonTo": season, "spec": None})
    if kind == "finder":
        text = b.get("text") if isinstance(b.get("text"), str) else ""
        if not text.strip():
            raise _Drop("Finder block: no condition sentence given.")
        got = ctx.finder(text, ctx.today)
        if got["dropped"] or got["not_understood"]:
            for n in got["dropped"] + got["not_understood"]:
                ctx.note(f"Finder: {n}")
        settings = finder_settings(got["spec"], members.get("player"))
        return ({"type": "finder", "w": 12, "h": 12, "title": "Player Finder", "settings": settings},
                {"type": "finder", "spec": got["spec"], "flag": bool(got["dropped"] or got["not_understood"]),
                 "dataset": got["spec"]["dataset"], "columns": None, "chart": None, "x": None, "y": None,
                 "seasonFrom": got["spec"].get("season_from"), "seasonTo": got["spec"].get("season_to"), "tool": None,
                 "member": None})
    ds_key = b.get("dataset")
    if ds_key is not None and ds_key not in BOARD_DATASETS:
        ctx.note(f"Block: {ds_key!r} isn't a board dataset; the default was used.")
        ds_key = None
    if ds_key is None:
        ds_key = "player_season" if "player" in members else "team_season"
    entity = WC.DATASETS[ds_key].entity
    sid = members.get(entity)
    if sid is None:
        raise _Drop(f"A {WC.DATASETS[ds_key].label.lower()} block needs a {entity} set; none was named.")
    lo, hi = _season_pair(b, ctx)
    if kind == "table":
        return _table_block(ds_key, sid, b.get("columns"), lo, hi, ctx)
    chart = b.get("chart") if b.get("chart") in CHARTS else "scatter"
    if b.get("chart") not in CHARTS and b.get("chart") is not None:
        ctx.note(f"Chart block: {b.get('chart')!r} isn't a chart kind; scatter was used.")
    cols = _columns(ds_key, [c for c in (b.get("x"), b.get("y")) if c], ctx, "Chart block")
    x = b.get("x") if b.get("x") in cols else None
    y = b.get("y") if b.get("y") in cols else None
    if chart == "scatter" and (x is None or y is None):
        raise _Drop("Scatter chart: it needs a stat on each axis.")
    if chart != "scatter" and y is None:
        if x is not None:
            y, x = x, None
        else:
            raise _Drop(f"{chart.capitalize()} chart: it needs a stat.")
    settings = {"dataset": ds_key, "setId": sid, "seasonFrom": lo, "seasonTo": hi, "per": "game", "minGames": None,
                "minPoss": None, "playersMatch": "any", "chart": chart, "x": x, "y": y, "size": None, "color": "member",
                "facet": "none", "groupBy": "none", "lineX": "season", "agingEra": "all", "split": None, "bins": 0,
                "style": "box", "context": True, "trend": False, "ci": True}
    title = f"{_stat_label(ds_key, y)}" + (f" vs {_stat_label(ds_key, x)}" if x else "")
    return ({"type": "chart", "w": 8, "h": 12, "title": title, "settings": settings},
            {"type": "chart", "dataset": ds_key, "chart": chart, "x": x, "y": y, "seasonFrom": lo, "seasonTo": hi,
             "columns": None, "tool": None, "member": None, "spec": None})


def _table_block(ds_key, sid, cols, lo, hi, ctx):
    columns = _columns(ds_key, cols, ctx, "Table block") or list(DEFAULT_COLUMNS[ds_key])
    one_season = lo is not None and lo == hi
    settings = {"dataset": ds_key, "setId": sid, "columns": columns, "seasonFrom": lo, "seasonTo": hi,
                "groupBy": "none" if one_season else "entity", "per": "game", "sort": [], "limit": 50,
                "minGames": None, "minPoss": None, "playersMatch": "any", "showN": False, "showCi": True}
    title = WC.DATASETS[ds_key].label + (f" · {_season_label(lo)}" if one_season else
                                         f" · {_season_label(lo)} to {_season_label(hi)}" if lo and hi else "")
    return ({"type": "table", "w": 8, "h": 8, "title": title, "settings": settings},
            {"type": "table", "dataset": ds_key, "columns": columns, "seasonFrom": lo, "seasonTo": hi, "chart": None,
             "x": None, "y": None, "tool": None, "member": None, "spec": None})


def _stat_label(ds_key, key):
    col = WC.DATASETS[ds_key].columns.get(key) if key else None
    return col.label if col else (key or "")


def _block_words(s):
    rng = ""
    if s.get("seasonFrom") and s.get("seasonTo") and s["seasonFrom"] == s["seasonTo"]:
        rng = f" {_season_label(s['seasonFrom'])}"
    elif s.get("seasonFrom") or s.get("seasonTo"):
        rng = f" {_season_label(s['seasonFrom']) if s.get('seasonFrom') else '…'} to {_season_label(s['seasonTo']) if s.get('seasonTo') else 'now'}"
    if s["type"] == "table":
        return f"table ({', '.join(_stat_label(s['dataset'], c) for c in s['columns'][:6])}{', …' if len(s['columns']) > 6 else ''}){rng}"
    if s["type"] == "chart":
        axes = _stat_label(s["dataset"], s["y"]) + (f" vs {_stat_label(s['dataset'], s['x'])}" if s.get("x") else "")
        return f"{s['chart']} chart of {axes}{rng}"
    if s["type"] == "tool":
        return TOOL_LABELS[s["tool"]].lower() + rng
    return "finder" + rng


def _board_name(sets, scored):
    who = " vs ".join(m["name"] for s in sets for m in s["members"][:3])
    if sum(len(s["members"]) for s in sets) > 3:
        who += " and more"
    rng = next(((s.get("seasonFrom"), s.get("seasonTo")) for s in scored if s.get("seasonFrom") or s.get("seasonTo")), None)
    if rng and rng[0] and rng[0] == rng[1]:
        who += f" · {_season_label(rng[0])}"
    return who[:120]


def finder_settings(spec, set_id=None):
    """A Finder spec → the finder block's settings (components/workbench/finderSpec.js specToSettings)."""
    def ends(x):
        return ({"value": x["value"][0], "value2": x["value"][1]} if isinstance(x.get("value"), list)
                else {"value": x.get("value"), "value2": None})
    conditions = []
    for c in spec["conditions"]:
        if c["type"] == "value":
            conditions.append({"type": "value", "stat": c["stat"], "op": c["op"], **ends(c), "per": c.get("per") or "game",
                               "minN": c.get("min_n")})
        else:
            conditions.append({"type": c["type"], "tests": [{"stat": t["stat"], "op": t["op"], **ends(t)} for t in c["tests"]],
                               "countOp": c.get("count_op") or "gte", "count": c["count"]})
    filters = {f["key"]: f["value"] for f in spec.get("filters") or []}
    return {"dataset": spec["dataset"], "scope": spec["scope"], "seasonFrom": spec.get("season_from"),
            "seasonTo": spec.get("season_to"), "minGames": spec.get("min_games"), "setId": set_id,
            "where": ("home" if filters["home"] else "away") if "home" in filters else "all",
            "result": ("W" if filters["result"] else "L") if "result" in filters else "all",
            "opponent": filters.get("opponent"), "minMinutes": filters.get("min"), "conditions": conditions,
            "sort": None, "limit": 100}


def _finder_action(text, ctx):
    got = ctx.finder(text, ctx.today)
    flag = bool(got["dropped"] or got["not_understood"])
    board = {"name": "Player Finder", "sets": [],
             "blocks": [{"type": "finder", "w": 12, "h": 12, "title": "Player Finder", "settings": finder_settings(got["spec"])}]}
    return {"action": "run_finder", "spec": got["spec"], "sentence": got["sentence"], "flag": flag,
            "finder_dropped": got["dropped"], "finder_not_understood": got["not_understood"], "board": board,
            "preview": "Find players: " + got["sentence"]}


def _live_action(intent, ctx):
    team = intent.get("live_team")
    if team is not None:
        try:
            team = _team_value(team, "Live game")
        except _Drop as e:
            ctx.note(str(e) + " Today's games are shown instead.")
            team = None
    return {"action": "open_live_game", "team": team,
            "preview": f"Live: {_teams()[team]} today" if team else "Today's live games"}


def _refuse_action(reason, notes):
    if reason not in REASONS:
        reason = "off_topic"
    return {"action": "refuse", "reason": reason, "message": REASONS[reason], "preview": REASONS[reason]}


_NOT_A_GUESS = re.compile(r"end[- ]year|\bmeans\b|\bis\b [A-Z]|= [A-Z]|this season|last season|current season|today", re.I)


def _real_guesses(items, ctx):
    """The model lists its readings; keep only what is a guess (not 'this season = 2025-26', not 'Dame means
    Damian Lillard'), so the box shows a guess only when one was made."""
    labels = {_season_label(ctx.current), _season_label(ctx.last), str(ctx.current), str(ctx.last)}
    out = []
    for g in items:
        if _NOT_A_GUESS.search(g) and not re.search(r"\b(MVP|rookie|debut|championship|title|first|record)\b", g, re.I):
            continue
        if any(lab in g for lab in labels) and not re.search(r"[A-Za-z]{4,} (year|season) =", g):
            continue
        out.append(g)
    return out


def _default_finder(text, today):
    return P.parse(text, today=today)


def to_action(intent, text, today=None, seasons=None, choices=None, finder=None):
    """The model's answer (intent_schema form) + the typed text → (action, notes, guessed, not_understood).
    Every piece is checked against ask_pages / the catalogue; what can't be used is left out and said in notes.
    Raises ParseError when no action can be made."""
    if not isinstance(intent, dict):
        raise ParseError("The model's answer wasn't an object.", status=502)
    ctx = _Ctx(today or today_nba(), seasons or seasons_today(), choices, finder or _default_finder)
    guessed = _real_guesses(P._notes(intent.get("guessed")), ctx)
    not_understood = P._notes(intent.get("not_understood"))
    kind = intent.get("action")
    if kind not in ACTIONS:
        raise ParseError("The model's answer named no action the app has.", status=502)
    try:
        if kind == "refuse":
            action = _refuse_action(intent.get("refuse_reason"), ctx.notes)
        elif kind == "open_live_game":
            action = _live_action(intent, ctx)
        elif kind == "run_finder":
            action = _finder_action(text, ctx)
        elif kind == "build_board":
            action = _board_action(intent, ctx)
        else:
            action = _open_page(intent, ctx)
    except _NoData as e:
        ctx.note(str(e))
        action = _refuse_action("no_data", ctx.notes)
    except _Unknown as e:
        ctx.note(f"Nobody named {e} is on file.")
        action = _refuse_action("unknown_player", ctx.notes)
    if ctx.pending is not None and action["action"] in ("open_page", "build_board"):
        name, rows = ctx.pending
        action = {"action": "ask", "about": "player", "name": name,
                  "options": [{"id": r["id"], "name": r["name"], "from": r["from"], "to": r["to"], "team": r.get("team")}
                              for r in rows],
                  "then": action,
                  "preview": f"Which {name}? {len(rows)} players on file have that name."}
    return action, ctx.notes, guessed, not_understood


# ─── the per-minute budget (the free tier's limit is per Google project) ────

class CallBudget:
    """At most `per_minute` calls to Google a minute from this process; a Finder sentence costs two."""

    def __init__(self, per_minute=10):
        self.per_minute = per_minute
        self._calls = deque()
        self._lock = threading.Lock()

    def take(self):
        with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] > 60:
                self._calls.popleft()
            if len(self._calls) >= self.per_minute:
                raise ParseError(f"This server sends at most {self.per_minute} sentences a minute to the free Gemini "
                                 "tier; try again in a minute, or use the search.", status=429)
            self._calls.append(now)

    def reset(self):
        with self._lock:
            self._calls.clear()


# ─── one sentence, end to end ───────────────────────────────────────────────

def ask(text, model=MODEL, key=None, today=None, seasons=None, choices=None, timeout=P.TIMEOUT_S, budget=None):
    """Typed text → {text, action, preview, notes, guessed, not_understood, model, intent, usage, seconds}."""
    text = P.clean_text(text)
    today = today or today_nba()
    seasons = seasons or seasons_today()
    if budget:
        budget.take()
    t0 = time.time()
    intent, info = P.ask_gemini(text, system_prompt(today, seasons), intent_schema(), model=model, key=key, timeout=timeout)

    def finder(sentence, day):
        if budget:
            budget.take()
        return P.parse(sentence, model=model, key=key, today=day, timeout=timeout)

    try:
        action, notes, guessed, not_understood = to_action(intent, text, today, seasons, choices, finder)
    except ParseError as e:
        e.not_understood = P._notes(intent.get("not_understood") if isinstance(intent, dict) else None)
        raise
    return {"text": text, "action": action, "preview": action.get("preview"), "notes": notes, "guessed": guessed,
            "not_understood": not_understood, "model": info["model_version"] or model, "intent": intent,
            "usage": info["usage"], "seconds": round(time.time() - t0, 2)}
