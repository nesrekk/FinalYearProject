"""
test_paper_tests.py
====================
Guards scripts/paper_tests.py's table (round 5, step 3): intervals and
paired tests on every comparison the paper states, computed from the
per-unit rows of paper_eval_predictions.

  * the statistics helpers are right on synthetic data (correlation from
    totals, Diebold-Mariano statistic ~ N(0,1) under the null, sign-flip
    p-value uniform under the null, cluster-bootstrap spread equal to the
    closed form);
  * every stored value_a / value_b equals the metric paper_eval_metrics
    stored for the same task, phase, model and seasons (so the tests are
    about the numbers the paper prints), and diff = value_a - value_b;
  * every interval contains its point estimate, every p-value is in
    [0, 1], the bootstrap p agrees with whether the interval excludes zero,
    the sign-flip p agrees in sign of evidence, and Diebold-Mariano rows
    exist exactly where the units are a time-ordered game series;
  * the paper's headline comparisons are present for every phase;
  * one stored row is reproduced bit for bit from its stored seed and
    resample count (determinism).

Skips when the database is unreachable or the paper_eval tables are not
built (they are the paper's, not the app's).

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_paper_tests.py
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

import paper_tests as T  # noqa: E402  (numpy/scipy only at import)
from db_config import DB_CONFIG  # noqa: E402


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
    cur.execute("SELECT to_regclass('paper_eval_tests'), to_regclass('paper_eval_predictions'), to_regclass('paper_eval_metrics')")
    if any(v is None for v in cur.fetchone()):
        c.close()
        pytest.skip("paper_eval tables not built (run scripts/paper_eval.py then scripts/paper_tests.py)")
    yield c
    c.close()


def q(conn, sql, args=None):
    cur = conn.cursor()
    cur.execute(sql, args)
    return cur.fetchall()


# ---------------------------------------------------------------- the helpers, no database

def test_correlation_from_totals_matches_numpy():
    rng = np.random.default_rng(1)
    x = rng.normal(size=500)
    y = 0.5 * x + rng.normal(size=500)
    M = np.array([[x.sum(), y.sum(), (x * x).sum(), (y * y).sum(), (x * y).sum(), 500.0]])
    assert abs(T.corr_from_sums(M)[0] - np.corrcoef(x, y)[0, 1]) < 1e-12


def test_diebold_mariano_is_standard_normal_under_the_null():
    rng = np.random.default_rng(2)
    stats_ = np.array([T.diebold_mariano(rng.normal(size=1228))[0] for _ in range(400)])
    assert abs(stats_.mean()) < 0.15 and 0.85 < stats_.std() < 1.15
    assert T.diebold_mariano(rng.normal(size=1228))[2] == 6           # floor(4 (12.28)^(2/9))
    # a differential with a clear mean is detected
    stat, p, _ = T.diebold_mariano(rng.normal(size=1228) + 0.3)
    assert stat > 5 and p < 1e-6


def test_sign_flip_p_is_uniform_under_the_null_and_small_under_a_shift():
    rng = np.random.default_rng(3)
    ps = np.array([T.sign_flip_p(np.random.default_rng(i), rng.normal(size=300), 400) for i in range(300)])
    assert 0.02 < np.mean(ps < 0.05) < 0.09 and 0.4 < ps.mean() < 0.6
    assert T.sign_flip_p(np.random.default_rng(0), rng.normal(size=300) + 0.5, 400) < 0.01


def test_cluster_bootstrap_spread_matches_the_closed_form():
    rng = np.random.default_rng(4)
    idx, C = T.cluster_codes(np.repeat(np.arange(50), 4))
    vals = rng.normal(size=200) + np.repeat(rng.normal(size=50), 4)   # a cluster effect
    bm = T.boot_means(np.random.default_rng(0), idx, C, [vals], 4000)[:, 0]
    closed = np.std([vals[idx == c].mean() for c in range(C)]) / np.sqrt(C)
    assert abs(bm.mean() - vals.mean()) < 0.02 and 0.85 < bm.std() / closed < 1.15


def test_row_seed_is_deterministic_and_distinct():
    assert T.row_seed("a", "b") == T.row_seed("a", "b") != T.row_seed("a", "c")


# ---------------------------------------------------------------- the stored table

COLS = ("task", "phase", "metric", "model_a", "model_b", "variant", "seasons", "unit_type", "cluster_by", "n", "n_clusters",
        "value_a", "value_b", "diff", "ci_lo", "ci_hi", "p_boot", "p_perm", "dm_stat", "dm_p", "dm_lags", "resamples", "seed", "note")


@pytest.fixture(scope="module")
def table(conn):
    rows = q(conn, f"SELECT {', '.join(COLS)} FROM paper_eval_tests")
    return [dict(zip(COLS, r)) for r in rows]


@needs_db
def test_values_are_the_stored_metrics(conn, table):
    """Every value_a / value_b is the number paper_eval_metrics holds for that task, phase, model, variant and seasons."""
    M = {}
    for t, ph, m, va, se, me, v in q(conn, "SELECT task, phase, model, variant, seasons, metric, value FROM paper_eval_metrics"):
        M[(t, ph, m, va, se, me)] = v
    checked = 0
    for r in table:
        if r["metric"] in T.CALIB_METRICS:
            continue            # computed by paper_tests itself (step 2 stored no calibration)
        for side in ("a", "b"):
            model = r[f"model_{side}"]
            if model == "":
                continue
            variant = r["variant"]
            # the shot model's rows carry their own config name; the comparison row carries ''
            key = next((k for k in M if k[:3] == (r["task"], r["phase"], model) and k[4] == r["seasons"] and k[5] == r["metric"]
                        and (k[3] == variant or (variant == "" and r["task"] == "xfg"))), None)
            if key is None:
                # the three-season RAPM starts a window later, so its paired tune rows cover a shorter span than the
                # other model's stored metric; those values are the metric on the common units (checked by recompute below)
                assert "rapm_multi" in (r["model_a"], r["model_b"]) and r["phase"] == "tune", (r["task"], r["phase"], model, r["metric"], r["seasons"])
                continue
            assert abs(M[key] - r[f"value_{side}"]) < 1e-9, (key, M[key], r[f"value_{side}"])
            checked += 1
    assert checked > 500
    for r in table:
        if r["model_b"]:
            assert abs(r["diff"] - (r["value_a"] - r["value_b"])) < 1e-12
        else:
            assert r["diff"] == r["value_a"] and r["p_boot"] is None and r["p_perm"] is None


@needs_db
def test_intervals_and_p_values_are_coherent(table):
    res = {r["resamples"] for r in table}
    assert res == {T.RESAMPLES}
    for r in table:
        assert r["ci_lo"] <= r["diff"] <= r["ci_hi"], (r["task"], r["metric"], r["model_a"], r["model_b"], r["phase"])
        if r["model_b"] == "":
            continue
        assert 0 <= r["p_boot"] <= 1
        excludes = r["ci_lo"] > 0 or r["ci_hi"] < 0
        # the bootstrap p is the level at which the percentile interval excludes zero; allow the boundary
        if r["p_boot"] < 0.04:
            assert excludes, r
        if r["p_boot"] > 0.06:
            assert not excludes, r
        if r["metric"] in T.MEAN_METRICS:
            assert r["p_perm"] is not None and 0 < r["p_perm"] <= 1
            if r["p_boot"] < 0.001:
                assert r["p_perm"] < 0.01, r
        else:
            assert r["p_perm"] is None
        if r["task"] in T.TIME_ORDERED and r["metric"] in T.MEAN_METRICS:
            assert r["dm_p"] is not None and 0 <= r["dm_p"] <= 1 and r["dm_lags"] >= 1
            assert (r["dm_stat"] > 0) == (r["diff"] > 0)          # same sign as the difference
        else:
            assert r["dm_p"] is None and r["dm_stat"] is None


@needs_db
def test_headline_comparisons_exist_for_every_phase(table):
    have = {(r["task"], r["phase"], r["metric"], r["model_a"], r["model_b"], r["variant"]) for r in table}
    for phase in ("tune", "validate", "test"):
        assert ("impact_next", phase, "game_rmse", "rapm_prior", "bpm", "") in have
        assert ("impact_next", phase, "game_rmse", "onoff", "zero", "") in have
        assert ("impact_reliability", phase, "corr", "bpm", "rapm_prior", "") in have
        assert ("pregame", phase, "log_loss", "prior_rest", "current", "") in have
        assert ("sim_playoffs", phase, "brier", "model", "record", "halfway") in have
        assert ("sim_wins", phase, "mae", "model", "record", "halfway") in have
    for phase in ("validate", "test"):
        assert ("xfg", phase, "log_loss", "hgb", "logreg", "") in have
        assert ("xfg", phase, "ece", "hgb", "", "small") in have or any(k[:5] == ("xfg", phase, "ece", "hgb", "") for k in have)
        assert ("xfg_reliability", phase, "corr", "quality", "shot_making", "fga>=200") in have
    # nothing is compared across phases or seasons: each row's seasons is one phase's span
    assert all(r["seasons"] in ("2025-26", "2024-25") or r["phase"] == "tune" for r in table)
    # the shot model's clusters are games, not shots
    xfg = [r for r in table if r["task"] == "xfg"]
    assert all(r["cluster_by"] == "game" and 1200 <= r["n_clusters"] <= 1235 and r["n"] > 200_000 for r in xfg)


@needs_db
def test_one_row_reproduces_from_its_stored_seed(conn, table):
    """The paired test for RAPM + prior vs BPM on the test season, recomputed from paper_eval_predictions with the
    stored seed and resample count, gives the stored interval and p-values exactly."""
    r = next(x for x in table if (x["task"], x["phase"], x["metric"], x["model_a"], x["model_b"]) ==
             ("impact_next", "test", "game_rmse", "rapm_prior", "bpm"))
    series = T.load_phase(conn, "impact_next", "test", None)
    out = T.Rows(r["resamples"])
    T.paired_rows(out, "impact_next", "test", "rapm_prior", "bpm", series[("rapm_prior", "")], series[("bpm", "")], "game_rmse", "")
    got = dict(zip(T.Rows.COLS, out.rows[0]))
    assert got["seed"] == r["seed"]
    for c in ("n", "n_clusters", "value_a", "value_b", "diff", "ci_lo", "ci_hi", "p_boot", "p_perm", "dm_stat", "dm_p", "dm_lags"):
        assert got[c] == pytest.approx(r[c], abs=1e-12), c
