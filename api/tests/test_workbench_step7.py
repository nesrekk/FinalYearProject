"""
test_workbench_step7.py
=======================
Guards round 7 step 7, more data in the Workbench (api/workbench_catalogue.py,
api/routers/workbench.py):

  * model ratings on player seasons (RAPM in every version, the Rating
    Tracker, shot-aware xRAPM, shot value) equal their source tables, with the
    stored intervals, and the joins behind them cost nothing when unused
    (Postgres drops them: no model table in the plan of a box-score query);
  * on/off is computed from the corrected on-floor points: equal to the same
    sums written here in numpy and, row for row, to player_on_off (the On/Off
    page, rebuilt on the same sources 2026-10-03); its interval's
    game-clustered SE reproduces a numpy game bootstrap and the page's stored
    2,000-resample bootstrap SE;
  * lineups and pairs equal lineup_seasons / pair_seasons, a player set picks
    exactly the units with any / all of its players, and the possessions floor
    holds;
  * team possessions equal possession_seasons, and combined rates are summed
    points over summed possessions;
  * projections equal player_projections and the backtest table, and the 80%
    ranges cover about 80% of what happened (the build's own check);
  * the aging overlay is the Aging Curves page's curve and per-player path;
  * refusals: players on a dataset without them, a possessions floor where
    there are no possessions, non-integer player ids.

Skips when the database is unreachable.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_workbench_step7.py
"""

import os
import sys

import numpy as np
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

JOKIC, SGA, MURRAY, LEBRON = 203999, 1628983, 1627750, 2544


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import impact_api
    return TestClient(impact_api.app)


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    yield conn.cursor()
    conn.close()


def _q(client, **spec):
    r = client.post("/workbench/query", json=spec)
    assert r.status_code == 200, r.text
    return r.json()


def _one(rows, **match):
    hit = [r for r in rows if all(r.get(k) == v for k, v in match.items())]
    assert len(hit) == 1, (match, len(hit))
    return hit[0]


# ─── model ratings on player seasons ───────────────────────────────────────

def test_model_ratings_equal_their_tables(client, cur):
    cols = ["rapm", "orapm", "rapm_prior", "rapm_multi", "tracker", "tracker_smoothed", "xrapm_sa_prior", "sva",
            "sva_per100", "shot_pts_above", "shot_beyond"]
    d = _q(client, dataset="player_season", entities=[JOKIC, SGA], columns=cols, season_from=2026, season_to=2026)
    meta = {c["key"]: c for c in d["columns"]}
    assert meta["rapm"]["interval"] == "95% interval" and meta["rapm"]["method"] == "rapm"
    # The response names the model tables it read, and a box-score query doesn't.
    assert {"player_rapm", "player_rating_tracker", "shot_value_added"} <= set(d["_source"]["tables"])
    box = _q(client, dataset="player_season", entities=[JOKIC], columns=["pts"], season_from=2026)
    assert "player_rapm" not in box["_source"]["tables"]
    assert meta["sva"]["method"] == "shotmaking" and meta["xrapm_sa_prior"]["interval"] is None
    for row in d["rows"]:
        pid = row["player_id"]
        for key, (table, where) in {"rapm": ("player_rapm", "version = 'single'"),
                                    "rapm_prior": ("player_rapm", "version = 'prior'"),
                                    "rapm_multi": ("player_rapm", "version = 'multi'"),
                                    "tracker": ("player_rating_tracker", "kind = 'filtered'"),
                                    "tracker_smoothed": ("player_rating_tracker", "kind = 'smoothed'")}.items():
            cur.execute(f"SELECT rapm, rapm_ci_low, rapm_ci_high, poss FROM {table} WHERE {where} "
                        "AND player_id = %s AND season = 2026", (pid,))
            v, lo, hi, poss = cur.fetchone()
            assert row[key] == pytest.approx(v, abs=1e-3) and row["n"][key] == pytest.approx(poss, abs=0.1)
            assert row["ci"][key] == pytest.approx([lo, hi], abs=1e-3)
        cur.execute("SELECT orapm, orapm_se FROM player_rapm WHERE version = 'single' AND player_id = %s "
                    "AND season = 2026", (pid,))
        o, se = cur.fetchone()
        assert row["ci"]["orapm"] == pytest.approx([o - 1.959964 * se, o + 1.959964 * se], abs=1e-3)
        cur.execute("SELECT xrapm FROM paper_xrapm_players WHERE version = 'sa_prior' AND player_id = %s "
                    "AND season = 2026", (pid,))
        assert row["xrapm_sa_prior"] == pytest.approx(cur.fetchone()[0], abs=1e-3) and "xrapm_sa_prior" not in row["ci"]
        cur.execute("SELECT sva, total_pts + ft_total_pts, beyond, fga + fta FROM shot_value_added "
                    "WHERE player_id = %s AND season = 2026", (pid,))
        sva, above, beyond, shots = cur.fetchone()
        assert row["sva"] == pytest.approx(sva, abs=1e-2) and row["shot_pts_above"] == pytest.approx(above, abs=1e-2)
        assert row["shot_beyond"] == pytest.approx(beyond, abs=1e-2)
        assert row["sva_per100"] == pytest.approx(100 * sva / shots, abs=1e-3) and row["n"]["sva"] == shots
    # Combined over seasons, shot value adds up; a rating can't be combined.
    d = _q(client, dataset="player_season", entities=[JOKIC], columns=["sva"], season_from=2021, group_by="entity")
    cur.execute("SELECT SUM(sva::numeric) FROM shot_value_added v JOIN player_season_stats s USING (player_id, season) "
                "WHERE player_id = %s", (JOKIC,))
    assert d["rows"][0]["sva"] == pytest.approx(float(cur.fetchone()[0]), abs=0.05)
    r = client.post("/workbench/query", json={"dataset": "player_season", "columns": ["rapm"], "group_by": "entity"})
    assert r.status_code == 400 and "can't be combined" in r.json()["detail"]
    # Grouped rows carry no interval.
    d = _q(client, dataset="player_season", columns=["rapm", "pts"], season_from=2026, group_by="none", limit=5)
    assert all("ci" in row for row in d["rows"])


def test_model_joins_cost_nothing_when_unused(cur):
    """Every model join is on a unique key, so Postgres removes the ones a query doesn't read."""
    cur.execute(f"EXPLAIN SELECT s.pts, fga * gp {WC.PLAYER_SEASON.from_sql} WHERE s.season = 2026")
    plan = " ".join(r[0] for r in cur.fetchall())
    for t in ("player_rapm", "player_rating_tracker", "paper_xrapm_players", "shot_value_added", "player_bio"):
        assert t not in plan, t
    cur.execute(f"EXPLAIN SELECT r1.rapm, v.sva {WC.PLAYER_SEASON.from_sql} WHERE s.season = 2026")
    plan = " ".join(r[0] for r in cur.fetchall())
    assert "player_rapm" in plan and "shot_value_added" in plan and "player_rating_tracker" not in plan


# ─── on/off from the corrected points ──────────────────────────────────────

ONOFF_GAMES_SQL = """
    SELECT l.player_id, l.season, l.team_abbreviation, o.pts_for, o.pts_against,
           ((l.tm_fga + 0.44 * l.tm_fta - l.tm_oreb + l.tm_tov) + (l.op_fga + 0.44 * l.op_fta - l.op_oreb + l.op_tov)) / 2.0,
           gs.pts_for, gs.pts_against, tt.poss
    FROM player_game_lines l
    JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
    JOIN game_scores gs ON gs.game_id = f.game_id AND gs.team_abbreviation = f.team_abbreviation
    JOIN team_game_totals tt ON tt.game_id = l.game_id AND tt.team_abbreviation = l.team_abbreviation
    JOIN player_game_onfloor o ON o.player_id = l.player_id AND o.game_id = l.game_id AND o.game_ok
    WHERE l.seconds > 0 AND l.player_id = ANY(%s)
    ORDER BY l.player_id, l.season, l.team_abbreviation, l.game_id"""


def _onoff_arrays(cur, ids):
    cur.execute(ONOFF_GAMES_SQL, (ids,))
    out = {}
    for r in cur.fetchall():
        out.setdefault((r[0], r[1], r[2]), []).append([float(x) for x in r[3:]])
    return {k: np.array(v) for k, v in out.items()}


def _net_diff(a):
    """a: per-game [pf_on, pa_on, poss_on, pf_team, pa_team, poss_team, ...] -> on - off net, and its parts."""
    on = a[:, 0] - a[:, 1]
    off = np.maximum(0, a[:, 3] - a[:, 0]) - np.maximum(0, a[:, 4] - a[:, 1])
    poss_off = np.maximum(0, a[:, 5] - a[:, 2])
    return on, a[:, 2], off, poss_off


def _lin_se(on, b, off, e):
    A, B, C, E = on.sum(), b.sum(), off.sum(), e.sum()
    z = 100 * ((on - A / B * b) / B - (off - C / E * e) / E)
    return float(np.sqrt(len(z) / (len(z) - 1) * (z ** 2).sum()))


def test_on_off_equals_numpy_on_the_corrected_points(client, cur):
    ids = [JOKIC, SGA, MURRAY]
    arrays = _onoff_arrays(cur, ids)
    d = _q(client, dataset="player_onoff", entities=ids, columns=["on_off_net", "net_on", "net_off", "games", "pm_on"],
           season_from=2021, limit=100)
    assert d["rows"] and len(d["rows"]) == len(arrays)
    for row in d["rows"]:
        a = arrays[(row["player_id"], row["season"], row["team"])]
        on, b, off, e = _net_diff(a)
        assert row["games"] == len(a) and row["pm_on"] == pytest.approx(on.sum())
        assert row["net_on"] == pytest.approx(100 * on.sum() / b.sum(), abs=1e-3)
        assert row["net_off"] == pytest.approx(100 * off.sum() / e.sum(), abs=1e-3)
        est = 100 * (on.sum() / b.sum() - off.sum() / e.sum())
        assert row["on_off_net"] == pytest.approx(est, abs=1e-3)
        se = _lin_se(on, b, off, e)
        assert row["ci"]["on_off_net"] == pytest.approx([est - 1.959964 * se, est + 1.959964 * se], abs=2e-3)
    # Combined over seasons: pooled on minus pooled off, not a mean of differences.
    d = _q(client, dataset="player_onoff", entities=[JOKIC], columns=["on_off_net"], season_from=2021, group_by="entity")
    parts = [_net_diff(a) for k, a in arrays.items() if k[0] == JOKIC]
    on, b, off, e = (np.concatenate([p[i] for p in parts]) for i in range(4))
    assert d["rows"][0]["on_off_net"] == pytest.approx(100 * (on.sum() / b.sum() - off.sum() / e.sum()), abs=1e-3)
    assert "ci" not in d["rows"][0]


def test_on_off_interval_matches_a_game_bootstrap(cur):
    """The closed-form SE equals a game bootstrap within sampling error, on the corrected points."""
    arrays = _onoff_arrays(cur, [JOKIC, SGA])
    rng = np.random.default_rng(7)
    ratios = []
    for a in arrays.values():
        if len(a) < 40:
            continue
        on, b, off, e = _net_diff(a)
        idx = rng.integers(0, len(a), size=(4000, len(a)))
        boot = 100 * (on[idx].sum(1) / b[idx].sum(1) - off[idx].sum(1) / e[idx].sum(1))
        ratios.append(_lin_se(on, b, off, e) / boot.std(ddof=1))
    assert len(ratios) >= 8 and all(0.88 < r < 1.12 for r in ratios), ratios


def test_on_off_table_equals_the_workbench(client, cur):
    """player_on_off (scripts/build_player_on_off.py, the On/Off page) and the Workbench's player_onoff read the
    same sources: every row agrees to the table's rounding (2 decimals for ratings, 1 for possessions/minutes)."""
    cols = ["games", "minutes_on", "poss_on", "poss_off", "pm_on", "net_on", "net_off", "on_off_net", "on_off_ortg",
            "on_off_drtg"]
    d = _q(client, dataset="player_onoff", entities="all", columns=cols, limit=5000)
    cur.execute("""SELECT player_id, season, team_abbreviation, games, minutes_on, poss_on, poss_off,
                          pts_for_on - pts_against_on, net_on, net_off, on_off_net, on_off_ortg, on_off_drtg
                   FROM player_on_off""")
    table = {(r[0], r[1], r[2]): r[3:] for r in cur.fetchall()}
    assert len(table) >= 3600 and len(d["rows"]) == len(table)
    for row in d["rows"]:
        g, mins, p_on, p_off, pm, *rates = table[(row["player_id"], row["season"], row["team"])]
        assert row["games"] == g and row["pm_on"] == pm
        assert row["minutes_on"] == pytest.approx(mins, abs=0.051)
        assert row["poss_on"] == pytest.approx(p_on, abs=0.051) and row["poss_off"] == pytest.approx(p_off, abs=0.051)
        for key, v in zip(("net_on", "net_off", "on_off_net", "on_off_ortg", "on_off_drtg"), rates):
            assert (row[key] is None) == (v is None), key
            if v is not None:
                assert row[key] == pytest.approx(v, abs=0.0051), key


def test_on_off_interval_reproduces_the_on_off_pages_bootstrap(cur):
    """The closed-form game-clustered SE gives the SE that build_player_on_off.py stored from 2,000 game
    resamples of the same games (median ratio ~1.01 on its old points, measured 2026-10-03, and again on the
    corrected points it reads since)."""
    cur.execute("""SELECT player_id, season, team_abbreviation, on_off_se FROM player_on_off
                   WHERE on_off_se IS NOT NULL AND minutes_on >= 1000 ORDER BY player_id, season LIMIT 150""")
    stored = {(p, s, t): se for p, s, t, se in cur.fetchall()}
    arrays = _onoff_arrays(cur, sorted({k[0] for k in stored}))
    ratios = []
    for k, se in stored.items():
        on, b, off, e = _net_diff(arrays[k])
        ratios.append(_lin_se(on, b, off, e) / se)
    assert len(ratios) > 100
    assert 0.97 < float(np.median(ratios)) < 1.04 and np.percentile(ratios, 95) < 1.15, np.percentile(ratios, [5, 50, 95])


# ─── lineups and pairs ─────────────────────────────────────────────────────

def test_lineups_equal_the_table_and_pick_by_players(client, cur):
    d = _q(client, dataset="lineup_season", players=[JOKIC], columns=["net_rating", "poss", "minutes", "efg_pct"],
           season_from=2026, season_to=2026, min_poss=100, limit=200)
    assert d["rows"] and d["notes"][0].startswith("Only five-man lineups with any of the 1 chosen players")
    for row in d["rows"]:
        assert JOKIC in row["player_ids"] and row["poss"] >= 100 and len(row["player_names"]) == 5
        assert "Nikola Jokić" in row["player_names"] and row["franchise"] == "DEN"
        cur.execute("""SELECT net_rating, poss, (fgm + 0.5 * fg3m) / fga FROM lineup_seasons
                       WHERE season = 2026 AND team_abbreviation = %s AND player_ids = %s""",
                    (row["team"], row["player_ids"]))
        net, poss, efg = cur.fetchone()
        # The table stores ratings rounded to 0.01.
        assert row["net_rating"] == pytest.approx(net, abs=0.006) and row["poss"] == pytest.approx(poss, abs=0.05)
        assert row["efg_pct"] == pytest.approx(float(efg), abs=1e-4)
    cur.execute("SELECT COUNT(*) FROM lineup_seasons WHERE season = 2026 AND %s = ANY(player_ids) AND poss >= 100",
                (JOKIC,))
    assert d["n"]["matched"] == cur.fetchone()[0]
    # "all": every chosen player in the unit; the same five over several seasons pool their points.
    d = _q(client, dataset="lineup_season", players=[JOKIC, MURRAY], players_match="all", columns=["net_rating", "poss"],
           season_from=2021, group_by="lineup", min_poss=1000, limit=50)
    assert d["rows"]
    for row in d["rows"]:
        assert {JOKIC, MURRAY} <= set(row["player_ids"])
        cur.execute("SELECT SUM(pts_for - pts_against), SUM(poss) FROM lineup_seasons WHERE player_ids = %s",
                    (row["player_ids"],))
        pm, poss = cur.fetchone()
        assert row["poss"] == pytest.approx(poss, abs=0.1) and row["poss"] >= 1000
        assert row["net_rating"] == pytest.approx(100 * pm / poss, abs=1e-3)


def test_pairs(client, cur):
    d = _q(client, dataset="pair_season", players=[JOKIC, MURRAY], players_match="all",
           columns=["net_rating", "poss", "games"], season_from=2021)
    cur.execute("""SELECT season, net_rating, poss, games FROM pair_seasons
                   WHERE player_a = LEAST(%s, %s) AND player_b = GREATEST(%s, %s) ORDER BY season""",
                (JOKIC, MURRAY, JOKIC, MURRAY))
    want = cur.fetchall()
    assert len(d["rows"]) == len(want) >= 4
    for season, net, poss, games in want:
        row = _one(d["rows"], season=season)
        assert row["net_rating"] == pytest.approx(net, abs=0.006) and row["games"] == games
        assert {row["player_a_name"], row["player_b_name"]} == {"Nikola Jokić", "Jamal Murray"}
    d = _q(client, dataset="pair_season", players=[JOKIC], columns=["poss"], season_from=2026, min_poss=250, limit=100)
    assert d["rows"] and all(JOKIC in (r["player_a"], r["player_b"]) and r["poss"] >= 250 for r in d["rows"])


# ─── team possessions ──────────────────────────────────────────────────────

def test_team_possessions_equal_the_table(client, cur):
    keys = ["ppp", "ppp_steal", "share_steal", "d_ppp", "trans_share", "net_ppp"] + [f"share_{k}" for k, _ in WC.POSS_STARTS]
    d = _q(client, dataset="team_possessions", entities=["DEN"], columns=keys, season_from=2021)
    assert len(d["rows"]) == 6
    for row in d["rows"]:
        cur.execute("""SELECT start_type, poss, pts, d_poss, d_pts, trans_poss, timed_poss FROM possession_seasons
                       WHERE team = 'DEN' AND season = %s""", (row["season"],))
        by = {r[0]: r[1:] for r in cur.fetchall()}
        poss, pts, dposs, dpts, trans, timed = by["all"]
        assert row["ppp"] == pytest.approx(pts / poss, abs=1e-4) and row["d_ppp"] == pytest.approx(dpts / dposs, abs=1e-4)
        assert row["ppp_steal"] == pytest.approx(by["steal"][1] / by["steal"][0], abs=1e-4)
        assert row["share_steal"] == pytest.approx(by["steal"][0] / poss, abs=1e-4)
        assert row["trans_share"] == pytest.approx(trans / timed, abs=1e-4)
        assert row["net_ppp"] == pytest.approx(pts / poss - dpts / dposs, abs=1e-4)
        # The eight main starts are nearly every possession (held balls and gaps are the rest).
        assert 0.99 < sum(row[f"share_{k}"] for k, _ in WC.POSS_STARTS) <= 1.0 + 1e-9
    # Combined: summed points over summed possessions.
    d = _q(client, dataset="team_possessions", entities=["DEN"], columns=["ppp_steal", "net_ppp"], season_from=2021,
           group_by="entity")
    cur.execute("""SELECT SUM(pts) FILTER (WHERE start_type = 'steal')::float / SUM(poss) FILTER (WHERE start_type = 'steal'),
                          SUM(pts) FILTER (WHERE start_type = 'all')::float / SUM(poss) FILTER (WHERE start_type = 'all')
                          - SUM(d_pts) FILTER (WHERE start_type = 'all')::float / SUM(d_poss) FILTER (WHERE start_type = 'all')
                   FROM possession_seasons WHERE team = 'DEN'""")
    steal, net = cur.fetchone()
    assert d["rows"][0]["ppp_steal"] == pytest.approx(steal, abs=1e-4) and d["rows"][0]["net_ppp"] == pytest.approx(net, abs=1e-4)


# ─── projections ───────────────────────────────────────────────────────────

def test_projections_equal_their_tables_and_ranges_cover(client, cur):
    d = _q(client, dataset="player_projection", entities=[JOKIC, LEBRON], columns=["proj_pts", "proj_bpm", "proj_fg3_pct"],
           season_from=2027, season_to=2027)
    for row in d["rows"]:
        for stat in ("pts", "bpm", "fg3_pct"):
            cur.execute("SELECT projection, lo, hi FROM player_projections WHERE player_id = %s AND season = 2027 "
                        "AND stat = %s", (row["player_id"], stat))
            p, lo, hi = cur.fetchone()
            assert row[f"proj_{stat}"] == pytest.approx(p, abs=1e-4)
            assert row["ci"][f"proj_{stat}"] == pytest.approx([lo, hi], abs=1e-4)
    meta = {c["key"]: c for c in d["columns"]}
    assert meta["proj_pts"]["interval"] == "80% range" and meta["proj_pts"]["method"] == "projections"
    d = _q(client, dataset="player_projection", entities=[JOKIC], columns=["proj_pts", "act_pts", "miss_pts", "in_pts"],
           season_from=2024, season_to=2024)
    cur.execute("SELECT projection, actual, lo, hi FROM projection_backtest_rows WHERE player_id = %s AND season = 2024 "
                "AND stat = 'pts'", (JOKIC,))
    p, a, lo, hi = cur.fetchone()
    row = d["rows"][0]
    assert row["act_pts"] == pytest.approx(a, abs=1e-4) and row["miss_pts"] == pytest.approx(a - p, abs=1e-4)
    assert row["in_pts"] == (1.0 if lo <= a <= hi else 0.0)
    # Coverage of the 80% ranges over the whole backtest, combined: near 80% (built leave-one-season-out).
    d = _q(client, dataset="player_projection", columns=["in_pts", "in_bpm", "in_ts_pct", "miss_pts"], season_to=2026,
           group_by="all")
    row = d["rows"][0]
    for k in ("in_pts", "in_bpm", "in_ts_pct"):
        assert 0.74 < row[k] < 0.86, (k, row[k])
    cur.execute("SELECT AVG((actual BETWEEN lo AND hi)::int), AVG(actual - projection) FROM projection_backtest_rows "
                "WHERE stat = 'pts'")
    cov, bias = cur.fetchone()
    assert row["in_pts"] == pytest.approx(float(cov), abs=1e-4) and row["miss_pts"] == pytest.approx(float(bias), abs=1e-4)


# ─── aging overlay ─────────────────────────────────────────────────────────

def test_aging_overlay_is_the_aging_pages(client):
    r = client.post("/workbench/aging", json={"stat": "pts", "player_ids": [JOKIC, LEBRON], "era": "all"})
    assert r.status_code == 200
    d = r.json()
    page = client.get("/aging/curves", params={"stat": "pts", "era": "all"}).json()
    assert [p["age"] for p in d["curve"]] == [p["age"] for p in page["points"]]
    for mine, theirs in zip(d["curve"], page["points"]):
        # The overlay rounds to 6 significant digits.
        assert mine["level"] == pytest.approx(theirs["level"], rel=1e-5, abs=1e-6)
        ref = page["summary"]["ref_level"]
        assert mine["lo"] == pytest.approx(ref + theirs["ci_lo"], rel=1e-5, abs=1e-6) and mine["lo"] <= mine["level"] + 1e-9 <= mine["hi"] + 2e-9
    for p in d["players"]:
        theirs = client.get("/aging/player", params={"player_id": p["player_id"], "stat": "pts", "era": "all"}).json()
        assert p["offset"] == pytest.approx(theirs["offset"], rel=1e-5)
        assert [x["vs_league"] for x in p["seasons"]] == pytest.approx([x["vs_league"] for x in theirs["seasons"]],
                                                                        rel=1e-5, abs=1e-6)
    assert {p["player_name"] for p in d["players"]} == {"Nikola Jokić", "LeBron James"}
    assert client.post("/workbench/aging", json={"stat": "rapm", "player_ids": [JOKIC]}).status_code == 400
    assert client.post("/workbench/aging", json={"stat": "pts", "player_ids": [JOKIC], "era": "x"}).status_code == 400
    r = client.post("/workbench/aging", json={"stat": "pts", "player_ids": [JOKIC, 999999999]})
    assert r.status_code == 200 and r.json()["missing"] == [999999999]
    cat = client.get("/workbench/catalogue").json()["aging"]
    assert {s["key"] for s in cat["stats"]} >= {"pts", "bpm", "ts_pct"} and cat["eras"][0]["key"] == "all"


# ─── refusals ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec, status, words", [
    ({"dataset": "player_season", "columns": ["pts"], "players": [JOKIC]}, 400, "one player a row"),
    ({"dataset": "player_projection", "columns": ["proj_pts"], "min_poss": 100}, 400, "no possessions"),
    ({"dataset": "lineup_season", "columns": ["poss"], "players": ["Jokic"]}, 422, None),
    ({"dataset": "lineup_season", "columns": ["poss"], "players": [1.5]}, 422, None),
    ({"dataset": "lineup_season", "columns": ["poss"], "players": [JOKIC], "players_match": "some"}, 422, None),
    ({"dataset": "lineup_season", "columns": ["poss"], "players": [-3]}, 400, "positive"),
    ({"dataset": "lineup_season", "columns": ["poss"], "filters": [{"key": "players", "op": "eq", "value": [1]}]}, 400,
     "op must be"),
])
def test_refusals(client, spec, status, words):
    r = client.post("/workbench/query", json=spec)
    assert r.status_code == status, r.text
    if words:
        assert words in r.json()["detail"]


def test_players_filter_as_a_field_filter(client):
    d = _q(client, dataset="lineup_season", columns=["poss"], season_from=2026,
           filters=[{"key": "players", "op": "all", "value": [JOKIC, MURRAY]}], limit=500)
    assert d["rows"] and all({JOKIC, MURRAY} <= set(r["player_ids"]) for r in d["rows"])
