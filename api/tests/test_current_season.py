"""
test_current_season.py
=======================
Round 9 step 5: the app in "current season" mode (docs/qa/ROUND9_ISSUES.md R9-011, R9-029 to R9-03x).

  - api/current_season.py is the one rule: the newest complete season until a new season has a regular-season final
    stored, then that season, with "through <date>", games played and the early-season reliability warnings.
    Checked on today's database (2025-26 complete, 2026-27 not started) and on a scratch schema holding a copy of
    game_scores / luck_schedule_seasons / player_season_stats plus a fake 2026-27 night (search_path, no public
    table touched).
  - routes whose default season is the rule's (Stat Leaders' floor scales with the games played so far, the
    Leaderboard Builder's default season meets its games floor) or the newest season their own table has
    (matchups, play types, hustle, team ratings, a player's hot streak and garbage-time line).
  - /dashboard/week: the week's results, the biggest upset and the best game agree with the Best Games & Upsets
    tables; /games/by-date gives Game Replay's id for stored finals.
  - the frontend reads the rule (utils/season.js) instead of a season literal.

Usage:
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_current_season.py
"""

import os
import re
import sys
from datetime import date, timedelta

import psycopg2
import pytest
from fastapi.testclient import TestClient

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.dirname(_API_DIR)
_FRONTEND = os.path.join(_REPO, "frontend", "src")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from db_config import DB_CONFIG  # noqa: E402

SCHEMA = "zz_current_season"


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Local Postgres DB is not reachable.")


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    yield conn.cursor()
    conn.close()


@pytest.fixture(scope="module")
def client():
    from impact_api import app
    return TestClient(app)


def _public_live(cur):
    """Whether public already has a season past the newest complete one (from opening night)."""
    import current_season as C
    return C._live_block(cur, C.latest_complete_season(cur)) is not None


@needs_db
def test_the_rule_on_todays_database(cur):
    import current_season as C
    C.clear_cache()
    st = C.compute(cur)
    if _public_live(cur):
        pytest.skip("public has live-season finals: the scratch-schema test covers the rule")
    assert st["current"] == st["latest_complete"] == 2026 and st["live"] is None and st["early"] == []
    # the next season's first tip comes from the Forecast Ledger's locked schedule (ESPN, 1,200 counting games)
    assert st["upcoming"] == {"season": 2027, "label": "2026-27", "first_date": "2026-10-20", "scheduled": 1200}
    assert C.default_season_for(20, cur) == 2026


@pytest.fixture(scope="module")
def scratch(cur):
    """Copies of the three tables the rule reads, plus a fake opening night: BOS-NYK and LAL-GSW final on 2026-10-20,
    every player of four 2025-26 teams given a three-game 2026-27 line (search_path = the schema, then public)."""
    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    cur.execute(f"CREATE SCHEMA {SCHEMA}")
    for t in ("game_scores", "luck_schedule_seasons", "player_season_stats"):
        cur.execute(f"CREATE TABLE {SCHEMA}.{t} (LIKE public.{t} INCLUDING DEFAULTS)")
        cur.execute(f"INSERT INTO {SCHEMA}.{t} SELECT * FROM public.{t}")
    rows = [("9900000001", "BOS", "NYK", True, 110, 101), ("9900000001", "NYK", "BOS", False, 101, 110),
            ("9900000002", "LAL", "GSW", True, 99, 104), ("9900000002", "GSW", "LAL", False, 104, 99)]
    for gid, team, opp, home, pf, pa in rows:
        cur.execute(f"""INSERT INTO {SCHEMA}.game_scores (game_id, team_abbreviation, opponent, season, game_date, is_home,
                        neutral_site, pts_for, pts_against, periods, espn_id) VALUES (%s, %s, %s, 2027, '2026-10-20', %s,
                        false, %s, %s, 4, NULL)""", (gid, team, opp, home, pf, pa))
    cur.execute("""CREATE TEMP TABLE zz_cs_lines AS SELECT * FROM public.player_season_stats
                   WHERE season = 2026 AND team_abbreviation IN ('BOS', 'NYK', 'LAL', 'GSW')""")
    cur.execute("UPDATE zz_cs_lines SET season = 2027, gp = 3")
    cur.execute(f"INSERT INTO {SCHEMA}.player_season_stats SELECT * FROM zz_cs_lines")
    cur.execute("DROP TABLE zz_cs_lines")
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    c = conn.cursor()
    c.execute(f"SET search_path = {SCHEMA}, public")
    import current_season as C
    C.clear_cache()
    yield c
    C.clear_cache()
    conn.close()
    cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@needs_db
def test_the_rule_with_a_live_season(scratch):
    import current_season as C
    st = C.compute(scratch)
    assert st["current"] == 2027 and st["latest_complete"] == 2026 and st["upcoming"] is None
    live = st["live"]
    assert live["season"] == 2027 and live["games"] == 2 and live["through"] == "2026-10-20" == live["first_date"]
    assert live["team_games_min"] == live["team_games_max"] == 1 and live["teams"] == 4
    assert live["scheduled"] == 1200  # no season-to-date row yet: the ledger's counting games
    # early-season reliability: n / (n + M) with Stat Stability's M, median over rotation players
    early = {r["stat"]: r for r in st["early"]}
    assert early["pts"]["median_n"] == 3.0 and early["fg3_pct"]["reliability"] < 0.1
    for r in early.values():
        assert r["reliability"] == round(r["median_n"] / (r["median_n"] + r["stable_n"]), 2)
    # a tool with a games floor stays on the complete season until the live one can meet it
    C._cache.update(at=10 ** 12, value=st)  # status() for the helpers below without a second cursor
    try:
        assert C.default_season_for(20) == 2026 and C.default_season_for(1) == 2027
    finally:
        C.clear_cache()


@needs_db
def test_stat_leaders_floor_scales_in_a_live_season(scratch):
    import current_season as C
    from routers.leaders import qualifying, stored_floor
    C.clear_cache()
    f = stored_floor(scratch, "pts", 2027)
    assert f["min_gp"] == 3 and f == qualifying("pts", 3)  # ceil(0.7 x 3 games)
    assert stored_floor(scratch, "pts", 2026) == qualifying("pts")  # a complete season keeps 30 games
    C.clear_cache()


@needs_db
def test_meta_season_route(client, cur):
    import current_season as C
    C.clear_cache()
    d = client.get("/meta/season").json()
    assert d["current"] == C.compute(cur)["current"] and "game_scores" in d["_source"]["tables"]
    assert "regular-season final" in d["rule"]


@needs_db
def test_dashboard_week_matches_the_best_games_tables(client, cur):
    import current_season as C
    C.clear_cache()
    d = client.get("/dashboard/week").json()
    st = C.compute(cur)
    season = st["live"]["season"] if st["live"] else st["latest_complete"]
    cur.execute("SELECT MAX(game_date) FROM game_scores WHERE season = %s", (season,))
    through = cur.fetchone()[0]
    start = through - timedelta(days=6)
    assert d["season"] == season and d["through"] == through.isoformat() and d["from"] == start.isoformat()
    cur.execute("SELECT COUNT(DISTINCT game_id) FROM game_scores WHERE season = %s AND game_date BETWEEN %s AND %s",
                (season, start, through))
    assert d["games"] == len(d["results"]) == cur.fetchone()[0]
    # the biggest upset: the lowest pre-game chance among the week's winners that were underdogs (Upsets page rule)
    cur.execute("""SELECT MIN(CASE WHEN home_won THEN p_home ELSE 1 - p_home END) FROM game_pregame_odds
                   WHERE season = %s AND game_date BETWEEN %s AND %s
                     AND ((home_won AND p_home < 0.5) OR (NOT home_won AND p_home > 0.5))""", (season, start, through))
    low = cur.fetchone()[0]
    assert d["biggest_upset"]["winner_chance"] == round(low, 3) < 0.5
    up = client.get("/upsets", params={"season": season, "limit": 100}).json()["results"]
    week = [u for u in up if start.isoformat() <= u["date"] <= through.isoformat()]
    assert week and week[0]["game_id"] == d["biggest_upset"]["game_id"]
    cur.execute("""SELECT MAX(excitement) FROM best_games WHERE season = %s AND game_date BETWEEN %s AND %s AND score_ok""",
                (season, start, through))
    assert d["best_game"]["excitement"] == round(cur.fetchone()[0], 2)
    if not st["live"]:  # before opening night: 2025-26's last week, saying when 2026-27 starts
        assert d["through"] == "2026-04-12" and d["upcoming"]["first_date"] == "2026-10-20"


@needs_db
def test_stored_finals_carry_the_replay_id(client, cur):
    g = client.get("/games/by-date", params={"date": "2026-04-12"}).json()
    assert g["source"] == "stored" and len(g["games"]) == 15
    ids = [x["replay_id"] for x in g["games"]]
    cur.execute("SELECT COUNT(*) FROM pbp_games WHERE game_id = ANY(%s)", ([i for i in ids if i],))
    assert all(ids) and cur.fetchone()[0] == 15
    assert all(x["replay_id"] == f"espn_{x['espn_id']}" for x in g["games"])


@needs_db
def test_defaults_follow_each_tables_newest_season(client, cur):
    """Tracking fetches and season-end builds aren't in the daily update: their routes open on their own newest
    season, never on a live season they don't have (round 9 step 5's crawl on a live-season copy found five)."""
    cur.execute("SELECT MAX(season) FROM player_hustle")
    assert client.get("/hustle/leaders").json()["season"] == cur.fetchone()[0]
    cur.execute("SELECT MAX(season) FROM player_matchups WHERE off_player_id = 2544")
    m = client.get("/matchups/player/LeBron James", params={"player_id": 2544}).json()
    assert m["season"] == cur.fetchone()[0]
    cur.execute("SELECT MAX(season) FROM player_game_lines WHERE player_id = 2544")
    assert client.get("/games/hot-streak/2544").json()["season"] == cur.fetchone()[0]
    cur.execute("SELECT MAX(season) FROM team_seasons WHERE NOT is_league_avg AND o_rtg IS NOT NULL")
    assert client.get("/teams/compare/LAL/BOS").json()["season"] == cur.fetchone()[0]
    e = client.get("/era/translate", params={"player_id": 2544}).json()
    cur.execute("SELECT MAX(season) FROM league_season_averages")
    assert e["target"] <= cur.fetchone()[0]


def _frontend_files():
    for root, _dirs, files in os.walk(os.path.join(_FRONTEND, "components")):
        for f in files:
            if f.endswith((".jsx", ".js")):
                yield os.path.join(root, f)


def test_no_season_picker_defaults_to_a_literal():
    """Season pickers read utils/season.js (R9-011): no `useState(2026)`, `LATEST_SEASON = 2026` or a picker range
    ending at a literal season left in the components."""
    # on purpose: the Trajectory Forecaster opens on 2021-22 (its held-out showcase), Draft Value on a draft class
    allowed = {("TrajectoryForecasterSection.jsx", "useState(2022)"), ("DraftValueGuide.jsx", "useState(2015)")}
    bad = []
    pat = re.compile(r"useState\(\s*20\d\d\s*\)|LATEST_SEASON\s*=\s*20\d\d|DEFAULT_LAST_SEASON|length:\s*20\d\d\s*-\s*20\d\d|to\s*=\s*20\d\d\b")
    for p in _frontend_files():
        for i, line in enumerate(open(p, encoding="utf-8"), 1):
            if pat.search(line) and not any(os.path.basename(p) == f and lit in line for f, lit in allowed):
                bad.append(f"{os.path.relpath(p, _REPO)}:{i}: {line.strip()[:100]}")
    assert bad == [], bad
    season_js = open(os.path.join(_FRONTEND, "utils", "season.js"), encoding="utf-8").read()
    assert "/meta/season" in season_js and "export function currentSeason" in season_js
    main = open(os.path.join(_FRONTEND, "main.jsx"), encoding="utf-8").read()
    assert "loadSeasonInfo().finally" in main
