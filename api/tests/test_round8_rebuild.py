"""
Round 8 step 6a: the play-by-play lines and stints rebuilt through the one shared parser
(scripts/pbp_lineups.py). Pins each fix:

R8-022  ESPN's no-id players are matched through player_bio (an exact name one player active that
        season has; aliases only where ESPN's own events pair the two names); 99.5%+ of every
        season's minutes are tracked now (93.7-99.8% before).
R8-023  a substitution tagged to no team: ignored when it names nobody leaving (it duplicates the
        next one), applied to the players' team when it names both; no phantom minutes left.
R8-024  no player-game without a team (Miye Oni's 'NaN' row is UTA).
R8-025  the lines' on-court points use the stints' rules, so tm_pts - op_pts is the box-score +/-.
R8-026  free throws are credited to the stint on the floor at the foul (the box score's rule).
R8-027  the stints' 3PA take the NBA shot chart's call on misses, like the lines.

The parser rules are tested on small made-up games (no database); the rest reads the rebuilt tables.
"""

import os
import sys

import pandas as pd
import psycopg2
import pytest

_API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(_API_DIR), "scripts"))
sys.path.insert(0, _API_DIR)

import pbp_lineups as P  # noqa: E402
from db_config import DB_CONFIG  # noqa: E402

HOME, AWAY = "HOM", "AWY"
NAMES = {1: "Hal One", 2: "Hal Two", 3: "Hal Three", 4: "Hal Four", 5: "Hal Five", 6: "Hal Six",
         11: "Abe One", 12: "Abe Two", 13: "Abe Three", 14: "Abe Four", 15: "Abe Five", 16: "Abe Six"}


def _game(events, all_names=None, season=2023):
    """A Game from (seconds_remaining, team or None, person_id or None, action_type, description, home pts,
    away pts) rows in period 1; every listed starter fouls at 2870 so the parser sees him first."""
    rows = []
    for k, pid in enumerate([1, 2, 3, 4, 5, 11, 12, 13, 14, 15]):
        team = HOME if pid < 10 else AWAY
        rows.append((2870, team, pid, "Personal Foul", f"{NAMES[pid]} personal foul", 0, 0))
    rows += events
    df = pd.DataFrame([{"game_id": "t", "action_number": i + 1, "id": i + 1, "period": 1, "seconds_remaining": float(s),
                        "score_home": h, "score_away": a, "team_tricode": t, "person_id": p,
                        "player_name": NAMES.get(p) if p else None, "action_type": at, "description": d}
                       for i, (s, t, p, at, d, h, a) in enumerate(rows)])
    df["person_id"] = df["person_id"].astype(object).where(df["person_id"].notna(), None)
    df["team_tricode"] = df["team_tricode"].astype(object)
    return P.Game("t", season, None, df, {}, all_names if all_names is not None else P.NameIndex({}))


# A shooting foul by an away player, a home substitution between the two free throws, then two
# substitutions ESPN tagged to no team: "Abe Six enters the game for " (nobody leaving; the next row
# repeats it with the team) and "Hal Five enters the game for Hal Six" (both players named).
SCRIPT = [
    (2800, AWAY, 11, "Shooting Foul", "Abe One shooting foul", 0, 0),             # 11
    (2800, HOME, 1, "Free Throw - 1 of 2", "Hal One makes free throw 1 of 2", 1, 0),
    (2800, HOME, 6, "Substitution", "Hal Six enters the game for Hal Five", 1, 0),  # 13
    (2800, HOME, 1, "Free Throw - 2 of 2", "Hal One makes free throw 2 of 2", 2, 0),  # 14
    (2700, None, 16, "Substitution", "Abe Six enters the game for ", 2, 0),
    (2690, AWAY, 16, "Substitution", "Abe Six enters the game for Abe Two", 2, 0),
    (2600, None, 5, "Substitution", "Hal Five enters the game for Hal Six", 2, 0),
]


def test_run_credits_free_throws_at_the_foul_and_handles_teamless_subs():
    g = _game(SCRIPT)
    rows, played, totals = g.run(HOME)
    # Both free throws go to the five on the floor at the foul (Hal Five), none to Hal Six.
    assert rows[5]["tm_fta"] == 2 and rows[5]["tm_pts_shots"] == 2 and rows[5]["tm_pts_score"] == 2
    assert rows[6].get("tm_fta", 0) == 0 and rows[6].get("tm_pts_shots", 0) == 0
    assert rows[11]["op_pts_shots"] == 2 and totals == {"shots": [2, 0], "score": [2, 0]}
    # The nobody-leaving duplicate is ignored (Abe Six plays from 2690 once, not from 2700 twice);
    # the one naming both players goes to their team.
    assert rows[16]["seconds"] == 2690 - 2160 and rows[12]["seconds"] == 2880 - 2690
    assert rows[6]["seconds"] == 2800 - 2600 and rows[5]["seconds"] == (2880 - 2800) + (2600 - 2160)
    assert g.teamless_subs == {"ignored": 1, "applied": 1}
    assert g.team_of[16] == AWAY and g.team_of[5] == HOME


def test_stints_credit_free_throws_at_the_foul_and_agree_with_run():
    g = _game(SCRIPT)
    stints, _ = g.stints(HOME)
    first, second = stints[0], stints[1]
    assert 5 in first["home"] and 6 in second["home"]
    assert (first["h_fta"], first["h_ftm"], first["h_pts_shots"], first["foul_fts"]) == (2, 2, 2, [14])
    assert (second["h_fta"], second["h_pts_shots"]) == (0, 0)
    assert second["action_from"] <= 14 <= second["action_to"]       # the range still holds what was logged there
    # run() and the stints credit the same totals to every player.
    rows, _, _ = _game(SCRIPT).run(HOME)
    for pid in NAMES:
        on = [s for s in stints if pid in s["home"] + s["away"]]
        side = "h_" if pid < 10 else "a_"
        assert sum(s["t1"] - s["t0"] for s in on) == pytest.approx(rows[pid]["seconds"]), pid
        assert sum(s[side + "fta"] for s in on) == rows[pid].get("tm_fta", 0), pid


def test_zero_second_stint_at_the_foul_is_kept():
    """A player sent in to foul and taken out at the same clock: the stint has no seconds but the free
    throws are credited to it (the box score's +/- for him)."""
    script = [
        (2800, AWAY, 16, "Substitution", "Abe Six enters the game for Abe Five", 0, 0),
        (2800, AWAY, 16, "Personal Take Foul", "Abe Six personal take foul", 0, 0),
        (2800, AWAY, 15, "Substitution", "Abe Five enters the game for Abe Six", 0, 0),
        (2800, HOME, 1, "Free Throw - 1 of 2", "Hal One makes free throw 1 of 2", 1, 0),
        (2800, HOME, 1, "Free Throw - 2 of 2", "Hal One makes free throw 2 of 2", 2, 0),
    ]
    stints, _ = _game(script).stints(HOME)
    zero = [s for s in stints if 16 in s["away"]]
    assert len(zero) == 1 and zero[0]["t1"] == zero[0]["t0"] and zero[0]["h_pts_shots"] == 2
    rows, _, _ = _game(script).run(HOME)
    assert rows[16]["op_pts_shots"] == 2 and rows[16].get("seconds", 0) == 0


def test_points_method_and_foul_anchor():
    assert P.points_method([100, 90], [100, 92], (100, 90)) == ("shots", True)
    assert P.points_method([100, 90], [100, 92], (100, 92)) == ("score", True)
    assert P.points_method([100, 90], [100, 92], (101, 92)) == ("shots", False)
    assert P.points_method([100, 90], [100, 92], (None, 92)) == ("shots", False)
    assert P.is_foul_anchor({"kind": None, "pid": None, "action": "Shooting Foul"})
    assert P.is_foul_anchor({"kind": "fg", "pid": 7, "action": "Jump Shot"})
    assert not P.is_foul_anchor({"kind": "ft", "pid": 7, "action": "Free Throw - 1 of 2"})
    assert not P.is_foul_anchor({"kind": "sub", "pid": 7, "action": "Substitution"})
    assert not P.is_foul_anchor({"kind": "dreb", "pid": None, "action": "Defensive Team Rebound"})


def test_bio_fallback_in_the_parser():
    """A name with no id anywhere else resolves through the player_bio index (aliases included); a player
    who changed teams that season only for one of his teams."""
    idx = P.NameIndex({}, bio={(2023, "kenneth lofton"): (1631254, frozenset()),
                               (2023, "hal seven"): (99, frozenset({"XYZ"}))})
    g = _game([(2800, HOME, None, "Substitution", "Kenny Lofton Jr. enters the game for Hal Five", 0, 0),
               (2790, HOME, None, "Substitution", "Hal Seven enters the game for Hal Four", 0, 0)], all_names=idx)
    rows, _, _ = g.run(HOME)
    assert 1631254 in rows and rows[1631254]["seconds"] == 2800 - 2160
    assert g.from_bio[(HOME, "Kenny Lofton Jr.", 1631254)] == 1
    assert 99 not in rows and g.unmatched_at[(HOME, "Hal Seven")] >= 1
    assert P.norm("Jeenathan Williams") == P.norm("Nate Williams")


# ─── The rebuilt tables ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def cur():
    conn = psycopg2.connect(**DB_CONFIG)
    yield conn.cursor()
    conn.close()


def one(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchone()


def test_bio_fallback_players_are_in_the_lines(cur):
    """R8-022: two-way players ESPN gives no id now have lines; the index holds them."""
    names, idx = P.load_season_names(cur)
    assert idx.bio[(2023, "kenneth lofton")][0] == 1631254 and "kenneth lofton" not in names[2023]
    # Kenneth Lofton Jr. (MEM 2022-23), Alize Johnson (BKN 2020-21), Terquavion Smith in Embiid's 70-point game.
    assert one(cur, "SELECT COUNT(*) FROM player_game_lines WHERE player_id = 1631254 AND season = 2023 "
                    "AND team_abbreviation = 'MEM'")[0] >= 20
    assert one(cur, "SELECT COUNT(*) FROM player_game_lines WHERE player_id = 1628993 AND season = 2021")[0] >= 10
    assert one(cur, "SELECT seconds FROM player_game_lines WHERE player_id = 1631173 AND game_id = 'espn_401585236'")[0] > 0


def test_tracked_minutes_cover_every_season(cur):
    cur.execute("SELECT season, tracked_minutes_share, tracked_games, games FROM lineup_stint_seasons WHERE season <= 2026 ORDER BY season")
    rows = cur.fetchall()
    assert [r[0] for r in rows] == [2021, 2022, 2023, 2024, 2025, 2026]
    assert all(r[1] >= 0.995 for r in rows)
    assert sum(r[2] for r in rows) >= 7150
    # Every player-game with seconds has a stint; every stint player-game a line.
    assert one(cur, """SELECT COUNT(*) FROM player_game_lines l WHERE l.seconds > 0 AND NOT EXISTS (
                           SELECT 1 FROM lineup_stints s WHERE s.game_id = l.game_id
                           AND (s.home_ids @> ARRAY[l.player_id::integer] OR s.away_ids @> ARRAY[l.player_id::integer]))""")[0] == 0


def test_every_line_has_a_team_and_no_phantom_minutes(cur):
    """R8-023, R8-024."""
    assert one(cur, "SELECT COUNT(*) FROM player_game_lines WHERE team_abbreviation IS NULL OR team_abbreviation = 'NaN'")[0] == 0
    assert one(cur, "SELECT team_abbreviation, seconds FROM player_game_lines WHERE player_id = 1629671 "
                    "AND game_id = 'espn_401360071'") == ("UTA", 89)
    # Dončić on 2022-01-30: 37.1 minutes (45.4 while a team-less substitution opened a phantom lineup).
    assert abs(one(cur, "SELECT seconds FROM player_game_lines WHERE player_id = 1629029 "
                        "AND game_id = 'espn_401360574'")[0] / 60 - 37.05) < 0.1


def test_on_court_points_are_the_box_score_plus_minus(cur):
    """R8-025, R8-026: the lines' on-court margin equals player_game_onfloor (free throws at the foul) in
    every reconciled player-game and the stints' sums in every one; a full-minute team's summed margin is
    5 x the final margin (it was in 75% of team-games before)."""
    assert one(cur, """SELECT COUNT(*) FROM player_game_lines l JOIN player_game_onfloor o USING (player_id, game_id)
                       WHERE o.game_ok AND l.tm_pts - l.op_pts <> o.plus_minus""")[0] == 0
    assert one(cur, """WITH s AS (
                           SELECT game_id, u.pid, SUM(home_pts) pf, SUM(away_pts) pa FROM lineup_stints, unnest(home_ids) u(pid) GROUP BY 1, 2
                           UNION ALL
                           SELECT game_id, u.pid, SUM(away_pts), SUM(home_pts) FROM lineup_stints, unnest(away_ids) u(pid) GROUP BY 1, 2)
                       SELECT COUNT(*) FROM s JOIN player_game_lines l ON l.game_id = s.game_id AND l.player_id = s.pid
                       WHERE (l.tm_pts, l.op_pts) <> (s.pf, s.pa)""")[0] == 0
    full, five = one(cur, """
        WITH l AS (SELECT game_id, team_abbreviation t, SUM(seconds) s, SUM(tm_pts - op_pts) pm FROM player_game_lines GROUP BY 1, 2),
             f AS (SELECT 'espn_' || espn_id game_id, team_abbreviation t, pts_for - pts_against m, periods
                   FROM game_scores WHERE espn_id IS NOT NULL)
        SELECT COUNT(*), COUNT(*) FILTER (WHERE l.pm = 5 * f.m) FROM l JOIN f USING (game_id, t)
        WHERE ABS(l.s - 5 * (2880 + 300 * GREATEST(f.periods - 4, 0))) <= 1""")
    assert full > 14000 and five / full > 0.999


def test_moved_free_throws_are_listed(cur):
    """R8-026: each free throw credited to an earlier stint is listed on it, is a free throw, and was logged
    after that stint's own events; the per-game count adds up."""
    listed, not_ft, inside = one(cur, """
        WITH f AS (SELECT s.game_id, s.action_to, u.a FROM lineup_stints s, unnest(s.foul_ft_actions) u(a))
        SELECT COUNT(*), COUNT(*) FILTER (WHERE e.action_type NOT LIKE 'Free Throw%%'),
               COUNT(*) FILTER (WHERE f.action_to IS NOT NULL AND f.a <= f.action_to)
        FROM f JOIN pbp_events e ON e.game_id = f.game_id AND e.action_number = f.a""")
    assert listed > 50000 and not_ft == 0 and inside == 0
    assert one(cur, "SELECT SUM(fts_at_foul) FROM lineup_stint_games")[0] == listed


def test_stint_threes_take_the_chart_call(cur):
    """R8-027: per team-game the stints' 3PA equal the lines' wherever both count the same FGA (they differ
    only where a shooter the parser can't name has no line)."""
    assert one(cur, """
        WITH st AS (SELECT game_id, home_team t, SUM(home_fg3a) a, SUM(home_fga) f FROM lineup_stints GROUP BY 1, 2
                    UNION ALL SELECT game_id, away_team, SUM(away_fg3a), SUM(away_fga) FROM lineup_stints GROUP BY 1, 2),
             li AS (SELECT game_id, team_abbreviation t, SUM(fg3a) a, SUM(fga) f FROM player_game_lines GROUP BY 1, 2)
        SELECT COUNT(*) FILTER (WHERE st.a <> li.a AND st.f = li.f) FROM st JOIN li USING (game_id, t)""")[0] == 0
