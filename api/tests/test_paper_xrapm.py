"""
test_paper_xrapm.py
====================
Guards round 5 step 4, expected-points RAPM: scripts/build_shot_making.py's
per-shot table (shot_xfg), scripts/paper_xrapm.py's tables (paper_xrapm_*)
and the xrapm_* models scripts/paper_eval.py / paper_tests.py add.

  * shot_xfg holds every regular-season shot from 2020-21 on, once, with a
    probability in (0, 1) whose season mean is within half a point of the
    season's make rate (the cross-fit is calibrated);
  * paper_xrapm_stints covers every stint of lineup_stints; on tracked stints
    the parsed attempts equal the stored FGA and FTA, the attempt-accounted
    points plus the residual equal the stored points, expected points sum to
    within 1% of actual points in every season, and 99%+ of attempts were
    priced by the shot chart;
  * the expected-points target has less spread than the actual one in every
    season (that is the point of it), and every fit's lambda is on the grid;
  * under the protocol the xrapm models have rows in every phase, their
    hyperparameters were chosen on the tune seasons, their metrics recompute
    from the stored games, and paper_eval_tests holds the paired comparisons
    the paper states.

Skips when the database is unreachable or the tables are not built.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_paper_xrapm.py
"""

import os
import sys

import numpy as np
import psycopg2
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _d in (os.path.join(_ROOT, "api"), os.path.join(_ROOT, "scripts")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from db_config import DB_CONFIG  # noqa: E402

TUNE_SPAN = "2020-21 to 2023-24"
XRAPM = ("xrapm_single", "xrapm_prior")


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    c = conn.cursor()
    c.execute("SELECT to_regclass('shot_xfg'), to_regclass('paper_xrapm_stints'), to_regclass('paper_xrapm_fits'), to_regclass('paper_eval_tests')")
    if any(v is None for v in c.fetchone()):
        conn.close()
        pytest.skip("step-4 tables not built (build_shot_making.py, paper_xrapm.py, paper_eval.py, paper_tests.py)")
    yield c
    conn.close()


def q(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


@needs_db
def test_shot_xfg_covers_every_regular_season_shot_once_and_is_calibrated(cur):
    (n_shots,), = q(cur, "SELECT count(*) FROM player_shots WHERE game_id LIKE '002%%' AND season >= '2020-21'")
    (n_xfg, lo, hi), = q(cur, "SELECT count(*), min(p_make), max(p_make) FROM shot_xfg")
    assert n_xfg == n_shots and n_xfg > 1_250_000
    assert 0 < lo and hi < 1
    (missing,), = q(cur, """SELECT count(*) FROM player_shots s WHERE s.game_id LIKE '002%%' AND s.season >= '2020-21'
                            AND NOT EXISTS (SELECT 1 FROM shot_xfg x WHERE x.shot_id = s.id)""")
    assert missing == 0
    rows = q(cur, """SELECT x.season, avg(x.p_make), avg(s.shot_made_flag), count(DISTINCT x.fold)
                     FROM shot_xfg x JOIN player_shots s ON s.id = x.shot_id GROUP BY 1 ORDER BY 1""")
    assert [r[0] for r in rows] == [2021, 2022, 2023, 2024, 2025, 2026]
    for season, p, y, folds in rows:
        assert abs(float(p) - float(y)) < 0.005, (season, p, y)
        assert folds == 5


@needs_db
def test_stints_reconcile_with_lineup_stints(cur):
    (n_st,), = q(cur, "SELECT count(*) FROM lineup_stints")
    (n_x, n_missing), = q(cur, """SELECT count(*), count(*) FILTER (WHERE l.stint_id IS NULL)
                                  FROM paper_xrapm_stints x LEFT JOIN lineup_stints l ON l.stint_id = x.stint_id""")
    assert n_x == n_st and n_missing == 0
    (bad_fga, bad_fta, bad_pts, n_tracked), = q(cur, """
        SELECT count(*) FILTER (WHERE x.home_ev_fga <> l.home_fga OR x.away_ev_fga <> l.away_fga),
               count(*) FILTER (WHERE x.home_ev_fta <> l.home_fta OR x.away_ev_fta <> l.away_fta),
               count(*) FILTER (WHERE x.home_pts_events + x.home_residual <> l.home_pts OR x.away_pts_events + x.away_residual <> l.away_pts),
               count(*)
        FROM paper_xrapm_stints x JOIN lineup_stints l ON l.stint_id = x.stint_id WHERE x.tracked_ok""")
    assert n_tracked > 280_000 and bad_fga == 0 and bad_fta == 0 and bad_pts == 0
    # xpts = expected FG points + expected FT points + the unpriced residual, on every tracked stint
    (bad_sum,), = q(cur, """SELECT count(*) FROM paper_xrapm_stints WHERE tracked_ok AND (
                            abs(home_xpts - (home_xfg_pts + home_xft_pts + home_residual)) > 1e-3 OR
                            abs(away_xpts - (away_xfg_pts + away_xft_pts + away_residual)) > 1e-3)""")
    assert bad_sum == 0
    rows = q(cur, """SELECT season, sum(home_pts + away_pts), sum(home_xpts + away_xpts),
                            sum(home_fga_chart + away_fga_chart)::float / sum(home_ev_fga + away_ev_fga),
                            sum(abs(home_residual) + abs(away_residual))
                     FROM paper_xrapm_stints WHERE tracked_ok GROUP BY 1 ORDER BY 1""")
    for season, pts, xpts, matched, res in rows:
        assert abs(float(xpts) / float(pts) - 1) < 0.01, (season, pts, xpts)
        assert matched > 0.97, (season, matched)          # 2025-26 sits at 97.5%: the chart lags the play-by-play there
        assert res < 200, (season, res)                    # the unpriced residual is a few dozen points a season


@needs_db
def test_target_has_less_spread_and_fits_are_on_the_grid(cur):
    import build_rapm as R
    sd = {(k, s): v for k, s, v in q(cur, "SELECT key, season, value FROM paper_xrapm_meta WHERE key IN ('sd_pts100', 'sd_xpts100') AND season > 0")}
    seasons = sorted({s for _, s in sd})
    assert len(seasons) == 6
    for s in seasons:
        assert sd[("sd_xpts100", s)] < 0.7 * sd[("sd_pts100", s)], s
    fits = q(cur, "SELECT version, season, lambda, prior_scale, lambda_rule, cv_rmse, cv_rmse_zero, r_with_rapm FROM paper_xrapm_fits")
    assert len(fits) == 12
    for version, season, lam, scale, rule, cv, cv0, r in fits:
        assert lam in R.LAMBDAS and cv < cv0
        if version == "prior":
            assert scale in R.PRIOR_SCALES and rule == "single_lambda"
        else:
            assert scale is None and rule == "cv_min"
        assert 0.3 < r < 0.95, (version, season, r)        # related to actual-points RAPM, not the same number
    (n_players,), = q(cur, "SELECT count(*) FROM paper_xrapm_players WHERE qualified")
    assert n_players > 2000
    (M,), = q(cur, "SELECT value FROM paper_xrapm_meta WHERE key = 'ft_shrink_attempts'")
    (stable,), = q(cur, "SELECT stable_n FROM stat_stability WHERE stat = 'ft_pct' AND variant = 'catalogue'")
    assert abs(M - stable) < 1e-9


@needs_db
def test_protocol_scores_xrapm_in_every_phase_with_tune_chosen_hyperparameters(cur):
    have = {}
    for task, phase, model in q(cur, "SELECT DISTINCT task, phase, model FROM paper_eval_metrics WHERE model LIKE 'xrapm%%'"):
        have.setdefault((task, phase), set()).add(model)
    for phase in ("tune", "validate", "test"):
        for task in ("impact_next", "impact_heldout", "impact_reliability"):
            assert set(XRAPM) <= have[(task, phase)], (task, phase)
    ch = {(m, p): on for m, p, on in q(cur, "SELECT model, parameter, chosen_on FROM paper_eval_choices WHERE task = 'impact' AND model LIKE 'xrapm%%'")}
    assert ch[("xrapm_single", "lambda")] == TUNE_SPAN and ch[("xrapm_prior", "prior_scale")] == TUNE_SPAN and ch[("xrapm_prior", "lambda")] == TUNE_SPAN
    for phase in ("validate", "test"):
        for model in XRAPM:
            rows = np.array(q(cur, """SELECT pred, actual FROM paper_eval_predictions WHERE task = 'impact_next' AND phase = %s AND model = %s
                                      AND variant = '' ORDER BY unit_id""", (phase, model)), float)
            (v, n), = q(cur, """SELECT value, n FROM paper_eval_metrics WHERE task = 'impact_next' AND phase = %s AND model = %s
                                AND variant = '' AND metric = 'game_rmse'""", (phase, model))
            assert n == len(rows) and 1200 <= n <= 1230
            assert abs(np.sqrt(np.mean((rows[:, 0] - rows[:, 1]) ** 2)) - v) < 1e-9
            # the same games as the actual-points version (paired tests need identical units)
            (n_rapm,), = q(cur, """SELECT count(*) FROM paper_eval_predictions a JOIN paper_eval_predictions b
                                   ON b.task = a.task AND b.phase = a.phase AND b.season = a.season AND b.unit_id = a.unit_id AND b.variant = ''
                                   WHERE a.task = 'impact_next' AND a.phase = %s AND a.model = %s AND a.variant = '' AND b.model = 'rapm_single'
                                   AND a.actual = b.actual""", (phase, model))
            assert n_rapm == n


@needs_db
def test_paired_comparisons_are_stored(cur):
    have = {(t, ph, me, a, b) for t, ph, me, a, b in q(cur, "SELECT task, phase, metric, model_a, model_b FROM paper_eval_tests WHERE model_a LIKE 'xrapm%%' OR model_b LIKE 'xrapm%%'")}
    for phase in ("tune", "validate", "test"):
        assert ("impact_next", phase, "game_rmse", "xrapm_single", "rapm_single") in have
        assert ("impact_next", phase, "game_rmse", "xrapm_prior", "rapm_prior") in have
        assert ("impact_next", phase, "game_rmse", "xrapm_prior", "bpm") in have
        assert ("impact_heldout", phase, "game_rmse", "xrapm_single", "rapm_single") in have
        assert ("impact_reliability", phase, "corr", "xrapm_single", "rapm_single") in have
        assert ("impact_next", phase, "game_rmse", "xrapm_single", "") in have     # an interval on the number itself
    rows = q(cur, """SELECT diff, ci_lo, ci_hi, p_boot, dm_p FROM paper_eval_tests
                     WHERE task = 'impact_next' AND metric = 'game_rmse' AND model_a LIKE 'xrapm%%' AND model_b <> ''""")
    for d, lo, hi, p, dm in rows:
        assert lo <= d <= hi and 0 <= p <= 1 and 0 <= dm <= 1
