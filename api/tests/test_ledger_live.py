"""
test_ledger_live.py
====================
Forecast Ledger nightly scoring (round 6 step 2): scripts/ledger_update.py and
api/ledger_live.py.

  * replay: the nightly code run over all of 2025-26 from game_scores, with the
    Season Simulator's own prior for that season, gives back every stored
    game_pregame_odds rating, home court and expected margin (the in-season
    route is the simulator's), back-to-backs equal the stored ones, and every
    date's odds use only results dated before it;
  * opening day: with no results, the nightly odds equal the locked preseason
    odds of every scheduled game;
  * scoring helpers: official-row choice, Wilson intervals, metrics;
  * the stored log (once the season has started): no logged forecast uses a
    result from its game's date or later, digests match the stored results,
    and official rows recompute from ledger_results with the tagged code.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_ledger_live.py
"""

import json
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import psycopg2
import pytest

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_API)
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _d in (_API, _SCRIPTS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import ledger_lib as LL  # noqa: E402
import ledger_live as LV  # noqa: E402
import ledger_update as U  # noqa: E402
import luck_lib as luck  # noqa: E402
import season_sim_lib as L  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402


def _db():
    try:
        return psycopg2.connect(**DB_CONFIG, connect_timeout=3)
    except Exception:
        return None


_CONN = _db()
needs_db = pytest.mark.skipif(_CONN is None, reason="Postgres DB is not reachable")


def _has(table):
    if _CONN is None:
        return False
    cur = _CONN.cursor()
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    return cur.fetchone()[0] is not None


def _frozen_equals_working_tree():
    """The working-tree model files are the tag's (so this process's imports are the frozen code)."""
    try:
        for f in U.FROZEN_MODULES:
            tag = subprocess.run(["git", "rev-parse", f"{U.DEFAULT_TAG}:{f}"], cwd=_ROOT, capture_output=True, text=True).stdout.strip()
            wt = subprocess.run(["git", "hash-object", f], cwd=_ROOT, capture_output=True, text=True).stdout.strip()
            if not tag or tag != wt:
                return False
        return True
    except Exception:
        return False


def _events_from_game_scores(season):
    g = L.prepare_rest(pd.read_sql(L.GAMES_REST_SQL.format(where="WHERE g.season = %s"), _CONN, params=(season,)))
    h = L.home_rows(g)
    return pd.DataFrame({"espn_id": h.game_id.astype(str).to_numpy(), "game_date": h.game_date.to_numpy(),
                         "tip_utc": [f"{d}T23:00Z" for d in h.game_date], "home": h.home.to_numpy(), "away": h.away.to_numpy(),
                         "neutral_site": (h.venue == 0).to_numpy(), "status": "STATUS_FINAL", "completed": True,
                         "home_pts": h.pts_for.to_numpy(int), "away_pts": h.pts_against.to_numpy(int), "counts": True,
                         "note": ""}), h


@needs_db
@pytest.mark.skipif(not _has("game_pregame_odds"), reason="game_pregame_odds not built")
def test_replay_of_2025_26_reproduces_the_season_simulators_ratings():
    season = 2026
    res, h = _events_from_game_scores(season)
    prev = L.prepare_rest(pd.read_sql(L.GAMES_REST_SQL.format(where="WHERE g.season = %s"), _CONN, params=(season - 1,)))
    cur = _CONN.cursor()
    cur.execute("SELECT name, value FROM season_sim_params WHERE name IN ('carry', 'tau2', 'hca_n0')")
    p = dict(cur.fetchall())
    sp = L.season_prior(prev)
    teams = sorted(set(res.home) | set(res.away))
    means = {t: p["carry"] * sp["ratings"].get(L.franchise(t), 0.0) for t in teams}
    prior = {"as_is": LL.prior_for(means, p["tau2"], sp["hca"], sp["sigma"], p["hca_n0"]),
             "roster": LL.prior_for(means, p["tau2"], sp["hca"], sp["sigma"], p["hca_n0"])}
    beta = {"exp_margin": 0.14, "home_b2b": -0.24, "away_b2b": 0.25}
    sched = U.schedule_frame(LL, res)
    rows = []
    for d in sorted(res.game_date.unique()):
        before = res[res.game_date < d]
        new = U.odds_for_date(LL, L, luck, d, sched[sched.game_date == d], before, teams, prior, beta, season)
        assert all(r["last_result_date"] is None or r["last_result_date"] < d for r in new)
        assert all(r["games_used"] == len(before) for r in new)
        rows += new
    got = pd.DataFrame(rows)
    ref = pd.read_sql("SELECT game_id, r_home, r_away, hca, exp_margin, home_b2b, away_b2b FROM game_pregame_odds WHERE season = %s",
                      _CONN, params=(season,))
    ref["game_id"] = ref.game_id.astype(str)
    a = got[got.forecast == "as_is"].merge(ref, left_on="espn_id", right_on="game_id", suffixes=("", "_ref"))
    assert len(a) == len(ref) == 1230
    for c in ("r_home", "r_away", "hca", "exp_margin"):
        assert np.abs(a[c].astype(float) - a[f"{c}_ref"].astype(float)).max() < 1e-9, c
    assert (a.home_b2b == a.home_b2b_ref.astype(bool)).all() and (a.away_b2b == a.away_b2b_ref.astype(bool)).all()
    rec = got[got.forecast == "record"]
    first = rec.game_date == rec.game_date.min()
    assert (rec[first].p_home == 0.5).all() and rec.p_home.between(0, 1).all()
    # the standings as of the last morning add up
    tl = pd.DataFrame(U.team_log(LL, L, luck, max(res.game_date), sched, res, teams, prior, beta, season))
    assert (tl.games + tl.games_left >= 82).all() and tl.groupby("forecast").wins.sum().eq(tl.groupby("forecast").losses.sum()).all()


@needs_db
@pytest.mark.skipif(not _has("ledger_lock"), reason="no Forecast Ledger lock")
def test_opening_day_nightly_odds_equal_the_locked_preseason_odds():
    cur = _CONN.cursor()
    lock = U.read_lock(cur, LL, LL.SEASON)
    prior = U.priors(LL, lock)
    s = pd.read_sql("SELECT * FROM ledger_schedule WHERE season = %s", _CONN, params=(LL.SEASON,))
    res = s.assign(status="STATUS_SCHEDULED", completed=False, home_pts=None, away_pts=None, counts=s.counted)
    sched = U.schedule_frame(LL, res)
    locked = pd.read_sql("SELECT key, p_home, home_b2b, away_b2b FROM ledger_forecasts WHERE season = %s AND kind = 'game' AND forecast = 'roster'",
                         _CONN, params=(LL.SEASON,)).set_index("key")
    d = min(sched.game_date)
    day = sched[sched.counts]           # every game, from opening-day ratings (no results)
    rows = pd.DataFrame(U.odds_for_date(LL, L, luck, d, day, res.iloc[:0], lock["teams"], prior, lock["beta"], LL.SEASON))
    r = rows[rows.forecast == "roster"].set_index("espn_id")
    assert len(r) == len(locked) == 1200
    assert np.abs(r.p_home - locked.p_home.reindex(r.index)).max() < 2e-6
    assert (r.home_b2b == locked.home_b2b.reindex(r.index)).all()


def test_scoring_helpers():
    lo, hi = LV.wilson(7, 10)
    assert abs(lo - 0.3968) < 1e-3 and abs(hi - 0.8922) < 1e-3
    assert LV.wilson(0, 0) == (None, None)
    df = pd.DataFrame({"espn_id": ["1", "1", "2"], "version": ["as_is", "record", "as_is"], "p": [0.8, 0.5, 0.3],
                       "y": [1.0, 1.0, 1.0], "before_tip": [True, True, False], "game_date": ["2026-10-20"] * 3})
    c = LV.common(df, ("as_is", "record"))
    assert set(c.espn_id) == {"1"}
    m = {x["version"]: x for x in LV.metrics(df)}
    assert abs(m["as_is"]["brier"] - (0.04 + 0.49) / 2) < 1e-12 and m["as_is"]["n_before_tip"] == 1
    assert abs(m["record"]["log_loss"] - np.log(2)) < 1e-12
    run = [r for r in LV.running(df) if r["version"] == "as_is"]
    assert run[-1]["n"] == 2 and abs(run[-1]["brier"] - m["as_is"]["brier"]) < 1e-12


@needs_db
@pytest.mark.skipif(not _has("ledger_game_log"), reason="the nightly update hasn't run yet")
def test_no_logged_forecast_uses_a_result_from_its_date_or_later():
    cur = _CONN.cursor()
    cur.execute("""SELECT COUNT(*) FROM ledger_game_log
                   WHERE season = %s AND last_result_date IS NOT NULL AND last_result_date >= game_date""", (LL.SEASON,))
    assert cur.fetchone()[0] == 0
    cur.execute("""SELECT COUNT(*) FROM ledger_game_log g
                   WHERE season = %s AND games_used > (SELECT COUNT(*) FROM ledger_results r WHERE r.season = g.season
                                                        AND r.counts AND r.completed AND r.game_date < g.game_date)""", (LL.SEASON,))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM ledger_game_log WHERE season = %s AND before_tip AND computed_at >= tip_utc::timestamptz", (LL.SEASON,))
    assert cur.fetchone()[0] == 0


@needs_db
@pytest.mark.skipif(not _has("ledger_game_log"), reason="the nightly update hasn't run yet")
@pytest.mark.skipif(not _frozen_equals_working_tree(), reason="working-tree model files differ from the tag")
def test_official_rows_recompute_from_stored_results():
    lock = U.read_lock(_CONN.cursor(), LL, LL.SEASON)
    prior = U.priors(LL, lock)
    res = pd.read_sql("SELECT * FROM ledger_results WHERE season = %s", _CONN, params=(LL.SEASON,))
    if not len(res):
        pytest.skip("no results stored")
    res["game_date"] = pd.to_datetime(res.game_date).dt.date
    log = pd.read_sql("""SELECT DISTINCT ON (espn_id, forecast) * FROM ledger_game_log WHERE season = %s
                         ORDER BY espn_id, forecast, computed_at""", _CONN, params=(LL.SEASON,))
    if not len(log):
        pytest.skip("nothing logged yet (the season starts 2026-10-20)")
    sched = U.schedule_frame(LL, res)
    dates = sorted(log.game_date.unique())[-5:]              # the last five dates logged
    for d in dates:
        finals = res[res.counts & res.completed & (res.game_date < d)]
        day = sched[sched.counts & (sched.game_date == d)]
        got = pd.DataFrame(U.odds_for_date(LL, L, luck, d, day, finals, lock["teams"], prior, lock["beta"], LL.SEASON))
        want = log[log.game_date == d]
        j = want.merge(got, on=["espn_id", "forecast"], suffixes=("", "_now"))
        same = j.results_digest == j.results_digest_now      # rows computed from exactly today's stored results
        assert same.any()
        assert np.abs(j.p_home[same] - j.p_home_now[same]).max() < 1e-12


@needs_db
@pytest.mark.skipif(not _has("ledger_game_log"), reason="the nightly update hasn't run yet")
def test_live_endpoint():
    from fastapi.testclient import TestClient
    from impact_api import app
    body = TestClient(app).get("/ledger/live").json()
    assert body["status"]["games_scheduled"] >= 1200
    assert {m["version"] for m in body["metrics"]} <= set(LV.VERSIONS)
    assert len(body["teams"]) in (0, 30)
    assert body["min_test_games"] == LV.MIN_TEST_GAMES
