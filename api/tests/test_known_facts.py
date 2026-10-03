"""
test_known_facts.py
====================
Guards the stored data against things that are either true in the real world
or must hold inside our own tables. Separate from test_smoke.py (which checks
that endpoints answer); this file checks that the numbers underneath are right.

Two kinds of test, kept apart on purpose:

  A. REAL-WORLD FACTS   Each one names its outside source in a comment and was
     read from that source before it was added (same rule as Greats trivia:
     never add a fact from memory). If one fails, the stored data or a build
     script is wrong, not the fact.
     Basketball-Reference blocks automated fetches (HTTP 403), so the sources
     are ESPN box scores, Wikipedia and NBA.com pages, read on 2026-09-29.

  B. INTERNAL INVARIANTS   Relations between our own tables that must hold
     (totals that add up, keys that match, reconciliation rates). Where a
     small, understood gap exists the test pins its size; the tolerance and
     the reason are written next to it, so a bigger gap fails.

Every test skips cleanly if its table isn't built on this database (a
Layerbase mirror that hasn't been synced yet, a machine without the gitignored
Kaggle export, ...). Run against either database:

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_known_facts.py
    DB_TARGET=layerbase /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests/test_known_facts.py

Adding a fact: see README "Known-facts test suite".
"""

import os
import sys

import psycopg2
import pytest

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from db_config import DB_CONFIG  # noqa: E402


def _db_reachable() -> bool:
    try:
        conn = psycopg2.connect(**DB_CONFIG, connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="Postgres DB is not reachable: these are real-data checks, not mocks.",
)


@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.set_session(readonly=True, autocommit=True)
    c = conn.cursor()
    yield c
    c.close()
    conn.close()


def need(cur, *tables):
    """Skip the calling test unless every table exists and has rows."""
    for t in tables:
        cur.execute("SELECT to_regclass(%s)", (f"public.{t}",))
        if cur.fetchone()[0] is None:
            pytest.skip(f"{t} not built on this database")
        cur.execute(f"SELECT EXISTS (SELECT 1 FROM {t})")
        if not cur.fetchone()[0]:
            pytest.skip(f"{t} is empty on this database")


def one(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchone()


def rows(cur, sql, params=None):
    cur.execute(sql, params)
    return cur.fetchall()


# ═════════════════════════════════════════════════════════════════════════════
# A. REAL-WORLD FACTS
# ═════════════════════════════════════════════════════════════════════════════

# The five 70+ point games since 2020-21 (the span of player_game_lines), with
# the box-score line each source gives. Keys: pts, fgm, fga, fg3m, fg3a, ftm, fta.
# player_id is the NBA id.
#
#   Adebayo, MIA 150-129 WAS, 2026-03-10: Wikipedia "Bam Adebayo's 83-point game"
#     https://en.wikipedia.org/wiki/Bam_Adebayo's_83-point_game
#     (20/43 FG, 7/22 3P, 36/43 FT) and ESPN
#     https://www.espn.com/nba/game/_/gameId/401810793/wizards-heat (20/43, 36/43).
#     Our lines say 44 FGA: see test_adebayo_fga_known_gap.
#   Doncic, DAL 148-143 ATL, 2024-01-26: ESPN recap
#     https://www.espn.com/nba/recap/_/gameId/401585262 (25/33, 8/13, 15/16).
#   Embiid, PHI 133-123 SAS, 2024-01-22: ESPN box score
#     https://www.espn.com/nba/boxscore/_/gameId/401585236 (24/41, 1/2, 21/23).
#   Lillard, POR 131-114 HOU, 2023-02-26: ESPN box score
#     https://www.espn.com/nba/boxscore/_/gameId/401469072 (22/38, 13/22, 14/14).
#   Mitchell, CLE 145-134 CHI (OT), 2023-01-02: ESPN box score
#     https://www.espn.com/nba/boxscore/_/gameId/401468707 (22/34, 7/15, 20/25).
SEVENTY_POINT_GAMES = [
    pytest.param(1628389, "2026-03-10", "MIA", dict(pts=83, fgm=20, fg3m=7, fg3a=22, ftm=36, fta=43), id="adebayo-83"),
    pytest.param(1629029, "2024-01-26", "DAL", dict(pts=73, fgm=25, fga=33, fg3m=8, fg3a=13, ftm=15, fta=16), id="doncic-73"),
    pytest.param(203954, "2024-01-22", "PHI", dict(pts=70, fgm=24, fga=41, fg3m=1, fg3a=2, ftm=21, fta=23), id="embiid-70"),
    pytest.param(203081, "2023-02-26", "POR", dict(pts=71, fgm=22, fga=38, fg3m=13, fg3a=22, ftm=14, fta=14), id="lillard-71"),
    pytest.param(1628378, "2023-01-02", "CLE", dict(pts=71, fgm=22, fga=34, fg3m=7, fg3a=15, ftm=20, fta=25), id="mitchell-71"),
]


@pytest.mark.parametrize("player_id,date,team,line", SEVENTY_POINT_GAMES)
def test_seventy_point_game_matches_box_score(cur, player_id, date, team, line):
    need(cur, "player_game_lines")
    cols = list(line)
    row = one(
        cur,
        f"SELECT {', '.join(cols)} FROM player_game_lines "
        "WHERE player_id = %s AND game_date = %s AND team_abbreviation = %s",
        (player_id, date, team),
    )
    assert row is not None, f"no line for player {player_id} on {date}"
    assert dict(zip(cols, row)) == line


def test_adebayo_fga_known_gap(cur):
    """Adebayo's official line is 43 FGA (Wikipedia, ESPN). The play-by-play
    lines count 44: ESPN logs a Q2 heave at 1440.1 s (after the buzzer) as a
    miss. The NBA shot chart (player_shots) has the official 43. Pinned so the
    gap can't grow; if the parser is fixed to 43 this still passes."""
    need(cur, "player_game_lines", "player_shots", "game_scores")
    lines_fga = one(
        cur,
        "SELECT fga FROM player_game_lines WHERE player_id = 1628389 AND game_date = '2026-03-10'",
    )[0]
    chart = one(
        cur,
        """SELECT COUNT(*), COUNT(*) FILTER (WHERE shot_made_flag = 1) FROM player_shots
           WHERE player_id = 1628389
             AND game_id = (SELECT game_id FROM game_scores WHERE espn_id = '401810793' LIMIT 1)""",
    )
    assert chart == (43, 20), f"shot chart should show 20/43, got {chart}"
    assert lines_fga - 43 in (0, 1)


def test_only_five_seventy_point_games_since_2020_21(cur):
    """Sources: the Adebayo Wikipedia article and the Embiid write-up
    (https://www.nba.com/sixers/news/embiid-scores-70-points-vs-spurs-jan-22-2024)
    list the 70-point games in NBA history; the only ones after Booker (2017)
    are Mitchell, Lillard, Embiid, Doncic and Adebayo."""
    need(cur, "player_game_lines")
    ids = [r[0] for r in rows(cur, "SELECT player_id FROM player_game_lines WHERE pts >= 70 ORDER BY game_date")]
    assert ids == [1628378, 203081, 203954, 1629029, 1628389]


# Regular-season records. Source: Wikipedia season pages, read 2026-09-29:
#   1995-96 Bulls 72-10  https://en.wikipedia.org/wiki/1995%E2%80%9396_Chicago_Bulls_season
#   2015-16 Warriors 73-9  https://en.wikipedia.org/wiki/2015%E2%80%9316_Golden_State_Warriors_season
#   2023-24 Celtics 64-18  https://en.wikipedia.org/wiki/2023%E2%80%9324_Boston_Celtics_season
# team_seasons is the Basketball-Reference-derived export (gitignored Kaggle
# data); the 2015-16 and 2023-24 records are also counted from game_scores,
# which comes from ESPN's scoreboard, an independent path.
TEAM_RECORDS = [
    pytest.param(1996, "CHI", 72, 10, id="1995-96-bulls"),
    pytest.param(2016, "GSW", 73, 9, id="2015-16-warriors"),
    pytest.param(2024, "BOS", 64, 18, id="2023-24-celtics"),
]


@pytest.mark.parametrize("season,abbr,wins,losses", TEAM_RECORDS)
def test_team_season_record(cur, season, abbr, wins, losses):
    need(cur, "team_seasons")
    row = one(
        cur,
        "SELECT w, l FROM team_seasons WHERE season = %s AND abbreviation = %s AND lg = 'NBA' AND NOT is_league_avg",
        (season, abbr),
    )
    assert row == (wins, losses)


@pytest.mark.parametrize("season,abbr,wins,losses", [p for p in TEAM_RECORDS if p.values[0] >= 2010])
def test_team_season_record_from_game_scores(cur, season, abbr, wins, losses):
    need(cur, "game_scores")
    row = one(
        cur,
        """SELECT COUNT(*) FILTER (WHERE pts_for > pts_against), COUNT(*) FILTER (WHERE pts_for < pts_against)
           FROM game_scores WHERE season = %s AND team_abbreviation = %s""",
        (season, abbr),
    )
    assert row == (wins, losses)


def test_wilt_1961_62_scoring_average(cur):
    """Wilt Chamberlain, 1961-62: 50.4 points a game over 80 games (4,029
    points). Source: https://en.wikipedia.org/wiki/1961%E2%80%9362_Philadelphia_Warriors_season
    and Land of Basketball's leaders table (read 2026-09-29)."""
    need(cur, "player_season_stats")
    row = one(
        cur,
        "SELECT gp, pts FROM player_season_stats WHERE player_name = 'Wilt Chamberlain' AND season = 1962",
    )
    assert row == (80, 50.4)


def test_lebron_rookie_season_averages(cur):
    """LeBron James, 2003-04: 79 games, 20.9 points, 5.5 rebounds, 5.9 assists.
    Source: Wikipedia, List of career achievements by LeBron James
    (https://en.wikipedia.org/wiki/List_of_career_achievements_by_LeBron_James), read 2026-09-29."""
    need(cur, "player_season_stats")
    row = one(
        cur,
        "SELECT gp, pts, reb, ast FROM player_season_stats WHERE player_name = 'LeBron James' AND season = 2004",
    )
    assert row == (79, 20.9, 5.5, 5.9)


def test_curry_402_threes_2015_16(cur):
    """Stephen Curry made 402 three-pointers in 2015-16, the record then.
    Source: https://en.wikipedia.org/wiki/2015%E2%80%9316_Golden_State_Warriors_season
    (read 2026-09-29). Checked two ways: the stored regular-season shots, and the
    per-game season line (5.1 x 79 games = 402.9, per-game is rounded)."""
    need(cur, "player_shots", "player_season_stats")
    made = one(
        cur,
        """SELECT COUNT(*) FILTER (WHERE shot_made_flag = 1) FROM player_shots
           WHERE player_name = 'Stephen Curry' AND season = '2015-16'
             AND shot_type LIKE '3%' AND game_id LIKE '002%'""",
    )[0]
    assert made == 402
    fg3m, gp = one(
        cur,
        "SELECT fg3m, gp FROM player_season_stats WHERE player_name = 'Stephen Curry' AND season = 2016",
    )
    assert abs(fg3m * gp - 402) <= 0.05 * gp


def test_team_season_totals_match_basketball_reference(cur):
    """Season point totals, opponent totals and records equal Basketball-
    Reference's for all 510 team-seasons 2009-10 to 2025-26 (30 teams x 17).
    team_seasons is the B-Ref export; team_luck_schedule sums game_scores
    (ESPN). B-Ref's points per game are rounded to 0.1, hence the 0.05."""
    need(cur, "team_luck_schedule", "team_seasons")
    n, worst_for, worst_against, bad_record = one(
        cur,
        """SELECT COUNT(*),
                  MAX(ABS(l.pts_for::float / l.games - t.pts_per_game)),
                  MAX(ABS(l.pts_against::float / l.games - t.opp_pts_per_game)),
                  SUM((l.wins <> t.w OR l.losses <> t.l OR l.games <> t.g)::int)
           FROM team_luck_schedule l
           JOIN team_seasons t ON t.season = l.season AND t.abbreviation = l.team_abbreviation
                              AND t.lg = 'NBA' AND NOT t.is_league_avg""",
    )
    assert n == 510
    assert worst_for <= 0.0501 and worst_against <= 0.0501
    assert bad_record == 0


def test_srs_matches_basketball_reference(cur):
    """Our SRS (from ESPN final scores) against Basketball-Reference's published
    SRS over the same 510 team-seasons: correlation >= 0.999 (2026-09-29: 0.99996)
    and no team-season more than 0.3 apart (largest 0.257: B-Ref rounds and
    solves its own ratings slightly differently)."""
    need(cur, "team_luck_schedule", "team_seasons")
    r, worst = one(
        cur,
        """SELECT CORR(l.srs, t.srs), MAX(ABS(l.srs - t.srs))
           FROM team_luck_schedule l
           JOIN team_seasons t ON t.season = l.season AND t.abbreviation = l.team_abbreviation
                              AND t.lg = 'NBA' AND NOT t.is_league_avg""",
    )
    assert r >= 0.999
    assert worst <= 0.3


# Every player's +/- in the same five 70+ point games, from ESPN's box score
# (the "+/-" column; read 2026-10-03 from the box score data behind the pages
# above, https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event=<id>,
# whose numbers the www.espn.com/nba/boxscore/_/gameId/<id> pages show).
# player_id -> ESPN +/-.
BOX_PLUS_MINUS = {
    "espn_401810793": {1628389: 20, 1642857: 10, 1631170: 15, 1642066: 15, 1641796: 13, 1630558: 16, 1630696: 7,
                       1631323: 6, 1642352: 7, 1631211: -2, 1642884: -2, 1642860: -10, 1642267: -20, 1641731: -19,
                       1630264: -5, 1642848: -14, 1630702: -8, 1642259: -9, 1630551: -7, 1630536: -2, 1641774: -11},
    "espn_401585262": {1630180: -14, 1627749: -21, 1630552: 6, 1629027: 9, 203992: 3, 203991: -7, 1630168: 2,
                       1629726: 5, 201988: -8, 1629029: 13, 1630182: 9, 203501: -5, 1629684: 5, 1641726: 16, 203957: 2,
                       1627884: -2, 1626158: -3, 1630702: -10},
    "espn_401585236": {1630178: 12, 203954: 11, 202699: 21, 201587: 3, 1626162: -5, 1627863: 2, 1630231: 9,
                       1630194: -6, 1627788: 13, 1641741: -5, 1630170: -17, 1630200: -13, 1631110: -6, 1641705: 0,
                       203926: -7, 1629640: 4, 1628380: -10, 1631104: 3, 1630577: 0, 1626224: -4},
    "espn_401469072": {1631095: -27, 1630231: -7, 1631102: -4, 1630227: -9, 1631106: -11, 1630578: -14, 1630256: -7,
                       1630528: -10, 1630586: 5, 1626246: -1, 203081: 21, 1629680: 29, 203924: 14, 1629629: 30,
                       1630570: 9, 1629234: 14, 1629642: -7, 1631101: -14, 1631133: -7, 1630553: -4, 1628995: 0},
    "espn_401468707": {201942: -9, 202696: -19, 203897: -12, 1630245: -9, 1630172: -2, 1627936: -1, 1627884: -6,
                       1629632: -4, 201609: 2, 203083: 5, 1628378: 19, 1627747: 7, 1628386: 11, 201567: 6, 1626224: 15,
                       1630171: 3, 1630205: -6, 203526: 3, 201577: -3},
}
# Mitchell's game (overtime): four Bulls are one point off, one free throw the
# play-by-play places in a different lineup than the official box (pinned).
BOX_PLUS_MINUS_OFF_BY_ONE = {"espn_401468707": {1630172, 1627936, 1627884, 1629632}}


@pytest.mark.parametrize("game_id", list(BOX_PLUS_MINUS))
def test_on_floor_plus_minus_matches_box_score(cur, game_id):
    """player_game_onfloor (free throws credited to the lineup at the foul) gives
    every player's box-score +/-; player_game_lines' tm_pts - op_pts gets 3 to 8
    of the ~20 right in these games (2026-10-03)."""
    need(cur, "player_game_onfloor")
    ours = dict(rows(cur, "SELECT player_id, plus_minus FROM player_game_onfloor WHERE game_id = %s", (game_id,)))
    box = BOX_PLUS_MINUS[game_id]
    assert set(ours) == set(box)
    pinned = BOX_PLUS_MINUS_OFF_BY_ONE.get(game_id, set())
    for pid, pm in box.items():
        if pid in pinned:
            assert abs(ours[pid] - pm) == 1, pid
        else:
            assert ours[pid] == pm, pid


# ═════════════════════════════════════════════════════════════════════════════
# B. INTERNAL INVARIANTS
# ═════════════════════════════════════════════════════════════════════════════

def test_game_scores_and_fatigue_are_the_same_rows(cur):
    need(cur, "game_scores", "team_game_fatigue")
    (orphans,) = one(
        cur,
        """SELECT COUNT(*) FROM game_scores g
           FULL JOIN team_game_fatigue f USING (game_id, team_abbreviation)
           WHERE g.game_id IS NULL OR f.game_id IS NULL""",
    )
    assert orphans == 0
    n_scores, n_fatigue = one(
        cur, "SELECT (SELECT COUNT(*) FROM game_scores), (SELECT COUNT(*) FROM team_game_fatigue)"
    )
    assert n_scores == n_fatigue


def test_every_game_has_two_mirrored_rows(cur):
    need(cur, "game_scores")
    (not_two,) = one(
        cur, "SELECT COUNT(*) FROM (SELECT game_id FROM game_scores GROUP BY 1 HAVING COUNT(*) <> 2) x"
    )
    (not_mirrored,) = one(
        cur,
        """SELECT COUNT(*) FROM game_scores a
           JOIN game_scores b ON a.game_id = b.game_id AND a.team_abbreviation = b.opponent
           WHERE a.pts_for <> b.pts_against OR a.opponent <> b.team_abbreviation""",
    )
    assert not_two == 0
    assert not_mirrored == 0


def test_team_game_totals_reconcile_with_final_scores(cur):
    """Points taken from the play-by-play equal the real final in all but the
    ~43 games with bad play-by-play (checked 2026-09-29: 86 of 14,458 team-games,
    99.4%). The ESPN play-by-play links to game_scores through espn_id."""
    need(cur, "team_game_totals", "game_scores")
    n, bad = one(
        cur,
        """SELECT COUNT(*), SUM((t.pts_for <> g.pts_for OR t.pts_against <> g.pts_against)::int)
           FROM team_game_totals t
           JOIN game_scores g ON 'espn_' || g.espn_id = t.game_id AND g.team_abbreviation = t.team_abbreviation""",
    )
    assert n >= 14400
    assert bad <= 100


def test_lineup_stints_reconcile_per_game(cur):
    """Five-man stints: game_ok games (7,220 of 7,232 on 2026-09-29) have stint
    seconds equal to game length and stint points equal to the real final."""
    need(cur, "lineup_stint_games")
    n, ok, sec_bad, pts_bad, len_bad = one(
        cur,
        """SELECT COUNT(*), SUM(game_ok::int),
                  SUM((game_ok AND stint_seconds <> game_length)::int),
                  SUM((points_ok AND (home_pts <> final_home OR away_pts <> final_away))::int),
                  SUM((game_length <> 2880 + 300 * (periods - 4))::int)
           FROM lineup_stint_games""",
    )
    assert n >= 7232
    assert ok / n >= 0.995
    assert sec_bad == 0
    assert pts_bad == 0
    # 3 games (an NBA Cup final and games with missing periods) have odd lengths.
    assert len_bad <= 5


def test_lineup_stint_rows_add_up_to_game_length(cur):
    need(cur, "lineup_stints", "lineup_stint_games")
    (bad,) = one(
        cur,
        """SELECT COUNT(*) FROM lineup_stint_games g
           JOIN (SELECT game_id, SUM(seconds) s FROM lineup_stints GROUP BY 1) x USING (game_id)
           WHERE g.game_ok AND ABS(x.s - g.game_length) > 0.5""",
    )
    assert bad == 0


def test_on_floor_plus_minus_adds_up_to_five_times_the_margin(cur):
    """player_game_onfloor: in a game_ok game, a side with five on the floor all
    game sums to 5 x the real final margin (all 12,874 such team-games on
    2026-10-03); the rest (a stretch with four or six listed) mostly don't."""
    need(cur, "player_game_onfloor", "lineup_stint_games", "lineup_stints")
    n, bad = one(
        cur,
        """WITH five AS (
               SELECT game_id, home_team AS team, bool_and(n_home = 5) ok FROM lineup_stints GROUP BY 1, 2
               UNION ALL
               SELECT game_id, away_team, bool_and(n_away = 5) FROM lineup_stints GROUP BY 1, 2),
           pm AS (SELECT game_id, team, SUM(plus_minus) s FROM player_game_onfloor GROUP BY 1, 2)
           SELECT COUNT(*), SUM((pm.s <> 5 * CASE WHEN pm.team = g.home_team THEN g.final_home - g.final_away
                                                  ELSE g.final_away - g.final_home END)::int)
           FROM pm JOIN five USING (game_id, team) JOIN lineup_stint_games g USING (game_id)
           WHERE g.game_ok AND five.ok""",
    )
    assert n >= 12800
    assert bad == 0


def test_on_floor_seconds_equal_the_stints(cur):
    """Same lineups as lineup_stints: a player's on-floor seconds equal his
    stints' (to their 0.1 s rounding)."""
    need(cur, "player_game_onfloor", "lineup_stints")
    (bad,) = one(
        cur,
        """WITH sides AS (
               SELECT game_id, u.pid, seconds FROM lineup_stints, unnest(home_ids) u(pid)
               UNION ALL SELECT game_id, u.pid, seconds FROM lineup_stints, unnest(away_ids) u(pid)),
           s AS (SELECT game_id, pid AS player_id, SUM(seconds) sec, COUNT(*) n FROM sides GROUP BY 1, 2)
           SELECT COUNT(*) FROM player_game_onfloor o JOIN s USING (game_id, player_id)
           WHERE ABS(o.seconds - s.sec) > 0.05 * s.n + 0.1""",
    )
    assert bad == 0


def test_on_plus_off_equals_team_total_over_games_played(cur):
    """A player's on-court plus off-court games, possessions and points equal his
    team's totals over the games he played (off-court is defined that way):
    possessions from team_game_totals, points from the real final score
    (game_scores), over the games whose play-by-play reconciles. Since the
    2026-10-03 rebuild on the corrected on-floor points no on-court total
    exceeds the final, so points match in every row (before, 26 of 3,665 rows
    were off, the old on-court points double-counting)."""
    need(cur, "player_on_off", "player_game_lines", "team_game_totals", "game_scores", "player_game_onfloor")
    n, bad_games, worst_poss, bad_pts = one(
        cur,
        """WITH pgm AS (
               SELECT l.player_id, l.season, l.team_abbreviation, l.game_id,
                      gs.pts_for, gs.pts_against
               FROM player_game_lines l
               JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
               JOIN game_scores gs ON gs.game_id = f.game_id AND gs.team_abbreviation = f.team_abbreviation
               JOIN player_game_onfloor o ON o.player_id = l.player_id AND o.game_id = l.game_id AND o.game_ok
               WHERE l.seconds > 0),
           tot AS (
               SELECT pgm.player_id, pgm.season, pgm.team_abbreviation,
                      COUNT(*) g, SUM(pgm.pts_for) pf, SUM(pgm.pts_against) pa, SUM(t.poss) pos
               FROM pgm JOIN team_game_totals t
                 ON t.game_id = pgm.game_id AND t.team_abbreviation = pgm.team_abbreviation
               GROUP BY 1, 2, 3)
           SELECT COUNT(*),
                  SUM((o.games <> tot.g)::int),
                  MAX(ABS(o.poss_on + o.poss_off - tot.pos)),
                  SUM(((o.pts_for_on + o.pts_for_off) <> tot.pf
                       OR (o.pts_against_on + o.pts_against_off) <> tot.pa)::int)
           FROM player_on_off o JOIN tot USING (player_id, season, team_abbreviation)""",
    )
    assert n >= 3600
    assert bad_games == 0
    assert worst_poss <= 0.5
    assert bad_pts == 0


def test_league_on_court_net_is_about_zero(cur):
    """Every point one side scores is against the other side's five, so summed
    over all players the on-court points for and against cancel. Not exactly:
    players ESPN gives no id get no on-floor row, so a side with one of them on
    the floor is credited to four (player_game_onfloor sums to 0 in 2025-26,
    where every player has an id): within 0.1% of the points scored each season
    (2026-10-03, on the corrected on-floor points: at most 0.064%, 2025-26 2
    points; 2026-09-29 on the old points: at most 0.06%)."""
    need(cur, "player_on_off")
    data = rows(cur, """SELECT season, SUM(pts_for_on - pts_against_on), SUM(pts_for_on)
           FROM player_on_off GROUP BY 1 ORDER BY 1""")
    assert len(data) >= 6
    for season, net, scored in data:
        assert abs(net) / scored <= 0.001, f"{season}: net {net} of {scored}"


def test_player_game_lines_season_totals_match_season_stats(cur):
    """Play-by-play lines against NBA.com's per-game season line (x games
    played), league-wide per season. Within 0.5% for points, rebounds, assists,
    steals and three-point attempts (2026-09-29: at most 0.25%). Blocks are left
    out on purpose: per-game blocks are rounded to 0.1, too coarse for a league
    total to be checked that tightly."""
    need(cur, "player_game_lines", "player_season_stats")
    data = rows(cur, """SELECT s.season,
                  SUM(l.pts)::float / SUM(s.pts * s.gp),
                  SUM(l.oreb + l.dreb)::float / SUM(s.reb * s.gp),
                  SUM(l.ast)::float / SUM(s.ast * s.gp),
                  SUM(l.stl)::float / SUM(s.stl * s.gp),
                  SUM(l.fg3a)::float / SUM(s.fg3a * s.gp)
           FROM (SELECT player_id, season, SUM(pts) pts, SUM(oreb) oreb, SUM(dreb) dreb,
                        SUM(ast) ast, SUM(stl) stl, SUM(fg3a) fg3a
                 FROM player_game_lines GROUP BY 1, 2) l
           JOIN player_season_stats s USING (player_id, season)
           GROUP BY 1 ORDER BY 1""")
    assert len(data) >= 6
    for season, *ratios in data:
        for name, ratio in zip(("pts", "reb", "ast", "stl", "fg3a"), ratios):
            assert abs(ratio - 1) <= 0.005, f"{season} {name}: {ratio:.4f}"


def test_assist_pairs_equal_game_log_assists(cur):
    """Assist Network and the Game Log parse the same play-by-play, so a
    player's assists per team-season agree, except the 4 made shots with data
    errors the network drops (README, Assist Network entry) and the three NBA Cup
    finals, which it also drops. 2026-09-29: 4 player-team-seasons, each off by
    exactly one assist with the Game Log higher."""
    need(cur, "assist_pairs", "player_game_lines", "game_scores")
    data = rows(cur, """SELECT COALESCE(a.n, 0), COALESCE(l.n, 0)
           FROM (SELECT season, team_abbreviation, passer_id pid, SUM(ast) n
                 FROM assist_pairs GROUP BY 1, 2, 3) a
           FULL JOIN (SELECT season, team_abbreviation, player_id pid, SUM(ast) n
                      FROM player_game_lines
                      WHERE game_id IN (SELECT 'espn_' || espn_id FROM game_scores)
                      GROUP BY 1, 2, 3) l USING (season, team_abbreviation, pid)
           WHERE a.n IS DISTINCT FROM l.n AND COALESCE(l.n, 0) > 0""")
    assert len(data) <= 10
    for network, log in data:
        assert log - network in (0, 1)


def test_pregame_odds_look_like_real_basketball(cur):
    """Domain sanity, not a source fact: the held-out favourite wins 60-72% of
    games each season (2026-09-29: 62.3-68.6%), home teams win 50-63% (54.1-61.2%),
    and the fan-less 2020-21 season has a clearly smaller home edge than the
    2010-11 to 2018-19 average."""
    need(cur, "game_pregame_odds")
    data = rows(cur, """SELECT season, AVG(home_won::int),
                  AVG(((p_home >= 0.5) = home_won)::int)
           FROM game_pregame_odds GROUP BY 1 ORDER BY 1""")
    assert len(data) >= 15
    home = {}
    for season, home_rate, fav_rate in data:
        assert 0.60 <= float(fav_rate) <= 0.72, f"{season}: favourite {fav_rate:.3f}"
        assert 0.50 <= home_rate <= 0.63, f"{season}: home {home_rate:.3f}"
        home[season] = float(home_rate)
    before = [home[s] for s in range(2011, 2020) if s in home]
    assert home[2021] < sum(before) / len(before) - 0.02
