"""
test_rating_tracker.py
=======================
Guards round 6 step 7, the Rating Tracker: scripts/rating_tracker_lib.py
(the state-space RAPM), scripts/build_rating_tracker.py's tables
(player_rating_tracker, rating_tracker_fit, rating_tracker_curve,
rating_tracker_validation), the tracker version of the RAPM API and profile
block, and the rapm_tracker model under the paper's protocol.

  * the algebra: with no carry-over the tracker reproduces build_rapm's
    ridge toward a scaled BPM (stored check under 1e-6), and the stored
    stint noise variance is in the range the stints show;
  * the tables reconcile: one filtered and one smoothed row per RAPM
    player-season with the same sizes, rapm = offence + defence, sd > 0,
    the interval is +-1.96 sd, the smoothed sd is never above the filtered
    one, the last season's two kinds coincide, newcomers carry nothing in;
  * the hyperparameters were chosen on the tune span by the protocol's
    criterion, from a full (not --quick) run, with every evaluation counted,
    and the stored tune RMSE is the drift profile's chosen point;
  * the validation rows exist and the known results hold: the tracker beats
    everyone-average and one-season RAPM on every next-season test, its
    fitted scale is about 1, its year-to-year correlation is above
    one-season RAPM's;
  * the API: /rapm/options lists the tracker; /rapm?version=tracker returns
    both kinds with Jokic first among qualified players from 2021-22 on; the
    profile block carries both kinds and the fit;
  * the protocol (if paper_eval / paper_tests have been rerun): rapm_tracker
    has rows in every phase, its hyperparameters were recorded as chosen on
    the tune seasons, and the paired comparisons the paper states exist.

Skips when the database is unreachable or the tables are not built.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_rating_tracker.py
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
JOKIC = 203999


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
    c.execute("SELECT to_regclass('player_rating_tracker'), to_regclass('rating_tracker_fit'), to_regclass('rating_tracker_curve'), "
              "to_regclass('rating_tracker_validation')")
    if any(v is None for v in c.fetchone()):
        conn.close()
        pytest.skip("rating tracker tables not built (run scripts/build_rating_tracker.py)")
    yield c
    conn.close()


def q(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def test_library_constants_match_the_protocol():
    import rating_tracker_lib as T   # imports build_rapm; no database work at import
    assert T.TUNE_SEASONS == (2021, 2022, 2023, 2024)
    assert T.tune_pairs() == [(2021, 2022), (2022, 2023), (2023, 2024)]
    assert set(T.PARAMS) == set(T.BOUNDS) == {"lambda0", "lambda_q", "lambda_b", "prior_scale", "phi"}
    par = {"lambda0": 1234.5, "lambda_q": 42.0, "lambda_b": 9.9e4, "prior_scale": 0.7, "phi": 0.85}
    back = T.unpack(T.pack(par))
    assert all(abs(back[k] - par[k]) < 1e-9 * max(1.0, par[k]) for k in par)


@needs_db
def test_fit_row_is_a_full_tune_only_choice_with_a_sane_noise_variance(cur):
    (on, crit, l0, lq, lb, k, phi, rmse, games, s2, quick, evals, conv, ridge, drift_sd, newcomer_sd), = q(cur, """
        SELECT estimated_on, criterion, lambda0, lambda_q, lambda_b, prior_scale, phi, tune_rmse, tune_games, sigma2, quick,
               evaluations, converged, ridge_check, drift_sd, newcomer_sd FROM rating_tracker_fit WHERE version = 'tracker'""")
    assert on == TUNE_SPAN and "next-season" in crit and "2020-21 -> 2021-22" in crit
    assert not quick and evals > 100 and conv
    assert 10 < l0 < 1e6 and 10 < lq < 1e6 and 10 < lb < 1e6 and 0 < k < 2 and 0.3 < phi <= 1.0
    assert 3600 <= games <= 3700 and 13 < rmse < 16
    assert ridge["max_abs_diff"] < 1e-6 and ridge["lambda"] == 3000 and ridge["prior_scale"] == 0.5
    # sigma2 is the stint noise per possession-weighted row: points per 100 squared times possessions ~ 100^2 x Var(points
    # per possession) ~ 10,000-15,000 on these stints; the implied sds are a fraction of a rating
    assert 10_000 < s2 < 16_000
    assert abs(drift_sd - np.sqrt(s2 / lq)) < 0.01 and abs(newcomer_sd - np.sqrt(s2 / l0)) < 0.01


@needs_db
def test_rows_reconcile_with_player_rapm(cur):
    (n_f, n_s), = q(cur, "SELECT count(*) FILTER (WHERE kind = 'filtered'), count(*) FILTER (WHERE kind = 'smoothed') FROM player_rating_tracker")
    (n_single,), = q(cur, "SELECT count(*) FROM player_rapm WHERE version = 'single'")
    assert n_f == n_s == n_single > 3000
    (missing, size_diff), = q(cur, """
        SELECT count(*) FILTER (WHERE t.player_id IS NULL),
               count(*) FILTER (WHERE abs(t.poss - p.poss) > 0.05 OR t.games <> p.games OR t.stints <> p.stints)
        FROM player_rapm p LEFT JOIN player_rating_tracker t ON t.kind = 'filtered' AND t.season = p.season AND t.player_id = p.player_id
        WHERE p.version = 'single'""")
    assert missing == 0 and size_diff == 0
    (bad,), = q(cur, """SELECT count(*) FROM player_rating_tracker WHERE abs(rapm - (orapm + drapm)) > 0.002
                        OR orapm_sd <= 0 OR drapm_sd <= 0 OR rapm_sd <= 0
                        OR abs(rapm_ci_low - (rapm - 1.959964 * rapm_sd)) > 0.002 OR abs(rapm_ci_high - (rapm + 1.959964 * rapm_sd)) > 0.002
                        OR (qualified <> (poss >= 1000))""")
    assert bad == 0
    (wider,), = q(cur, """SELECT count(*) FROM player_rating_tracker f JOIN player_rating_tracker s
                          ON s.kind = 'smoothed' AND s.season = f.season AND s.player_id = f.player_id
                          WHERE f.kind = 'filtered' AND s.rapm_sd > f.rapm_sd + 1e-3""")
    assert wider == 0
    (last,), = q(cur, "SELECT max(season) FROM player_rating_tracker")
    (last_diff,), = q(cur, """SELECT count(*) FROM player_rating_tracker f JOIN player_rating_tracker s
                              ON s.kind = 'smoothed' AND s.season = f.season AND s.player_id = f.player_id
                              WHERE f.kind = 'filtered' AND f.season = %s AND (abs(f.rapm - s.rapm) > 1e-3 OR abs(f.rapm_sd - s.rapm_sd) > 1e-3)""", (last,))
    assert last_diff == 0        # the smoother's last season is the filter's
    (new_carry,), = q(cur, "SELECT count(*) FROM player_rating_tracker WHERE seasons_seen = 1 AND (carried <> 0 OR first_season <> season)")
    assert new_carry == 0
    (seen_bad,), = q(cur, """SELECT count(*) FROM (SELECT player_id, season, seasons_seen,
                                 row_number() OVER (PARTITION BY player_id ORDER BY season) AS rn
                                 FROM player_rating_tracker WHERE kind = 'filtered') x WHERE seasons_seen <> rn""")
    assert seen_bad == 0


@needs_db
def test_curve_and_validation_hold_the_known_results(cur):
    rows = q(cur, "SELECT lambda_q, next_rmse, neg2ll, chosen FROM rating_tracker_curve ORDER BY lambda_q")
    assert len(rows) >= 14 and sum(1 for r in rows if r[3]) == 1
    (lq, rmse), = q(cur, "SELECT lambda_q, tune_rmse FROM rating_tracker_fit")
    chosen = next(r for r in rows if r[3])
    assert abs(chosen[0] - lq) < 1e-9 and abs(chosen[1] - rmse) < 1e-4    # the curve's RMSE is stored to 4 decimals
    assert chosen[1] <= min(r[1] for r in rows) + 1e-4       # the choice is the profile's minimum along the drift
    nxt = {(s, m): (g, sc, cov) for s, m, g, sc, cov in q(cur, "SELECT season, model, game_rmse, scale_fit, coverage FROM rating_tracker_validation WHERE test = 'next_season'")}
    seasons = sorted({s for s, _ in nxt})
    assert seasons == [2022, 2023, 2024, 2025, 2026]
    for s in seasons:
        assert nxt[(s, "rapm_tracker")][0] < nxt[(s, "zero")][0] - 0.5
        assert nxt[(s, "rapm_tracker")][0] < nxt[(s, "rapm_single")][0]
        assert 0.9 < nxt[(s, "rapm_tracker")][1] < 1.1                     # correctly sized
        assert nxt[(s, "rapm_tracker")][2] >= nxt[(s, "rapm_single")][2]  # carries ratings of players who sat out
    held = dict((s, g) for s, g in q(cur, "SELECT season, game_rmse FROM rating_tracker_validation WHERE test = 'held_out_games' AND model = 'rapm_tracker'"))
    app = dict((s, g) for s, g in q(cur, "SELECT season, game_rmse FROM rapm_validation WHERE test = 'held_out_games' AND model = 'zero'"))
    assert set(held) == {2021, 2022, 2023, 2024, 2025, 2026} and all(held[s] < app[s] for s in held)
    y2y = {(s, m): c for s, m, c in q(cur, "SELECT season, model, corr FROM rating_tracker_validation WHERE test = 'year_to_year'")}
    for s in seasons:
        assert y2y[(s, "rapm_tracker_smoothed")] > y2y[(s, "rapm_tracker")] > y2y[(s, "rapm_single")] + 0.2


@needs_db
def test_api_tracker_version_and_profile_block(cur):
    from fastapi.testclient import TestClient
    from impact_api import app
    client = TestClient(app)
    o = client.get("/rapm/options").json()
    tv = next(v for v in o["versions"] if v["id"] == "tracker")
    assert tv["seasons"] == [2021, 2022, 2023, 2024, 2025, 2026] and o["tracker"]["built"] and o["tracker"]["kinds"] == ["filtered", "smoothed"]
    for kind in ("filtered", "smoothed"):
        d = client.get("/rapm", params={"version": "tracker", "season": 2024, "kind": kind}).json()
        assert d["tracker"]["kind"] == kind and d["fit"]["tracker"]["estimated_on"] == TUNE_SPAN and len(d["tracker"]["curve"]) >= 14
        assert d["_source"]["tables"] if isinstance(d["_source"], dict) and "tables" in d["_source"] else True
        qrows = [p for p in d["players"] if p["qualified"]]
        assert qrows[0]["player_id"] == JOKIC and qrows[0]["rapm_rank"] == 1
        for p in d["players"]:
            assert abs(p["rapm"] - (p["orapm"] + p["drapm"])) < 0.003 and p["rapm_se"] > 0
            assert p["rapm_ci_low"] <= p["rapm"] <= p["rapm_ci_high"]
            assert p["seasons_seen"] >= 1 and (p["seasons_seen"] > 1 or p["carried"] == 0)
        held = {r["model"]: r for r in d["validation"]["held_out_games"]}
        assert {"rapm_tracker", "rapm_single", "bpm", "zero"} <= set(held)
        nxt = {r["model"]: r for r in d["validation"]["next_season_from_this"]}
        assert {"rapm_tracker", "rapm_single", "rapm_prior", "rapm_multi", "bpm", "zero"} <= set(nxt)
        assert d["tracker"]["n_carried"] + d["tracker"]["n_newcomers"] == len(d["players"])
    assert client.get("/rapm", params={"version": "tracker", "kind": "nope"}).status_code == 400
    den = client.get("/rapm", params={"version": "tracker", "season": 2024, "team": "DEN"}).json()
    assert all("DEN" in p["team_list"] for p in den["players"]) and next(p for p in den["players"] if p["player_id"] == JOKIC)["rapm_rank"] == 1
    prof = client.get(f"/player-profile/{JOKIC}").json()
    blk = prof["rating_tracker"]
    kinds = {(r["season"], r["kind"]) for r in blk["rows"]}
    assert {(s, k) for s in range(2021, 2027) for k in ("filtered", "smoothed")} <= kinds
    assert blk["qualified_poss"] == 1000 and 0.3 < blk["fit"]["phi"] <= 1 and blk["fit"]["estimated_on"] == TUNE_SPAN
    f24 = next(r for r in blk["rows"] if r["season"] == 2024 and r["kind"] == "filtered")
    assert f24["rank"] == 1 and f24["rapm"] > 8 and f24["seasons_seen"] == 4 and f24["carried"] > 5
    assert "rating_tracker" in prof["coverage"] and prof["coverage"]["rating_tracker"]["from"] == 2021


@needs_db
def test_protocol_rows_for_the_tracker(cur):
    cur.execute("SELECT to_regclass('paper_eval_metrics'), to_regclass('paper_eval_tests')")
    if any(v is None for v in cur.fetchone()):
        pytest.skip("paper_eval tables not built")
    have = {(ph, t) for ph, t in q(cur, "SELECT DISTINCT phase, task FROM paper_eval_metrics WHERE model = 'rapm_tracker' AND variant = ''")}
    if not have:
        pytest.skip("paper_eval.py --only impact not rerun since the tracker was built")
    for ph in ("tune", "validate", "test"):
        assert {(ph, "impact_next"), (ph, "impact_heldout"), (ph, "impact_reliability")} <= have
    choices = {p: (v, on) for p, v, on in q(cur, "SELECT parameter, value, chosen_on FROM paper_eval_choices WHERE task = 'impact' AND model = 'rapm_tracker'")}
    assert {"lambda0", "lambda_q", "lambda_b", "prior_scale", "phi", "sigma2"} <= set(choices)
    assert all(on == TUNE_SPAN for _, on in choices.values())
    (l0, lq, lb, k, phi), = q(cur, "SELECT lambda0, lambda_q, lambda_b, prior_scale, phi FROM rating_tracker_fit")
    for name, val in (("lambda0", l0), ("lambda_q", lq), ("lambda_b", lb), ("prior_scale", k), ("phi", phi)):
        assert abs(float(choices[name][0]) - val) < 1e-5
    # the protocol's test-season number equals the platform's validation to well under 0.1 (same ratings; only the
    # intercept and home term convention differs, as for every other model)
    (proto,), = q(cur, "SELECT value FROM paper_eval_metrics WHERE task = 'impact_next' AND phase = 'test' AND model = 'rapm_tracker' AND metric = 'game_rmse' AND variant = ''")
    (platform,), = q(cur, "SELECT game_rmse FROM rating_tracker_validation WHERE test = 'next_season' AND season = 2026 AND model = 'rapm_tracker'")
    assert abs(proto - platform) < 0.1
    tests = {(t, ph, a, b) for t, ph, a, b in q(cur, "SELECT task, phase, model_a, model_b FROM paper_eval_tests WHERE model_a = 'rapm_tracker' OR model_b = 'rapm_tracker'")}
    if not tests:
        pytest.skip("paper_tests.py --only impact not rerun since the tracker was built")
    for ph in ("tune", "validate", "test"):
        for b in ("bpm", "rapm_prior", "rapm_multi", "rapm_single", "zero"):
            assert ("impact_next", ph, "rapm_tracker", b) in tests
        assert ("impact_reliability", ph, "rapm_tracker", "rapm_prior") in tests and ("impact_reliability", ph, "bpm", "rapm_tracker") in tests
