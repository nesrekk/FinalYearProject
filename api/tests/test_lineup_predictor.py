"""
test_lineup_predictor.py
========================
Guards round 6 step 9, the Lineup Predictor (scripts/build_lineup_predictor.py
-> lineup_predictor_units / _players / _teams / _fit / _metrics / _tests;
api/lineup_predictor_lib.py; the "try a lineup" endpoints):

  * every unit's net rating, weight and noise recompute from its own
    possessions, and the units add up to the possessions table (every tracked
    possession of 2021-22 on is in exactly one unit per side);
  * no look-ahead: a lineup first used in the team's first game has nothing
    "so far", the season-to-date possessions grow with the first game, every
    choice was made on the tune seasons, and the stored predictions recompute
    from the fit of the right phase (tune out-of-fold aside);
  * the stored metrics recompute from the units;
  * the endpoints give back the stored unit and the app fit's formula, and
    refuse ids that aren't the team's.

Skips when the database is unreachable or the script was never run.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_lineup_predictor.py
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
import lineup_predictor_lib as LP  # noqa: E402


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
    c.execute("SELECT to_regclass('lineup_predictor_units') IS NOT NULL AND to_regclass('lineup_predictor_fit') IS NOT NULL")
    if not c.fetchone()[0]:
        conn.close()
        pytest.skip("build_lineup_predictor.py has not been run")
    yield c
    conn.close()


def _all(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def _fit(cur, fit_on, model):
    rows = _all(cur, "SELECT name, value FROM lineup_predictor_fit WHERE fit_on = %s AND model = %s", (fit_on, model))
    d = dict(rows)
    return d["intercept"], {k.split(":", 1)[1]: v for k, v in d.items() if k.startswith("coef:")}


def test_units_recompute_and_add_up(cur):
    rows = np.array(_all(cur, "SELECT pf, po, pa, pd, net, w, noise, season FROM lineup_predictor_units"), float)
    pf, po, pa, pd_, net, w, noise, season = rows.T
    assert (po > 0).all() and (pd_ > 0).all()
    assert np.abs(LP.net_rating(pf, po, pa, pd_) - net).max() < 1e-3          # REAL columns
    assert np.abs(LP.harmonic_weight(po, pd_) / w - 1).max() < 1e-6
    s2 = dict(_all(cur, "SELECT season, var_pop(pts)::float8 FROM possessions WHERE tracked_ok GROUP BY season"))
    expect = 1e4 * np.array([s2[int(s)] for s in season]) * (1 / po + 1 / pd_)
    assert np.abs(expect / noise - 1).max() < 1e-6
    assert sorted({int(s) for s in season}) == [2022, 2023, 2024, 2025, 2026]
    # every tracked possession is one offensive possession of exactly one unit, minus the dropped one-ended units
    tot = _all(cur, "SELECT COUNT(*), SUM(pts) FROM possessions WHERE tracked_ok AND season >= 2022")[0]
    po_sum, pd_sum, pf_sum = _all(cur, "SELECT SUM(po), SUM(pd), SUM(pf) FROM lineup_predictor_units")[0]
    teams = _all(cur, "SELECT SUM(po), SUM(pd), SUM(pf) FROM lineup_predictor_teams")[0]
    assert teams[0] == tot[0] and teams[1] == tot[0] and teams[2] == tot[1]
    assert 0.98 * tot[0] < po_sum <= tot[0] and 0.98 * tot[0] < pd_sum <= tot[0] and pf_sum <= tot[1]
    # players' on-court sums: five players on every possession
    bad = _all(cur, """SELECT t.season, t.team FROM lineup_predictor_teams t
                       JOIN (SELECT season, team, SUM(po) po, SUM(pd) pd FROM lineup_predictor_players GROUP BY 1, 2) p USING (season, team)
                       WHERE p.po <> 5 * t.po OR p.pd <> 5 * t.pd""")
    assert bad == []


def test_no_look_ahead(cur):
    first = _all(cur, "SELECT MAX(td_poss), MAX(ABS(td_team)), MAX(ABS(td_on)) FROM lineup_predictor_units WHERE first_game_no = 1")[0]
    assert first == (0.0, 0.0, 0.0)
    # within a team-season, more games before the first use means at least as many possessions so far
    worse = _all(cur, """SELECT COUNT(*) FROM (
                            SELECT td_poss, LAG(td_poss) OVER (PARTITION BY season, team ORDER BY first_game_no, first_date) AS prev
                            FROM lineup_predictor_units) x WHERE td_poss < prev""")[0][0]
    assert worse == 0
    # "later" = first used after the stated game
    later_after = _all(cur, "SELECT value FROM lineup_predictor_fit WHERE name = 'const:later_after'")[0][0]
    assert _all(cur, "SELECT COUNT(*) FROM lineup_predictor_units WHERE later <> (first_game_no > %s)", (later_after,))[0][0] == 0
    meta = _all(cur, "SELECT detail FROM lineup_predictor_fit WHERE name = 'meta'")[0][0]
    assert (meta["tune"], meta["validate"], meta["test"]) == ("2021-22 to 2023-24", "2024-25", "2025-26")
    for (detail,) in _all(cur, "SELECT detail FROM lineup_predictor_fit WHERE name = 'cv'"):
        assert detail["criterion"].endswith("2021-22 to 2023-24")
    phases = dict(_all(cur, "SELECT season, MIN(phase) FROM lineup_predictor_units GROUP BY season"))
    assert phases == {2022: "tune", 2023: "tune", 2024: "tune", 2025: "validate", 2026: "test"}


def test_predictions_recompute_from_the_phase_fit(cur):
    feats = {"fit": LP.MODELS["fit"], "full": LP.MODELS["full"]}
    for model in ("fit", "full"):
        for phase, fit_on in (("validate", "tune"), ("test", "tune_validate")):
            b0, coef = _fit(cur, fit_on, model)
            cols = ["r_sum_bpm" if f == "r_sum" else f for f in feats[model]]
            rows = np.array(_all(cur, f"SELECT {', '.join(cols)}, pred_{model} FROM lineup_predictor_units WHERE phase = %s", (phase,)), float)
            pred = b0 + rows[:, :-1] @ np.array([coef[f] for f in feats[model]])
            assert np.abs(pred - rows[:, -1]).max() < 1e-3, (model, phase)
    # the plain sum is the summed ratings themselves; the zero model is zero
    assert _all(cur, "SELECT MAX(ABS(pred_sum - r_sum_bpm)), MAX(ABS(pred_zero)) FROM lineup_predictor_units")[0] == (0.0, 0.0)


def test_metrics_recompute(cur):
    for phase, subset, model in (("test", "later", "full"), ("validate", "all", "scaled"), ("tune", "later", "fit")):
        where = "phase = %s" + (" AND later" if subset == "later" else "")
        y, p, w, nz = np.array(_all(cur, f"SELECT net, pred_{model}, w, noise FROM lineup_predictor_units WHERE {where}", (phase,)), float).T
        wmse = np.sum(w * (y - p) ** 2) / w.sum()
        noise = np.sum(w * nz) / w.sum()
        var = np.sum(w * (y - np.sum(w * y) / w.sum()) ** 2) / w.sum()
        stored = _all(cur, "SELECT wmse, noise, r2_true FROM lineup_predictor_metrics WHERE phase = %s AND subset = %s AND model = %s",
                      (phase, subset, model))[0]
        assert abs(stored[0] / wmse - 1) < 1e-4 and abs(stored[1] / noise - 1) < 1e-4
        assert abs(stored[2] - (1 - (wmse - noise) / (var - noise))) < 1e-3
    # every pair is tested in every phase and subset, with both metrics
    n = _all(cur, """SELECT COUNT(*) FROM lineup_predictor_tests WHERE model_b <> '' AND metric IN ('wmse', 'r2_true')""")[0][0]
    assert n == 3 * 2 * 11 * 2
    # (the zero model's own share sits at its bound: every resample's mean moves off zero, so R2_true <= 0)
    assert _all(cur, "SELECT COUNT(*) FROM lineup_predictor_tests WHERE NOT (ci_lo <= diff AND diff <= ci_hi) AND model_a <> 'zero'")[0][0] == 0


def test_endpoints(cur):
    from fastapi.testclient import TestClient
    import impact_api
    c = TestClient(impact_api.app)
    o = c.get("/lineup-predictor/options").json()
    assert o["seasons"] == [2022, 2023, 2024, 2025, 2026] and o["source"] in LP.RATING_SOURCES
    t = c.get("/lineup-predictor/team", params={"team": "BOS", "season": 2026}).json()
    top = t["lineups"][0]
    stored = _all(cur, """SELECT net, (po + pd) / 2.0::float8, pred_fit FROM lineup_predictor_units
                          WHERE season = 2026 AND team = 'BOS' ORDER BY (po + pd) DESC, player_ids LIMIT 1""")[0]
    assert abs(top["net"] - stored[0]) < 0.06 and abs(top["poss"] - stored[1]) <= 0.5
    ids = ",".join(str(i) for i in reversed(top["player_ids"]))          # order doesn't matter
    r = c.get("/lineup-predictor/predict", params={"team": "BOS", "season": 2026, "ids": ids}).json()
    assert r["actual"] is not None and abs(r["actual"]["net"] - top["net"]) < 1e-9
    b0, coef = _fit(cur, "app", "fit")
    src = o["source"]
    x = LP.fit_features([p["rating"] for p in r["players"]], [p["gravity"] for p in r["players"]],
                        [p["role"] for p in r["players"]], [p["usage"] for p in r["players"]])
    assert abs(b0 + sum(coef[f] * x[f] for f in LP.MODELS["fit"]) - r["preseason"]["pred"]) < 0.1     # inputs rounded for the page
    assert r["preseason"]["lo"] < r["preseason"]["pred"] < r["preseason"]["hi"]
    assert r["preseason"]["obs_lo"] < r["preseason"]["lo"] and src == r["source"]
    assert c.get("/lineup-predictor/predict", params={"team": "BOS", "season": 2026, "ids": "1,2,3,4,5"}).status_code == 404
    assert c.get("/lineup-predictor/predict", params={"team": "BOS", "season": 2026, "ids": ids.split(",")[0] * 1}).status_code == 400
    assert c.get("/lineup-predictor/team", params={"team": "BOS", "season": 2021}).status_code == 404
