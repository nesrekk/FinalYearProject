"""
test_pregame_availability.py
=============================
Guards round 6 step 6, availability-aware pre-game odds
(scripts/build_pregame_availability.py -> pregame_availability_odds,
pregame_availability_players, pregame_availability_fit,
pregame_availability_tests; api/availability_lib.py; the what-if endpoint):

  * every regular-season game 2020-21 on is stored, its base odds are the
    app's held-out game_pregame_odds, and only CHI-LAC 2026-01-20 (not in the
    ESPN play-by-play) has no lineups;
  * every stored probability recomputes from the base odds, the lineup
    feature and its own season's coefficient; the protocol's base odds are
    paper_eval's stored prior_rest predictions, and the metrics recompute;
  * no look-ahead: a player's expected minutes are his mean over his earlier
    regular-season games for the team that season (player_game_lines joined
    to game_scores, so the NBA Cup finals don't count), ratings are the
    projection made before the season (or replacement level), no coefficient
    used for a season was fitted on it, and the rating source was chosen on
    2024-25 without the test season;
  * the stored roster rows reproduce every team-game's lineup strength;
  * the what-if endpoint gives back the stored odds with nothing changed,
    moves the right way when a player is put back, and refuses ids that
    aren't in the game's lists.

Skips when the database is unreachable or the script was never run.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_pregame_availability.py
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
import availability_lib as A  # noqa: E402

SOURCES = tuple(A.RATING_SOURCES)


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
    c.execute("SELECT to_regclass('pregame_availability_odds') IS NOT NULL AND to_regclass('pregame_availability_fit') IS NOT NULL")
    if not c.fetchone()[0]:
        conn.close()
        pytest.skip("build_pregame_availability.py has not been run")
    yield c
    conn.close()


def _all(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def _coef(cur, phase="platform"):
    return {(s, season): (b, se) for s, season, b, se in _all(
        cur, "SELECT source, season, value, se FROM pregame_availability_fit WHERE kind = 'coef' AND name = 'b' AND phase = %s", (phase,))}


def _const(cur, name, source):
    return _all(cur, "SELECT value FROM pregame_availability_fit WHERE kind = 'constant' AND name = %s AND source = %s",
                (name, source))[0][0]


def test_every_game_and_its_base_odds(cur):
    n, missing, base_diff, lineless = _all(cur, """
        SELECT COUNT(g.game_id), COUNT(g.game_id) - COUNT(o.game_id), MAX(ABS(o.p_base - g.p_home)),
               STRING_AGG(o.game_id, ',') FILTER (WHERE NOT o.lineups_ok)
        FROM game_pregame_odds g LEFT JOIN pregame_availability_odds o USING (game_id) WHERE g.season >= 2021""")[0]
    assert n == 7230 and missing == 0 and base_diff == 0
    assert lineless == "0022500614"
    # without lineups the odds stay the base odds
    p, b = _all(cur, "SELECT p_bpm, p_base FROM pregame_availability_odds WHERE game_id = '0022500614'")[0]
    assert abs(p - b) < 1e-12


def test_probabilities_recompute_from_their_season_coefficient(cur):
    coef = _coef(cur)
    for src in SOURCES:
        rows = np.array(_all(cur, f"""SELECT season, p_base, avail_{src}, p_{src} FROM pregame_availability_odds
                                      WHERE p_{src} IS NOT NULL ORDER BY game_id"""), float)
        b = np.array([coef[(src, int(s))][0] for s in rows[:, 0]])
        assert np.abs(A.predict(rows[:, 1], rows[:, 2], b) - rows[:, 3]).max() < 1e-12
        assert int(rows[:, 0].min()) == A.MIN_SEASON[src]
        # the feature is the two sides' deviations
        dev = np.array(_all(cur, f"""SELECT avail_{src}, (s_home_{src} - ref_home_{src}) - (s_away_{src} - ref_away_{src})
                                     FROM pregame_availability_odds WHERE lineups_ok AND p_{src} IS NOT NULL"""), float)
        assert np.abs(dev[:, 0] - dev[:, 1]).max() < 1e-9


def test_protocol_base_is_paper_eval_and_metrics_recompute(cur):
    worst = _all(cur, """SELECT MAX(ABS(o.p_eval_base - p.pred)), COUNT(*) FROM pregame_availability_odds o
                         JOIN paper_eval_predictions p ON p.task = 'pregame' AND p.model = 'prior_rest' AND p.unit_id = o.game_id""")[0]
    assert worst[0] == 0 and worst[1] == 7230
    for phase, lo, hi in (("tune", 2021, 2024), ("validate", 2025, 2025), ("test", 2026, 2026)):
        for src in SOURCES:
            rows = np.array(_all(cur, f"""SELECT home_won::int, p_eval_{src} FROM pregame_availability_odds
                                          WHERE season BETWEEN %s AND %s AND p_eval_{src} IS NOT NULL""", (lo, hi)), float)
            y, p = rows[:, 0], np.clip(rows[:, 1], 1e-6, 1 - 1e-6)
            ll = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
            stored = _all(cur, """SELECT value FROM pregame_availability_fit WHERE kind = 'metric' AND name = 'log_loss'
                                  AND phase = %s AND source = %s""", (phase, src))[0][0]
            assert abs(ll - stored) < 1e-12
            t = _all(cur, """SELECT value_a, n FROM pregame_availability_tests WHERE phase = %s AND metric = 'log_loss'
                             AND model_a = %s AND model_b = 'prior_rest'""", (phase, f"avail_{src}"))[0]
            assert abs(t[0] - stored) < 1e-12 and t[1] == len(y)


def test_nothing_fitted_on_the_season_it_scores(cur):
    for season, fitted_on in _all(cur, """SELECT season, detail->'fitted_on' FROM pregame_availability_fit
                                          WHERE kind = 'coef' AND name = 'b' AND phase IN ('platform', 'tune')"""):
        assert season not in fitted_on
    for phase, season, fitted_on in _all(cur, """SELECT phase, season, detail->'fitted_on' FROM pregame_availability_fit
                                                 WHERE kind = 'coef' AND name = 'b' AND phase IN ('validate', 'test')"""):
        assert max(fitted_on) < season and 2026 not in fitted_on
    phase, season, detail = _all(cur, "SELECT phase, season, detail FROM pregame_availability_fit WHERE kind = 'choice'")[0]
    assert phase == "validate" and season == 2025 and set(detail["candidates"]) == set(SOURCES)
    assert _all(cur, "SELECT source FROM pregame_availability_fit WHERE kind = 'choice'")[0][0] == min(detail["candidates"], key=detail["candidates"].get)
    # constants come from the tune seasons only
    assert {d["seasons"] for (d,) in _all(cur, "SELECT detail FROM pregame_availability_fit WHERE kind = 'constant' AND detail IS NOT NULL")} == {"2020-21 to 2023-24"}


def test_expected_minutes_use_earlier_games_only(cur):
    rows = _all(cur, """SELECT p.game_id, p.team, p.player_id, p.exp_min, o.game_date, o.season FROM pregame_availability_players p
                        JOIN pregame_availability_odds o USING (game_id)
                        WHERE p.exp_from = 'team' ORDER BY md5(p.game_id || p.team || p.player_id) LIMIT 150""")
    assert len(rows) == 150
    for game_id, team, pid, exp_min, d, season in rows:
        # regular-season games only (the join to game_scores drops the NBA Cup finals, like the script)
        (mean,) = _all(cur, """SELECT AVG(l.seconds / 60.0) FROM player_game_lines l
                               JOIN game_scores g ON 'espn_' || g.espn_id = l.game_id AND g.team_abbreviation = l.team_abbreviation
                               WHERE l.player_id = %s AND l.team_abbreviation = %s AND l.season = %s AND l.game_date < %s
                               AND l.seconds > 0""", (pid, team, season, d))[0]
        assert mean is not None and abs(float(mean) - exp_min) < 1e-6, (game_id, team, pid)


def test_ratings_are_preseason_or_replacement(cur):
    repl = _const(cur, "replacement", "bpm")
    bad = _all(cur, """SELECT COUNT(*) FROM pregame_availability_players p JOIN pregame_availability_odds o USING (game_id)
                       LEFT JOIN projection_backtest_rows r ON r.stat = 'bpm' AND r.season = o.season AND r.player_id = p.player_id
                       WHERE (r.player_id IS NOT NULL AND (NOT p.rated_bpm OR ABS(p.r_bpm - r.projection) > 1e-9))
                          OR (r.player_id IS NULL AND (p.rated_bpm OR ABS(p.r_bpm - %s) > 1e-9))""", (repl,))[0][0]
    assert bad == 0
    bad = _all(cur, """SELECT COUNT(*) FROM pregame_availability_players p JOIN pregame_availability_odds o USING (game_id)
                       JOIN player_rapm r ON r.version = 'prior' AND r.season = o.season - 1 AND r.player_id = p.player_id
                       WHERE o.season >= 2022 AND ABS(p.r_rapm - r.rapm::text::float8) > 1e-9   -- rapm is float4: compare its printed value""")[0][0]
    assert bad == 0


def test_roster_rows_reproduce_lineup_strength(cur):
    for src in SOURCES:
        fill = _const(cur, "replacement", src)
        rows = _all(cur, f"""SELECT o.game_id, side, s, SUM(p.r_{src} * p.exp_min), SUM(p.exp_min)
                             FROM pregame_availability_odds o
                             CROSS JOIN LATERAL (VALUES ('h', o.home, o.s_home_{src}), ('a', o.away, o.s_away_{src})) v(side, team, s)
                             JOIN pregame_availability_players p ON p.game_id = o.game_id AND p.team = v.team AND p.played
                             WHERE o.lineups_ok AND o.p_{src} IS NOT NULL GROUP BY 1, 2, 3""")
        arr = np.array([r[2:] for r in rows], float)
        assert len(rows) > 12000
        assert np.abs(A.strength_from_sums(arr[:, 1], arr[:, 2], fill) - arr[:, 0]).max() < 1e-9


def test_what_if_endpoint(cur):
    from fastapi.testclient import TestClient
    import impact_api
    c = TestClient(impact_api.app)
    gid = "0022400690"     # WAS @ MIN, 2025-02-01: Edwards, Randle and DiVincenzo sat
    stored = _all(cur, "SELECT p_bpm FROM pregame_availability_odds WHERE game_id = %s", (gid,))[0][0]
    j = c.get(f"/pregame/availability/game/{gid}").json()
    assert j["available"] and abs(j["whatif"]["p"] - round(stored, 4)) < 1e-9 and not j["whatif"]["changed"]
    sat = [p for p in j["roster"] if not p["played"] and p["home"]]
    best = max(sat, key=lambda p: p["r"])
    j2 = c.get(f"/pregame/availability/game/{gid}?add={best['player_id']}").json()
    assert j2["whatif"]["p"] > j["whatif"]["p"] and j2["whatif"]["p10"] <= j2["whatif"]["p"] <= j2["whatif"]["p90"]
    assert abs(j2["whatif"]["p"] - best["p_if_toggled"]) < 1e-3
    assert c.get(f"/pregame/availability/game/{gid}?out={best['player_id']}").status_code == 400
    assert c.get("/pregame/availability/game/0022500614").json()["available"] is False
    m = c.get("/pregame/availability/model").json()
    assert m["chosen"] in SOURCES and [p["phase"] for p in m["phases"]] == ["tune", "validate", "test"]
    assert len(c.get("/pregame/availability/games", params={"date": "2025-02-01"}).json()["games"]) == 9
