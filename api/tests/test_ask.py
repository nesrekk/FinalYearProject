"""
test_ask.py
===========
Guards round 10 step 7, "ask in English everywhere" (POST /ask, api/routers/ask.py;
the work in api/ask_lib.py; what may be opened in api/ask_pages.py). No test
calls Google: the model's answer is faked, so these check what the server does
with whatever comes back.

  * the registry matches the app: every page id is in App.jsx's PAGES and every
    PAGES id is in the registry; every tab is in analyticsTabs.js; every link key
    the registry names is read by that page's component (parseParam / useUrlSync);
    every literal list of allowed values equals the component's; the tools are
    workbenchTools.js's; the refusal reasons are the sentences file's;
  * the schema's enums are the registry's and the catalogue's, with no maxItems
    beside an enum;
  * the model's answer is untrusted input: a key the page doesn't read, a value
    outside its list, a season the page's data doesn't have (a refusal, not the
    nearest season), a nobody (a refusal), a shared name (an ask, never a guess),
    a stat the catalogue lacks, are each left out or refused and said;
  * names become ids through the app's own search; a board is a shape
    cleanBoard() accepts; a Finder sentence goes through the Finder box's parse;
  * the endpoint: an answer → action + preview; the same sentence today from
    memory; the per-minute guard; no key → 503; a choice answers an ask;
  * the test sentences (150, 60 dev / 90 test) and the stored evaluation: every
    failed sentence-run is one with no answer, the status endpoint says how many,
    and a --resume can only complete the same evaluation.

Skips when the database is unreachable.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_ask.py
"""

import datetime
import glob
import json
import os
import re
import sys

import psycopg2
import pytest

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API)
_SRC = os.path.join(_ROOT, "frontend", "src")
if _API not in sys.path:
    sys.path.insert(0, _API)

from db_config import DB_CONFIG  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")

TODAY = datetime.date(2026, 10, 9)
SEASONS = (2026, 2026)


@pytest.fixture(scope="module")
def A():
    import ask_lib
    return ask_lib


@pytest.fixture(scope="module")
def AP():
    import ask_pages
    return ask_pages


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from impact_api import app
    return TestClient(app)


@pytest.fixture()
def router(monkeypatch):
    from routers import ask as R
    R._budget.reset()
    monkeypatch.setattr(R, "_answers", type(R._answers)())
    return R


def intent(**over):
    base = {"action": "open_page", "page": None, "tab": None, "params": [],
            "board": {"player_names": [], "teams": [], "blocks": []},
            "live_team": None, "refuse_reason": None, "guessed": [], "not_understood": []}
    base.update(over)
    return base


def kv(**params):
    return [{"key": k, "value": str(v)} for k, v in params.items()]


def block(**over):
    b = {"type": "table", "dataset": None, "columns": [], "chart": None, "x": None, "y": None, "season_from": None,
         "season_to": None, "tool": None, "member": None, "text": None}
    b.update(over)
    return b


FINDER_SPEC = {"dataset": "player_season", "scope": "season", "season_from": 2010, "season_to": None, "min_games": 40,
               "filters": [], "conditions": [{"type": "value", "stat": "pts", "op": "gte", "value": 25, "per": "game"}]}


def fake_finder(text, day):
    return {"spec": json.loads(json.dumps(FINDER_SPEC)), "sentence": f"Players who ({text})", "dropped": [],
            "not_understood": ["rookies"] if "rookie" in text else []}


def act(A, i, text="x", choices=None):
    return A.to_action(i, text, TODAY, SEASONS, choices=choices, finder=fake_finder)


def _src(*parts):
    return open(os.path.join(_SRC, *parts)).read()


# ─── the registry matches the app ───────────────────────────────────────────

def test_pages_and_tabs_are_the_apps(AP):
    pages = re.findall(r"^\s+([a-z]+): [A-Z]\w+,$", _src("App.jsx").split("const PAGES = {")[1].split("};")[0], re.M)
    assert set(pages) == set(AP.PAGES), set(pages) ^ set(AP.PAGES)
    tabs = re.findall(r"id: '([a-z]+)'", _src("components", "analytics", "analyticsTabs.js"))
    assert set(tabs) == set(AP.TABS), set(tabs) ^ set(AP.TABS)
    labels = dict(re.findall(r"\{ id: '([a-z]+)', label: '([^']+)'", _src("components", "layout", "navConfig.js")))
    for pid, p in AP.PAGES.items():
        if pid in labels and pid != "analytics":
            assert p.label == labels[pid], (pid, p.label, labels[pid])
    tab_labels = dict(re.findall(r"id: '([a-z]+)', label: '([^']+)'", _src("components", "analytics", "analyticsTabs.js")))
    for tid, t in AP.TABS.items():
        assert t.label == tab_labels[tid], (tid, t.label)


def _component_keys():
    """{component file: {key: (type, literal list or None)}} for every parseParam / useUrlSync key the pages read."""
    pat = re.compile(r"parseParam\.(int|num|oneOf|str|list)\(\s*\w+\s*,\s*'([a-zA-Z_]+)'(?:\s*,\s*(\[[^\]]*\]))?")
    sync = re.compile(r"useUrlSync\(\s*\{([^}]*)\}", re.S)
    out = {}
    for f in glob.glob(os.path.join(_SRC, "components", "**", "*.jsx"), recursive=True):
        s = open(f).read()
        keys = {}
        for m in pat.finditer(s):
            lit = m.group(3)
            values = tuple(re.findall(r"'([^']*)'", lit)) if lit and not lit.startswith("[...") else None
            keys.setdefault(m.group(2), (m.group(1), values))
        for m in sync.finditer(s):
            for k in re.findall(r"\b([a-zA-Z_]+)\s*[:,}]", m.group(1)):
                keys.setdefault(k, ("sync", None))
        # Inputs read with a plain get() or a local helper (Shot Charts' season label, the Builder's stat and seasons).
        for k in re.findall(r"\.get\('([a-zA-Z_]+)'\)", s) + re.findall(r"\bseason\('([a-zA-Z_]+)'", s):
            keys.setdefault(k, ("get", None))
        if keys:
            out[os.path.relpath(f, os.path.join(_SRC, "components"))] = keys
    return out


COMPONENT_OF = {  # registry id → the component that reads its link (App.jsx PAGES / the Analytics sections)
    "scores": "pages/LiveScores.jsx", "compare": "pages/PlayerComparison.jsx", "shotcharts": "pages/ShotCharts.jsx",
    "trade": "pages/TradeAnalyzer.jsx", "tradeimpact": "pages/TradeImpact.jsx", "games": "pages/GamesHub.jsx",
    "methodology": "pages/Methodology.jsx", "builder": "pages/LeaderboardBuilder.jsx",
    "regression": "pages/RegressionExplorer.jsx", "breakouts": "pages/BreakoutDetector.jsx",
    "stability": "pages/StatStability.jsx", "player": "pages/PlayerProfile.jsx", "team": "pages/TeamProfile.jsx",
    "rolefinder": "pages/RoleFinder.jsx", "era": "pages/EraTranslator.jsx", "aging": "pages/AgingCurves.jsx",
    "projections": "pages/Projections.jsx", "statline": "pages/StatLineFinder.jsx", "gamefinder": "pages/GameFinder.jsx",
    "plays": "pages/PlayFinder.jsx", "hotstreaks": "pages/HotStreaks.jsx", "rapm": "pages/Rapm.jsx",
    "rotations": "pages/Rotations.jsx", "assists": "pages/AssistNetwork.jsx", "simulator": "pages/SeasonSimulator.jsx",
    "ledger": "pages/ForecastLedger.jsx", "bestgames": "pages/BestGames.jsx", "possessions": "pages/PossessionExplorer.jsx",
    "coaching": "pages/CoachingDecisions.jsx", "splits": "pages/SituationalSplits.jsx",
    "tab:rim": "RimDeterrenceSection.jsx", "tab:lineups": "LineupChemistrySection.jsx",
    "tab:pairs": "PairChemistrySection.jsx", "tab:onoff": "OnOffSection.jsx", "tab:luck": "LuckScheduleSection.jsx",
}


def test_every_registry_key_is_read_by_its_page(AP):
    comp = _component_keys()
    entries = [(pid, p) for pid, p in AP.PAGES.items() if p.keys] + [(f"tab:{t}", p) for t, p in AP.TABS.items() if p.keys]
    for pid, p in entries:
        file = COMPONENT_OF[pid]
        read = comp[file]
        for key, k in p.keys.items():
            assert key in read, (pid, key, sorted(read))
            kind, literal = read[key]
            if literal is not None and k.type == "enum":
                assert set(k.values) == set(literal), (pid, key, k.values, literal)
            if kind == "int":
                assert k.type in ("int", "season", "player") or (k.type == "enum" and all(v.isdigit() for v in k.values)), (pid, key, kind, k.type)
            if kind == "list":
                assert k.type == "list", (pid, key)
        for key in p.free:
            assert key in read, (pid, "free", key)
    # Pages the registry says read nothing really read nothing (else an ask could land on them with details).
    bare = {pid for pid, p in AP.PAGES.items() if not p.keys and pid not in ("analytics", "workbench", "report", "reportcard", "quality")}
    for pid in bare:
        assert pid not in COMPONENT_OF or not comp.get(COMPONENT_OF[pid]), pid


def test_literal_lists_match_the_code(AP):
    import workbench_catalogue as WC
    from routers import hot_streaks, role_finder
    import hot_streaks as HS
    import situational_splits as SS
    assert set(AP.LEADERBOARD_STATS) == set(WC.leaderboard_stats())
    assert set(AP.GAME_FINDER_STATS) == set(WC.game_finder_stats())
    assert tuple(AP.PROJ_STATS) == tuple(WC.PROJ_STATS)
    assert set(AP.HOT_STREAK_STATS) == set(HS.STATS) and tuple(int(w) for w in AP.HOT_STREAK_WINDOWS) == tuple(HS.WINDOWS)
    assert set(AP.SPLITS) == set(SS.SPLITS) and set(AP.SPLIT_STATS) == set(SS.STATS)
    assert set(AP.ROLE_PRESETS) == set(role_finder.PRESETS)
    assert hot_streaks is not None
    cards = re.findall(r"^\s+id: '([a-z_0-9]+)',\n\s+name:", _src("components", "pages", "methodologyContent.js"), re.M)
    assert set(AP.METHODOLOGY_CARDS) == set(cards), set(AP.METHODOLOGY_CARDS) ^ set(cards)
    tools = re.findall(r"^\s+([a-z]+): \{ label: '([^']+)', icon: '\w+', entity: '(\w+)'", _src("utils", "workbenchTools.js"), re.M)
    import ask_lib
    assert {t: e for t, _l, e in tools} == ask_lib.TOOLS
    assert {t: lab for t, lab, _e in tools} == ask_lib.TOOL_LABELS
    games = re.findall(r"\{ id: '([a-z]+)', label:", _src("components", "pages", "GamesHub.jsx"))
    assert set(games) == set(AP.GAMES)
    from similarity_api import LINE_STATS
    assert set(AP.LINE_STATS) == set(LINE_STATS)
    with psycopg2.connect(**DB_CONFIG) as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT stat FROM aging_curve_summary")
        assert set(AP.AGING_STATS) == {r[0] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT stat FROM stat_stability")
        assert set(AP.STABILITY_STATS) == {r[0] for r in cur.fetchall()}
        cur.execute("SELECT MIN(season) FROM player_season_stats")
        assert cur.fetchone()[0] == AP.FIRST_PLAYER_SEASON
        cur.execute("SELECT MIN(season) FROM team_seasons")
        assert cur.fetchone()[0] == AP.FIRST_TEAM_SEASON
        cur.execute("SELECT MIN(season) FROM player_game_lines")
        assert cur.fetchone()[0] == AP.FIRST_PBP_SEASON
        cur.execute("SELECT MIN(season) FROM player_shots")
        assert cur.fetchone()[0] == f"{AP.FIRST_SHOT_SEASON - 1}-{str(AP.FIRST_SHOT_SEASON)[-2:]}"
        cur.execute("SELECT MAX(season) FROM player_salaries")
        assert cur.fetchone()[0] == AP.LAST_SALARY_SEASON


def test_schema_enums_are_the_registrys(A, AP):
    s = A.intent_schema()
    props = s["properties"]
    assert set(props["page"]["enum"]) == set(AP.PAGES) | {None}
    assert set(props["tab"]["enum"]) == set(AP.TABS) | {None}
    assert set(props["params"]["items"]["properties"]["key"]["enum"]) == set(AP.all_keys())
    assert set(props["live_team"]["enum"]) == set(AP.CODES) | {None} and len(AP.CODES) == 30
    assert set(props["refuse_reason"]["enum"]) == set(A.REASONS) | {None}
    assert set(props["board"]["properties"]["blocks"]["items"]["properties"]["dataset"]["enum"]) == set(A.BOARD_DATASETS) | {None}
    assert "maxItems" not in json.dumps(s)
    sentences = json.load(open(os.path.join(_API, "ask_sentences.json")))
    assert set(sentences["refusal_reasons"]) == set(A.REASONS)
    prompt = A.system_prompt(TODAY, SEASONS)
    for pid in AP.PAGES:
        assert f"- {pid}: " in prompt, pid
    assert "2025-26 = 2026" in prompt and "Today is 2026-10-09" in prompt


# ─── the model's answer is untrusted input ──────────────────────────────────

def test_a_good_answer_opens_the_page_with_ids(A):
    a, notes, _g, _n = act(A, intent(page="compare", params=kv(aid="Nikola Jokić", bid="Joel Embiid", season="this season")))
    assert a["action"] == "open_page" and a["page"] == "compare" and a["hash"] is None
    assert a["params"] == {"aid": 203999, "bid": 203954, "season": 2026} and notes == []
    assert a["href"] == "?page=compare&aid=203999&bid=203954&season=2026"
    assert a["preview"] == "Open Player Comparison · Nikola Jokić · Joel Embiid · 2025-26"
    a, _n, _g, _u = act(A, intent(page="shotcharts", params=kv(pid="Stephen Curry", season="2016")))
    assert a["params"] == {"pid": 201939, "season": "2015-16"} and a["preview"].endswith("2015-16 · regular season")
    a, _n, _g, _u = act(A, intent(page="analytics", tab="onoff", params=kv(team="Nuggets", season="2026")))
    assert a["hash"] == "onoff" and a["params"] == {"team": "DEN", "season": 2026} and a["href"].endswith("#onoff")


def test_unknown_keys_and_values_are_left_out_and_said(A):
    a, notes, _g, _u = act(A, intent(page="player", params=kv(id="Jimmy Butler", season="2026", sql="DROP TABLE x")))
    assert a["params"] == {"id": 202710}                       # Jimmy Butler III, by the app's own search
    assert any("no 'season' input" in n for n in notes) and any("'sql'" in n for n in notes)
    a, notes, _g, _u = act(A, intent(page="builder", params=kv(stat="ppg", order="lowest", n="7", **{"from": "2010", "to": "2010"})))
    assert a["params"] == {"from": 2010, "to": 2010} and len(notes) == 3
    a, notes, _g, _u = act(A, intent(page="leaders", params=kv(stat="pts")))
    assert a["params"] == {} and "reads nothing from a link" in notes[0]
    a, notes, _g, _u = act(A, intent(page="player", tab="onoff", params=kv(id="LeBron James")))
    assert a["params"] == {"id": 2544} and a["hash"] is None and "tab" in notes[0].lower()
    with pytest.raises(A.ParseError) as e:
        act(A, intent(page="analytics"))
    assert e.value.status == 422
    with pytest.raises(A.ParseError) as e:
        act(A, intent(page="nowhere"))
    assert e.value.status == 502


def test_seasons_outside_the_data_are_refused_not_moved(A):
    a, notes, _g, _u = act(A, intent(page="shotcharts", params=kv(pid="Michael Jordan", season="1990-91")))
    assert a["action"] == "refuse" and a["reason"] == "no_data" and "1996-97" in notes[0]
    a, notes, _g, _u = act(A, intent(page="gamefinder", params=kv(f="pts:gte:50", **{"from": "2015"})))
    assert a["action"] == "refuse" and a["reason"] == "no_data" and "2020-21" in notes[0]
    a, notes, _g, _u = act(A, intent(page="team", params=kv(abbr="BOS", season="2030")))
    assert a["action"] == "refuse" and a["reason"] == "no_data"
    a, _n, _g, _u = act(A, intent(page="team", params=kv(abbr="BOS", season="1960")))
    assert a["action"] == "open_page" and a["params"] == {"abbr": "BOS", "season": 1960}


def test_names_are_resolved_asked_or_refused(A):
    a, notes, _g, _u = act(A, intent(page="player", params=kv(id="Troy Bolton")))
    assert a["action"] == "refuse" and a["reason"] == "unknown_player" and "Troy Bolton" in notes[0]
    a, _n, _g, _u = act(A, intent(page="player", params=kv(id="Mike James")))
    assert a["action"] == "ask" and a["about"] == "player" and a["name"] == "Mike James"
    assert sorted(o["id"] for o in a["options"]) == [2229, 1628455]
    assert a["then"]["page"] == "player" and a["then"]["params"] == {"id": "<chosen>"}
    a, _n, _g, _u = act(A, intent(page="player", params=kv(id="Mike James")), choices={"mike james": 2229})
    assert a["action"] == "open_page" and a["params"] == {"id": 2229}
    a, _n, _g, _u = act(A, intent(page="compare", params=kv(aid="Reggie Williams", bid="Reggie Miller")))
    assert a["action"] == "ask" and sorted(o["id"] for o in a["options"]) == [199, 202130] and a["then"]["params"]["bid"] == 397
    assert A.resolve_player("Shai Gilgeous Alexander")[1]["id"] == 1628983
    assert A.resolve_player("Luka Doncic")[1]["id"] == 1629029
    assert A.resolve_player("Curry")[0] == "none"           # a surname alone is nobody's full name


def test_values_are_checked_by_type(A):
    a, notes, _g, _u = act(A, intent(page="gamefinder", params=kv(player="Luka Dončić", f="pts:gte:10,reb:gte:10,ast:gte:10,bogus:gte:1")))
    assert a["params"] == {"player": 1629029, "f": "pts:gte:10,reb:gte:10,ast:gte:10"} and "'bogus:gte:1'" in notes[0]
    a, notes, _g, _u = act(A, intent(page="gamefinder", params=kv(f="pts:gte:10,fg3_pct:gte:40")))
    assert a["params"] == {"f": "pts:gte:10,fg3_pct:gte:0.4"}   # a percentage travels as a share
    assert "points ≥ 10" in a["preview"]
    a, _n, _g, _u = act(A, intent(page="statline", params=kv(line="pts:30,ts_pct:65")))
    assert a["params"] == {"line": "pts:30,ts_pct:0.65"}
    a, notes, _g, _u = act(A, intent(page="scores", params=kv(date="yesterday")))
    assert a["params"] == {"date": "2026-10-08"}
    a, notes, _g, _u = act(A, intent(page="scores", params=kv(date="2026-10-09")))
    assert a["params"] == {} and "default" in notes[0]
    a, notes, _g, _u = act(A, intent(page="scores", params=kv(date="2027-01-01")))
    assert a["action"] == "refuse" and a["reason"] == "no_data"
    a, _n, _g, _u = act(A, intent(page="plays", params=kv(player="Victor Wembanyama", cat="blk", per="4", clutch="1")))
    assert a["params"] == {"player": 1641705, "cat": "blk", "per": "4", "clutch": "1"}
    a, _n, _g, _u = act(A, intent(page="shotcharts", params=kv(pid="Nikola Jokić", view="heat map")))
    assert a["params"] == {"pid": 203999, "view": "heatmap"}


def test_page_defaults_are_never_written_and_guesses_are_real(A):
    # The page shows its default anyway; writing it would make an unasked key (the exam counts that wrong).
    a, notes, _g, _u = act(A, intent(page="aging", params=kv(stat="fg3_pct", era="all")))
    assert a["params"] == {"stat": "fg3_pct"} and notes == []
    a, _n, _g, _u = act(A, intent(page="aging", params=kv(stat="ast", era="modern")))
    assert a["params"] == {"stat": "ast", "era": "modern"}
    a, _n, _g, _u = act(A, intent(page="rapm", params=kv(season="2025", version="single")))
    assert a["params"] == {"season": 2025}
    a, _n, _g, _u = act(A, intent(page="statline", params=kv(line="pts:27,reb:7,ast:7", n="25")))
    assert a["params"] == {"line": "pts:27,reb:7,ast:7", "n": "25"}
    # Only a real assumption reaches the box's "guessed" line.
    _a, _n, guessed, _u = act(A, intent(page="player", params=kv(id="LeBron James"),
                                        guessed=["this season = 2025-26", "Bron means LeBron James", "2020-21 end year is 2021",
                                                 "unanimous MVP year = 2015-16", "rookie season = 2003-04"]))
    assert guessed == ["unanimous MVP year = 2015-16", "rookie season = 2003-04"]


def test_boards_are_built_from_ids_and_the_catalogue(A):
    i = intent(action="build_board", board={"player_names": ["LeBron James", "Kevin Durant", "Giannis Antetokounmpo"], "teams": [],
                                             "blocks": [block(dataset="player_season", columns=["pts", "reb", "ast", "ppg"],
                                                              season_from=2026, season_to=2026)]})
    a, notes, _g, _u = act(A, i)
    assert a["action"] == "build_board" and a["sets"] == [{"kind": "player", "ids": [2544, 201142, 203507]}]
    assert a["blocks"][0]["type"] == "table" and a["blocks"][0]["columns"] == ["pts", "reb", "ast"]
    assert a["blocks"][0]["seasonFrom"] == 2026 and any("'ppg'" in n for n in notes)
    board = a["board"]
    assert board["sets"][0]["kind"] == "player" and [m["id"] for m in board["sets"][0]["members"]] == [2544, 201142, 203507]
    assert all(m["name"] and isinstance(m["color"], int) for m in board["sets"][0]["members"])
    assert [b["type"] for b in board["blocks"]] == ["set", "table"]
    assert board["blocks"][1]["settings"]["setId"] == "s0" and board["blocks"][1]["settings"]["groupBy"] == "none"
    assert a["preview"].startswith("New board: LeBron James vs Kevin Durant vs Giannis Antetokounmpo")
    # Two teams and no blocks: a team-season table with the default columns; a team written as a player joins the
    # team set after the teams the model understood (the evaluation compares codes as a set).
    a, _n, _g, _u = act(A, intent(action="build_board", board={"player_names": ["Celtics"], "teams": ["LAL"], "blocks": []}))
    assert a["sets"] == [{"kind": "team", "codes": ["LAL", "BOS"]}] and a["blocks"][0]["dataset"] == "team_season"
    assert a["board"]["sets"][0]["members"][1] == {"id": "BOS", "name": "Boston Celtics", "color": 1}
    # A chart needs its axes from the dataset; a tool block binds to a member; a finder block uses the Finder's engine.
    a, _n, _g, _u = act(A, intent(action="build_board", board={
        "player_names": ["Stephen Curry", "James Harden"], "teams": [],
        "blocks": [block(type="chart", chart="scatter", x="usg_pct", y="ts_pct", season_from=2016),
                   block(type="tool", tool="shots", member="James Harden", season_from=2019),
                   block(type="finder", text="players who averaged 25 points since 2010"),
                   block(type="tool", tool="rotation")]}))
    kinds = [b["type"] for b in a["blocks"]]
    assert kinds == ["chart", "tool", "finder"]
    assert a["blocks"][0]["x"] == "usg_pct" and (a["blocks"][0]["seasonFrom"], a["blocks"][0]["seasonTo"]) == (2016, 2026)   # "since": up to today
    assert a["blocks"][1]["member"] == 201935 and a["board"]["blocks"][2]["settings"]["season"] == 2019
    assert a["blocks"][2]["spec"] == FINDER_SPEC and a["board"]["blocks"][3]["settings"]["conditions"][0]["minN"] is None
    a, notes, _g, _u = act(A, intent(action="build_board", board={"player_names": ["Troy Bolton", "LeBron James"], "teams": [], "blocks": []}))
    assert a["action"] == "refuse" and a["reason"] == "unknown_player" and "Troy Bolton" in notes[0]
    with pytest.raises(A.ParseError):
        act(A, intent(action="build_board", board={"player_names": [], "teams": [], "blocks": []}))


def test_finder_live_and_refuse(A):
    a, _n, _g, _u = act(A, intent(action="run_finder"), text="rookies who averaged 15 points")
    assert a["action"] == "run_finder" and a["spec"] == FINDER_SPEC and a["flag"] is True
    assert a["board"]["blocks"][0]["type"] == "finder" and a["preview"].startswith("Find players: ")
    a, _n, _g, _u = act(A, intent(action="open_live_game", live_team="bos"))
    assert a == {"action": "open_live_game", "team": "BOS", "preview": "Live: Boston Celtics today"}
    a, _n, _g, _u = act(A, intent(action="open_live_game"))
    assert a["team"] is None
    a, _n, _g, _u = act(A, intent(action="refuse", refuse_reason="betting_advice"))
    assert a["action"] == "refuse" and a["reason"] == "betting_advice" and "never" in a["preview"]
    a, _n, _g, _u = act(A, intent(action="refuse", refuse_reason="made_up"))
    assert a["reason"] == "off_topic"
    with pytest.raises(A.ParseError):
        act(A, intent(action="dance"))


# ─── the endpoint ───────────────────────────────────────────────────────────

def test_endpoint_returns_the_action_and_remembers_it(A, client, router, monkeypatch):
    calls = []

    def fake(text, system, schema, model=A.MODEL, key=None, timeout=25):
        calls.append(text)
        return intent(page="shotcharts", params=kv(pid="Stephen Curry", season="2015-16"), guessed=["unanimous MVP year = 2015-16"]), \
            {"usage": {}, "model_version": "fake"}

    monkeypatch.setattr(A.P, "ask_gemini", fake)
    monkeypatch.setattr(A, "seasons_today", lambda: SEASONS)
    r = client.post("/ask", json={"text": "  Steph's shot chart from his unanimous MVP year "})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["action"]["page"] == "shotcharts" and d["action"]["params"] == {"pid": 201939, "season": "2015-16"}
    assert d["preview"] == "Open Shot Charts · Stephen Curry · 2015-16 · regular season"
    assert d["guessed"] == ["unanimous MVP year = 2015-16"] and d["notes"] == [] and "intent" not in d
    r = client.post("/ask", json={"text": "steph's shot chart from his unanimous mvp year"})
    assert r.status_code == 200 and len(calls) == 1
    # A choice answers an ask.
    monkeypatch.setattr(A.P, "ask_gemini", lambda *a, **k: (intent(page="player", params=kv(id="Mike James")), {"usage": {}, "model_version": None}))
    d = client.post("/ask", json={"text": "Mike James profile"}).json()
    assert d["action"]["action"] == "ask" and sorted(o["id"] for o in d["action"]["options"]) == [2229, 1628455]
    d = client.post("/ask", json={"text": "Mike James profile", "choices": {"Mike James": 1628455}}).json()
    assert d["action"]["action"] == "open_page" and d["action"]["params"] == {"id": 1628455}


def test_endpoint_refusals_and_guards(A, client, router, monkeypatch):
    monkeypatch.setattr(A.P, "api_key", lambda: None)
    r = client.post("/ask", json={"text": "who led the league in assists in 2010"})
    assert r.status_code == 503 and "GEMINI_API_KEY" in r.json()["detail"]["message"]
    s = client.get("/ask/status").json()
    assert s["available"] is False and "Google" in s["sends"] and "refuse" in s["actions"] and "no_data" in s["reasons"]
    assert client.post("/ask", json={"text": "   "}).status_code == 400
    assert client.post("/ask", json={"text": "x" * 301}).status_code == 400
    assert client.post("/ask", json={"text": "x", "sql": "SELECT 1"}).status_code == 422
    monkeypatch.setattr(A.P, "api_key", lambda: "test-key")
    monkeypatch.setattr(A.P, "ask_gemini", lambda *a, **k: (intent(page="analytics"), {"usage": {}, "model_version": None}))
    r = client.post("/ask", json={"text": "analytics please"})
    assert r.status_code == 422 and "Which Analytics tool" in r.json()["detail"]["message"]
    router._budget.reset()
    router._budget.per_minute = 2
    try:
        monkeypatch.setattr(A.P, "ask_gemini", lambda *a, **k: (intent(action="open_live_game"), {"usage": {}, "model_version": None}))
        codes = [client.post("/ask", json={"text": f"live games {n}"}).status_code for n in (1, 2, 3)]
        assert codes == [200, 200, 429]
    finally:
        router._budget.per_minute = router.PER_MINUTE
        router._budget.reset()


def test_stored_evaluation_stays_current_after_its_day(A, router, monkeypatch):
    day = datetime.date(2026, 10, 9)
    ev = {"evaluated": "2026-10-09", "model": A.MODEL, "prompt_version": A.prompt_version(day, SEASONS), "seasons": [2026, 2026]}
    assert A.prompt_version(day, SEASONS) != A.prompt_version(day + datetime.timedelta(days=1), SEASONS)
    monkeypatch.setattr(A, "seasons_today", lambda: SEASONS)
    assert router._eval_current(ev)
    assert not router._eval_current({**ev, "prompt_version": "000000000000"})
    assert not router._eval_current({**ev, "model": "another-model"})
    monkeypatch.setattr(A, "seasons_today", lambda: (2027, 2026))        # opening night moved "this season"
    assert not router._eval_current(ev)


# ─── the test sentences and the stored evaluation ───────────────────────────

def test_sentences_cover_only_what_the_registry_has(A, AP):
    d = json.load(open(os.path.join(_API, "ask_sentences.json")))
    s = d["sentences"]
    assert len(s) == 150 and len({x["id"] for x in s}) == 150
    assert sum(x["split"] == "dev" for x in s) == 60 and sum(x["split"] == "test" for x in s) == 90
    for x in s:
        e = x["expect"]
        if e["action"] == "open_page":
            page = AP.page_for(e["page"], e["hash"])
            assert page is not None, x["id"]
            for key in e["params"]:
                assert key in page.keys or key in page.free, (x["id"], key)
        elif e["action"] == "build_board":
            for b in e["blocks"]:
                assert b["type"] in ("table", "chart", "tool", "finder")
                if b.get("dataset"):
                    assert b["dataset"] in A.BOARD_DATASETS
        elif e["action"] == "refuse":
            assert e["reason"] in A.REASONS
        elif e["action"] == "open_live_game":
            assert e["team"] is None or e["team"] in AP.CODES


def test_stored_evaluation_is_of_these_sentences(A):
    path = os.path.join(_API, "ask_eval.json")
    if not os.path.exists(path):
        pytest.skip("not evaluated yet (scripts/ask_eval.py --split test --runs 3 --write)")
    ev = json.load(open(path))
    s = json.load(open(os.path.join(_API, "ask_sentences.json")))
    test = {x["id"]: x["text"] for x in s["sentences"] if x["split"] == "test"}
    assert ev["split"] == "test" and ev["model"] == A.MODEL and ev["n"] == len(test)
    assert ev["sentences_written"] == s["written"] <= ev["evaluated"]
    for run in ev["runs_detail"]:
        assert {r["id"]: r["text"] for r in run["results"]} == test
        assert run["summary"]["right"] == sum(r["right"] for r in run["results"])
        assert run["summary"]["failed"] == sum(r["outcome"] == "failed" for r in run["results"])
        for r in run["results"]:
            if r["outcome"] == "failed":              # no answer, never a wrong one counted as unanswered
                assert r["action"] is None and r["error"] and not r["right"]
    assert ev["right_by_run"] == [r["summary"]["right"] for r in ev["runs_detail"]]
    assert ev["failed_by_run"] == [r["summary"]["failed"] for r in ev["runs_detail"]]
    for res in ev["resumed"]:                         # a resume only fills in unanswered sentence-runs
        assert ev["evaluated"] <= res["day"] and res["asked"] >= res["unanswered_after"]


def test_status_reports_unanswered_runs(router):
    """The status endpoint's summary stands on the answered sentence-runs and says when runs are missing."""
    ev = router._evaluation()
    if ev is None:
        pytest.skip("not evaluated yet")
    assert ev["answered"] + sum(ev["failed_by_run"]) == ev["n"] * ev["runs"]
    assert ev["complete"] == (sum(ev["failed_by_run"]) == 0)
    assert sum(v["right"] for v in ev["by_action"].values()) == sum(ev["right_by_run"])
    assert sum(v["failed"] for v in ev["by_action"].values()) == sum(ev["failed_by_run"])
    assert isinstance(ev["resumed"], list) and (ev["median_seconds"] is None or ev["median_seconds"] > 0)


def test_a_resume_only_completes_the_same_evaluation(A):
    """scripts/ask_eval.py --resume asks the unanswered sentence-runs again, and refuses when anything that could
    change an answer changed: the prompt, the engine's code, the model, the seasons (opening night)."""
    sys.path.insert(0, os.path.join(_ROOT, "scripts"))
    import ask_eval as E
    failed = {"id": "t01", "outcome": "failed", "right": False, "action": None, "error": "429: ... for today ..."}
    ev = {"split": "test", "model": A.MODEL, "lib_sha256": E.lib_sha(), "evaluated": TODAY.isoformat(),
          "seasons": list(SEASONS), "prompt_version": A.prompt_version(TODAY, SEASONS),
          "runs_detail": [{"results": [failed]}]}
    assert E.resume_blockers(ev, A.MODEL, E.lib_sha(), SEASONS) == []
    assert any("prompt" in w for w in E.resume_blockers({**ev, "prompt_version": "000000000000"}, A.MODEL, E.lib_sha(), SEASONS))
    assert any("changed since" in w for w in E.resume_blockers({**ev, "lib_sha256": "000000000000"}, A.MODEL, E.lib_sha(), SEASONS))
    assert any("model" in w for w in E.resume_blockers(ev, "another-model", E.lib_sha(), SEASONS))
    assert any("seasons moved" in w for w in E.resume_blockers(ev, A.MODEL, E.lib_sha(), (2027, 2026)))
    assert any("nothing to resume" in w for w in E.resume_blockers({**ev, "runs_detail": [{"results": [{**failed, "outcome": "right"}]}]}, A.MODEL, E.lib_sha(), SEASONS))
    err = A.ParseError("The free Gemini quota for today is used up", status=429)
    assert E.day_quota_gone(err) and not E.day_quota_gone(A.ParseError("too many a minute", status=429))
