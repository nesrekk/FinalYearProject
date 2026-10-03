"""
test_workbench.py
=================
Guards round 7 step 1, the Workbench catalogue and query API
(api/workbench_catalogue.py, api/routers/workbench.py):

  * the existing pages' catalogues are unchanged now that they're built from
    the Workbench catalogue (Leaderboard STATS / attempt floors, Game Finder
    STATS / base join; frozen copies of the pre-switch literals below);
  * the catalogue is well formed and every first season follows the
    Leaderboard's rule (first season recorded for 90%+ of the dataset's rows);
  * every verified column runs on its dataset, one row at a time and combined
    in every per mode;
  * unknown keys and injection-shaped strings are refused, user values never
    reach the SQL text, the row cap holds, a slow statement is stopped and a
    write can't run;
  * one known player-season equals the Leaderboard's numbers, one known game
    line equals the Game Finder's, and a rate over a span is summed makes over
    summed attempts (checked against SQL written here, not the query layer);
  * team games and team seasons agree on every team-season's wins.

Skips when the database is unreachable.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_workbench.py
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


def _q(client, **spec):
    r = client.post("/workbench/query", json=spec)
    assert r.status_code == 200, r.text
    return r.json()


# The pages' catalogues as they were written before round 7 (git 5cf83b4).
LEGACY_LEADERBOARD = {
    "pts": ("Points", "Per game", "num1", 1950, True, None),
    "reb": ("Rebounds", "Per game", "num1", 1951, True, None),
    "ast": ("Assists", "Per game", "num1", 1950, True, None),
    "stl": ("Steals", "Per game", "num1", 1974, True, None),
    "blk": ("Blocks", "Per game", "num1", 1974, True, None),
    "tov": ("Turnovers", "Per game", "num1", 1978, False, None),
    "fg3m": ("3-pointers made", "Per game", "num1", 1980, True, None),
    "fg3a": ("3-point attempts", "Per game", "num1", 1980, True, None),
    "fta": ("Free-throw attempts", "Per game", "num1", 1950, True, None),
    "oreb": ("Offensive rebounds", "Per game", "num1", 1974, True, None),
    "min": ("Minutes", "Per game", "num1", 1952, True, None),
    "fg_pct": ("Field-goal %", "Shooting", "pct", 1950, True, "fga"),
    "fg3_pct": ("3-point %", "Shooting", "pct", 1980, True, "fg3a"),
    "ft_pct": ("Free-throw %", "Shooting", "pct", 1950, True, "fta"),
    "ts_pct": ("True shooting %", "Shooting", "pct", 1950, True, "fga"),
    "efg_pct": ("Effective FG %", "Shooting", "pct", 1980, True, "fga"),
    "usg_pct": ("Usage %", "Rates", "pct", 1978, True, None),
    "ast_pct": ("Assist %", "Rates", "pct", 1965, True, None),
    "reb_pct": ("Rebound %", "Rates", "pct", 1971, True, None),
    "oreb_pct": ("Offensive rebound %", "Rates", "pct", 1974, True, None),
    "tov_pct": ("Turnover %", "Rates", "pct", 1978, False, None),
    "off_rating": ("Offensive rating", "Impact", "num1", 2010, True, None),
    "def_rating": ("Defensive rating", "Impact", "num1", 2010, False, None),
    "net_rating": ("Net rating", "Impact", "signed1", 2010, True, None),
    "plus_minus": ("Plus-minus", "Impact", "signed1", 2010, True, None),
    "bpm": ("BPM", "Impact", "signed1", 1974, True, None),
    "obpm": ("Offensive BPM", "Impact", "signed1", 1974, True, None),
    "dbpm": ("Defensive BPM", "Impact", "signed1", 1974, True, None),
    "vorp": ("VORP", "Impact", "num1", 1974, True, None),
    "impact_score_raw": ("Impact score (raw)", "Impact", "num2", 2010, True, None),
    "age": ("Age", "Other", "int", 1950, True, None),
}
LEGACY_GAME_FINDER = {
    "pts": ("Points", "l.pts", "int"),
    "reb": ("Rebounds", "(l.oreb + l.dreb)", "int"),
    "ast": ("Assists", "l.ast", "int"),
    "stl": ("Steals", "l.stl", "int"),
    "blk": ("Blocks", "l.blk", "int"),
    "tov": ("Turnovers", "l.tov", "int"),
    "oreb": ("Offensive rebounds", "l.oreb", "int"),
    "dreb": ("Defensive rebounds", "l.dreb", "int"),
    "fgm": ("Field goals made", "l.fgm", "int"),
    "fga": ("Field goal attempts", "l.fga", "int"),
    "fg3m": ("Threes made", "l.fg3m", "int"),
    "fg3a": ("Three-point attempts", "l.fg3a", "int"),
    "ftm": ("Free throws made", "l.ftm", "int"),
    "fta": ("Free throw attempts", "l.fta", "int"),
    "min": ("Minutes", "(l.seconds / 60.0)", "num1"),
    "fg_pct": ("FG%", "(l.fgm::float / NULLIF(l.fga, 0))", "pct"),
    "fg3_pct": ("3P%", "(l.fg3m::float / NULLIF(l.fg3a, 0))", "pct"),
    "ft_pct": ("FT%", "(l.ftm::float / NULLIF(l.fta, 0))", "pct"),
    "ts_pct": ("True shooting %", "(l.pts / NULLIF(2 * (l.fga + 0.44 * l.fta), 0))", "pct"),
}
LEGACY_BASE_FROM = """
    FROM player_game_lines l
    JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
    LEFT JOIN game_scores gs ON gs.game_id = f.game_id AND gs.team_abbreviation = f.team_abbreviation
"""


def test_existing_page_catalogues_unchanged():
    from routers import game_log, leaderboard
    assert leaderboard.STATS == LEGACY_LEADERBOARD
    assert list(leaderboard.STATS) == list(LEGACY_LEADERBOARD)
    assert leaderboard.ATTEMPT_DEFAULTS == {"fga": 5.0, "fg3a": 2.0, "fta": 2.0}
    assert game_log.STATS == LEGACY_GAME_FINDER and list(game_log.STATS) == list(LEGACY_GAME_FINDER)
    assert game_log.BASE_FROM == LEGACY_BASE_FROM
    assert game_log.BASE_WHERE == ["l.seconds > 0"]
    assert game_log.MARGIN_SQL == "(gs.pts_for - gs.pts_against)"
    # The Leaderboard's SQL reads the season-table column named by the key; the
    # catalogue's expression is that column for every stat but the stored age.
    for k in LEGACY_LEADERBOARD:
        assert WC.PLAYER_SEASON.columns[k].sql == f"s.{k}"


def test_catalogue_is_well_formed():
    reserved = {"n", "n_rows", "n_games", "reliability", "player_name"}
    for ds in WC.DATASETS.values():
        fields = {f for f, _ in ds.row_fields} | {f for g in ds.groupings.values() for f in g.fields} | \
            {f for g in ds.groupings.values() for f, _ in g.extra}
        assert set(ds.per_modes) <= set(WC.PER_MODES) and "game" in ds.per_modes
        assert ("per36" in ds.per_modes) == (ds.minutes_sql is not None)
        assert set(ds.groupings) <= {"entity", "season", "team", "opponent", "home", "result"}
        for d in ds.dims.values():
            assert d.type in ("team", "text", "bool", "date") and (d.type != "text" or d.values)
        for c in ds.columns.values():
            assert c.kind in WC.KINDS and c.fmt in WC.FORMATS, c.key
            assert c.key not in fields | reserved | set(ds.dims), (ds.key, c.key)
            assert c.label and c.short and c.group and c.sources, c.key
            assert (c.status == "verified") == (c.reason is None) and c.status in ("verified", "excluded"), c.key
            if c.kind == "count":
                assert c.total, c.key
                assert set(c.per_modes or ()) <= set(ds.per_modes), c.key
            if c.kind == "ratio":
                assert c.num and c.den and c.agg_text, c.key
            if c.kind == "wmean":
                assert c.weight and c.weight_label, c.key
            if c.stability:
                assert c.sample, c.key
            for frag in (c.sql, c.total, c.num, c.den, c.weight, c.n_sql, c.sample):
                assert frag is None or "%" not in frag, c.key
            if c.attempts:
                assert c.attempts in WC.MIN_ATTEMPTS_PER_GAME
    # Franchise map: every team code in the game tables maps to a franchise key.
    fmap = dict(zip(*WC.FRANCHISE_MAP))
    assert fmap["NJN"] == "BKN" and fmap["NOH"] == "NOP" and fmap["PHX"] == "PHX" and fmap["SEA"] == "OKC"


def test_stability_keys_exist(cur):
    cur.execute("SELECT to_regclass('stat_stability') IS NOT NULL")
    if not cur.fetchone()[0]:
        pytest.skip("stat_stability not built")
    cur.execute("SELECT stat FROM stat_stability WHERE variant = 'catalogue' AND stable_n IS NOT NULL")
    have = {r[0] for r in cur.fetchall()}
    for ds in WC.DATASETS.values():
        for c in ds.columns.values():
            assert c.stability is None or c.stability in have, (ds.key, c.key)


def _coverage(cur, ds, col, season):
    """Share of the season's rows with the column recorded; a season-table
    shooting % counts among the rows with attempts (no attempts, no %)."""
    expr = {"count": col.total, "ratio": f"CASE WHEN ({col.den}) IS NOT NULL THEN {col.num} END"}.get(col.kind) \
        or col.sql
    where = [*ds.where, f"{ds.season_sql} = %s"]
    if col.attempts:
        where.append(f"s.{col.attempts} > 0")
    where = " AND ".join(where)
    cur.execute(f"SELECT AVG(CASE WHEN ({expr}) IS NOT NULL THEN 1.0 ELSE 0 END) {ds.from_sql} WHERE {where}",
                [*ds.from_params, season])
    v = cur.fetchone()[0]
    return None if v is None else float(v)


def test_first_seasons_follow_the_90_percent_rule(cur):
    """First season = the first one with the column recorded on 90%+ of the
    dataset's rows (the Leaderboard's rule): true at that season, false the
    season before."""
    for ds in WC.DATASETS.values():
        cur.execute(f"SELECT MIN({ds.season_sql}) {ds.from_sql} {'WHERE ' + ' AND '.join(ds.where) if ds.where else ''}",
                    list(ds.from_params))
        start = cur.fetchone()[0]
        for c in ds.columns.values():
            assert c.first_season >= start, (ds.key, c.key)
            assert _coverage(cur, ds, c, c.first_season) >= 0.9, (ds.key, c.key, c.first_season)
            if c.first_season > start:
                before = _coverage(cur, ds, c, c.first_season - 1)
                assert before is None or before < 0.9, (ds.key, c.key, before)


def test_age_on_feb1_matches_aging_curves_formula(cur):
    """The SQL age equals build_aging_curves.py's pandas formula on every player-season."""
    cur.execute(f"""SELECT s.season, b.birth_date, {WC.AGE_FEB1}
                    FROM player_season_stats s JOIN player_bio b ON b.player_id = s.player_id
                    WHERE b.birth_date IS NOT NULL""")
    rows = cur.fetchall()
    assert len(rows) > 23000
    for season, bd, age in rows:
        expect = season - bd.year - int(bd.month > 2 or (bd.month == 2 and bd.day > 1))
        assert int(age) == expect, (season, bd, age)


def test_every_verified_column_runs(client):
    for ds in WC.DATASETS.values():
        cols = [k for k, c in ds.columns.items() if c.status == "verified"]
        aggs = [k for k in cols if ds.columns[k].kind != "none"]
        last = client.get("/workbench/catalogue").json()
        last = next(x for x in last["datasets"] if x["key"] == ds.key)["seasons"]["to"]
        d = _q(client, dataset=ds.key, columns=cols, season_from=last, limit=200)
        assert d["n"]["rows"] == min(200, d["n"]["matched"]) >= 30 and d["_source"]["tables"]
        for k in cols:
            assert any(r[k] is not None for r in d["rows"]), (ds.key, k)
            assert all(k in r["n"] for r in d["rows"])
        for per in ds.per_modes:
            d = _q(client, dataset=ds.key, columns=aggs, group_by="entity", per=per, limit=5000)
            assert not d["truncated"] and d["n"]["source_rows"] > 1000
            for k in aggs:
                assert any(r[k] is not None for r in d["rows"]), (ds.key, per, k)
            meta = {m["key"]: m for m in d["columns"]}
            assert all(meta[k]["format"] in WC.FORMATS for k in aggs)
        d = _q(client, dataset=ds.key, columns=aggs, group_by="all")
        assert d["n"]["rows"] == 1 and d["rows"][0]["n_rows"] == d["n"]["source_rows"]


def test_catalogue_endpoint(client):
    r = client.get("/workbench/catalogue")
    assert r.status_code == 200
    d = r.json()
    assert d["_source"]["tables"] and d["limits"]["row_cap"] == 5000
    by = {x["key"]: x for x in d["datasets"]}
    assert set(by) == set(WC.DATASETS)
    assert by["player_season"]["seasons"]["from"] == 1950 and by["player_game"]["seasons"]["from"] == 2021
    for ds in d["datasets"]:
        assert ds["rows"] > 1000 and ds["columns"] and ds["_source"]["tables"]
        for c in ds["columns"]:
            assert c["label"] and c["combines"] and c["format"] in WC.FORMATS
            assert (c["status"] == "excluded") == bool(c["reason"])
    excluded = {(ds["key"], c["key"]) for ds in d["datasets"] for c in ds["columns"] if c["status"] == "excluded"}
    # Round 7 step 2 rebuilt per-game on-floor +/- from the stints; only the two-convention age stays out.
    assert excluded == {("player_season", "age")}


@pytest.mark.parametrize("spec, status, words", [
    (dict(dataset="nope", columns=["pts"]), 400, "Unknown dataset"),
    (dict(dataset="player_season", columns=["pts; DROP TABLE player_bio"]), 400, "isn't in the player_season"),
    (dict(dataset="player_season", columns=["pts"], extra=1), 422, "Extra inputs"),
    (dict(dataset="player_season", columns=["pts"], filters=[{"key": "pts", "op": "gte", "value": "1; DROP"}]),
     400, "needs a number"),
    (dict(dataset="player_season", columns=["pts"], filters=[{"key": "pts", "op": "gte", "value": 1, "x": 2}]),
     422, "Extra inputs"),
    (dict(dataset="player_season", columns=["pts"], filters=[{"key": "team", "op": "eq", "value": "BOS' OR '1'='1"}]),
     400, "isn't a team code"),
    (dict(dataset="player_season", columns=["pts"], filters=[{"key": "pts", "op": "; DROP", "value": 1}]),
     400, "op must be"),
    (dict(dataset="player_season", columns=["pts"], filters=[{"key": "pg_sleep(10)", "op": "gte", "value": 1}]),
     400, "isn't in the player_season"),
    (dict(dataset="player_season", columns=["pts"], sort=[{"key": "pts; DROP", "dir": "desc"}]), 400, "Can't sort"),
    (dict(dataset="player_season", columns=["pts"], sort=[{"key": "pts", "dir": "desc; DROP"}]), 422, "literal"),
    (dict(dataset="player_season", columns=["pts"], group_by="player_id) --"), 400, "Can't group"),
    (dict(dataset="player_season", columns=["pts"], per="per1000"), 422, "literal"),
    (dict(dataset="player_season", columns=["pts"], entities=["1 OR 1=1"]), 400, "whole numbers"),
    (dict(dataset="team_season", columns=["w"], entities=["BOS'--"]), 400, "isn't a team code"),
    (dict(dataset="team_season", columns=["w"], entities=["XYZ"]), 400, "No team seasons"),
    (dict(dataset="player_game", columns=["pts"], filters=[{"key": "date", "op": "gte", "value": "2024-01-01'"}]),
     400, "isn't a date"),
    (dict(dataset="team_season", columns=["w"], filters=[{"key": "league", "op": "eq", "value": "ABA"}]),
     400, "must be among"),
    (dict(dataset="player_season", columns=["age"]), 400, "two conventions"),
    (dict(dataset="player_season", columns=["impact_score_raw"], group_by="entity"), 400, "can't be combined"),
    (dict(dataset="team_season", columns=["w"], per="per36"), 400, "isn't available"),
    (dict(dataset="player_season", columns=["pts"], limit=5001), 422, "less than or equal"),
    (dict(dataset="player_season", columns=[]), 422, "at least 1"),
    (dict(dataset="player_season", columns=["pts"], season_from=2030), 400, "cover"),
])
def test_rejects_unknown_keys_and_injection(client, spec, status, words):
    r = client.post("/workbench/query", json=spec)
    assert r.status_code == status, r.text
    assert words.lower() in r.text.lower()


def test_user_values_are_bound_never_written(client):
    from routers.workbench import QuerySpec, compile_query
    spec = QuerySpec(dataset="player_game", columns=["pts", "fg_pct"], entities=[1628389], group_by="entity",
                     filters=[{"key": "opponent", "op": "in", "value": ["WAS", "PHX"]},
                              {"key": "date", "op": "between", "value": ["2026-03-01", "2026-03-31"]},
                              {"key": "min", "op": "gte", "value": 31.75}],
                     having=[{"key": "pts", "op": "gte", "value": 77.25}], season_from=2026, season_to=2026)
    plan = compile_query(spec)
    for text in ("WAS", "PHX", "2026-03", "31.75", "77.25", "1628389"):
        assert text not in plan.sql
    assert plan.sql.count("%s") == len(plan.params)
    d = _q(client, **spec.model_dump())
    assert [r["pts"] for r in d["rows"]] == [83.0]


def test_row_cap_timeout_and_read_only():
    from fastapi import HTTPException
    from impact_core import get_db
    from routers.workbench import execute_readonly
    with pytest.raises(HTTPException) as e:
        execute_readonly("SELECT pg_sleep(2)", [], timeout_ms=100)
    assert e.value.status_code == 504
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        execute_readonly("CREATE TABLE zz_workbench_should_not_exist (a int)", [])
    # Nothing leaks into the pooled connections: no open transaction, default timeout.
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('zz_workbench_should_not_exist')")
        assert cur.fetchone()[0] is None
        cur.execute("SHOW statement_timeout")
        assert cur.fetchone()[0] == "0"
        conn.rollback()


def test_truncation_is_reported(client):
    d = _q(client, dataset="player_season", columns=["pts"], season_from=2026, season_to=2026, limit=10)
    assert d["truncated"] and d["n"]["rows"] == 10 and d["n"]["matched"] > 400
    assert d["notes"][0].startswith("Showing 1-10 of")
    pts = [r["pts"] for r in d["rows"]]
    assert pts == sorted(pts, reverse=True)


def test_known_player_season_matches_leaderboard(client):
    """Curry 2015-16 in the Workbench = the Leaderboard Builder's row (value and reliability)."""
    for stat in ("pts", "ts_pct", "fg3_pct", "bpm", "net_rating"):
        lb = client.get("/leaderboard/custom", params={"stat": stat, "season_from": 2016, "season_to": 2016,
                                                       "top_n": 100}).json()
        row = next(r for r in lb["results"] if r["player_id"] == 201939)
        d = _q(client, dataset="player_season", columns=[stat], entities=[201939], season_from=2016, season_to=2016)
        (w,) = d["rows"]
        assert (w["player_name"], w["team"], w["season"], w["n_games"]) == ("Stephen Curry", "GSW", 2016, 79)
        assert w[stat] == row["value"], stat
        if "sample" in row:
            assert w["reliability"][stat] == row["sample"], stat
    d = _q(client, dataset="player_season", columns=["pts", "age_feb1"], entities=[201939], season_from=2016,
           season_to=2016, per="total")
    # 30.1 a game x 79 games (the published total is 2,375: per-game values are rounded).
    assert d["rows"][0]["pts"] == pytest.approx(30.1 * 79) and d["rows"][0]["age_feb1"] == 27


def test_known_game_line_matches_game_finder(client):
    """Adebayo's 83 (2026-03-10) in the Workbench = the Game Finder's row, every stat."""
    gf = client.get("/games/finder", params={"f": "pts:gte:80", "season_from": 2026}).json()
    (g,) = [r for r in gf["results"] if r["player_id"] == 1628389]
    d = _q(client, dataset="player_game", columns=list(LEGACY_GAME_FINDER), entities=[1628389],
           filters=[{"key": "date", "op": "eq", "value": "2026-03-10"}])
    (w,) = d["rows"]
    assert (w["date"], w["team"], w["opponent"], w["game_id"], w["home"], w["win"]) == \
        (g["date"], g["team"], g["opponent"], g["nba_game_id"], g["home"], g["win"])
    for k in LEGACY_GAME_FINDER:
        if k in g:
            assert w[k] == pytest.approx(g[k], abs=0.05 if k == "min" else 5e-5), k
    assert w["pts"] == 83 and w["fta"] == 43
    assert w["fg_pct"] == pytest.approx(20 / 44, abs=5e-5)


def test_on_floor_plus_minus(client, cur):
    """Per-game on-floor +/- comes from player_game_onfloor (round 7 step 2): Adebayo's 83 was +20 (ESPN's
    box score, test_known_facts), a span sums the games, and the 12 games that don't reconcile are left out."""
    d = _q(client, dataset="player_game", columns=["plus_minus", "onfloor_pts_for", "onfloor_pts_against"],
           entities=[1628389], filters=[{"key": "date", "op": "eq", "value": "2026-03-10"}])
    (w,) = d["rows"]
    assert (w["plus_minus"], w["onfloor_pts_for"] - w["onfloor_pts_against"]) == (20, 20)
    pid = 203999
    cur.execute("""SELECT SUM(plus_minus), SUM(pts_for), COUNT(*) FILTER (WHERE NOT game_ok) FROM player_game_onfloor
                   WHERE player_id = %s AND season = 2024 AND game_ok AND seconds > 0""", (pid,))
    total, pts_for, _ = cur.fetchone()
    (w,) = _q(client, dataset="player_game", columns=["plus_minus", "onfloor_pts_for"], entities=[pid],
              group_by="entity", season_from=2024, season_to=2024, per="total")["rows"]
    assert (w["plus_minus"], w["onfloor_pts_for"]) == (total, pts_for)
    cur.execute(f"""SELECT l.player_id, l.game_date {WC.PLAYER_GAME_FROM}
                    JOIN player_game_onfloor o ON o.player_id = l.player_id AND o.game_id = l.game_id
                    WHERE NOT o.game_ok AND l.seconds > 0 LIMIT 1""")
    bad_pid, bad_date = cur.fetchone()
    (w,) = _q(client, dataset="player_game", columns=["plus_minus", "pts"], entities=[bad_pid],
              filters=[{"key": "date", "op": "eq", "value": str(bad_date)}])["rows"]
    assert w["plus_minus"] is None and w["pts"] is not None and w["n"]["plus_minus"] in (0, None)


def test_rate_over_a_span_is_summed_makes_over_summed_attempts(client, cur):
    pid = 203999  # Jokić, every game line 2020-21 to 2025-26
    cur.execute(f"""SELECT SUM(l.fgm)::float / SUM(l.fga), SUM(l.fg3m)::float / SUM(l.fg3a),
                           SUM(l.pts) / (2 * SUM(l.fga + 0.44 * l.fta)), AVG(l.fgm::float / NULLIF(l.fga, 0)),
                           36 * SUM(l.pts) / SUM(l.seconds / 60.0), SUM(l.pts)::float / COUNT(*), COUNT(*),
                           SUM(l.fga), 100 * SUM(l.pts) / SUM({WC.ON_COURT_POSS})
                    {WC.PLAYER_GAME_FROM} WHERE l.seconds > 0 AND l.player_id = %s""", (pid,))
    fg, fg3, ts, mean_of_games, p36, ppg, games, fga, p100 = cur.fetchone()
    d = _q(client, dataset="player_game", columns=["fg_pct", "fg3_pct", "ts_pct", "pts"], entities=[pid],
           group_by="entity")
    (w,) = d["rows"]
    assert w["fg_pct"] == pytest.approx(fg, abs=5e-5) and w["fg3_pct"] == pytest.approx(fg3, abs=5e-5)
    assert w["ts_pct"] == pytest.approx(float(ts), abs=5e-5) and w["pts"] == pytest.approx(ppg, abs=5e-5)
    assert abs(w["fg_pct"] - mean_of_games) > 0.001          # not the mean of the games' percentages
    assert w["n_games"] == games and w["n"]["fg_pct"] == fga
    for per, expect in (("per36", p36), ("per100", p100)):
        (w,) = _q(client, dataset="player_game", columns=["pts"], entities=[pid], group_by="entity", per=per)["rows"]
        assert w["pts"] == pytest.approx(float(expect), abs=5e-4), per
    # The season table over the same seasons: attempt-weighted (made / attempted), close to the game lines.
    cur.execute("""SELECT SUM(fg_pct * fga * gp) / SUM(fga * gp), AVG(fg_pct) FROM player_season_stats
                   WHERE player_id = %s AND season BETWEEN 2021 AND 2026""", (pid,))
    weighted, plain = cur.fetchone()
    (s,) = _q(client, dataset="player_season", columns=["fg_pct"], entities=[pid], group_by="entity",
              season_from=2021, season_to=2026)["rows"]
    assert s["fg_pct"] == pytest.approx(weighted, abs=5e-5) and s["n_rows"] == 6
    assert s["fg_pct"] == pytest.approx(fg, abs=0.002) and weighted != plain


def test_team_games_add_up_to_team_seasons(client):
    """Wins from the final scores = Basketball-Reference's for every team-season 2009-10 on."""
    games = _q(client, dataset="team_game", columns=["wins", "games"], group_by=["entity", "season"], limit=5000)
    seasons = _q(client, dataset="team_season", columns=["w", "g"], season_from=2010, limit=5000)
    by_game = {(r["franchise"], r["season"]): (r["wins"], r["games"]) for r in games["rows"]}
    by_season = {(r["franchise"], r["season"]): (r["w"], r["g"]) for r in seasons["rows"]}
    assert len(by_game) == len(by_season) == 510
    assert by_game == by_season
    # NJN's games count for the Nets franchise.
    d = _q(client, dataset="team_game", columns=["games"], entities=["BKN"], season_from=2010, season_to=2013,
           group_by=["team"], sort=[{"key": "team", "dir": "asc"}])
    assert [(r["team"], r["games"]) for r in d["rows"]] == [("BKN", 82), ("NJN", 230)]
