"""
paper_data_audit.py
====================
Data-quality audit of the public feeds the platform is built on (round 5,
step 5): every error class found so far in the ESPN play-by-play, the NBA
shot chart and the season/game tables, re-measured from the database by a
check that anyone can rerun, with how it was found, its size and what the
pipeline does about it. Nothing is copied from the README: each count below
is recomputed here, and the README's "Known real gaps" list is only the
checklist of what to measure.

Error classes (key: what is measured)
  Play-by-play (ESPN, pbp_events / pbp_games, regular season 2020-21 on)
    twin_copies       nba_api play-by-play games that are ESPN games again (same
                      date and teams; a neutral-site game has no nba_api home team)
    cup_finals        games ESPN files as regular season that the NBA does not
                      count (no NBA scoreboard game in game_scores)
    wrong_player      events tagged with a same-surname player. Measured by
                      replaying the fetch's original matcher (exact name in that
                      season's player_season_stats, else rapidfuzz WRatio >= 90
                      over that season's names) on the names stored now: an event
                      whose stored player the matcher would give to someone else
                      is one the original fetch mis-tagged. The repair
                      (repair_espn_player_ids.py) is also rerun as a check: it
                      must find nothing left.
    tag_text          single events whose text does not contain the tagged
                      player's name although the player's other events of that
                      game do (the rest of his events carry his name, so the
                      spelling is not the reason); of those, how many name another
                      player tagged in the same game, and how many a teammate
    unidentified      events with a player name and no player id, the distinct
                      names per season, and the share of minutes with fewer than
                      five identified players a side (lineup_stint_seasons)
    teamless_sub      substitutions logged with no team, the games they are in,
                      and the player-games where the game lines (Game.run) credit
                      more seconds than the stints (Game.stints); the one
                      player_game_lines row with team 'NaN' comes from one of them
    score_fields      per season, games where summing every positive step of
                      ESPN's score fields misses the real final score; games where
                      a score field steps backwards; and the consequence for the
                      game lines' on-court points (team-games with full minutes
                      whose summed on-court margin is not five times the final)
    last_score        games whose last play-by-play score differs from ESPN's own
                      scoreboard final (team_game_totals vs game_scores)
    unreconciled      games whose stints fail the reconciliation against the real
                      final score, game length and team totals, by reason
    missed_threes     missed field goals matched to the shot chart whose text calls
                      them a two while the chart calls them a three (and the
                      reverse); median distance from the chart's coordinates; made
                      shots, whose value comes from the score step, as a check
    clock_offset      the clock of the same shot in the two feeds (matched by order
                      within game, shooter and period, identical make/miss
                      sequences): median and 99th percentile of |difference|, the
                      share over 5 s and the largest; and regulation events whose
                      ESPN clock lies outside their own period
    clock_lag         ESPN's time of each event against NBA.com's own play-by-play
                      of the same game (the nba_api twin games of 2024-25, the
                      only season with both; events matched by order within game,
                      period, player and kind): ESPN's median lag by event class
                      (made shots, later free throws, rebounds, steals, dead-ball
                      turnovers, misses) and the share within 2 s, against the
                      corrected clock's (pbp_event_clock, build_event_clock.py).
                      Re-measured with build_event_clock.clock_check() on its own
                      parse and shot match of those games; it must equal the
                      check build_event_clock stored in pbp_event_clock_meta
                      (round 6 step 12)
  Shot chart (NBA, player_shots)
    chart_gaps        reconciled games with no chart rows at all, and the share of
                      play-by-play attempts matched to the chart per season
    zero_distance     regular-season threes with shot_distance = 0, per season
    unlocated         regular-season shots at (0, 0) per season (before 2010-11,
                      shots with no exact location sit there)
  Season and game tables
    plus_minus        team_game_fatigue.plus_minus (summed player plus-minus / 5)
                      against the real final margin (game_scores)
    wrong_team        player_season_stats rows whose team is none of the teams in
                      the player's game lines that season (2020-21 on; before that
                      there are no game lines to check against)
    age_convention    player_season_stats.age (2009-10 on, NBA.com's) against the
                      age on 1 February from player_bio.birth_date

Judgment calls
  * "Full minutes" for a team-game means the players' seconds add up to five
    times the game length within one second.
  * The tag/text rule compares folded names (accents, dots and apostrophes
    removed, lower case), the repair script's own fold(); events with an empty
    description count as not containing the name.
  * The chart match share is over every parsed field-goal attempt of the two teams
    with an identified shooter (one with no id cannot be matched: that is the
    unidentified class); the share over all attempts is stored beside it.
    paper_xrapm.py's share is over tracked stints only, so it is a little higher.
  * Clock offset: ESPN's seconds_remaining counts down the whole of regulation
    (2880 at the tip) and each overtime; it is turned into seconds left in the
    period before it is compared with the chart's minutes/seconds remaining.

Tables written (dropped and rebuilt; nothing else is changed):
  paper_data_audit          key, season (0 = all seasons), value, note, and the
                            LaTeX macro name and format paper_numbers.py prints it
                            with (NULL: stored, not printed);
  paper_data_audit_classes  one row per error class: feed, name, how detected,
                            size (LaTeX with \\pn macros), handling and its kind
                            (repaired / worked around / excluded / disclosed).
Also writes paper/tables/data_audit.tex (the paper's table; every number in it
is a \\pn macro from paper/numbers.tex; paper/ is untracked on purpose).

Runtime about 2 minutes (one play-by-play parse and one shot match); read-only
on every other table; deterministic.

Usage (Python: /Library/Frameworks/Python.framework/Versions/3.14/bin/python3):
    cd scripts && python3 paper_data_audit.py
Then: paper_numbers.py --check. Rerun after a play-by-play re-fetch or repair,
build_lineup_stints.py, build_player_game_lines.py, or a player_shots reload.
"""

import os
import time
import warnings
from collections import defaultdict

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from rapidfuzz import fuzz, process

from db_config import DB_CONFIG
from fetch_pbp_espn import NAME_MATCH_FLOOR, _normalize_name
from pbp_lineups import PERIOD_SECONDS, Game, chart_matches, load_espn, load_season_names, match_coordinates
from repair_espn_player_ids import find as find_wrong_ids
from repair_espn_player_ids import fold

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEX_OUT = os.path.join(ROOT, "paper", "tables", "data_audit.tex")
FULL_MINUTES_TOL = 1.0      # seconds: a team-game's player seconds within 1 s of 5 x the game length
CLOCK_BIG = 5.0             # seconds: "an offset over 5 s"
LOCATED_LAST = 2010         # the last season whose unlocated shots sit at (0, 0); exact locations from 2010-11
AGE_FIRST_SEASON = 2010     # player_season_stats.age is NBA.com's from 2009-10 on (Basketball-Reference's before)
T0 = time.time()


def log(msg):
    print(f"{msg}  [{time.time() - T0:.0f}s]", flush=True)


class Audit:
    """Measurements: (key, season) -> (value, note, macro, fmt)."""

    def __init__(self):
        self.rows = {}

    def put(self, key, value, note, season=0, macro=None, fmt=None):
        if (key, season) in self.rows:
            raise ValueError(f"{key} {season} measured twice")
        self.rows[(key, season)] = (None if value is None else float(value), note, macro, fmt)

    def get(self, key, season=0):
        return self.rows[(key, season)][0]


def q(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


# ── Play-by-play: feed-level checks in SQL ───────────────────────────────────

def feed_checks(cur, A):
    (rows, games), = q(cur, """SELECT count(*), count(DISTINCT e.game_id) FROM pbp_events e
                               JOIN pbp_games g USING (game_id) WHERE g.source = 'nba_api'""")
    (twin_same, neutral), = q(cur, """
        SELECT count(*) FILTER (WHERE EXISTS (SELECT 1 FROM pbp_games e WHERE e.source = 'espn' AND e.game_date = n.game_date
                     AND ((e.home_team = n.home_team AND e.away_team = n.away_team)
                          OR (e.home_team = n.away_team AND e.away_team = n.home_team)))),
               count(*) FILTER (WHERE n.home_team IS NULL AND EXISTS (SELECT 1 FROM pbp_games e WHERE e.source = 'espn'
                     AND e.game_date = n.game_date AND n.away_team IN (e.home_team, e.away_team)))
        FROM pbp_games n WHERE n.source = 'nba_api'""")
    A.put("twin_rows", rows, "pbp_events rows of nba_api games", macro="DqTwinRows", fmt="integer")
    A.put("twin_games", games, "nba_api games in pbp_games", macro="DqTwinGames", fmt="integer")
    A.put("twin_same_teams", twin_same, "nba_api games with an ESPN game on the same date and the same two teams",
          macro="DqTwinSameTeams", fmt="integer")
    A.put("twin_neutral", neutral, "nba_api games with no home team whose date and away team match an ESPN game",
          macro="DqTwinNeutral", fmt="word")

    cup = q(cur, """SELECT g.game_id, count(e.id) FROM pbp_games g JOIN pbp_events e USING (game_id)
                    WHERE g.source = 'espn' AND g.game_id NOT IN
                          (SELECT 'espn_' || espn_id FROM game_scores WHERE espn_id IS NOT NULL) GROUP BY 1""")
    A.put("cup_games", len(cup), "ESPN regular-season games with no game_scores (NBA scoreboard) row: "
          + ", ".join(g for g, _ in cup), macro="DqCupGames", fmt="word")
    A.put("cup_events", sum(n for _, n in cup), "their pbp_events rows")

    # Unidentified players: named events without an id, per season.
    for season, named, noid, names in q(cur, """
            SELECT g.season, count(*), count(*) FILTER (WHERE e.person_id IS NULL),
                   count(DISTINCT e.player_name) FILTER (WHERE e.person_id IS NULL)
            FROM pbp_events e JOIN pbp_games g USING (game_id)
            WHERE g.source = 'espn' AND coalesce(e.player_name, '') <> '' GROUP BY 1 ORDER BY 1"""):
        A.put("unid_named_events", named, "ESPN events with a player name", season)
        A.put("unid_events", noid, "of those, events with no person_id", season)
        A.put("unid_names", names, "distinct player names with no person_id", season)
    (named, noid), = q(cur, """SELECT count(*), count(*) FILTER (WHERE e.person_id IS NULL)
                              FROM pbp_events e JOIN pbp_games g USING (game_id)
                              WHERE g.source = 'espn' AND coalesce(e.player_name, '') <> ''""")
    A.put("unid_named_events", named, "ESPN events with a player name, all seasons")
    A.put("unid_events", noid, "of those, events with no person_id, all seasons", macro="DqUnidEvents", fmt="integer")
    A.put("unid_events_share", noid / named, "their share", macro="DqUnidEventsPct", fmt="pct1")
    for season, share in q(cur, "SELECT season, bad_lineup_minutes / minutes FROM lineup_stint_seasons ORDER BY 1"):
        A.put("unid_minutes_share", share, "lineup_stint_seasons.bad_lineup_minutes / minutes", season)

    # Events stamped outside their own period's clock (ESPN's seconds_remaining counts down regulation from 2880).
    (n, g), = q(cur, """SELECT count(*), count(DISTINCT e.game_id) FROM pbp_events e JOIN pbp_games p USING (game_id)
                        WHERE p.source = 'espn' AND e.period <= 4
                          AND (e.seconds_remaining < %s * (4 - e.period) OR e.seconds_remaining > %s * (5 - e.period))""",
                    (PERIOD_SECONDS, PERIOD_SECONDS))
    A.put("clock_outside_events", n, "regulation events whose clock lies outside their own period",
          macro="DqClockOutside", fmt="integer")
    A.put("clock_outside_games", g, "games holding them", macro="DqClockOutsideGames", fmt="integer")

    # Substitutions with no team, and what they do to the game lines. Seconds are summed as numeric: a float sum's
    # last digits depend on the order Postgres adds the rows in, and this table must be identical run to run.
    (subs, sub_games), = q(cur, """SELECT count(*), count(DISTINCT e.game_id) FROM pbp_events e JOIN pbp_games g USING (game_id)
                                   WHERE g.source = 'espn' AND e.action_type = 'Substitution' AND e.team_tricode IS NULL""")
    A.put("teamless_subs", subs, "ESPN substitution events with no team", macro="DqTeamlessSubs", fmt="word")
    A.put("teamless_games", sub_games, "games holding them", macro="DqTeamlessGames", fmt="word")
    (pg, higher, games, in_teamless, lo, hi), = q(cur, """
        WITH s AS (SELECT game_id, pid, SUM(seconds::numeric) secs FROM (
                       SELECT game_id, unnest(home_ids) pid, seconds FROM lineup_stints
                       UNION ALL SELECT game_id, unnest(away_ids), seconds FROM lineup_stints) x GROUP BY 1, 2),
             d AS (SELECT s.game_id, round(l.seconds::numeric - s.secs, 1) extra FROM s
                   JOIN player_game_lines l ON l.game_id = s.game_id AND l.player_id = s.pid
                   WHERE abs(s.secs - l.seconds) > 0.2)
        SELECT count(*), count(*) FILTER (WHERE extra > 0), count(DISTINCT game_id),
               count(*) FILTER (WHERE game_id IN (SELECT game_id FROM pbp_events WHERE action_type = 'Substitution'
                                                  AND team_tricode IS NULL)),
               min(extra), max(extra) FROM d""")
    A.put("teamless_player_games", pg, "player-games where player_game_lines seconds differ from the stints' by > 0.2 s",
          macro="DqTeamlessPlayerGames", fmt="word")
    A.put("teamless_lines_higher", higher, "of those, the lines credit more")
    A.put("teamless_pg_games", games, "games they are in")
    A.put("teamless_pg_in_sub_games", in_teamless, "of those player-games, in a game with a team-less substitution")
    A.put("teamless_extra_min", lo, "smallest excess (seconds)", macro="DqTeamlessExtraMin", fmt="int_round")
    A.put("teamless_extra_max", hi, "largest excess (seconds)", macro="DqTeamlessExtraMax", fmt="int_round")
    nan = q(cur, """SELECT l.game_id FROM player_game_lines l WHERE l.team_abbreviation = 'NaN' OR l.team_abbreviation IS NULL""")
    A.put("nan_team_rows", len(nan), "player_game_lines rows with team 'NaN'", macro="DqNanTeamRows", fmt="word")
    A.put("nan_team_in_sub_games", sum(1 for (g,) in nan if q(cur, """SELECT 1 FROM pbp_events WHERE game_id = %s
              AND action_type = 'Substitution' AND team_tricode IS NULL""", (g,))),
          "of those, in a game with a team-less substitution")


def score_checks(cur, A):
    # Crediting every positive step of the score fields, against the real final (lineup_stint_games.final_*).
    for season, games, ok in q(cur, """
            WITH s AS (SELECT e.game_id,
                              e.score_home - lag(e.score_home) OVER w dh, e.score_away - lag(e.score_away) OVER w da
                       FROM pbp_events e JOIN lineup_stint_games g ON g.game_id = e.game_id
                       WINDOW w AS (PARTITION BY e.game_id ORDER BY e.action_number))
            SELECT g.season, count(*), sum(((t.ph = g.final_home) AND (t.pa = g.final_away))::int)
            FROM (SELECT game_id, sum(greatest(dh, 0)) ph, sum(greatest(da, 0)) pa FROM s GROUP BY 1) t
            JOIN lineup_stint_games g USING (game_id) GROUP BY 1 ORDER BY 1"""):
        A.put("score_steps_games", games, "games with a real final", season)
        A.put("score_steps_miss", games - ok, "games where the summed positive score steps miss the real final", season)
    (back, games), = q(cur, """
        WITH s AS (SELECT e.game_id, e.score_home - lag(e.score_home) OVER w dh, e.score_away - lag(e.score_away) OVER w da
                   FROM pbp_events e JOIN pbp_games g USING (game_id)
                   WHERE g.source = 'espn' AND e.score_home IS NOT NULL AND e.score_away IS NOT NULL
                   WINDOW w AS (PARTITION BY e.game_id ORDER BY e.action_number, e.id))
        SELECT count(DISTINCT game_id) FILTER (WHERE dh < 0 OR da < 0), count(DISTINCT game_id) FROM s""")
    A.put("score_backwards_games", back, "ESPN games where a score field decreases from one event to the next",
          macro="DqScoreBackwardGames", fmt="integer")
    A.put("espn_games", games, "ESPN games", macro="DqEspnGames", fmt="integer")
    (tg, full, five), = q(cur, """
        WITH l AS (SELECT game_id, team_abbreviation t, sum(seconds::numeric) secs, sum((tm_pts - op_pts)::numeric) onc
                   FROM player_game_lines GROUP BY 1, 2),
             g AS (SELECT 'espn_' || espn_id game_id, team_abbreviation t, pts_for - pts_against m, periods
                   FROM game_scores WHERE espn_id IS NOT NULL)
        SELECT count(*), count(*) FILTER (WHERE abs(secs - 5 * (2880 + 300 * greatest(periods - 4, 0))) < %s),
               count(*) FILTER (WHERE abs(secs - 5 * (2880 + 300 * greatest(periods - 4, 0))) < %s AND onc = 5 * m)
        FROM l JOIN g USING (game_id, t)""", (FULL_MINUTES_TOL, FULL_MINUTES_TOL))
    A.put("oncourt_team_games", tg, "team-games in player_game_lines with a real final")
    A.put("oncourt_full", full, "of those, players' seconds within 1 s of 5 x the game length",
          macro="DqOnCourtFull", fmt="integer")
    A.put("oncourt_five", five, "of those, summed on-court margin (tm_pts - op_pts) exactly 5 x the final margin")
    A.put("oncourt_off_share", 1 - five / full, "share of full-minute team-games whose on-court margin is not 5 x the final",
          macro="DqOnCourtOffPct", fmt="pct0")

    (n, bad), = q(cur, """SELECT count(DISTINCT t.game_id), count(DISTINCT t.game_id) FILTER (
                              WHERE t.pts_for <> g.pts_for OR t.pts_against <> g.pts_against)
                          FROM team_game_totals t JOIN game_scores g
                            ON g.espn_id IS NOT NULL AND t.game_id = 'espn_' || g.espn_id AND g.team_abbreviation = t.team_abbreviation""")
    A.put("last_score_games", n, "games in both team_game_totals (last play-by-play score) and game_scores",
          macro="DqLastScoreDenom", fmt="integer")
    A.put("last_score_bad", bad, "of those, the last play-by-play score differs from ESPN's scoreboard final",
          macro="DqLastScoreGames", fmt="integer")

    kinds = defaultdict(int)
    for reason, in q(cur, "SELECT reason FROM lineup_stint_games WHERE NOT game_ok"):
        kinds["cup" if reason.startswith("not in game_scores") else "score" if reason.startswith("points") else "totals"] += 1
    A.put("unrec_cup", kinds["cup"], "failed games with no real final (not in game_scores)", macro="DqUnrecCup", fmt="word")
    A.put("unrec_score", kinds["score"], "failed games whose stint points miss the real final", macro="DqUnrecScore", fmt="word")
    A.put("unrec_totals", kinds["totals"], "failed games whose points match but team totals (FGA/FTA/OREB/TOV) differ",
          macro="DqUnrecTotals", fmt="word")
    (games, ok), = q(cur, "SELECT count(*), sum(game_ok::int) FROM lineup_stint_games")
    A.put("unrec_games", games - ok, "lineup_stint_games with game_ok false", macro="DqUnrecGames", fmt="word")


def table_checks(cur, A):
    (games, bad, point, nonzero, sign, s0, s1), = q(cur, """
        WITH j AS (SELECT f.game_id, f.season, f.plus_minus pm, g.pts_for - g.pts_against m, f.win
                   FROM team_game_fatigue f JOIN game_scores g ON g.game_id = f.game_id AND g.team_abbreviation = f.team_abbreviation)
        SELECT count(DISTINCT game_id), count(DISTINCT game_id) FILTER (WHERE abs(pm - m) > 1e-6),
               count(DISTINCT game_id) FILTER (WHERE abs(pm - m) >= 1),
               (SELECT count(*) FROM (SELECT game_id FROM team_game_fatigue GROUP BY 1 HAVING abs(sum(plus_minus)) > 1e-6) x),
               count(DISTINCT game_id) FILTER (WHERE pm <> 0 AND (pm > 0) <> win), min(season), max(season) FROM j""")
    A.put("pm_games", games, "games in team_game_fatigue joined to game_scores", macro="DqPlusMinusDenom", fmt="integer")
    A.put("pm_bad", bad, "games where plus_minus differs from the final margin", macro="DqPlusMinusGames", fmt="integer")
    A.put("pm_point", point, "of those, by a point or more", macro="DqPlusMinusPoint", fmt="integer")
    A.put("pm_nonzero", nonzero, "games whose two plus_minus values do not add to zero", macro="DqPlusMinusNonzero", fmt="integer")
    A.put("pm_sign", sign, "games where the sign of plus_minus disagrees with the stored win flag", macro="DqPlusMinusSign", fmt="word")
    A.put("pm_first_season", s0, "first season", macro="DqPlusMinusFirstSeason", fmt="season")

    total = 0
    for season, n in q(cur, """WITH l AS (SELECT player_id, season, array_agg(DISTINCT team_abbreviation) teams
                                          FROM player_game_lines GROUP BY 1, 2)
                               SELECT s.season, count(*) FILTER (WHERE NOT (s.team_abbreviation = ANY (l.teams)))
                               FROM player_season_stats s JOIN l USING (player_id, season) GROUP BY 1 ORDER BY 1"""):
        A.put("wrong_team_rows", n, "player_season_stats rows whose team is none of the player's game-line teams", season)
        total += n
    A.put("wrong_team_rows", total, "the same, all seasons", macro="DqWrongTeamRows", fmt="integer")

    (n, older, same), = q(cur, """
        WITH a AS (SELECT s.age, extract(year FROM age(make_date(s.season, 2, 1), b.birth_date))::int feb1
                   FROM player_season_stats s JOIN player_bio b USING (player_id)
                   WHERE s.season >= %s AND b.birth_date IS NOT NULL AND s.age IS NOT NULL)
        SELECT count(*), count(*) FILTER (WHERE age = feb1 + 1), count(*) FILTER (WHERE age = feb1) FROM a""", (AGE_FIRST_SEASON,))
    A.put("age_rows", n, "player-seasons 2009-10 on with an age and a birth date")
    A.put("age_first_season", AGE_FIRST_SEASON, "first season checked (NBA.com ages from 2009-10)", macro="DqAgeFirstSeason", fmt="season")
    A.put("age_older", older / n, "share whose NBA.com age is one year above the age on 1 February", macro="DqAgeOlderPct", fmt="pct0")
    A.put("age_same", same / n, "share equal to it")
    for season, share in q(cur, """
            WITH a AS (SELECT s.season, s.age, extract(year FROM age(make_date(s.season, 2, 1), b.birth_date))::int feb1
                       FROM player_season_stats s JOIN player_bio b USING (player_id)
                       WHERE s.season >= %s AND b.birth_date IS NOT NULL AND s.age IS NOT NULL)
            SELECT season, avg((age = feb1 + 1)::int) FROM a GROUP BY 1""", (AGE_FIRST_SEASON,)):
        A.put("age_older", share, "the same, per season", season)


def chart_checks(cur, A):
    for season, threes, zero, shots, origin in q(cur, """
            SELECT int4(left(season, 4)) + 1, count(*) FILTER (WHERE shot_type LIKE '3%%'),
                   count(*) FILTER (WHERE shot_type LIKE '3%%' AND shot_distance = 0), count(*),
                   count(*) FILTER (WHERE loc_x = 0 AND loc_y = 0)
            FROM player_shots WHERE game_id LIKE '002%%' GROUP BY 1 ORDER BY 1"""):
        A.put("zero_dist_threes", zero / threes, "regular-season threes with shot_distance = 0", season)
        A.put("origin_shots", origin / shots, "regular-season shots at (0, 0)", season)
    seasons = sorted(s for k, s in A.rows if k == "zero_dist_threes")
    first = seasons[0]
    later = [A.get("zero_dist_threes", s) for s in seasons[1:]]
    A.put("zero_dist_first_season", first, "first season on the chart", macro="DqChartFirstSeason", fmt="season")
    A.put("zero_dist_first", A.get("zero_dist_threes", first), "share in the first season", macro="DqZeroDistFirstPct", fmt="pct0")
    A.put("zero_dist_min", min(later), "smallest share in a later season", macro="DqZeroDistMinPct", fmt="pct0")
    A.put("zero_dist_max", max(later), "largest share in a later season", macro="DqZeroDistMaxPct", fmt="pct0")
    # (0, 0) is also the basket itself, where a dunk is really taken: before 2010-11 about a quarter of shots sit
    # there; from 2010-11 almost none (the chart then records dunks a little off the rim), so the early rows are
    # shots with no exact location.
    early = [s for s in seasons if s <= LOCATED_LAST]
    A.put("origin_last_season", early[-1], "last season with a quarter-scale share at (0, 0)", macro="DqOriginLastSeason", fmt="season")
    A.put("origin_min", min(A.get("origin_shots", s) for s in early), "smallest share, first season to 2009-10",
          macro="DqOriginMinPct", fmt="pct0")
    A.put("origin_max", max(A.get("origin_shots", s) for s in early), "largest share, first season to 2009-10",
          macro="DqOriginMaxPct", fmt="pct0")
    A.put("origin_after_max", max(A.get("origin_shots", s) for s in seasons if s > LOCATED_LAST),
          "largest share from 2010-11 on", macro="DqOriginAfterMaxPct", fmt="pct0")


# ── Play-by-play: event-level checks in Python ───────────────────────────────

def identity_checks(conn, cur, A):
    ev = pd.read_sql_query(
        """SELECT e.id, e.game_id, g.season, e.team_tricode, e.person_id, e.player_name, e.description
           FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
           WHERE g.source = 'espn' AND e.person_id IS NOT NULL ORDER BY e.game_id, e.action_number, e.id""", conn)
    ev["person_id"] = ev.person_id.astype("int64")

    # 1. Replay the fetch's original matcher (season exact, else season fuzzy >= floor) on the stored names.
    names = defaultdict(dict)
    for season, pid, name in q(cur, "SELECT DISTINCT season, player_id, player_name FROM player_season_stats WHERE season >= 2021"):
        names[season][_normalize_name(name)] = int(pid)
    keys = ev.groupby(["season", "player_name", "person_id"]).size().reset_index(name="n")
    wrong = []
    for r in keys.itertuples(index=False):
        m = names[r.season]
        k = _normalize_name(r.player_name)
        if k in m:
            got = m[k]
        else:
            best = process.extractOne(k, m.keys(), scorer=fuzz.WRatio)
            got = m[best[0]] if best and best[1] >= NAME_MATCH_FLOOR else None
        if got is not None and got != r.person_id:
            wrong.append((r.season, r.player_name, r.person_id))
    hit = ev.set_index(["season", "player_name", "person_id"]).index.isin(wrong)
    A.put("wrong_player_events", hit.sum(), "events the season-only matcher gives to another player: "
          + "; ".join(f"{n} ({s})" for s, n, _ in wrong), macro="WrongPlayerEvents", fmt="integer")
    A.put("wrong_player_games", ev[hit].game_id.nunique(), "games they are in", macro="WrongPlayerGames", fmt="word")
    A.put("wrong_player_players", len(wrong), "players", macro="DqWrongPlayerPlayers", fmt="word")
    left = find_wrong_ids(conn)
    A.put("wrong_player_left", len(left), "events repair_espn_player_ids.find() would still change (0 = repaired)")

    # 2. Single events whose text lacks the tagged name although his other events of the game carry it.
    ev["pn"] = ev.player_name.map(fold)
    ev["dn"] = ev.description.map(fold)
    ev["hit"] = [p in d for p, d in zip(ev.pn, ev.dn)]
    grp = ev.groupby(["game_id", "team_tricode", "person_id"]).hit.transform("any")
    single = ev[~ev.hit & grp]
    tagged = ev.groupby("game_id").apply(lambda g: dict(zip(g.pn, g.team_tricode)), include_groups=False)
    other = same = 0
    for r in single.itertuples(index=False):
        named = [n for n in tagged[r.game_id] if n != r.pn and n in r.dn]
        if named:
            other += 1
            same += any(tagged[r.game_id][n] == r.team_tricode for n in named)
    A.put("tag_text_events", len(single), "events whose text lacks the tagged player's name although his other events "
          "of the game carry it", macro="TagTextDisagree", fmt="integer")
    A.put("tag_text_games", single.game_id.nunique(), "games they are in")
    A.put("tag_text_other", other, "of those, the text names another player tagged in the same game",
          macro="DqTagTextOther", fmt="integer")
    A.put("tag_text_teammate", same, "of those, a teammate of the tagged player", macro="DqTagTextTeammate", fmt="integer")
    A.put("tag_text_events_checked", len(ev), "ESPN events with a player id")
    # per-event frames for build_data_quality.py (the per-game flags); the audit itself ignores them
    return ev[hit][["id", "game_id"]].reset_index(drop=True), single[["id", "game_id"]].reset_index(drop=True)


def shot_checks(conn, cur, A):
    """One parse of every ESPN game: the text's two/three call on every attempt, ESPN's clock, then the match."""
    season_names, all_names = load_season_names(cur)
    games, grouped = load_espn(conn)
    rows = []
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None:
            continue
        game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
        game.home = g.home_team          # parse() needs the home side for score steps (walk() sets it the same way)
        sides = (g.home_team, g.away_team)
        for e in game.parse():
            if e["kind"] == "fg" and e["team"] in sides:
                rows.append((g.game_id, int(g.season), e["action_number"], e["pid"], e["period"], bool(e["made"]),
                             e["val"] == 3, e["secs"]))
        if i % 2000 == 0:
            log(f"  {i} of {len(games)} games parsed")
    del grouped
    shots = pd.DataFrame(rows, columns=["game_id", "season", "action_number", "pid", "period", "made", "text_three", "secs"])
    m = match_coordinates(conn, shots)
    m["matched"] = m.nba_shot_id.notna()
    known = m[m.pid.notna()]
    for season, g in m.groupby("season"):
        A.put("chart_match_share_all", g.matched.mean(), "field-goal attempts of the two teams matched to the chart", season)
    for season, g in known.groupby("season"):
        A.put("chart_match_share", g.matched.mean(), "the same, attempts with an identified shooter", season)
    A.put("chart_match_share_all", m.matched.mean(), "field-goal attempts of the two teams matched to the chart, all seasons")
    A.put("chart_match_share", known.matched.mean(), "the same, attempts with an identified shooter, all seasons",
          macro="DqChartMatchPct", fmt="pct1")
    by = known.groupby("season").matched.mean()
    A.put("chart_match_min", by.min(), "lowest season", macro="DqChartMatchMinPct", fmt="pct1")
    A.put("chart_match_min_season", by.idxmin(), "that season", macro="DqChartMatchMinSeason", fmt="season")
    A.put("chart_match_other_min", by.drop(by.idxmin()).min(), "lowest among the other seasons",
          macro="DqChartMatchOtherMinPct", fmt="pct1")
    (missing,), = q(cur, """WITH charted AS (SELECT DISTINCT game_id FROM player_shots WHERE game_id LIKE '002%%' AND season >= '2020-21')
                            SELECT count(*) FROM lineup_stint_games WHERE game_ok AND nba_game_id IS NOT NULL
                            AND nba_game_id NOT IN (SELECT game_id FROM charted)""")
    A.put("chart_missing_games", missing, "reconciled games whose NBA id has no player_shots row",
          macro="DqChartMissingGames", fmt="word")

    mm = m[m.matched].copy()
    mm["nba_three"] = mm.shot_type.str.startswith("3")
    miss = mm[~mm.made]
    t2n3 = miss[~miss.text_three & miss.nba_three]
    A.put("miss_threes_as_twos", len(t2n3), "matched missed shots the text calls a two and the chart a three",
          macro="MissedThreesAsTwos", fmt="integer")
    A.put("miss_threes_median_ft", float(np.median(t2n3.coord_ft)), "their median distance from the chart's coordinates (ft)",
          macro="MissedThreesMedianFt", fmt="int_round")
    A.put("miss_twos_as_threes", int((miss.text_three & ~miss.nba_three).sum()),
          "matched missed shots the text calls a three and the chart a two", macro="DqMissedTwosAsThrees", fmt="integer")
    A.put("miss_matched", len(miss), "matched missed shots", macro="DqMissesMatched", fmt="integer")
    A.put("miss_disagree_share", (miss.text_three != miss.nba_three).mean(), "share of matched misses where the two calls differ",
          macro="DqMissDisagreePct", fmt="pct1")
    made = mm[mm.made]
    A.put("made_disagree", int((made.text_three != made.nba_three).sum()),
          "matched made shots whose value from the score step differs from the chart's call", macro="DqMadeDisagree", fmt="integer")
    A.put("made_matched", len(made), "matched made shots")

    clock = pd.DataFrame(q(cur, """SELECT id, minutes_remaining * 60 + seconds_remaining FROM player_shots
                                    WHERE game_id LIKE '002%%' AND season >= '2020-21'"""), columns=["nba_shot_id", "nba_clock"])
    mm = mm.merge(clock, on="nba_shot_id", how="left")
    espn = np.where(mm.period <= 4, mm.secs - PERIOD_SECONDS * (4 - mm.period), mm.secs)
    signed = espn - mm.nba_clock.to_numpy(dtype=float)
    off = np.abs(signed)
    A.put("clock_signed_median", float(np.median(signed)), "median of ESPN clock - chart clock (s; positive = ESPN shows "
          "more time left, i.e. logs the shot earlier)")
    A.put("clock_espn_more_share", float((signed > 0).mean()), "share of matched attempts where ESPN shows more time left")
    top = mm.assign(off=off).nlargest(5, "off")
    A.put("clock_largest", float(off.max()), "largest five: " + "; ".join(
        f"{r.game_id} #{r.action_number} P{r.period} {r.off:.0f} s" for r in top.itertuples()))
    A.put("clock_pairs", len(off), "matched attempts compared", macro="DqClockPairs", fmt="integer")
    A.put("clock_median", float(np.median(off)), "median |ESPN clock - chart clock| (s)", macro="DqClockMedianSec", fmt="int_round")
    A.put("clock_p99", float(np.percentile(off, 99)), "99th percentile (s)", macro="DqClockPctlSec", fmt="int_round")
    A.put("clock_big_share", float((off > CLOCK_BIG).mean()), f"share over {CLOCK_BIG:.0f} s", macro="DqClockBigPct", fmt="pct1")
    A.put("clock_max", float(off.max()), "largest (s); the largest are order-matching slips or clock errors (see clock_largest)")
    # per-attempt frames for build_data_quality.py (the per-game flags); the audit itself ignores them
    return m, mm.assign(clock_off=off)


LAG_CLASSES = (("fg_made", "Made"), ("ft_later_made", "FtLater"), ("reb", "Reb"), ("tov_steal", "Steal"), ("tov_dead", "Dead"),
               ("fg_miss", "Miss"))


def clock_lag_checks(conn, cur, A):
    """ESPN's clock against NBA.com's play-by-play of the same games (the twin games), by event class: build_event_clock's
    own clock_check() on a fresh parse and shot match of those games. It must equal pbp_event_clock_meta's stored check
    (the corrected clock it describes is the stored one)."""
    import build_event_clock as EC      # imported here: only this check needs it
    twins = [r[0] for r in q(cur, """SELECT DISTINCT 'espn_' || s.espn_id FROM pbp_games n JOIN game_scores s ON s.game_id = n.game_id
                                     JOIN pbp_games g ON g.game_id = 'espn_' || s.espn_id
                                     WHERE n.source = 'nba_api' AND s.espn_id IS NOT NULL ORDER BY 1""")]
    seasons = q(cur, "SELECT DISTINCT season FROM pbp_games WHERE game_id = ANY(%s)", (twins,))
    if seasons != [(EC.TWIN_SEASON,)]:
        raise SystemExit(f"clock_lag: the twin games are not all of {EC.TWIN_SEASON} ({seasons})")
    season_names, all_names = load_season_names(cur)
    games, grouped = load_espn(conn, game_ids=twins)
    chart_t = EC.chart_clock(conn, chart_matches(conn, games, grouped, season_names, all_names))
    chk = EC.clock_check(conn, season_names, all_names, chart_t, grouped)
    (stored,), = q(cur, "SELECT value FROM pbp_event_clock_meta WHERE key = 'clock_check'")
    stored = stored if isinstance(stored, dict) else __import__("json").loads(stored)
    if stored != __import__("json").loads(__import__("json").dumps(chk)):
        raise SystemExit("clock_lag: the re-measured check differs from pbp_event_clock_meta's: rerun build_event_clock.py")
    A.put("lag_twin_games", chk["twin_games"], "nba_api twin games (NBA.com play-by-play of an ESPN game)",
          macro="DqLagTwinGames", fmt="integer")
    A.put("lag_season", EC.TWIN_SEASON, "their season", macro="DqLagSeason", fmt="season")
    A.put("lag_events", chk["matched_events"], "events matched to NBA.com's by order within game, period, player and kind",
          macro="DqLagEvents", fmt="integer")
    for cls, m in LAG_CLASSES:
        c = chk["classes"][cls]
        A.put(f"lag_{cls}_n", c["n"], f"{cls}: events matched")
        A.put(f"lag_{cls}_median", c["espn_lag_median"], f"{cls}: median of ESPN time - NBA.com time (s; positive = ESPN later)",
              macro=f"DqLag{m}Sec", fmt="int_round")
        A.put(f"lag_{cls}_median_odd", c["espn_lag_median_odd"], f"{cls}: the same on the odd half of the games")
        A.put(f"lag_{cls}_median_even", c["espn_lag_median_even"], f"{cls}: the same on the even half")
        A.put(f"lag_{cls}_espn_within2", c["espn_within_2s"], f"{cls}: share of ESPN times within 2 s of NBA.com's")
        A.put(f"lag_{cls}_corr_within2", c["corrected_within_2s"], f"{cls}: share of corrected times within 2 s")
    A.put("lag_made_espn_within2", chk["classes"]["fg_made"]["espn_within_2s"], "made shots: ESPN within 2 s",
          macro="DqLagMadeWithinPct", fmt="pct0")
    A.put("lag_espn_within2", chk["all"]["espn_within_2s"], "every matched event: ESPN's time within 2 s of NBA.com's",
          macro="DqLagEspnWithinPct", fmt="pct1")
    A.put("lag_corr_within2", chk["all"]["corrected_within_2s"], "every matched event: the corrected clock within 2 s",
          macro="DqLagCorrWithinPct", fmt="pct1")
    A.put("lag_espn_abs_median", chk["all"]["espn_abs_err_median"], "every matched event: median |ESPN - NBA.com| (s)")
    A.put("lag_corr_abs_median", chk["all"]["corrected_abs_err_median"], "every matched event: median |corrected - NBA.com| (s)")


# ── The error classes: the paper's table ─────────────────────────────────────
# size: LaTeX with \pn macros only (defined in paper/numbers.tex by paper_numbers.py from paper_data_audit).

CLASSES = [
    # feed, key, class, how detected, size, handling kind, handling
    ("Play-by-play", "twin_copies", "Duplicate copies of games",
     "nba\\_api games matched to ESPN games on date and teams",
     "\\pnDqTwinGames{} games, \\pnDqTwinRows{} events, all ESPN games again (\\pnDqTwinSameTeams{} same teams, "
     "\\pnDqTwinNeutral{} neutral site)",
     "excluded", "one copy kept per game"),
    ("Play-by-play", "cup_finals", "Games the NBA does not count",
     "ESPN regular-season games with no NBA scoreboard game",
     "\\pnDqCupGames{} (NBA Cup finals)",
     "excluded", "kept in game lines, dropped from season tables"),
    ("Play-by-play", "wrong_player", "Event tagged to a same-surname player",
     "text never names the tagged player; the season-only fuzzy matcher reproduces it",
     "\\pnWrongPlayerEvents{} events, \\pnWrongPlayerGames{} games, \\pnDqWrongPlayerPlayers{} players",
     "repaired", "ids corrected by rule, fetch fixed, all tables rebuilt"),
    ("Play-by-play", "tag_text", "Tag and text name different players",
     "tagged name absent from one event, present in the player's other events that game",
     "\\pnTagTextDisagree{} events (\\pnDqTagTextOther{} name another player of the game, \\pnDqTagTextTeammate{} a teammate)",
     "disclosed", "left as is; nothing says which is right"),
    ("Play-by-play", "unidentified", "Player with no identifier",
     "named events with no player id; minutes with fewer than five identified players",
     "\\pnDqUnidEvents{} events (\\pnDqUnidEventsPct\\%); \\pnUnidMinutesPctMin--\\pnUnidMinutesPctMax\\% of minutes "
     "a season before \\pnStintLastSeason",
     "excluded", "stints left out of lineup tables"),
    ("Play-by-play", "teamless_sub", "Substitution with no team",
     "substitutions with an empty team; game-line minutes above stint minutes",
     "\\pnDqTeamlessSubs{} events in \\pnDqTeamlessGames{} games; \\pnDqTeamlessPlayerGames{} player-games "
     "+\\pnDqTeamlessExtraMin{} to +\\pnDqTeamlessExtraMax{} s; \\pnDqNanTeamRows{} line with no team",
     "disclosed", "stints right; game lines not rebuilt"),
    ("Play-by-play", "score_fields", "Stale or backward score fields",
     "summed positive score steps vs.\\ the real final; score falling between events",
     "\\pnStaleGamesPct\\% of games \\pnStaleFirstSeason{} to \\pnStaleLastSeason; backward steps in \\pnDqScoreBackwardGames{} "
     "of \\pnDqEspnGames{} games; on-court margin $\\neq 5\\times$ final in \\pnDqOnCourtOffPct\\% of team-games",
     "worked around", "points from made shots; score fields only as fallback"),
    ("Play-by-play", "last_score", "Last score $\\neq$ final score",
     "last play-by-play score vs.\\ ESPN scoreboard final",
     "\\pnDqLastScoreGames{} of \\pnDqLastScoreDenom{} games",
     "worked around", "finals and margins from the scoreboard"),
    ("Play-by-play", "missed_threes", "Missed three worded as a two",
     "text's two/three call vs.\\ the chart's on matched misses",
     "\\pnMissedThreesAsTwos{} misses (median \\pnMissedThreesMedianFt{} ft), \\pnDqMissedTwosAsThrees{} the other way; "
     "\\pnDqMissDisagreePct\\% of \\pnDqMissesMatched{} matched misses",
     "repaired", "chart's call used where matched"),
    ("Play-by-play", "clock_offset", "Clock wrong or unlike the chart's",
     "clock of the same shot in both feeds (matched by order); events outside their own period",
     "median gap \\pnDqClockMedianSec{} s, \\pnDqClockBigPct\\% over 5 s, 99th percentile \\pnDqClockPctlSec{} s; "
     "\\pnDqClockOutside{} events in \\pnDqClockOutsideGames{} games outside their period",
     "worked around", "feeds matched by order, never by clock"),
    ("Play-by-play", "clock_lag", "Clock late by event type",
     "event times vs.\\ NBA.com's play-by-play (\\pnDqLagTwinGames{} games)",
     "made shots a median \\pnDqLagMadeSec{} s late, rebounds \\pnDqLagRebSec, turnovers \\pnDqLagStealSec--\\pnDqLagDeadSec; "
     "\\pnDqLagEspnWithinPct\\% of events within 2 s",
     "worked around", "clock rebuilt (\\pnDqLagCorrWithinPct\\% within 2 s)"),
    ("Play-by-play", "unreconciled", "Game does not reconcile",
     "stint points, length and team totals vs.\\ real final and box totals",
     "\\pnDqUnrecGames{} of \\pnGamesParsed{} games (\\pnDqUnrecScore{} score, \\pnDqUnrecTotals{} rebound count, "
     "\\pnDqUnrecCup{} no final)",
     "excluded", "stored, flagged, left out of every ranking"),
    ("Shot chart", "chart_gaps", "Games missing from the chart",
     "reconciled games with no chart rows; attempts matched",
     "\\pnDqChartMissingGames{} games; \\pnDqChartMatchPct\\% of attempts matched (\\pnDqChartMatchMinPct\\% in "
     "\\pnDqChartMatchMinSeason, \\pnDqChartMatchOtherMinPct\\%+ in the others)",
     "disclosed", "unmatched attempts use the text's call"),
    ("Shot chart", "zero_distance", "Three with distance zero",
     "\\texttt{shot\\_distance} $=0$ on three-point attempts",
     "\\pnDqZeroDistMinPct--\\pnDqZeroDistMaxPct\\% of threes a season (\\pnDqZeroDistFirstPct\\% in \\pnDqChartFirstSeason)",
     "worked around", "distance from coordinates"),
    ("Shot chart", "unlocated", "Shot with no exact location",
     "share of shots at $(0,0)$ by season",
     "\\pnDqOriginMinPct--\\pnDqOriginMaxPct\\% of shots a season to \\pnDqOriginLastSeason, under \\pnDqOriginAfterMaxPct\\% after",
     "worked around", "season term in the shot model; off the quality map"),
    ("Season tables", "plus_minus", "Plus-minus field $\\neq$ final margin",
     "summed player plus-minus $\\div 5$ vs.\\ the real margin",
     "\\pnDqPlusMinusGames{} of \\pnDqPlusMinusDenom{} games since \\pnDqPlusMinusFirstSeason{} (\\pnDqPlusMinusPoint{} by "
     "1+ point, \\pnDqPlusMinusNonzero{} not summing to zero, \\pnDqPlusMinusSign{} wrong sign)",
     "worked around", "margins from the scoreboard"),
    ("Season tables", "wrong_team", "Season row lists a wrong team",
     "season row's team vs.\\ the teams of the player's game lines",
     "\\pnDqWrongTeamRows{} rows, \\pnDqWrongTeamMin{} to \\pnDqWrongTeamMax{} a season since \\pnStintFirstSeason",
     "disclosed", "team pages use play-by-play teams"),
    ("Season tables", "age_convention", "Two age conventions",
     "reported age vs.\\ age on 1 February from birth date",
     "\\pnDqAgeOlderPct\\% of player-seasons since \\pnDqAgeFirstSeason{} one year older",
     "disclosed", "new features use birth dates"),
]
KINDS = ("repaired", "worked around", "excluded", "disclosed")


def class_counts(A):
    per_season = [A.get("wrong_team_rows", s) for (k, s) in A.rows if k == "wrong_team_rows" and s]
    A.put("wrong_team_min", min(per_season), "fewest in a season", macro="DqWrongTeamMin", fmt="word")
    A.put("wrong_team_max", max(per_season), "most in a season", macro="DqWrongTeamMax", fmt="word")
    A.put("classes", len(CLASSES), "error classes in the audit", macro="DqClasses", fmt="integer")
    for kind in KINDS:
        n = sum(c[5] == kind for c in CLASSES)
        A.put(f"classes_{kind.replace(' ', '_')}", n, f"classes handled as '{kind}'",
              macro="DqClasses" + kind.title().replace(" ", ""), fmt="word")
    feeds = []
    for c in CLASSES:
        if c[0] not in feeds:
            feeds.append(c[0])
    for f in feeds:
        A.put(f"classes_{f.lower().replace(' ', '_').replace('-', '_')}", sum(c[0] == f for c in CLASSES),
              f"classes in {f}", macro="DqClasses" + f.title().replace(" ", "").replace("-", ""), fmt="word")


def tex_table():
    out = ["% paper/tables/data_audit.tex -- GENERATED by scripts/paper_data_audit.py (the class list, CLASSES).",
           "% Every number is a \\pn macro from numbers.tex (paper_numbers.py, from paper_data_audit). Do not edit.",
           "\\begin{table*}[!t]",
           "\\caption{Data-quality audit of the public feeds: every error class found, how it was detected, its size "
           "(re-measured from the database) and what the pipeline does about it}\\label{tab:audit}",
           "\\centering", "\\footnotesize", "\\setlength{\\tabcolsep}{4pt}",
           "\\begin{tabular}{p{3.0cm}p{4.2cm}p{6.0cm}p{3.7cm}}", "\\toprule",
           "Error class & How detected & Size & Handling\\\\", "\\midrule"]
    feed = None
    for f, _key, name, how, size, kind, handling in CLASSES:
        if f != feed:
            if feed is not None:
                out.append("\\addlinespace")
            out.append(f"\\multicolumn{{4}}{{l}}{{\\emph{{{f}}}}}\\\\")
            feed = f
        out.append(f"{name} & {how} & {size} & \\textsc{{{kind}}}: {handling}\\\\")
    out += ["\\bottomrule", "\\end{tabular}", "\\end{table*}", ""]
    return "\n".join(out)


def write(conn, A):
    cur = conn.cursor()
    for t in ("paper_data_audit", "paper_data_audit_classes"):
        cur.execute(f"DROP TABLE IF EXISTS {t}")
    cur.execute("""CREATE TABLE paper_data_audit (key TEXT NOT NULL, season INTEGER NOT NULL, value DOUBLE PRECISION,
                   note TEXT, macro TEXT UNIQUE, fmt TEXT, PRIMARY KEY (key, season))""")
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_data_audit VALUES %s",
                                   [(k, s, v, n, m, f) for (k, s), (v, n, m, f) in sorted(A.rows.items())])
    cur.execute("""CREATE TABLE paper_data_audit_classes (ord INTEGER PRIMARY KEY, feed TEXT NOT NULL, key TEXT UNIQUE NOT NULL,
                   error_class TEXT NOT NULL, detection TEXT NOT NULL, size_tex TEXT NOT NULL, handling_kind TEXT NOT NULL,
                   handling TEXT NOT NULL)""")
    psycopg2.extras.execute_values(cur, "INSERT INTO paper_data_audit_classes VALUES %s",
                                   [(i + 1, *c) for i, c in enumerate(CLASSES)])
    conn.commit()
    for t in ("paper_data_audit", "paper_data_audit_classes"):
        cur.execute(f"SELECT count(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        log(f"{t}: {cur.fetchone()}")


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    A = Audit()
    feed_checks(cur, A)
    log("feed checks")
    score_checks(cur, A)
    log("score checks")
    table_checks(cur, A)
    chart_checks(cur, A)
    log("table and chart checks")
    identity_checks(conn, cur, A)
    log("identity checks")
    shot_checks(conn, cur, A)
    log("shot checks")
    clock_lag_checks(conn, cur, A)
    log("clock lag checks")
    class_counts(A)
    conn.rollback()
    write(conn, A)
    os.makedirs(os.path.dirname(TEX_OUT), exist_ok=True)
    with open(TEX_OUT, "w") as f:
        f.write(tex_table())
    log(f"wrote {TEX_OUT}")
    for (k, s), (v, n, m, f) in sorted(A.rows.items()):
        if m:
            print(f"  {m:28s} {'(none)' if v is None else f'{v:>14,.4f}':>14}  {n}")
    conn.close()


if __name__ == "__main__":
    main()
