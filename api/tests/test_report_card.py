"""
test_report_card.py
===================
Guards round 6 step 10, the Model Report Card (scripts/build_report_card.py ->
report_card_units / _game_sums / _tests / _pooled / _choices / _meta;
api/report_card_lib.py; GET /report-card/*):

  * no look-ahead: every choice made for season T was made on seasons before T;
  * the rolling origin reproduces the protocol where they coincide: 2024-25
    and 2025-26 pre-game scores equal paper_eval's validate/test metrics, and
    2024-25's impact scores equal paper_eval's validate metrics for every model
    (same tune pairs);
  * every stored score recomputes from its units (game rows or per-game sums),
    every pair's difference is the difference of its two scores, and every
    pooled row recomputes from the per-season rows with report_card_lib;
  * the endpoints rank, orient and pool consistently (a vs b = -(b vs a)).

Skips when the database is unreachable or the script was never run.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_report_card.py
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
import report_card_lib as RC  # noqa: E402


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="Postgres DB is not reachable")


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    c = conn.cursor()
    c.execute("SELECT to_regclass('report_card_tests') IS NOT NULL AND to_regclass('report_card_pooled') IS NOT NULL")
    if not c.fetchone()[0]:
        conn.close()
        pytest.skip("build_report_card.py has not been run")
    yield c
    conn.close()


def _all(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchall()


def _end_year(label):
    """'2020-21 to 2023-24' -> 2024; '2023-24' -> 2024."""
    last = label.split(" to ")[-1]
    return int(last[:4]) + 1


def test_every_task_and_season(cur):
    got = dict(_all(cur, "SELECT task, array_agg(DISTINCT season ORDER BY season) FROM report_card_tests GROUP BY task"))
    assert set(got) == set(RC.TASK_ORDER)
    for task, seasons in got.items():
        assert seasons[0] == RC.TASKS[task]["from"] and seasons[-1] == 2026, (task, seasons)
        assert seasons == list(range(seasons[0], 2027)), task


def test_no_choice_uses_its_own_season(cur):
    rows = _all(cur, "SELECT task, model, parameter, season, chosen_on FROM report_card_choices WHERE chosen_on <> 'n/a'")
    assert len(rows) > 100
    for task, model, parameter, season, chosen_on in rows:
        assert _end_year(chosen_on) < season, (task, model, parameter, season, chosen_on)


def test_matches_the_protocol(cur):
    # pre-game 2024-25 / 2025-26: same constants, coefficients and features as paper_eval's validate / test
    for phase, season in (("validate", 2025), ("test", 2026)):
        rows = _all(cur, """SELECT t.model_a, t.metric, t.value_a, m.value FROM report_card_tests t
                            JOIN paper_eval_metrics m ON m.task = 'pregame' AND m.phase = %s AND m.model = t.model_a AND m.metric = t.metric
                                                     AND m.variant = ''
                            WHERE t.task = 'pregame' AND t.season = %s AND t.model_b = ''""", (phase, season))
        assert len(rows) == 4 * 2
        assert max(abs(a - b) for _, _, a, b in rows) < 1e-12
    # impact 2024-25: the rolling tune pairs are the protocol's, so every model's game RMSE is paper_eval's validate number
    rows = _all(cur, """SELECT t.model_a, t.value_a, m.value FROM report_card_tests t
                        JOIN paper_eval_metrics m ON m.task = 'impact_next' AND m.phase = 'validate' AND m.model = t.model_a
                                                 AND m.metric = 'game_rmse' AND m.variant = ''
                        WHERE t.task = 'impact_next' AND t.season = 2025 AND t.model_b = ''""")
    assert len(rows) == len(RC.TASKS["impact_next"]["models"])
    assert max(abs(a - b) for _, a, b in rows) < 1e-9


def test_scores_recompute_from_units(cur):
    # a unit task: pre-game log loss and the simulator's win-total RMSE
    for task, metric, variant, sql in (
        ("pregame", "log_loss", "", """SELECT model, season, AVG(-(actual * LN(GREATEST(LEAST(pred, 1 - 1e-6), 1e-6))
                                              + (1 - actual) * LN(1 - GREATEST(LEAST(pred, 1 - 1e-6), 1e-6))))
                                       FROM report_card_units WHERE task = 'pregame' GROUP BY 1, 2"""),
        ("sim_wins", "rmse", "halfway", """SELECT model, season, SQRT(AVG((pred - actual) ^ 2)) FROM report_card_units
                                            WHERE task = 'sim_wins' AND variant = 'halfway' GROUP BY 1, 2"""),
        ("impact_next", "game_rmse", "", """SELECT model, season, SQRT(AVG((pred - actual) ^ 2)) FROM report_card_units
                                             WHERE task = 'impact_next' GROUP BY 1, 2"""),
        ("impact_poss", "poss_rmse", "", """SELECT model, season, SQRT(SUM(sq) / SUM(n)) FROM report_card_game_sums
                                             WHERE task = 'impact_poss' GROUP BY 1, 2"""),
        ("xfg", "log_loss", "", """SELECT model, season, SUM(ll) / SUM(n) FROM report_card_game_sums WHERE task = 'xfg' GROUP BY 1, 2"""),
    ):
        recomputed = {(m, s): v for m, s, v in _all(cur, sql)}
        stored = {(m, s): v for m, s, v in _all(cur, """SELECT model_a, season, value_a FROM report_card_tests
                                                        WHERE task = %s AND metric = %s AND variant = %s AND model_b = ''""",
                                                     (task, metric, variant))}
        assert stored and set(stored) == set(recomputed), task
        assert max(abs(stored[k] - recomputed[k]) for k in stored) < 1e-9, task


def test_pairs_and_pooling(cur):
    bad = _all(cur, """SELECT COUNT(*) FROM report_card_tests t
                       JOIN report_card_tests a ON a.task = t.task AND a.metric = t.metric AND a.variant = t.variant AND a.season = t.season
                                               AND a.model_a = t.model_a AND a.model_b = ''
                       JOIN report_card_tests b ON b.task = t.task AND b.metric = t.metric AND b.variant = t.variant AND b.season = t.season
                                               AND b.model_a = t.model_b AND b.model_b = ''
                       WHERE t.model_b <> '' AND ABS(t.diff - (a.value_a - b.value_a)) > 1e-12""")
    assert bad[0][0] == 0
    assert _all(cur, "SELECT COUNT(*) FROM report_card_tests WHERE NOT (ci_lo <= diff + 1e-12 AND diff - 1e-12 <= ci_hi) AND model_b <> ''")[0][0] < 5
    pooled = _all(cur, "SELECT task, metric, variant, model_a, model_b, k, mu, ci_lo, ci_hi, tau2, flips FROM report_card_pooled")
    assert len(pooled) > 100
    for task, metric, variant, a, b, k, mu, lo, hi, tau2, flips in pooled[::7]:
        rows = _all(cur, """SELECT diff, se FROM report_card_tests WHERE task = %s AND metric = %s AND variant = %s
                            AND model_a = %s AND model_b = %s ORDER BY season""", (task, metric, variant, a, b))
        d, se = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])
        re = RC.random_effects(d, se)
        assert re["k"] == k and abs(re["mu"] - mu) < 1e-12 and abs(re["ci_lo"] - lo) < 1e-12 and abs(re["ci_hi"] - hi) < 1e-12
        assert flips == int(np.sum(np.sign(d) == -np.sign(mu)))


def test_random_effects_known_case():
    # equal standard errors, no spread beyond noise: tau2 = 0 and mu = the plain mean
    re = RC.random_effects([0.1, 0.1, 0.1, 0.1], [0.05] * 4)
    assert re["tau2"] == 0 and abs(re["mu"] - 0.1) < 1e-15 and re["ci_lo"] < 0.1 < re["ci_hi"]
    # widely spread seasons: tau2 > 0 and the interval is wider than the fixed-effect one
    re = RC.random_effects([-1.0, 1.0, -1.0, 1.0, 0.0], [0.1] * 5)
    assert re["tau2"] > 0.5 and (re["ci_hi"] - re["ci_lo"]) / 2 > 1.96 * re["se_fixed"]
    assert re["pi_lo"] < re["ci_lo"] and re["pi_hi"] > re["ci_hi"]


def test_endpoints(cur):
    from fastapi.testclient import TestClient
    import impact_api
    c = TestClient(impact_api.app)
    o = c.get("/report-card/options").json()
    assert [t["key"] for t in o["tasks"]] == list(RC.TASK_ORDER)
    d = c.get("/report-card/task", params={"task": "pregame", "metric": "log_loss"}).json()
    assert len(d["seasons"]) == 14 and all(sorted(m["rank"] for m in [x for mm in d["models"] for x in mm["seasons"] if x["season"] == s["season"]])[0] == 1
                                           for s in d["seasons"])
    ab = c.get("/report-card/pair", params={"task": "impact_next", "a": "bpm", "b": "rapm_tracker"}).json()
    ba = c.get("/report-card/pair", params={"task": "impact_next", "a": "rapm_tracker", "b": "bpm"}).json()
    assert [r["diff"] for r in ab["seasons"]] == [-r["diff"] for r in ba["seasons"]]
    assert abs(ab["pooled"]["mu"] + ba["pooled"]["mu"]) < 1e-9 and ab["pooled"]["a_better"] == ba["pooled"]["b_better"]
    assert c.get("/report-card/task", params={"task": "nope"}).status_code == 400
    assert c.get("/report-card/pair", params={"task": "xfg", "a": "sa", "b": "sa"}).status_code == 400
