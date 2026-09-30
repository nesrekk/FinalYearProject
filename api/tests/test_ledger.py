"""
test_ledger.py
===============
Forecast Ledger (round 6 step 1): the frozen rules in api/ledger_lib.py and the
additions to api/season_sim_lib.py they use, and, once scripts/ledger_lock.py
has run, the lock itself:

  * the depth-chart minutes rule, the placeholder games that bring every team
    to 82, and back-to-backs from schedule dates (equal to the stored rest days
    of every 2025-26 team-game);
  * the playoff simulation: seeds 1-8 filled once per conference, 8 / 4 / 2 / 1
    teams through each round in every run;
  * the lock: re-exporting the stored rows reproduces the stored SHA-256 (and
    through the API download too); no forecast row is stamped at or after the
    first tip; the as-is prior equals the Season Simulator's opening day; the
    in-season route (prior_for + ratings_on + game_odds with no games played)
    gives back every locked game probability; title odds sum to one.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_ledger.py
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import psycopg2
import pytest

_API = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(os.path.dirname(_API), "scripts")
for _d in (_API, _SCRIPTS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import ledger_lib as LL  # noqa: E402
import season_sim_lib as L  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402


def _db():
    try:
        return psycopg2.connect(**DB_CONFIG, connect_timeout=3)
    except Exception:
        return None


_CONN = _db()
needs_db = pytest.mark.skipif(_CONN is None, reason="Postgres DB is not reachable")


def _locked():
    if _CONN is None:
        return False
    cur = _CONN.cursor()
    cur.execute("SELECT to_regclass('public.ledger_lock')")
    if cur.fetchone()[0] is None:
        return False
    cur.execute("SELECT 1 FROM ledger_lock WHERE season = %s", (LL.SEASON,))
    return cur.fetchone() is not None


needs_lock = pytest.mark.skipif(not _locked(), reason="no Forecast Ledger lock yet (scripts/ledger_lock.py --lock)")


def test_depth_chart_minutes():
    p = pd.DataFrame({"proj_min": [34.0, 32.0, np.nan, 30.0, 30.0, 28.0, 26.0, 24.0, 22.0, 20.0, 12.0],
                      "proj_bpm": [5.0, 1.0, np.nan, np.nan, 0.0, -1.0, 2.0, -3.0, 0.5, 1.0, 4.0]})
    p["order_key"] = [f"{i:02d}" for i in range(len(p))]
    out, team_bpm, gap = LL.allocate_minutes(p)
    assert gap == 0 and abs(out.minutes.sum() - 240) < 1e-9
    m = out.set_index("order_key").minutes
    assert m.tolist() == [34, 32, 30, 30, 28, 26, 24, 22, 14, 0, 0]              # in order of projected minutes
    assert m["02"] == 0 and m["10"] == 0                                          # no projection / depth chart full
    expect = (34 * 5 + 32 * 1 + 30 * LL.REPLACEMENT_BPM + 30 * 0 + 28 * -1 + 26 * 2 + 24 * -3 + 22 * 0.5 + 14 * 1) / 48
    assert abs(team_bpm - expect) < 1e-12
    short = p.head(3)
    out, team_bpm, gap = LL.allocate_minutes(short)
    assert gap == 240 - 66 and abs(team_bpm - (34 * 5 + 32 * 1 + gap * LL.REPLACEMENT_BPM) / 48) < 1e-12


def test_placeholder_games_reach_82_and_balance_venues():
    rows = pd.DataFrame({"home": ["A"] * 40 + ["B"] * 39, "away": ["B"] * 40 + ["A"] * 39, "venue": 1})
    out = LL.placeholder_games(["A", "B"], rows)
    n = pd.Series([t for t, _ in out]).value_counts()
    assert n["A"] == 82 - 79 and n["B"] == 82 - 79
    home = {t: sum(1 for x, v in out if x == t and v == 1) for t in "AB"}
    assert home["A"] + 40 in (41, 42) and home["B"] + 39 in (41, 42)


@needs_db
def test_b2b_from_dates_equals_stored_rest_days():
    g = pd.read_sql("""SELECT g.game_id AS espn_id, g.game_date, g.team_abbreviation AS home, g.opponent AS away,
                              fh.rest_days AS hr, fa.rest_days AS ar
                       FROM game_scores g
                       JOIN team_game_fatigue fh ON fh.game_id = g.game_id AND fh.team_abbreviation = g.team_abbreviation
                       JOIN team_game_fatigue fa ON fa.game_id = g.game_id AND fa.team_abbreviation = g.opponent
                       WHERE g.season = 2026 AND g.team_abbreviation < g.opponent""", _CONN)
    s = LL.add_b2b(g.assign(counted=True))
    assert len(s) == 1230
    assert (s.home_b2b == (g.hr == 0)).all() and (s.away_b2b == (g.ar == 0)).all()


def test_playoff_simulation_invariants():
    rng = np.random.default_rng(1)
    teams = sorted(t for t in L.CONFERENCE if t not in ("NJN", "NOH"))
    st = L.Standings(teams, LL.EMPTY_PLAYED)
    games = pd.DataFrame([(h, a) for h in teams for a in teams if h != a], columns=["home", "away"])
    games = games.assign(venue=1, home_b2b=False, away_b2b=False)
    beta = {"exp_margin": 0.14}
    r = {t: float(x) for t, x in zip(teams, np.linspace(-6, 6, len(teams)))}
    runs = 400
    draws = L.draw_ratings(r, {t: 4.0 for t in teams}, teams, runs, rng)
    sim = L.simulate(st, games, 2027, L.model_p_matrix(games, st, beta, 2.0), draws, beta, 2.0, runs, rng,
                     extra_games=[(teams[0], 1), (teams[1], -1)])
    assert sim["games"][0] == 59 and sim["games"][2] == 58
    for conf in L.CONFERENCES:
        seeds = sim["seed"][:, st.conf_idx[conf]]
        for s in range(1, 9):
            assert ((seeds == s).sum(1) == 1).all()
    po = L.simulate_playoffs(sim, st, draws, beta, 2.0, rng)
    assert (po["round2"].sum(1) == 8).all() and (po["conf_finals"].sum(1) == 4).all()
    assert (po["finals"].sum(1) == 2).all() and (po["title"].sum(1) == 1).all()
    assert (po["title"] <= po["finals"]).all() and (po["finals"] <= po["conf_finals"]).all()
    assert (po["round2"] <= (sim["seed"] > 0)).all()


@needs_lock
def test_lock_reexport_reproduces_the_stored_hash():
    cur = _CONN.cursor()
    cur.execute("SELECT lock_sha256, csv_bytes, csv_lines FROM ledger_lock WHERE season = %s", (LL.SEASON,))
    digest, nbytes, nlines = cur.fetchone()
    data = LL.canonical_csv(cur, LL.SEASON)
    assert LL.sha256(data) == digest and len(data) == nbytes and data.count(b"\n") == nlines
    tampered = data.replace(b"p_title,", b"p_title,1", 1)
    assert LL.sha256(tampered) != digest


@needs_lock
def test_no_forecast_row_after_the_first_tip():
    cur = _CONN.cursor()
    cur.execute("SELECT first_tip_utc, locked_at FROM ledger_lock WHERE season = %s", (LL.SEASON,))
    first_tip, locked_at = cur.fetchone()
    assert locked_at < first_tip
    cur.execute("SELECT COUNT(*), MAX(locked_at), COUNT(DISTINCT locked_at) FROM ledger_forecasts WHERE season = %s", (LL.SEASON,))
    n, latest, distinct = cur.fetchone()
    assert n > 0 and latest < first_tip and distinct == 1 and latest == locked_at
    cur.execute("SELECT value FROM ledger_meta WHERE season = %s AND key = 'first_tip_utc'", (LL.SEASON,))
    assert pd.Timestamp(cur.fetchone()[0]) == pd.Timestamp(first_tip)


@needs_lock
def test_lock_contents_are_consistent():
    f = pd.read_sql("SELECT * FROM ledger_forecasts WHERE season = %s", _CONN, params=(LL.SEASON,))
    teams = f[f.kind == "team"]
    games = f[f.kind == "game"]
    assert (teams.groupby("forecast").size() == 30).all()
    assert (games.groupby("forecast").size() == games.groupby("forecast").size().iloc[0]).all()
    for _, t in teams.groupby("forecast"):
        assert abs(t.p_title.sum() - 1) < 1e-4 and abs(t.p_finals.sum() - 2) < 1e-4
        assert abs(t.p_playoffs.sum() - 16) < 1e-4 and abs(t.p_conf_finals.sum() - 4) < 1e-4
        assert ((t.games_scheduled + t.games_placeholder) == 82).all()
        assert (t.wins_p10 <= t.mean_wins).all() and (t.mean_wins <= t.wins_p90).all()
    s = pd.read_sql("SELECT * FROM ledger_schedule WHERE season = %s AND counted", _CONN, params=(LL.SEASON,))
    assert set(s.espn_id) == set(games.key)


@needs_lock
def test_as_is_prior_is_the_season_simulators_and_in_season_route_reproduces_the_odds():
    cur = _CONN.cursor()
    cur.execute("SELECT key, value FROM ledger_meta WHERE season = %s", (LL.SEASON,))
    meta = dict(cur.fetchall())
    games = L.prepare_rest(pd.read_sql(L.GAMES_REST_SQL.format(where="WHERE g.season = %s"), _CONN,
                                       params=(LL.SEASON - 1,)))
    f = pd.read_sql("SELECT * FROM ledger_forecasts WHERE season = %s", _CONN, params=(LL.SEASON,))
    t = f[(f.kind == "team")]
    teams = sorted(t.key.unique())
    params = {k: float(meta[k]) for k in ("carry", "tau2", "hca_n0")}
    ref = L.ratings_as_of(LL.EMPTY_PLAYED, teams, L.season_prior(games), params)
    beta = {k: float(v) for k, v in json.loads(meta["pregame_beta"]).items()}
    for fc in LL.FORECASTS:
        tf = t[t.forecast == fc].set_index("key")
        prior, prm = LL.prior_for(tf.prior_mean.to_dict(), float(meta[f"prior_var_{fc}"]), meta["hca_prev"],
                                  meta["sigma_prev"], meta["hca_n0"])
        rat = LL.ratings_on(LL.EMPTY_PLAYED, teams, prior, prm)
        if fc == "as_is":
            assert max(abs(rat["r_post"][x] - ref["r_post"][x]) for x in teams) < 1e-5
            assert abs(rat["hca"] - ref["hca"]) < 1e-12
        g = f[(f.kind == "game") & (f.forecast == fc)]
        em, p = LL.game_odds(g, rat, beta)
        assert np.abs(p - g.p_home.to_numpy(float)).max() < 2e-6
        assert np.abs(em - g.exp_margin.to_numpy(float)).max() < 2e-6


@needs_lock
def test_api_download_is_the_hashed_csv():
    from fastapi.testclient import TestClient
    from impact_api import app
    c = TestClient(app)
    pre = c.get("/ledger/preseason")
    assert pre.status_code == 200
    body = pre.json()
    assert body["lock"]["hash_reproduced"] is True
    assert set(body["forecasts"]) == set(LL.FORECASTS)
    csv = c.get("/ledger/lock.csv")
    assert csv.status_code == 200 and LL.sha256(csv.content) == body["lock"]["lock_sha256"]
    games = c.get("/ledger/games", params={"team": "BOS"}).json()["games"]
    assert games and all("BOS" in (g["home"], g["away"]) for g in games)
    assert c.get("/ledger/roster/BOS").status_code == 200
    assert c.get("/ledger/hindcast").json()["rows"]
