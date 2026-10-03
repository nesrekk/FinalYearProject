"""
test_workbench_finder.py
========================
Guards round 7 step 6, the Workbench Player Finder (POST /workbench/finder,
api/routers/workbench_finder.py):

  * known answers checked against outside pages (URL and read date beside
    each): the 30-point scorers of 2022-23, Shai Gilgeous-Alexander's run of
    20-point games (72 in 2024-25, the 80th and 100th on the dates reported),
    his 30 points a game on 55% shooting in 2025-26;
  * the semantics written in the router's docstring: a value over several rows
    equals the Workbench table's combined value (summed stat over summed
    games; summed makes over summed attempts, checked against SQL written
    here), a count counts rows meeting every test in the same row, a streak
    equals the Game Finder's longest streak, and min_n / min_games / filters
    / a set restrict as they say;
  * every operator and every level (value, count, streak) on both datasets
    and both scopes, and every verified catalogue stat in a value slot and a
    test slot, run without error;
  * bad requests are refused with a reason (unknown keys, stats not offered,
    a rate that can't be combined, conditions missing their parts).

Skips when the database is unreachable.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_workbench_finder.py
"""

import os
import sys

import psycopg2
import pytest

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API not in sys.path:
    sys.path.insert(0, _API)

from db_config import DB_CONFIG  # noqa: E402
import workbench_catalogue as WC  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")

SGA = 1628983


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from impact_api import app
    return TestClient(app)


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    yield conn.cursor()
    conn.close()


def _find(client, **spec):
    r = client.post("/workbench/finder", json=spec)
    assert r.status_code == 200, r.text
    return r.json()


def _value(stat, op="gte", value=0, **kw):
    return {"type": "value", "stat": stat, "op": op, "value": value, **kw}


def _tests(*tests):
    return [{"stat": s, "op": o, "value": v} for s, o, v in tests]


# ─── known answers, checked against outside pages ──────────────────────────

def test_thirty_point_scorers_2022_23(client):
    # ESPN, 2022-23 regular-season points per game (qualified players), read
    # 2026-10-03: https://www.espn.com/nba/stats/player/_/season/2023/seasontype/2
    # Embiid 33.1 (66 GP), Dončić 32.4 (66), Lillard 32.2 (58),
    # Gilgeous-Alexander 31.4 (68), Antetokounmpo 31.1 (63), Tatum 30.1 (74).
    # 58 games was that season's scoring-title minimum.
    d = _find(client, dataset="player_season", season_from=2023, season_to=2023, min_games=58,
              conditions=[_value("pts", "gte", 30)])
    got = [(r["player_name"], r["conditions"][0]["value"], r["n_games"]) for r in d["rows"]]
    assert got == [("Joel Embiid", 33.1, 66), ("Luka Dončić", 32.4, 66), ("Damian Lillard", 32.2, 58),
                   ("Shai Gilgeous-Alexander", 31.4, 68), ("Giannis Antetokounmpo", 31.1, 63),
                   ("Jayson Tatum", 30.1, 74)]
    assert d["n"] == {"rows": 6, "matched": 6, "players": 6, "pool": d["n"]["pool"]}
    assert d["sentence"] == "Players who, in 2022-23, over at least 58 games, averaged at least 30 points per game."


def _sga_run(client, **extra):
    d = _find(client, dataset="player_game", scope="span", entities=[SGA],
              conditions=[{"type": "streak", "tests": _tests(("pts", "gte", 20)), "count": 1}], **extra)
    (row,) = d["rows"]
    return row["conditions"][0]


def test_gilgeous_alexander_twenty_point_streak(client):
    # Wikipedia, "Shai Gilgeous-Alexander", read 2026-10-03:
    # https://en.wikipedia.org/wiki/Shai_Gilgeous-Alexander
    # 2024-25: "His streak of 72 consecutive games scoring at least 20 points";
    # 2025-26: on November 4 his 80th consecutive 20-point game, on December 22
    # his 100th, and on March 12, 2026 (35 points against Boston) he broke Wilt
    # Chamberlain's record for consecutive 20-point games.
    # The NBA counts games played; the NBA Cup final he played on 2024-12-17
    # isn't a regular-season game, and the Game Log leaves it out too.
    assert _sga_run(client, season_to=2025) == {"streak": 72, "from": "2024-11-01", "to": "2025-04-08"}
    upto = lambda day: _sga_run(client, filters=[{"key": "date", "op": "lte", "value": day}])  # noqa: E731
    assert upto("2025-11-04") == {"streak": 80, "from": "2024-11-01", "to": "2025-11-04"}
    assert upto("2025-11-03")["streak"] == 79
    assert upto("2025-12-22") == {"streak": 100, "from": "2024-11-01", "to": "2025-12-22"}
    record = upto("2026-03-12")
    assert record == {"streak": 127, "from": "2024-11-01", "to": "2026-03-12"}
    assert upto("2026-03-11")["streak"] == 126  # the record he passed that night
    whole = _sga_run(client)
    assert whole["from"] == "2024-11-01" and whole["streak"] >= 127


def test_thirty_on_fifty_five_percent_2025_26(client):
    # Wikipedia (as above), 2025-26: "the first guard in NBA history to average
    # at least 30 points on at least 55% shooting from the field in a season."
    d = _find(client, dataset="player_season", season_from=2026, season_to=2026, min_games=20,
              conditions=[_value("pts", "gte", 30), _value("fg_pct", "gte", 0.55)])
    assert SGA in [r["player_id"] for r in d["rows"]]


# ─── semantics ──────────────────────────────────────────────────────────────

def test_values_equal_the_workbench_table(client):
    """A value over a player's games is the Workbench table's combined value
    (the same catalogue SQL), every kind: count, ratio, wmean."""
    stats = ["pts", "fg3_pct", "ts_pct", "min", "age_feb1"]
    d = _find(client, dataset="player_game", scope="season", season_from=2024, season_to=2024,
              conditions=[_value(s, "gte", -1e9) for s in stats], sort={"key": "n_games", "dir": "desc"}, limit=40)
    q = client.post("/workbench/query", json={
        "dataset": "player_game", "columns": stats, "season_from": 2024, "season_to": 2024,
        "group_by": ["entity", "season"], "limit": 5000}).json()
    table = {(r["player_id"], r["season"]): r for r in q["rows"]}
    assert len(d["rows"]) == 40
    for row in d["rows"]:
        t = table[(row["player_id"], row["season"])]
        assert row["n_games"] == t["n_games"]
        for s, c in zip(stats, row["conditions"]):
            assert c["value"] == t[s] and c["n"] == t["n"][s], (row["player_name"], s)


def test_rate_is_summed_makes_over_summed_attempts(client, cur):
    d = _find(client, dataset="player_game", scope="span", season_from=2022, season_to=2025, entities=[SGA],
              conditions=[_value("fg3_pct"), _value("ts_pct")])
    cur.execute("""SELECT SUM(l.fg3m)::float / SUM(l.fg3a), SUM(l.fg3a),
                          SUM(l.pts) / (2 * SUM(l.fga + 0.44 * l.fta))
                   FROM player_game_lines l
                   JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
                   WHERE l.player_id = %s AND l.season BETWEEN 2022 AND 2025 AND l.seconds > 0""", (SGA,))
    fg3, fg3a, ts = cur.fetchone()
    c = d["rows"][0]["conditions"]
    assert c[0]["value"] == round(fg3, 4) and c[0]["n"] == fg3a
    assert c[1]["value"] == round(float(ts), 4)


def test_counts_need_every_test_in_the_same_game(client, cur):
    together = _find(client, dataset="player_game", season_from=2024, season_to=2024, entities=[SGA], conditions=[
        {"type": "count", "tests": _tests(("pts", "gte", 30), ("ast", "gte", 7)), "count": 1}])
    apart = _find(client, dataset="player_game", season_from=2024, season_to=2024, entities=[SGA], conditions=[
        {"type": "count", "tests": _tests(("pts", "gte", 30)), "count": 1},
        {"type": "count", "tests": _tests(("ast", "gte", 7)), "count": 1}])
    cur.execute("""SELECT COUNT(*) FILTER (WHERE l.pts >= 30 AND l.ast >= 7), COUNT(*) FILTER (WHERE l.pts >= 30),
                          COUNT(*) FILTER (WHERE l.ast >= 7), COUNT(*)
                   FROM player_game_lines l
                   JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
                   WHERE l.player_id = %s AND l.season = 2024 AND l.seconds > 0""", (SGA,))
    both, pts30, ast7, games = cur.fetchone()
    assert together["rows"][0]["conditions"] == [{"count": both, "of": games}]
    assert apart["rows"][0]["conditions"] == [{"count": pts30, "of": games}, {"count": ast7, "of": games}]
    assert both < min(pts30, ast7)
    assert "in the same game" in together["sentence"]


def test_streaks_equal_the_game_finder(client):
    """The finder's longest streak per player = the Game Finder's (same rows,
    same rule: games he played, in date order)."""
    gf = client.get("/games/finder", params={"f": "pts:gte:30", "mode": "streaks", "limit": 40}).json()
    best = {r["player_id"]: r["games"] for r in gf["results"]}
    d = _find(client, dataset="player_game", scope="span", limit=40,
              conditions=[{"type": "streak", "tests": _tests(("pts", "gte", 30)), "count": 2}])
    got = {r["player_id"]: r["conditions"][0]["streak"] for r in d["rows"]}
    shared = set(best) & set(got)
    assert len(shared) >= 30
    assert all(best[p] == got[p] for p in shared)
    # A home-games-only streak equals the Game Finder's with home=home.
    gf = client.get("/games/finder", params={"f": "pts:gte:25", "mode": "streaks", "home": "home", "limit": 10}).json()
    d = _find(client, dataset="player_game", scope="span", limit=10, filters=[{"key": "home", "op": "eq", "value": True}],
              conditions=[{"type": "streak", "tests": _tests(("pts", "gte", 25)), "count": 2}])
    assert [r["games"] for r in gf["results"]] == [r["conditions"][0]["streak"] for r in d["rows"]]


def test_season_scope_ends_a_streak_with_the_season(client):
    span = _find(client, dataset="player_game", scope="span", entities=[SGA],
                 conditions=[{"type": "streak", "tests": _tests(("pts", "gte", 20)), "count": 1}])
    seasons = _find(client, dataset="player_game", scope="season", entities=[SGA],
                    conditions=[{"type": "streak", "tests": _tests(("pts", "gte", 20)), "count": 1}])
    by_season = {r["season"]: r["conditions"][0]["streak"] for r in seasons["rows"]}
    assert by_season[2025] == 72 and span["rows"][0]["conditions"][0]["streak"] > by_season[2026]


def test_floors_filters_and_sets(client, cur):
    base = dict(dataset="player_game", season_from=2025, season_to=2025)
    loose = _find(client, **base, conditions=[_value("fg3_pct", "gte", 0.45)], limit=5000)
    floored = _find(client, **base, conditions=[_value("fg3_pct", "gte", 0.45, min_n=200)], limit=5000)
    assert all(r["conditions"][0]["n"] >= 200 for r in floored["rows"])
    assert any(r["conditions"][0]["n"] < 200 for r in loose["rows"])
    assert floored["n"]["matched"] < loose["n"]["matched"]
    assert "on at least 200 3-point attempts" in floored["sentence"]

    games = _find(client, **base, min_games=60, conditions=[_value("pts", "gte", 25)])
    assert all(r["n_games"] >= 60 for r in games["rows"])

    home = _find(client, **base, entities=[SGA], filters=[{"key": "home", "op": "eq", "value": True}],
                 conditions=[_value("pts", "gte", 0)])
    cur.execute("""SELECT COUNT(*), AVG(l.pts) FROM player_game_lines l
                   JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
                   WHERE l.player_id = %s AND l.season = 2025 AND l.seconds > 0 AND f.is_home""", (SGA,))
    n, avg = cur.fetchone()
    (row,) = home["rows"]
    assert row["n_games"] == n and row["conditions"][0]["value"] == round(float(avg), 4)
    assert "counting only home games" in home["sentence"]

    pool = _find(client, **base, entities=[SGA, 203999], conditions=[_value("pts", "gte", 0)])
    assert {r["player_id"] for r in pool["rows"]} == {SGA, 203999} and pool["n"]["pool"] == 2


def test_reliability_and_n_are_reported(client):
    d = _find(client, dataset="player_game", season_from=2026, season_to=2026, limit=5000,
              conditions=[_value("fg3_pct", "gte", 0.5)])
    rel = [r["conditions"][0]["reliability"] for r in d["rows"]]
    assert all(x is not None and set(x) == {"n", "reliability", "noisy"} for x in rel)
    assert any(x["noisy"] for x in rel)  # 50% on a handful of threes is mostly luck
    assert d["conditions"][0]["column"]["n_unit"] == "3-point attempts"


def test_sorting_and_paging(client):
    spec = dict(dataset="player_season", season_from=2020, season_to=2026, min_games=40,
                conditions=[_value("pts", "gte", 25), _value("tov", "lte", 4)])
    d = _find(client, **spec, sort={"key": "c1", "dir": "asc"}, limit=10)
    tov = [r["conditions"][1]["value"] for r in d["rows"]]
    assert tov == sorted(tov)
    page2 = _find(client, **spec, sort={"key": "c1", "dir": "asc"}, limit=10, offset=10)
    assert page2["n"]["matched"] == d["n"]["matched"]
    assert not {(r["player_id"], r["season"]) for r in d["rows"]} & {(r["player_id"], r["season"]) for r in page2["rows"]}
    lte = _find(client, dataset="player_season", season_from=2026, season_to=2026, min_games=60,
                conditions=[_value("tov", "lte", 1)])
    vals = [r["conditions"][0]["value"] for r in lte["rows"]]
    assert vals == sorted(vals)  # an "at most" condition ranks lowest first


# ─── every op, level and stat runs ─────────────────────────────────────────

@pytest.mark.parametrize("op,value", [("gte", 20), ("gt", 20), ("lte", 5), ("lt", 5), ("eq", 10), ("ne", 10),
                                      ("between", [10, 12])])
def test_every_operator(client, op, value):
    for dataset, scope in (("player_season", "season"), ("player_game", "span")):
        d = _find(client, dataset=dataset, scope=scope, season_from=2025, season_to=2025, conditions=[
            _value("pts", op, value),
            {"type": "count", "tests": [{"stat": "pts", "op": op, "value": value}], "count": 1},
            {"type": "streak", "tests": [{"stat": "pts", "op": op, "value": value}], "count": 1}])
        assert d["n"]["matched"] >= 0
    for count_op in ("gte", "gt", "lte", "lt", "eq"):
        d = _find(client, dataset="player_game", season_from=2025, season_to=2025, conditions=[
            {"type": "count", "tests": _tests(("pts", "gte", 30)), "count_op": count_op, "count": 3}])
        want = {"gte": lambda k: k >= 3, "gt": lambda k: k > 3, "lte": lambda k: k <= 3,
                "lt": lambda k: k < 3, "eq": lambda k: k == 3}[count_op]
        assert all(want(r["conditions"][0]["count"]) for r in d["rows"])


@pytest.mark.parametrize("dataset", ["player_season", "player_game"])
@pytest.mark.parametrize("scope", ["season", "span"])
def test_every_verified_stat_in_every_slot(client, dataset, scope):
    ds = WC.DATASETS[dataset]
    keys = [c.key for c in ds.columns.values() if c.status == "verified"]
    combinable = [k for k in keys if ds.columns[k].kind != "none" or (dataset == "player_season" and scope == "season")]
    for i in range(0, len(keys), 8):
        chunk = keys[i:i + 8]
        d = _find(client, dataset=dataset, scope=scope, season_from=2025, season_to=2025, limit=5,
                  conditions=[{"type": "count", "tests": [{"stat": k, "op": "gte", "value": 0} for k in chunk[:4]], "count": 1},
                              {"type": "streak", "tests": [{"stat": k, "op": "gte", "value": 0} for k in chunk[4:] or chunk[:1]], "count": 1}])
        assert isinstance(d["rows"], list)
    for i in range(0, len(combinable), 8):
        d = _find(client, dataset=dataset, scope=scope, season_from=2025, season_to=2025, limit=5,
                  conditions=[_value(k, "gte", -1e9) for k in combinable[i:i + 8]])
        assert d["rows"] and all(len(r["conditions"]) == len(combinable[i:i + 8]) for r in d["rows"])
    # The per modes a counting stat honours.
    for per in ("total", "per36", "per100"):
        d = _find(client, dataset=dataset, scope=scope, season_from=2025, season_to=2025, limit=3, min_games=30,
                  conditions=[_value("pts", "gte", 0, per=per)])
        assert d["rows"]


# ─── refusals ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec,words", [
    ({"conditions": [_value("nope")]}, "isn't in the player_season catalogue"),
    ({"conditions": [_value("age")]}, "isn't offered"),
    ({"conditions": [_value("pts")], "dataset": "team_season"}, None),
    ({"conditions": [_value("pts")], "sql": "drop table x"}, None),
    ({"conditions": [{**_value("pts"), "extra": 1}]}, None),
    ({"conditions": [_value("pts", "gte", "30; drop table player_season_stats")]}, "needs a number"),
    ({"conditions": [_value("pts", "like", 3)]}, "op must be"),
    ({"conditions": [_value("impact_score_raw")], "scope": "span"}, "can't be combined"),
    ({"conditions": [{"type": "count", "tests": [], "count": 3}]}, "at least one test"),
    ({"conditions": [{"type": "count", "tests": _tests(("pts", "gte", 30))}]}, "needs a count"),
    ({"conditions": [{"type": "streak", "stat": "pts", "tests": _tests(("pts", "gte", 30)), "count": 3}]}, "takes tests"),
    ({"conditions": [{**_value("pts"), "tests": _tests(("pts", "gte", 1))}]}, "tests belong"),
    ({"conditions": [{"type": "count", "tests": _tests(*[("pts", "gte", 1)] * 5), "count": 1}]}, None),
    ({"conditions": [_value("pts")] * 9}, None),
    ({"conditions": [_value("pts")], "sort": {"key": "pts; --"}}, "Can't sort"),
    ({"conditions": [_value("pts")], "entities": ["1628983"]}, None),
    ({"conditions": [_value("pts")], "filters": [{"key": "opponent", "op": "eq", "value": "BOS"}]}, None),
    ({"conditions": [_value("pts")], "season_from": 2020, "season_to": 2010}, "after"),
])
def test_refusals(client, spec, words):
    r = client.post("/workbench/finder", json=spec)
    assert r.status_code in (400, 422), r.text
    if words:
        assert words in r.text
