"""
test_paper_eval.py
===================
Guards scripts/paper_eval.py's tables (round 5, step 2): one evaluation
protocol for every model the paper compares (tune 2020-21 to 2023-24,
validate 2024-25, test 2025-26), with per-unit predictions in
paper_eval_predictions and summary metrics in paper_eval_metrics.

  * the split itself is what the plan says, and every hyperparameter was
    chosen on the tune seasons, every model family on the validation season
    (nothing on the test season);
  * every task has validate and test rows for the models the paper compares;
  * the stored summary metrics are recomputable from the stored per-unit
    rows (RMSE, log loss, Brier, correlation), so step 3 can resample them;
  * the numbers agree with the platform's own validation where the two
    protocols coincide (next-season game RMSE on 2025-26 for models with no
    tuned hyperparameter).

Skips when the database is unreachable or the paper_eval tables are not
built (they are step 2's output, not part of the app).

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_paper_eval.py
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
import local_only  # noqa: E402

TUNE, VALIDATE, TEST = (2021, 2022, 2023, 2024), 2025, 2026
TUNE_SPAN, VAL_LABEL, TEST_LABEL = "2020-21 to 2023-24", "2024-25", "2025-26"
IMPACT_MODELS = {"rapm_single", "rapm_prior", "rapm_multi", "bpm", "bpm_scaled", "onoff", "onoff_scaled", "zero", "rapm_tracker",
                 "xrapm_lf_single", "xrapm_lf_prior", "xrapm_sa_single", "xrapm_sa_prior"}
XFG_MODELS = {"constant", "zone", "logreg", "hgb"}
FORMS = {"baseline", "current", "prior", "prior_rest"}


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
    names = ("paper_eval_metrics", "paper_eval_predictions", "paper_eval_choices")
    c.execute("SELECT " + ", ".join(f"to_regclass('{t}')" for t in names))
    missing = [t for t, v in zip(names, c.fetchone()) if v is None]
    if missing:
        conn.close()
        pytest.skip(local_only.kept_local_reason(missing) or "paper_eval tables not built (run scripts/paper_eval.py)")
    yield c
    conn.close()


def q(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def test_protocol_constants_match_the_plan():
    import paper_eval as PE   # imports the model scripts; no database work at import
    assert PE.TUNE == TUNE and PE.VALIDATE == VALIDATE and PE.TEST == TEST
    assert PE.span(PE.TUNE) == TUNE_SPAN and PE.label(PE.TEST) == TEST_LABEL
    assert set(PE.PHASE_OF.values()) == {"tune", "validate", "test"}


@needs_db
def test_split_recorded_and_nothing_chosen_on_the_test_season(cur):
    rows = {(m, p): (v, on) for m, p, v, on in q(cur, "SELECT model, parameter, value, chosen_on FROM paper_eval_choices WHERE task = 'protocol'")}
    assert rows[("all", "tune_seasons")][0] == TUNE_SPAN
    assert rows[("all", "validate_season")][0] == VAL_LABEL
    assert rows[("all", "test_season")][0] == TEST_LABEL
    for task, model, param, on in q(cur, "SELECT task, model, parameter, chosen_on FROM paper_eval_choices WHERE task <> 'protocol'"):
        assert TEST_LABEL not in on, f"{task}.{model}.{param} was chosen on the test season ({on})"
    # Hyperparameters on tune; model families on validate.
    hyper = {(t, m, p): on for t, m, p, on in q(cur, "SELECT task, model, parameter, chosen_on FROM paper_eval_choices WHERE task = 'impact'")}
    assert all(on == TUNE_SPAN for (t, m, p), on in hyper.items() if p != "lambda" or m != "rapm_multi"), hyper
    assert hyper[("impact", "rapm_multi", "lambda")] == "2022-23 to 2023-24"      # the one full window inside tune
    fam = dict(q(cur, "SELECT task, chosen_on FROM paper_eval_choices WHERE (task, parameter) IN (('xfg', 'model'), ('pregame', 'form'))"))
    assert fam == {"xfg": VAL_LABEL, "pregame": VAL_LABEL}


@needs_db
def test_every_task_has_validate_and_test_rows_for_the_compared_models(cur):
    have = {}
    for task, phase, model in q(cur, "SELECT DISTINCT task, phase, model FROM paper_eval_metrics"):
        have.setdefault((task, phase), set()).add(model)
    for phase in ("validate", "test"):
        assert IMPACT_MODELS <= have[("impact_next", phase)]
        assert IMPACT_MODELS <= have[("impact_heldout", phase)]
        assert {"rapm_single", "rapm_prior", "bpm", "onoff", "rapm_tracker"} <= have[("impact_reliability", phase)]
        assert XFG_MODELS <= have[("xfg", phase)]
        assert {"shot_making", "quality", "efg"} <= have[("xfg_reliability", phase)]
        assert FORMS <= have[("pregame", phase)]
        assert {"model", "record", "standings"} <= have[("sim_playoffs", phase)]
        assert {"model", "record"} <= have[("sim_wins", phase)]
    # The test season is scored once: one metric row per (task, model, variant, metric) with phase test.
    dup = q(cur, """SELECT task, model, variant, metric, count(*) FROM paper_eval_metrics WHERE phase = 'test'
                    GROUP BY 1, 2, 3, 4 HAVING count(*) > 1""")
    assert dup == []


def _preds(cur, task, phase, model, variant=""):
    rows = q(cur, """SELECT pred, actual FROM paper_eval_predictions WHERE task = %s AND phase = %s AND model = %s AND variant = %s
                     ORDER BY unit_id""", (task, phase, model, variant))
    a = np.array(rows, float)
    return a[:, 0], a[:, 1]


def _metric(cur, task, phase, model, metric, variant=""):
    v = q(cur, "SELECT value, n, seasons FROM paper_eval_metrics WHERE task = %s AND phase = %s AND model = %s AND variant = %s AND metric = %s",
          (task, phase, model, variant, metric))
    assert len(v) >= 1, (task, phase, model, variant, metric)
    return v


@needs_db
def test_impact_metrics_recompute_from_stored_games(cur):
    for phase in ("validate", "test"):
        for model in IMPACT_MODELS:
            p, a = _preds(cur, "impact_next", phase, model)
            (v, n, _), = _metric(cur, "impact_next", phase, model, "game_rmse")
            assert n == len(a) and 1200 <= n <= 1230, (model, n)
            assert abs(np.sqrt(np.mean((p - a) ** 2)) - v) < 1e-9
            (r, _, _), = _metric(cur, "impact_next", phase, model, "game_corr")
            assert abs(np.corrcoef(p, a)[0, 1] - r) < 1e-9
    p, a = _preds(cur, "impact_heldout", "test", "rapm_single")
    (v, n, _), = _metric(cur, "impact_heldout", "test", "rapm_single", "game_rmse")
    assert n == len(a) and abs(np.sqrt(np.mean((p - a) ** 2)) - v) < 1e-9
    x, y = _preds(cur, "impact_reliability", "test", "bpm")
    (r, n, _), = _metric(cur, "impact_reliability", "test", "bpm", "corr")
    assert n == len(x) >= 250 and abs(np.corrcoef(x, y)[0, 1] - r) < 1e-9


@needs_db
def test_impact_next_agrees_with_the_platform_where_protocols_coincide(cur):
    """Models with nothing tuned (BPM at scale 1, zero, on/off as published) predict 2025-26 from
    2024-25 in both; the only difference is that the paper protocol keeps 2024-25's intercept and
    home term instead of refitting them on 2025-26, which moves a game RMSE by well under 0.1."""
    app = dict(q(cur, "SELECT model, game_rmse FROM rapm_validation WHERE test = 'next_season' AND season = %s", (TEST,)))
    for model in ("bpm", "zero", "onoff"):
        (v, _, _), = _metric(cur, "impact_next", "test", model, "game_rmse")
        assert abs(v - app[model]) < 0.1, (model, v, app[model])


@needs_db
def test_xfg_metrics_recompute_from_stored_shots(cur):
    for phase, season in (("validate", VALIDATE), ("test", TEST)):
        n_shots = q(cur, "SELECT count(*) FROM player_shots WHERE game_id LIKE '002%%' AND season = %s",
                    (f"{season - 1}-{str(season)[-2:]}",))[0][0]
        for model in XFG_MODELS:
            variant = q(cur, "SELECT variant FROM paper_eval_metrics WHERE task = 'xfg' AND phase = %s AND model = %s AND metric = 'log_loss'",
                        (phase, model))[0][0]
            p, y = _preds(cur, "xfg", phase, model, variant)
            (v, n, _), = _metric(cur, "xfg", phase, model, "log_loss", variant)
            assert n == len(y) and n_shots - 50 <= n <= n_shots, (model, n, n_shots)    # a few impossible locations dropped
            pc = np.clip(p, 1e-6, 1 - 1e-6)
            ll = -np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc))
            assert abs(ll - v) < 1e-9
            (b, _, _), = _metric(cur, "xfg", phase, model, "brier", variant)
            assert abs(np.mean((pc - y) ** 2) - b) < 1e-9
    # The family the paper deploys was chosen on the validation season and is the best there.
    fam = q(cur, "SELECT value FROM paper_eval_choices WHERE task = 'xfg' AND parameter = 'model'")[0][0]
    ll = dict(q(cur, "SELECT model, value FROM paper_eval_metrics WHERE task = 'xfg' AND phase = 'validate' AND metric = 'log_loss'"))
    assert fam == min(ll, key=ll.get)


@needs_db
def test_pregame_and_sim_metrics_recompute_from_stored_units(cur):
    for phase in ("validate", "test"):
        for form in FORMS:
            p, y = _preds(cur, "pregame", phase, form)
            (v, n, _), = _metric(cur, "pregame", phase, form, "log_loss")
            assert n == len(y) == 1230
            pc = np.clip(p, 1e-6, 1 - 1e-6)
            assert abs(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc)) - v) < 1e-9
        for method in ("model", "record"):
            p, y = _preds(cur, "sim_playoffs", phase, method, "halfway")
            (v, n, _), = _metric(cur, "sim_playoffs", phase, method, "brier", "halfway")
            assert n == len(y) == 30 and int(y.sum()) == 16
            assert abs(np.mean((p - y) ** 2) - v) < 1e-9
            rows = np.array(q(cur, """SELECT pred, actual, lo, hi FROM paper_eval_predictions WHERE task = 'sim_wins' AND phase = %s
                                      AND model = %s AND variant = 'halfway'""", (phase, method)), float)
            (mae, _, _), = _metric(cur, "sim_wins", phase, method, "mae", "halfway")
            assert abs(np.mean(np.abs(rows[:, 0] - rows[:, 1])) - mae) < 1e-9
            (cov, _, _), = _metric(cur, "sim_wins", phase, method, "cover80", "halfway")
            assert abs(np.mean((rows[:, 1] >= rows[:, 2]) & (rows[:, 1] <= rows[:, 3])) - cov) < 1e-9
    # The form the simulator uses is the one the validation season chose.
    form = q(cur, "SELECT value FROM paper_eval_choices WHERE task = 'pregame' AND parameter = 'form'")[0][0]
    ll = dict(q(cur, "SELECT model, value FROM paper_eval_metrics WHERE task = 'pregame' AND phase = 'validate' AND metric = 'log_loss'"))
    assert form == min(ll, key=ll.get)


@needs_db
def test_pooled_tune_rows_cover_the_tune_seasons(cur):
    rows = q(cur, "SELECT DISTINCT task, seasons FROM paper_eval_metrics WHERE phase = 'tune' AND seasons LIKE '%% to %%'")
    spans = {}
    for task, s in rows:
        spans.setdefault(task, set()).add(s)
    assert spans["impact_next"] == {"2021-22 to 2023-24"}          # scored seasons of the three tune pairs
    assert spans["impact_heldout"] == {TUNE_SPAN, "2022-23 to 2023-24"}   # the window version starts a window later
    assert spans["pregame"] == {"2010-11 to 2023-24"}
    assert "2010-11 to 2023-24" in spans["sim_playoffs"]
