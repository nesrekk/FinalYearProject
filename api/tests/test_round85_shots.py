"""
test_round85_shots.py
======================
Round 8.5 step C (docs/qa/ROUND8_ISSUES.md R8-028, R8-088): the 2025-26 shot chart re-fetched from
stats.nba.com (scripts/fetch_season_shots.py) and merged into player_shots, pinned.

  - Every 2025-26 game has chart rows: the 1,230 regular-season games (the bulk file had none for four games
    of 2025-11-19/20) and the 91 playoff and play-in games (the bulk file had none).
  - The chart's attempts per game equal NBA.com's box score (game_team_box) in every game 2020-21 to
    2025-26 but the pinned few, so the 2025-26 chart is complete, not "thin" as R8-028 first read it.
  - The cached raw chart (if present) reproduces the stored 2025-26 rows exactly: a rerun writes nothing.
  - The merge rule pairs shots one-to-one on every key and treats a moved coordinate as a new shot.
  - rebuild_all.sh merges right after the bulk load (which would otherwise bring the old rows back).
  - R8-088 (open): ESPN's 2025-26 play-by-play logs end-of-quarter heaves that the NBA counts as team
    attempts only since 2025-26, so the lines' FGA run over NBA.com's by about one heave a game.

Usage:
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_round85_shots.py
"""

import os
import sys

import pandas as pd
import psycopg2
import pytest

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.dirname(_API_DIR)
_SCRIPTS = os.path.join(_REPO, "scripts")
for _d in (_API_DIR, _SCRIPTS):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from db_config import DB_CONFIG  # noqa: E402
import fetch_season_shots as F  # noqa: E402

CACHE = os.path.join(_REPO, "shots_data", "shotchart_detail", "2026")
FOUR = ["0022500259", "0022500260", "0022500261", "0022500265"]


def _db_reachable() -> bool:
    try:
        psycopg2.connect(**DB_CONFIG, connect_timeout=3).close()
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(not _db_reachable(), reason="Local Postgres DB is not reachable.")


@pytest.fixture(scope="module")
def conn():
    c = psycopg2.connect(**DB_CONFIG)
    yield c
    c.close()


@pytest.fixture(scope="module")
def cur(conn):
    k = conn.cursor()
    yield k
    k.close()


def one(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchone()


@needs_db
def test_2025_26_chart_covers_every_game(cur):
    reg, post = one(cur, """SELECT count(DISTINCT game_id) FILTER (WHERE game_id LIKE '002%%'),
                                   count(DISTINCT game_id) FILTER (WHERE game_id LIKE '004%%' OR game_id LIKE '005%%')
                            FROM player_shots WHERE season = '2025-26'""")
    (sched,) = one(cur, "SELECT count(DISTINCT game_id) FROM game_scores WHERE season = 2026")
    (n_post,) = one(cur, "SELECT count(*) FROM postseason_games WHERE season = 2026")
    assert reg == sched == 1230
    assert post == n_post == 91
    cur.execute("""SELECT game_id, count(*) FROM player_shots WHERE season = '2025-26' AND game_id = ANY(%s)
                   GROUP BY 1 ORDER BY 1""", (FOUR,))
    got = dict(cur.fetchall())
    assert sorted(got) == FOUR and all(n > 150 for n in got.values()), got
    cur.execute("""SELECT game_id FROM game_scores WHERE season = 2026
                   EXCEPT SELECT game_id FROM player_shots WHERE season = '2025-26'""")
    assert cur.fetchall() == []


@needs_db
def test_chart_attempts_equal_the_nba_box_score(cur):
    """Per game, the chart's attempts = NBA.com's box-score FGA (both teams). Measured 2026-10-06: equal in
    every game of 2020-21, 2021-22, 2022-23, 2024-25; 2023-24 has 13 games one attempt apart; 2025-26 one
    (0022500792: chart 170, box 169). Before the re-fetch the four games with no rows were 2025-26's gaps."""
    cur.execute("""WITH ch AS (SELECT LEFT(season, 4)::int + 1 AS season, game_id, count(*) n FROM player_shots
                               WHERE game_id LIKE '002%%' AND season >= '2020-21' GROUP BY 1, 2),
                        bx AS (SELECT season, game_id, sum(fga) n FROM game_team_box WHERE season <= 2026 GROUP BY 1, 2)
                   SELECT bx.season, count(*), count(*) FILTER (WHERE ch.n IS DISTINCT FROM bx.n),
                          max(abs(coalesce(ch.n, 0) - bx.n))
                   FROM bx LEFT JOIN ch USING (season, game_id) GROUP BY 1 ORDER BY 1""")
    rows = {s: (n, off, worst) for s, n, off, worst in cur.fetchall()}
    assert set(rows) == {2021, 2022, 2023, 2024, 2025, 2026}
    for season, (n, off, worst) in rows.items():
        assert off == {2024: 13, 2026: 1}.get(season, 0), (season, n, off, worst)
        assert worst <= 1, (season, worst)
    assert one(cur, """SELECT count(*) FROM player_shots WHERE season = '2025-26' AND game_id = '0022500792'""")[0] == 170


@needs_db
@pytest.mark.skipif(not os.path.exists(os.path.join(CACHE, "ATL_regular.csv")),
                    reason="the 2025-26 chart cache (shots_data/shotchart_detail/2026/, gitignored) isn't here")
def test_the_cached_chart_reproduces_the_stored_rows(conn):
    """The merge is idempotent: pairing the cached raw chart against player_shots leaves nothing to delete or
    insert, and every paired shot's 2/3 call agrees."""
    raw = F.fetch_all(2026, list(F.TYPES), CACHE, offline=True, refresh=False, pause=0)
    st = F.to_stage(raw, 2026)
    both, old_only, new_only = F.pair(st, F.stored(conn, 2026))
    assert len(old_only) == 0 and len(new_only) == 0
    assert len(both) == len(st) == 234_673
    assert (both.shot_type == both.shot_type_stored).all()
    # inserted rows follow the stored distance convention: coordinate distance rounded half-up
    assert (st.shot_distance == (((st.loc_x ** 2 + st.loc_y ** 2) ** 0.5) / 10 + 0.5).astype(int)).all()


def test_merge_pairs_one_to_one_and_a_moved_shot_is_new():
    k = dict(game_id="0022500001", player_id=1, period=1, minutes_remaining=11, seconds_remaining=30,
             loc_x=0, loc_y=10, shot_made_flag=1)
    st = pd.DataFrame([k, k, {**k, "loc_x": 5}]).assign(shot_type="2PT Field Goal", game_event_id=[1, 2, 3])
    sd = pd.DataFrame([k, {**k, "loc_x": 4}]).assign(shot_type="2PT Field Goal", id=[10, 11])
    both, old_only, new_only = F.pair(st, sd)
    assert list(both.id) == [10]                       # one stored copy pairs with one staged copy
    assert list(old_only.id) == [11]                   # a stored shot whose coordinates moved goes ...
    assert sorted(new_only.game_event_id) == [2, 3]    # ... and the moved one and the second copy come in


def test_rebuild_all_merges_right_after_the_bulk_load():
    with open(os.path.join(_SCRIPTS, "rebuild_all.sh")) as f:
        steps = [ln.split("#")[0].split() for ln in f if ln.startswith("step ")]
    names = [(s[1], " ".join(s[3:])) for s in steps]
    i = names.index(("load", "load_pbp_shots.py"))
    assert names[i + 1] == ("load", "fetch_season_shots.py --season 2026 --offline --apply")
    assert ("fetch", "fetch_season_shots.py --season 2026 --fetch-only") in names


@needs_db
def test_espn_heaves_explain_the_lines_surplus(cur):
    """R8-088 (open): from 2025-26 the NBA records a missed end-of-quarter heave (final 3 s of quarters 1-3,
    36+ ft, play started in the backcourt) as a team attempt, not the shooter's (ESPN, 2025-09-10,
    https://www.espn.com/nba/story/_/id/46217260/long-end-quarter-shots-count-nba-teams-not-players, read
    2026-10-06). ESPN's play-by-play logs them as "misses heave jump shot" (action type 'Heave Jump Shot',
    none before 2025-26) and player_game_lines counts them as the shooter's FGA, so the lines run over NBA.com's
    box-score FGA in 2025-26 by the game's heave count in 1,171 of 1,229 games. If this fails because the
    surplus is gone, the parser now drops them: update R8-088, README and this test."""
    cur.execute("""SELECT g.season, count(*) FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
                   WHERE g.source = 'espn' AND e.action_type = 'Heave Jump Shot' GROUP BY 1 ORDER BY 1""")
    assert [s for s, _ in cur.fetchall()][0] == 2026  # none before 2025-26 (the live season has them too)
    games, eq, surplus, heaves = one(cur, """
        WITH hv AS (SELECT e.game_id, count(*) n FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
                    WHERE g.source = 'espn' AND g.season = 2026 AND e.action_type = 'Heave Jump Shot' GROUP BY 1),
             bx AS (SELECT game_id, sum(fga) box FROM game_team_box WHERE season = 2026 GROUP BY 1),
             ln AS (SELECT s.game_id nba, l.game_id espn, sum(l.fga) lines FROM player_game_lines l
                    JOIN (SELECT DISTINCT game_id, espn_id FROM game_scores WHERE season = 2026) s
                      ON 'espn_' || s.espn_id = l.game_id GROUP BY 1, 2)
        SELECT count(*), count(*) FILTER (WHERE lines - box = coalesce(hv.n, 0)), sum(lines - box), sum(hv.n)
        FROM ln JOIN bx ON bx.game_id = ln.nba LEFT JOIN hv ON hv.game_id = ln.espn""")
    assert games == 1229 and eq >= 1171, (games, eq)
    assert 1000 <= surplus <= 1150 and 1050 <= heaves <= 1150, (surplus, heaves)
