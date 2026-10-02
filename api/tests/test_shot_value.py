"""
test_shot_value.py
===================
Guards round 6 step 8, Shot Value Added: scripts/build_shot_value.py's tables
(shot_value_*), the shooter-aware targets scripts/paper_xrapm.py adds and the
xrapm_lf_* / xrapm_sa_* models scripts/paper_eval.py and paper_tests.py score.

  * shot_value_shots prices every regular-season chart shot from 2020-21 on once,
    with the protocol's game folds, and the prices are pre-game: every shot of a
    shooter on one date carries the same skill term, and on his first date of a
    season that term is exactly the skill carried into the season;
  * nothing priced was used to choose anything: the hyperparameters rest on
    2010-11 to 2019-20 and the location models price seasons they never saw;
  * shot_value_lib reproduces the stored prices from the stored season-start
    skills (one season and class, recomputed here);
  * shot_value_added reconciles with the shots and the game lines, and its parts
    add up (total = skill + beyond; Shot Value Added = FG + FT skill);
  * the shooter-aware price beats the shooter-blind one on log loss in every
    season (the headline; a rebuild that breaks it must change the README);
  * paper_xrapm_stints carries the two targets and twenty per-fold targets, the
    round-5 columns untouched; paper_eval scores the four models in every phase
    with hyperparameters chosen on the tune seasons; paper_eval_tests holds the
    comparisons.

Skips when the database is unreachable or the tables are not built.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_shot_value.py
"""

import os
import sys

import numpy as np
import pandas as pd
import psycopg2
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _d in (os.path.join(_ROOT, "api"), os.path.join(_ROOT, "scripts")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from db_config import DB_CONFIG  # noqa: E402

TUNE_SPAN = "2020-21 to 2023-24"
NEW_MODELS = ("xrapm_lf_single", "xrapm_lf_prior", "xrapm_sa_single", "xrapm_sa_prior")


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")


@pytest.fixture(scope="module")
def conn():
    c = psycopg2.connect(**DB_CONFIG)
    c.set_session(readonly=True, autocommit=True)
    cur = c.cursor()
    cur.execute("SELECT to_regclass('shot_value_shots'), to_regclass('shot_value_added'), to_regclass('shot_value_validation')")
    if any(v is None for v in cur.fetchone()):
        c.close()
        pytest.skip("Shot Value Added not built (scripts/build_shot_value.py)")
    yield c
    c.close()


def q(conn, sql, args=None):
    cur = conn.cursor()
    cur.execute(sql, args)
    return cur.fetchall()


@needs_db
def test_every_chart_shot_is_priced_once_with_the_protocol_folds(conn):
    import build_rapm as R
    (n_shots,), = q(conn, "SELECT count(*) FROM shot_xfg")
    (n, lo, hi, seasons), = q(conn, "SELECT count(*), min(least(p_blind, p_lf, p_sa)), max(greatest(p_blind, p_lf, p_sa)), "
                                    "array_agg(DISTINCT season ORDER BY season) FROM shot_value_shots")
    assert n == n_shots and 0 < lo and hi < 1
    assert list(seasons) == [2021, 2022, 2023, 2024, 2025, 2026]
    (missing,), = q(conn, "SELECT count(*) FROM shot_xfg x WHERE NOT EXISTS (SELECT 1 FROM shot_value_shots s WHERE s.shot_id = x.shot_id)")
    assert missing == 0
    # the stored fold is build_rapm.game_fold of the game's ESPN id (a sample of games)
    rows = q(conn, """SELECT DISTINCT ON (g.espn_id) g.espn_id, s.fold FROM shot_value_shots s JOIN player_shots p ON p.id = s.shot_id
                      JOIN game_scores g ON g.game_id = p.game_id ORDER BY g.espn_id LIMIT 300""")
    assert all(R.game_fold(f"espn_{e}") == f for e, f in rows)


@needs_db
def test_prices_are_pre_game(conn):
    import shot_value_lib as V
    d = pd.read_sql_query("""SELECT s.season, s.player_id, s.game_date, s.cls, s.p_lf, s.p_sa FROM shot_value_shots s
                             WHERE s.season = 2025""", conn)
    d["term"] = V.logit(d.p_sa.to_numpy(float)) - V.logit(d.p_lf.to_numpy(float))
    # one skill term per shooter, date and class: no shot's own outcome (or a later shot's in the same game) is in it
    spread = d.groupby(["player_id", "game_date", "cls"]).term.agg(lambda x: x.max() - x.min())
    assert spread.max() < 2e-4, spread.max()
    # on his first date of the season the term is the skill carried in (shot_value_states.pre_mean)
    first = d[d.game_date == d.groupby(["player_id", "cls"]).game_date.transform("min")].groupby(["player_id", "cls"]).term.mean().reset_index()
    st = pd.read_sql_query("SELECT player_id, cls, pre_mean FROM shot_value_states WHERE season = 2025 AND cls <> 'ft'", conn)
    st["cls"] = st.cls.map({c: i for i, c in enumerate(V.FG_CLASSES)})
    m = first.merge(st, on=["player_id", "cls"])
    assert len(m) == len(first) > 400
    assert np.max(np.abs(m.term - m.pre_mean)) < 2e-4


@needs_db
def test_nothing_priced_chose_anything(conn):
    import shot_value_lib as V
    fit = {c: (on, detail) for c, on, detail in q(conn, "SELECT cls, estimated_on, detail FROM shot_value_fit")}
    assert set(fit) == set(V.CLASSES) | {"models"}
    for c in V.CLASSES:
        assert fit[c][0] == "2010-11 to 2019-20"
        assert max(V.FIT_SEASONS) < min(V.PRICED)
        starts = fit[c][1]["starts"]
        best = min(s["n2ll"] for s in starts)
        assert starts[-1]["start"] == "polish from the best" and abs(starts[-1]["n2ll"] - best) < 0.01, c
        # every start reaches the optimum, or stalled where the carry-over meets its bound (the known failure: worse)
        for s in starts:
            assert abs(s["n2ll"] - best) < 1.0 or s["params"]["phi"] >= 0.995, (c, s)
    models = fit["models"][1]
    assert models["trained_from"] == "1996-97" and models["window_from"] == "2010-11"
    assert set(models["iterations"]) == {V.label(s) for s in V.PRICED} and all(len(v) == 5 for v in models["iterations"].values())


@needs_db
def test_library_reproduces_the_stored_prices(conn):
    import shot_value_lib as V
    fit = pd.read_sql_query("SELECT cls, mu0, v0, phi, q, delta_var FROM shot_value_fit WHERE cls = 'three'", conn).iloc[0]
    par = {k: float(fit[k]) for k in V.PARAMS}
    st = pd.read_sql_query("SELECT player_id, pre_mean, pre_sd FROM shot_value_states WHERE season = 2026 AND cls = 'three' ORDER BY player_id", conn)
    pm = {p: i for i, p in enumerate(st.player_id)}
    x = pd.read_sql_query("""SELECT shot_id, game_date, player_id, made, p_blind, p_lf, p_sa FROM shot_value_shots
                             WHERE season = 2026 AND cls = 2 ORDER BY shot_id""", conn)
    a = V.logit(x.p_blind.to_numpy(float))
    r = V.in_season(par, st.pre_mean.to_numpy(float), st.pre_sd.to_numpy(float) ** 2, x.player_id.map(pm).to_numpy(),
                    pd.to_datetime(x.game_date).to_numpy(), a, x.made.to_numpy(float), np.ones(len(x)), float(fit.delta_var))
    # p_blind is stored as REAL, so the recomputation starts from a rounded offset: agreement to ~1e-6
    assert np.max(np.abs(V.expit(a + r["delta"] + r["theta"]) - x.p_sa.to_numpy(float))) < 2e-6
    assert np.max(np.abs(V.expit(a + r["delta"]) - x.p_lf.to_numpy(float))) < 2e-6


@needs_db
def test_player_table_reconciles_and_its_parts_add_up(conn):
    rows = q(conn, """SELECT a.player_id, a.season, a.fga, a.pts, a.fg3a, a.fta, a.ftm, s.n, s.pts, s.fg3a
                      FROM shot_value_added a LEFT JOIN (
                          SELECT x.player_id, x.season, count(*) n,
                                 sum(CASE WHEN x.made THEN CASE WHEN x.cls = 2 THEN 3 ELSE 2 END ELSE 0 END) pts,
                                 sum((x.cls = 2)::int) fg3a
                          FROM shot_value_shots x GROUP BY 1, 2) s USING (player_id, season)""")
    for pid, season, fga, pts, fg3a, fta, ftm, n, spts, sfg3a in rows:
        assert fga == (n or 0) and pts == (spts or 0) and fg3a == (sfg3a or 0), (pid, season)
    ft = dict(((p, s), (a, m)) for p, s, a, m in q(conn, """SELECT l.player_id, l.season, sum(l.fta), sum(l.ftm) FROM player_game_lines l
                                     WHERE EXISTS (SELECT 1 FROM game_scores g WHERE 'espn_' || g.espn_id = l.game_id) GROUP BY 1, 2"""))
    for pid, season, fga, pts, fg3a, fta, ftm, *_ in rows:
        a, m = ft.get((pid, season), (0, 0))
        assert (fta, ftm) == (a, m), (pid, season)
    (bad,), = q(conn, """SELECT count(*) FROM shot_value_added WHERE abs(total_pts - skill_pts - above_pts) > 0.05
                         OR abs(sva - skill_pts - ft_skill_pts) > 0.05 OR abs(beyond - above_pts - ft_above_pts) > 0.05
                         OR abs(skill_pts - (x_aware - x_blind)) > 0.05 OR abs(total_pts - (pts - x_blind)) > 0.05""")
    assert bad == 0
    (n_q,), = q(conn, "SELECT count(*) FROM shot_value_added WHERE qualified AND rank_sva IS NOT NULL")
    (n_all,), = q(conn, "SELECT count(*) FROM shot_value_added WHERE fga >= 200")
    assert n_q == n_all > 1500


@needs_db
def test_shooter_aware_prices_beat_blind_ones_in_every_season(conn):
    rows = q(conn, """SELECT scope, cls, log_loss, d_log_loss_vs_lf, ci_hi FROM shot_value_validation
                      WHERE price = 'sa' AND cls IN ('fg', 'ft') AND length(scope) = 7""")
    assert len(rows) == 12
    for scope, cls, ll, d, hi in rows:
        assert d < 0 and hi < 0, (scope, cls, d, hi)
    yty = {(s, p): r for s, p, r in q(conn, "SELECT scope, price, corr FROM shot_value_validation WHERE cls = 'yty'")}
    assert len(yty) == 30
    for (scope, part), r in yty.items():
        if part == "skill_pts":
            assert r > yty[(scope, "above_pts")], scope       # the skill part repeats more than the beyond part


@needs_db
def test_paper_xrapm_carries_the_new_targets(conn):
    import build_rapm as R
    import shot_value_lib as V
    (bad_len, n), = q(conn, "SELECT count(*) FILTER (WHERE cardinality(fold_xpts) <> %s), count(*) FROM paper_xrapm_stints",
                      (len(V.FOLD_LAYOUT) * R.FOLDS,))
    assert bad_len == 0 and n > 290_000
    rows = q(conn, """SELECT season, sum(home_pts + away_pts), sum(home_xpts_lf + away_xpts_lf), sum(home_xpts_sa + away_xpts_sa)
                      FROM paper_xrapm_stints WHERE tracked_ok GROUP BY 1 ORDER BY 1""")
    assert len(rows) == 6
    for season, pts, lf, sa in rows:
        assert abs(float(lf) / float(pts) - 1) < 0.02 and abs(float(sa) / float(pts) - 1) < 0.02, (season, pts, lf, sa)
    versions = {v for v, in q(conn, "SELECT DISTINCT version FROM paper_xrapm_fits")}
    assert versions == {"single", "prior", "lf_single", "lf_prior", "sa_single", "sa_prior"}
    meta = {k for k, in q(conn, "SELECT DISTINCT key FROM paper_xrapm_meta WHERE key LIKE 'aware_%%'")}
    assert {"aware_fg_fallback", "aware_ft_no_pid", "aware_recompute_worst"} <= meta
    (worst,), = q(conn, "SELECT value FROM paper_xrapm_meta WHERE key = 'aware_recompute_worst'")
    assert worst < 1e-6


@needs_db
def test_protocol_scores_the_new_models(conn):
    have = {}
    for task, phase, model in q(conn, "SELECT DISTINCT task, phase, model FROM paper_eval_metrics WHERE model LIKE 'xrapm%%'"):
        have.setdefault((task, phase), set()).add(model)
    for phase in ("tune", "validate", "test"):
        for task in ("impact_next", "impact_heldout", "impact_reliability"):
            assert set(NEW_MODELS) <= have[(task, phase)], (task, phase)
    ch = {(m, p): on for m, p, on in q(conn, "SELECT model, parameter, chosen_on FROM paper_eval_choices WHERE task = 'impact' AND model LIKE 'xrapm%%'")}
    for v in ("lf", "sa"):
        assert ch[(f"xrapm_{v}_single", "lambda")] == TUNE_SPAN and ch[(f"xrapm_{v}_prior", "prior_scale")] == TUNE_SPAN
    for phase in ("validate", "test"):
        for model in NEW_MODELS:
            rows = np.array(q(conn, """SELECT pred, actual FROM paper_eval_predictions WHERE task = 'impact_next' AND phase = %s
                                       AND model = %s AND variant = '' ORDER BY unit_id""", (phase, model)), float)
            (v, n), = q(conn, """SELECT value, n FROM paper_eval_metrics WHERE task = 'impact_next' AND phase = %s AND model = %s
                                 AND variant = '' AND metric = 'game_rmse'""", (phase, model))
            assert n == len(rows) and 1200 <= n <= 1230
            assert abs(np.sqrt(np.mean((rows[:, 0] - rows[:, 1]) ** 2)) - v) < 1e-9
    tests = {(t, ph, a, b) for t, ph, a, b in q(conn, "SELECT task, phase, model_a, model_b FROM paper_eval_tests WHERE model_a LIKE 'xrapm_%%a%%'")}
    for phase in ("tune", "validate", "test"):
        for a, b in (("xrapm_sa_single", "rapm_single"), ("xrapm_sa_prior", "rapm_prior"), ("xrapm_sa_single", "xrapm_single"),
                     ("xrapm_sa_single", "xrapm_lf_single"), ("xrapm_sa_prior", "bpm")):
            assert ("impact_next", phase, a, b) in tests, (phase, a, b)
        assert ("impact_heldout", phase, "xrapm_sa_single", "rapm_single") in tests
        assert ("impact_reliability", phase, "xrapm_sa_prior", "rapm_prior") in tests


@needs_db
def test_api_serves_the_tab_and_the_rapm_version(conn):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import rapm, shot_value
    app = FastAPI()
    app.include_router(shot_value.router)
    app.include_router(rapm.router)
    c = TestClient(app)
    o = c.get("/shots/shot-value/options").json()
    assert o["seasons"] == [2021, 2022, 2023, 2024, 2025, 2026] and len(o["params"]) == 4
    assert all(p["veteran_sd_pp"] > 0 and 0 < p["carry"] < 1 for p in o["params"])
    d = c.get("/shots/shot-value", params={"season": 2026}).json()
    assert d["n_qualified"] == len(d["players"]) > 300 and d["league"]["fga"] > 200_000
    top = max(d["players"], key=lambda r: r["sva"])
    assert abs(top["sva"] - (top["skill_pts"] + top["ft_skill_pts"])) < 0.2
    assert c.get("/shots/shot-value", params={"season": 2015}).status_code == 404
    curry = c.get("/shots/shot-value/player/201939").json()
    assert len(curry["seasons"]) == 6 and all(t["pre"] > 2 for t in curry["track"] if t["cls"] == "three")
    r = c.get("/rapm", params={"version": "shotaware", "season": 2026}).json()
    assert r["shotaware"]["kind"] == "prior" and r["players"] and r["noise"]["ci_excludes_zero"] is None
    assert {m["model"] for m in r["shotaware"]["protocol"]["scores"]} >= {"xrapm_sa_prior", "xrapm_sa_single", "rapm_prior", "bpm"}
    assert c.get("/rapm", params={"version": "shotaware", "kind": "filtered", "season": 2026}).json()["shotaware"]["kind"] == "prior"
    assert c.get("/rapm", params={"version": "shotaware", "kind": "smoothed"}).status_code == 400
