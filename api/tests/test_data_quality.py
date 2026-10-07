"""
test_data_quality.py
====================
Guards round 6 step 11, the Data Quality page (scripts/build_data_quality.py ->
data_quality_game_flags / _sensitivity / _meta; api/data_quality_lib.py;
GET /data-quality/*):

  * every play-by-play game has one flag row, its level is the worst handling
    among the classes that touch it, and the per-game counts add back up to the
    audit's numbers (paper_data_audit) wherever the two count the same thing;
  * every live check agrees with the stored audit at the precision the paper
    prints, and every class's plain-text size can be written from the audit;
  * the "every game" rows reproduce the stored tests (paper_eval_tests,
    pregame_availability_tests) and possession_seasons' league numbers, and
    each drop set drops exactly the games its rule names;
  * the random-drop control is there for the sets of 50+ games and sane;
  * the endpoints answer and agree with the tables.

Skips when the database is unreachable or the script was never run.

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_data_quality.py
"""

import os
import sys

import psycopg2
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _d in (os.path.join(_ROOT, "api"), os.path.join(_ROOT, "scripts")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from db_config import DB_CONFIG  # noqa: E402
import data_quality_lib as Q  # noqa: E402

FULL_RESAMPLES = 10_000


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
    c.execute("SELECT to_regclass('data_quality_game_flags') IS NOT NULL AND to_regclass('data_quality_sensitivity') IS NOT NULL")
    if not c.fetchone()[0]:
        conn.close()
        pytest.skip("build_data_quality.py has not been run")
    yield c
    conn.close()


@pytest.fixture(scope="module")
def audit(cur):
    cur.execute("SELECT key, season, value, fmt FROM paper_data_audit")
    return {(k, s): (v, f) for k, s, v, f in cur.fetchall()}


def one(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchone()


def test_one_flag_row_per_game_and_levels(cur):
    # the flags are the paper's (frozen on 2020-21 to 2025-26); the live season's games aren't flagged (R9-011)
    assert one(cur, """SELECT count(*) FROM (SELECT * FROM lineup_stint_games WHERE season <= 2026) g
                       FULL JOIN data_quality_game_flags f USING (game_id)
                       WHERE g.game_id IS NULL OR f.game_id IS NULL""")[0] == 0
    cur.execute("SELECT game_id, classes, n_classes, level FROM data_quality_game_flags")
    order = [lv for lv, _, _ in Q.LEVELS]
    for gid, classes, n, level in cur.fetchall():
        assert set(classes) <= set(Q.PER_GAME) and n == len(classes), gid
        worst = min((order.index(Q.LEVEL_OF[c]) for c in classes), default=order.index("clean"))
        assert level == order[worst], (gid, classes, level)
    assert set(Q.LEVEL_OF) == set(Q.PER_GAME) and not set(Q.PER_GAME) & set(Q.NOT_PER_GAME)


def test_flags_add_up_to_the_audit(cur, audit):
    A = lambda k, s=0: audit[(k, s)][0]  # noqa: E731
    (tag, wrong, teamless, unrec, cup, outside, chart_missing, twin, back, last, miss3, matched) = one(cur, """
        SELECT count(*) FILTER (WHERE tag_text_events > 0), count(*) FILTER (WHERE wrong_player_events > 0),
               count(*) FILTER (WHERE teamless_subs > 0), count(*) FILTER (WHERE unreconciled), count(*) FILTER (WHERE cup_final),
               count(*) FILTER (WHERE clock_outside_events > 0), count(*) FILTER (WHERE chart_missing AND game_ok),
               count(*) FILTER (WHERE twin), count(*) FILTER (WHERE score_backward), count(*) FILTER (WHERE last_score_off),
               sum(missed_three_calls), sum(chart_matched)
        FROM data_quality_game_flags""")
    assert tag == A("tag_text_games") and wrong == A("wrong_player_games") and teamless == A("teamless_games")
    assert unrec == A("unrec_games") and cup == A("cup_games") and outside == A("clock_outside_games")
    assert chart_missing == A("chart_missing_games") and twin == A("twin_same_teams") + A("twin_neutral")
    assert back == A("score_backwards_games") and last == A("last_score_bad")
    assert miss3 == A("miss_threes_as_twos") + A("miss_twos_as_threes")
    assert matched == A("clock_pairs")
    assert one(cur, "SELECT sum(tag_text_events), sum(wrong_player_events) FROM data_quality_game_flags") == \
        (A("tag_text_events"), A("wrong_player_events"))
    cur.execute("SELECT season, count(*) FILTER (WHERE score_stale) FROM data_quality_game_flags GROUP BY 1")
    for season, n in cur.fetchall():
        assert n == A("score_steps_miss", season), season


def test_live_checks_agree_and_sizes_write(cur, audit):
    for key, fn in Q.LIVE.items():
        bad = [r for r in Q.compare(fn(cur), audit) if not r["ok"]]
        assert not bad, (key, bad[:3])
    cur.execute("SELECT key FROM paper_data_audit_classes ORDER BY ord")
    keys = [k for (k,) in cur.fetchall()]
    assert len(keys) == 18 and set(keys) == set(Q.LIVE) | set(Q.BUILD_ONLY)

    def S(k):
        return {s: v for (kk, s), (v, _) in audit.items() if kk == k and s}
    for k in keys:
        assert Q.size_text(k, lambda kk, s=0: audit[(kk, s)][0], S)


def test_every_game_rows_reproduce_the_stored_tests(cur):
    resamples = one(cur, "SELECT max(resamples) FROM data_quality_sensitivity")[0]
    for result, table in (("impact", "paper_eval_tests"), ("availability", "pregame_availability_tests")):
        cur.execute(f"""SELECT count(*), max(abs(d.diff - t.diff)), max(abs(d.value_a - t.value_a)), max(abs(d.ci_lo - t.ci_lo)),
                               count(t.task)
                        FROM data_quality_sensitivity d LEFT JOIN {table} t
                          ON t.task = d.task AND t.phase = d.phase AND t.metric = d.metric AND t.model_a = d.model_a
                         AND t.model_b = d.model_b AND t.variant = d.variant AND t.seasons = d.seasons AND t.resamples = d.resamples
                        WHERE d.result = %s AND d.drop_set = 'none'""", (result,))
        n, dd, da, dl, matched = cur.fetchone()
        assert n > 0
        if resamples == FULL_RESAMPLES:
            assert matched == n and dd < 1e-12 and da < 1e-12 and dl < 1e-12, (result, n, matched, dd, da, dl)
    cur.execute("""SELECT model_a, value_a FROM data_quality_sensitivity
                   WHERE result = 'possessions' AND drop_set = 'none' AND metric = 'ppp' AND model_b = ''""")
    ppp = dict(cur.fetchall())
    cur.execute("""SELECT start_type, sum(pts)::float8 / sum(poss) FROM possession_seasons WHERE team = 'ALL'
                   AND start_type IN ('steal', 'made_fg') AND season <= 2026 GROUP BY 1""")
    league = dict(cur.fetchall())
    for k, v in league.items():
        assert abs(ppp[k] - v) < 1e-12, k


def test_drop_sets_drop_their_games(cur):
    # possessions: the result's games are the reconciled possession games
    scope = one(cur, "SELECT count(*) FROM possession_games WHERE game_ok AND season <= 2026")[0]
    cur.execute("""SELECT DISTINCT drop_set, games_dropped, games_in_scope FROM data_quality_sensitivity WHERE result = 'possessions'""")
    sets = cur.fetchall()
    assert {s for s, _, _ in sets} >= {"none", "flagged", "tag_text", "unidentified"}
    for key, dropped, in_scope in sets:
        assert in_scope == scope
        if key == "none":
            assert dropped == 0
            continue
        rule = "f.level IN ('flagged', 'excluded')" if key == "flagged" else "%s = ANY(f.classes)"
        args = () if key == "flagged" else (key,)
        n = one(cur, f"""SELECT count(*) FROM possession_games g JOIN data_quality_game_flags f USING (game_id)
                         WHERE g.game_ok AND {rule}""", args)[0]
        assert n == dropped, key
        if key != "flagged":
            assert 0 < n <= Q.MAX_DROP_SHARE * scope, key
    # a class on more than half the games is never a drop set on its own
    cur.execute("SELECT DISTINCT drop_set FROM data_quality_sensitivity")
    assert not {k for (k,) in cur.fetchall()} & {"zero_distance", "missed_threes"}
    # impact: the result's games are the games with a tracked stint side that had a possession (build_rapm.load_rows)
    scope = one(cur, "SELECT count(DISTINCT game_id) FROM lineup_stints WHERE tracked_ok AND (home_poss > 0 OR away_poss > 0) "
                     "AND season <= 2026")[0]
    assert one(cur, "SELECT min(games_in_scope), max(games_in_scope) FROM data_quality_sensitivity WHERE result = 'impact'") == (scope, scope)


def test_random_control(cur):
    cur.execute("""SELECT drop_set, model_b, games_dropped, rand_draws, rand_lo, rand_hi, rand_p FROM data_quality_sensitivity""")
    for key, b, dropped, draws, lo, hi, p in cur.fetchall():
        if b and key != "none" and dropped >= 50:
            assert draws == 30 and lo <= hi and 1 / 31 - 1e-12 <= p <= 1, (key, b)
        else:
            assert draws is None and p is None, (key, b)


def test_endpoints(cur):
    from fastapi.testclient import TestClient
    import impact_api
    c = TestClient(impact_api.app)
    o = c.get("/data-quality/overview").json()
    assert len(o["classes"]) == 18 and sum(x["live"]["mode"] == "live" for x in o["classes"]) == len(Q.LIVE)
    assert sum(lv["games"] for lv in o["levels"]) == o["games"] == one(cur, "SELECT count(*) FROM data_quality_game_flags")[0]
    assert set(o["results"]) == set(Q.RESULTS)
    ck = c.get("/data-quality/check/unreconciled").json()
    assert ck["agree"] == ck["total"] >= 1
    assert c.get("/data-quality/check/tag_text").status_code == 400 and c.get("/data-quality/check/nope").status_code == 404
    g = c.get("/data-quality/games", params={"cls": "tag_text", "limit": 200}).json()
    assert g["total"] == one(cur, "SELECT count(*) FROM data_quality_game_flags WHERE tag_text_events > 0")[0]
    assert all("tag_text" in x["classes"] and any(d["key"] == "tag_text" for d in x["details"]) for x in g["games"])
    assert c.get("/data-quality/games", params={"cls": "nope"}).status_code == 400
    s = c.get("/data-quality/sensitivity", params={"result": "impact"}).json()
    none = next(x for x in s["sets"] if x["key"] == "none")
    cell = next(x for x in none["cells"] if x["a"] == "rapm_prior" and x["b"] == "bpm" and x["phase"] == "test")
    ref = one(cur, """SELECT diff FROM paper_eval_tests WHERE task = 'impact_next' AND phase = 'test' AND metric = 'game_rmse'
                      AND model_a = 'rapm_prior' AND model_b = 'bpm' AND variant = ''""")[0]
    assert abs(cell["diff"] - ref) < 1e-6
    assert c.get("/data-quality/sensitivity", params={"result": "nope"}).status_code == 400
