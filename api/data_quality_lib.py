"""
data_quality_lib.py
====================
The Data Quality page's definitions (round 6, step 11), shared by
scripts/build_data_quality.py and api/routers/data_quality.py:

  * the per-game rule of each of the audit's error classes (paper_data_audit_classes,
    built by scripts/paper_data_audit.py): which column of data_quality_game_flags
    says the class touched a game, and the game's quality level;
  * the live check of each class: a query the API runs on request against the tables
    as they are now. Each one re-measures numbers the audit stored (paper_data_audit,
    by key and season) with its own, independent SQL, so the page can say whether the
    database still matches the audit. Three classes need a replay of every game in
    Python (fuzzy name matching, text folding, the shot-chart match) and are checked
    at build time only;
  * the plain-text size of each class, written from the audit's stored values;
  * the drop sets and results of the "does it matter?" view.

Nothing here writes to the database.
"""

from decimal import ROUND_HALF_UP, Decimal

FIRST_SEASON = 2021                 # play-by-play era (2020-21 on): the span of the per-game flags
STALE_SCORE_SHARE = 0.10            # paper_numbers.py's: a season "has stale score fields" when 10%+ of its games miss
MAX_DROP_SHARE = 0.5                # a class that touches more than half the scope's games is not dropped on its own
CLOCK_BIG = 5.0                     # paper_data_audit.CLOCK_BIG: "an offset over 5 s"
CHART_UNMATCHED_SHARE = 0.05        # a game's chart gap: no chart rows, or more than 5% of its identified attempts unmatched

# Quality level of a game, worst first. The level is the worst handling among the classes that touch it.
LEVELS = (
    ("excluded", "Excluded", "Left out of every ranking: no real final (an NBA Cup final) or it does not reconcile."),
    ("flagged", "Flagged", "Kept, with an error left in the data: an event's tag and text disagree, a substitution has no team, "
                           "a player has no id (those stints are left out of lineup tables) or the shot chart is missing attempts."),
    ("worked_around", "Worked around", "Touched only by errors the pipeline repairs or works around (stale score fields, the clock, "
                                       "missed threes worded as twos, zero distances, the plus-minus field, a duplicate copy)."),
    ("clean", "Clean", "No error class touches the game."),
)
LEVEL_OF = {
    "cup_finals": "excluded", "unreconciled": "excluded",
    "tag_text": "flagged", "teamless_sub": "flagged", "unidentified": "flagged", "chart_gaps": "flagged",
    "twin_copies": "worked_around", "wrong_player": "worked_around", "score_fields": "worked_around", "last_score": "worked_around",
    "missed_threes": "worked_around", "clock_offset": "worked_around", "zero_distance": "worked_around", "plus_minus": "worked_around",
}

# The per-game rule of each class: an SQL predicate on a data_quality_game_flags row, and what it means.
PER_GAME = {
    "twin_copies": ("twin", "an nba_api copy of the game exists (that copy is left out; this ESPN copy is used)"),
    "cup_finals": ("cup_final", "ESPN files the game as regular season; the NBA does not count it"),
    "wrong_player": ("wrong_player_events > 0", "events once tagged to a same-surname player (repaired)"),
    "tag_text": ("tag_text_events > 0", "an event's text lacks the tagged player's name though his other events of the game carry it"),
    "unidentified": ("untracked_seconds > 0", "a stint with fewer than five identified players a side"),
    "teamless_sub": ("teamless_subs > 0", "a substitution with no team"),
    "score_fields": ("score_stale OR score_backward",
                     "summing ESPN's positive score steps misses the real final, or a score field steps backwards"),
    "last_score": ("last_score_off", "the last play-by-play score differs from ESPN's scoreboard final"),
    "missed_threes": ("missed_three_calls > 0", "a matched miss the text calls a two and the chart a three, or the reverse"),
    "clock_offset": ("clock_outside_events > 0", "an event stamped outside its own period (ESPN's lag behind the chart, a median "
                                                 "4 s, is on every game: the game's own median is kept beside it)"),
    "unreconciled": ("NOT game_ok", "the stints fail the reconciliation (final score, game length, team totals)"),
    "chart_gaps": ("chart_missing OR chart_unmatched > %s * fg_attempts" % CHART_UNMATCHED_SHARE,
                   f"no shot-chart rows, or more than {CHART_UNMATCHED_SHARE:.0%} of the game's identified attempts unmatched"),
    "zero_distance": ("zero_dist_threes > 0", "a three-point attempt with shot_distance = 0"),
    "plus_minus": ("pm_off", "team_game_fatigue.plus_minus differs from the final margin"),
}
NOT_PER_GAME = {
    "wrong_team": "A season-table error (a player-season row with a wrong team), not a game's.",
    "age_convention": "A season-table convention (how ages are counted), not a game's.",
    "clock_lag": "On every game: ESPN logs live-ball events late by type (made shots a median 14 s), and pbp_event_clock rebuilds "
                 "the clock of every event. Measured only where NBA.com's own play-by-play exists (the twin games of 2024-25).",
    "unlocated": "Before 2010-11 only. In the play-by-play era a shot at (0, 0) is a real shot at the rim (about five a game, "
                 "dunks and tips), so it singles no game out.",
}

# Classes whose live check needs a replay of every game in Python: checked by the build only.
BUILD_ONLY = {
    "wrong_player": "Replays the fetch's fuzzy name matcher over every tagged event (rapidfuzz) and reruns the repair's finder, "
                    "which must find nothing left.",
    "tag_text": "Folds every tagged event's text and name (accents, dots, apostrophes) in Python, game by game.",
    "missed_threes": "Parses every game and matches each attempt to the shot chart by order within game, shooter and period.",
    "clock_lag": "Parses the twin games of 2024-25 and matches every event to NBA.com's play-by-play of the same game by order "
                 "within game, period, player and kind (build_event_clock.clock_check, which must equal pbp_event_clock_meta).",
}

# ── "Does it matter?" ────────────────────────────────────────────────────────

RESULTS = {
    "impact": {
        "label": "RAPM with a box-score prior vs BPM",
        "short": "Player impact",
        "what": "The protocol's player-impact comparison: ratings from season S predict the game margins of S+1 "
                "(paper_eval impact_next: tune = the margins of 2021-22 to 2023-24 pooled, validate 2024-25, test 2025-26). "
                "Negative = the first model's next-season error is lower.",
        "task": "impact_next", "metric": "game_rmse", "headline": ("rapm_prior", "bpm"),
        "pairs": [("rapm_prior", "bpm"), ("rapm_single", "bpm"), ("rapm_tracker", "bpm"), ("rapm_tracker", "rapm_prior")],
        "scopes": ("everywhere", "scoring"),
        "unit": "game margin RMSE (points)",
    },
    "possessions": {
        "label": "Points per possession after a steal vs after a made shot",
        "short": "Possessions",
        "what": "Possession Explorer's league result, 2020-21 to 2025-26 pooled: points per possession by how the possession began, "
                "and transition (first attempt within 7 s) against settled possessions among timed ones.",
        "task": "possessions", "metric": "ppp", "headline": ("steal", "made_fg"),
        "pairs": [("steal", "made_fg"), ("transition", "settled")],
        "scopes": ("everywhere",),
        "unit": "points per possession",
    },
    "availability": {
        "label": "Odds that know who played vs the pre-game model",
        "short": "Who played",
        "what": "Season Simulator's availability-aware odds (BPM of the players who played) against the protocol's pre-game model "
                "(paper_eval's prior_rest), by log loss. Scored on the kept games only: the stored odds are not refitted "
                "(one coefficient, fitted on every game).",
        "task": "pregame", "metric": "log_loss", "headline": ("avail_bpm", "prior_rest"),
        "pairs": [("avail_bpm", "prior_rest"), ("avail_rapm", "prior_rest")],
        "scopes": ("scoring",),
        "unit": "log loss",
    },
}
MODEL_LABELS = {
    "rapm_prior": "RAPM + BPM prior", "rapm_single": "One-season RAPM", "rapm_tracker": "Rating Tracker", "bpm": "BPM", "zero": "Zero",
    "steal": "After a steal", "made_fg": "After a made shot", "transition": "Transition", "settled": "Settled",
    "avail_bpm": "Who played (BPM)", "avail_rapm": "Who played (RAPM prior)", "prior_rest": "Pre-game model",
}
SCOPE_LABELS = {
    "everywhere": "Dropped from fitting and scoring",
    "scoring": "Dropped from scoring only",
}


def drop_sets(flag_rows, scope_ids):
    """[(key, label, predicate, ids)] for one result: every game, the flagged ones, then each per-game class that touches
    between one game and MAX_DROP_SHARE of the scope (flag_rows: {game_id: row dict with the class hits under 'classes'
    and 'quality'}; scope_ids: the result's games, as data_quality_game_flags ids)."""
    scope = [g for g in scope_ids if g in flag_rows]
    n = len(scope)
    out = [("none", "Every game", None, frozenset()),
           ("flagged", "Flagged or excluded games", "quality in (flagged, excluded)",
            frozenset(g for g in scope if flag_rows[g]["quality"] in ("flagged", "excluded")))]
    for key in PER_GAME:
        ids = frozenset(g for g in scope if key in flag_rows[g]["classes"])
        if 0 < len(ids) <= MAX_DROP_SHARE * n:
            out.append((key, None, PER_GAME[key][0], ids))
    return out


# ── Formatting: the precision the paper prints a number with ─────────────────

def _half_up(x, places):
    return Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def printed(value, fmt):
    """The audit number as the paper prints it (paper_numbers.py's formats), for comparing a live value with the stored one."""
    if value is None:
        return None
    if fmt in ("integer", "word", "season"):
        return int(round(value))
    if fmt == "int_round":
        return int(_half_up(value, 0))
    if fmt == "pct0":
        return float(_half_up(value * 100, 0))
    if fmt == "pct1":
        return float(_half_up(value * 100, 1))
    return float(value)


def agrees(live, stored, fmt):
    """Same number at the paper's printed precision (exact, to 1e-9 relative, when the audit row has no format)."""
    if live is None or stored is None:
        return live is None and stored is None
    if fmt:
        return printed(live, fmt) == printed(stored, fmt)
    return abs(live - stored) <= 1e-9 * max(1.0, abs(stored))


def season_label(s):
    return f"{s - 1}-{str(s)[-2:]}"


def _n(v):
    return f"{int(round(v)):,}"


def _pct(v, d=1):
    return f"{float(_half_up(v * 100, d)):.{d}f}%"


# ── The plain-text size of each class, from the audit's stored values ─────────
# A(key, season=0) -> value; S(key) -> {season: value} for the per-season rows.

def size_text(key, A, S):
    if key == "twin_copies":
        return (f"{_n(A('twin_games'))} games, {_n(A('twin_rows'))} events, all ESPN games again "
                f"({_n(A('twin_same_teams'))} same teams, {_n(A('twin_neutral'))} neutral site)")
    if key == "cup_finals":
        return f"{_n(A('cup_games'))} games (NBA Cup finals), {_n(A('cup_events'))} events"
    if key == "wrong_player":
        return (f"{_n(A('wrong_player_events'))} events in {_n(A('wrong_player_games'))} games, {_n(A('wrong_player_players'))} players; "
                f"{_n(A('wrong_player_left'))} left after the repair")
    if key == "tag_text":
        return (f"{_n(A('tag_text_events'))} events in {_n(A('tag_text_games'))} games ({_n(A('tag_text_other'))} name another player "
                f"of the game, {_n(A('tag_text_teammate'))} a teammate)")
    if key == "unidentified":
        sh = S("unid_minutes_share")
        last = max(sh)
        early = [v for s, v in sh.items() if s < last]
        return (f"{_n(A('unid_events'))} events ({_pct(A('unid_events_share'))}); {_pct(min(early), 0)} to {_pct(max(early), 0)} "
                f"of minutes a season before {season_label(last)} ({_pct(sh[last], 1)} in {season_label(last)})")
    if key == "teamless_sub":
        return (f"{_n(A('teamless_subs'))} events in {_n(A('teamless_games'))} games; {_n(A('teamless_player_games'))} player-games "
                f"credited +{_n(A('teamless_extra_min'))} to +{_n(A('teamless_extra_max'))} s in the game lines; "
                f"{_n(A('nan_team_rows'))} line with no team")
    if key == "score_fields":
        g, m = S("score_steps_games"), S("score_steps_miss")
        bad = [s for s in sorted(g) if m[s] / g[s] >= STALE_SCORE_SHARE]
        share = sum(m[s] for s in bad) / sum(g[s] for s in bad)
        return (f"summed score steps miss the final in {_pct(share, 0)} of games {season_label(bad[0])} to {season_label(bad[-1])}; "
                f"backward steps in {_n(A('score_backwards_games'))} of {_n(A('espn_games'))} games; on-court margin "
                f"≠ 5 × final in {_pct(A('oncourt_off_share'), 0)} of full-minute team-games")
    if key == "last_score":
        return f"{_n(A('last_score_bad'))} of {_n(A('last_score_games'))} games"
    if key == "missed_threes":
        return (f"{_n(A('miss_threes_as_twos'))} misses (median {_n(A('miss_threes_median_ft'))} ft) called a two by the text and a three "
                f"by the chart, {_n(A('miss_twos_as_threes'))} the other way; {_pct(A('miss_disagree_share'))} of "
                f"{_n(A('miss_matched'))} matched misses")
    if key == "clock_offset":
        return (f"median gap {_n(A('clock_median'))} s, {_pct(A('clock_big_share'))} over 5 s, 99th percentile {_n(A('clock_p99'))} s "
                f"({_n(A('clock_pairs'))} shots); {_n(A('clock_outside_events'))} events in {_n(A('clock_outside_games'))} games "
                f"outside their period")
    if key == "clock_lag":
        return (f"median lag behind NBA.com's play-by-play: made shots {_n(A('lag_fg_made_median'))} s, later free throws "
                f"{_n(A('lag_ft_later_made_median'))} s, rebounds {_n(A('lag_reb_median'))} s, steals {_n(A('lag_tov_steal_median'))} s, "
                f"dead-ball turnovers {_n(A('lag_tov_dead_median'))} s, misses {_n(A('lag_fg_miss_median'))} s; "
                f"{_pct(A('lag_espn_within2'))} of {_n(A('lag_events'))} events within 2 s ({_pct(A('lag_corr_within2'))} on the corrected "
                f"clock), {_n(A('lag_twin_games'))} games of {season_label(int(A('lag_season')))}")
    if key == "unreconciled":
        return (f"{_n(A('unrec_games'))} games ({_n(A('unrec_score'))} score, {_n(A('unrec_totals'))} team totals, "
                f"{_n(A('unrec_cup'))} no final)")
    if key == "chart_gaps":
        return (f"{_n(A('chart_missing_games'))} games with no chart rows; {_pct(A('chart_match_share'))} of attempts matched "
                f"({_pct(A('chart_match_min'))} in {season_label(int(A('chart_match_min_season')))}, "
                f"{_pct(A('chart_match_other_min'))}+ in the others)")
    if key == "zero_distance":
        return (f"{_pct(A('zero_dist_min'), 0)} to {_pct(A('zero_dist_max'), 0)} of threes a season "
                f"({_pct(A('zero_dist_first'), 0)} in {season_label(int(A('zero_dist_first_season')))})")
    if key == "unlocated":
        return (f"{_pct(A('origin_min'), 0)} to {_pct(A('origin_max'), 0)} of shots a season to "
                f"{season_label(int(A('origin_last_season')))}, at most {_pct(A('origin_after_max'), 2)} after")
    if key == "plus_minus":
        return (f"{_n(A('pm_bad'))} of {_n(A('pm_games'))} games since {season_label(int(A('pm_first_season')))} "
                f"({_n(A('pm_point'))} by a point or more, {_n(A('pm_nonzero'))} not summing to zero, {_n(A('pm_sign'))} wrong sign)")
    if key == "wrong_team":
        per = [v for s, v in S("wrong_team_rows").items() if s]
        return f"{_n(A('wrong_team_rows'))} rows, {_n(min(per))} to {_n(max(per))} a season since 2020-21"
    if key == "age_convention":
        return f"{_pct(A('age_older'), 0)} of player-seasons since {season_label(int(A('age_first_season')))} one year older"
    raise KeyError(key)


# ── Live checks: (audit key, season, value) re-measured from the tables as they are now ──────────────────────
# Independent SQL, not the audit's own code; build_data_quality.py checks that each one equals a fresh audit run.

def _one(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchone()


def _all(cur, sql, args=None):
    cur.execute(sql, args)
    return cur.fetchall()


def live_twin_copies(cur):
    rows, games = _one(cur, """SELECT count(*), count(DISTINCT game_id) FROM pbp_events
                               WHERE game_id IN (SELECT game_id FROM pbp_games WHERE source = 'nba_api')""")
    return [("twin_rows", 0, rows), ("twin_games", 0, games)]


def live_cup_finals(cur):
    (n,) = _one(cur, """SELECT count(*) FROM pbp_games g WHERE g.source = 'espn'
                        AND EXISTS (SELECT 1 FROM pbp_events e WHERE e.game_id = g.game_id)
                        AND NOT EXISTS (SELECT 1 FROM game_scores s WHERE 'espn_' || s.espn_id = g.game_id)""")
    return [("cup_games", 0, n)]


def live_unidentified(cur):
    named, noid = _one(cur, """SELECT count(*), count(*) FILTER (WHERE e.person_id IS NULL) FROM pbp_events e
                               JOIN pbp_games g ON g.game_id = e.game_id AND g.source = 'espn'
                               WHERE e.player_name IS NOT NULL AND e.player_name <> ''""")
    out = [("unid_events", 0, noid), ("unid_events_share", 0, noid / named if named else None)]
    out += [("unid_minutes_share", int(s), float(v)) for s, v in _all(
        cur, "SELECT season, bad_lineup_minutes / minutes FROM lineup_stint_seasons ORDER BY 1")]
    return out


def live_teamless_sub(cur):
    n, g = _one(cur, """SELECT count(*), count(DISTINCT e.game_id) FROM pbp_events e
                        JOIN pbp_games p ON p.game_id = e.game_id AND p.source = 'espn'
                        WHERE e.action_type = 'Substitution' AND e.team_tricode IS NULL""")
    (nan,) = _one(cur, "SELECT count(*) FROM player_game_lines WHERE team_abbreviation IS NULL OR team_abbreviation = 'NaN'")
    return [("teamless_subs", 0, n), ("teamless_games", 0, g), ("nan_team_rows", 0, nan)]


def live_score_fields(cur):
    out = []
    for season, games, miss in _all(cur, """
            WITH d AS (SELECT e.game_id, greatest(e.score_home - lag(e.score_home) OVER w, 0) up_h,
                              greatest(e.score_away - lag(e.score_away) OVER w, 0) up_a
                       FROM pbp_events e WHERE e.game_id IN (SELECT game_id FROM lineup_stint_games)
                       WINDOW w AS (PARTITION BY e.game_id ORDER BY e.action_number)),
                 t AS (SELECT game_id, sum(up_h) h, sum(up_a) a FROM d GROUP BY game_id)
            SELECT g.season, count(*), count(*) FILTER (WHERE (t.h = g.final_home AND t.a = g.final_away) IS NOT TRUE)
            FROM t JOIN lineup_stint_games g USING (game_id) GROUP BY g.season ORDER BY 1"""):
        out += [("score_steps_games", int(season), games), ("score_steps_miss", int(season), miss)]
    back, games = _one(cur, """
        WITH d AS (SELECT e.game_id, e.score_home < lag(e.score_home) OVER w OR e.score_away < lag(e.score_away) OVER w AS down
                   FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id AND g.source = 'espn'
                   WHERE e.score_home IS NOT NULL AND e.score_away IS NOT NULL
                   WINDOW w AS (PARTITION BY e.game_id ORDER BY e.action_number, e.id))
        SELECT count(DISTINCT game_id) FILTER (WHERE down), count(DISTINCT game_id) FROM d""")
    return out + [("score_backwards_games", 0, back), ("espn_games", 0, games)]


def live_last_score(cur):
    n, bad = _one(cur, """SELECT count(*), count(*) FILTER (WHERE bad) FROM (
                              SELECT t.game_id, bool_or(t.pts_for <> s.pts_for OR t.pts_against <> s.pts_against) bad
                              FROM team_game_totals t JOIN game_scores s
                                ON s.espn_id IS NOT NULL AND 'espn_' || s.espn_id = t.game_id AND s.team_abbreviation = t.team_abbreviation
                              GROUP BY t.game_id) x""")
    return [("last_score_games", 0, n), ("last_score_bad", 0, bad)]


def live_clock_offset(cur):
    (n, g), = [_one(cur, """SELECT count(*), count(DISTINCT e.game_id) FROM pbp_events e
                            JOIN pbp_games p ON p.game_id = e.game_id AND p.source = 'espn'
                            WHERE e.period BETWEEN 1 AND 4
                              AND NOT (e.seconds_remaining BETWEEN 720 * (4 - e.period) AND 720 * (5 - e.period))""")]
    # pbp_event_clock's 'chart' events carry the shot chart's own clock: the same shots the audit compares
    pairs, med, p99, big = _one(cur, """
        SELECT count(*), percentile_disc(0.5) WITHIN GROUP (ORDER BY d), percentile_cont(0.99) WITHIN GROUP (ORDER BY d),
               avg((d > %s)::int::float8)
        FROM (SELECT abs(e.seconds_remaining - c.seconds_remaining) d FROM pbp_event_clock c
              JOIN pbp_events e ON e.id = c.event_id WHERE c.source = 'chart') x""", (CLOCK_BIG,))
    return [("clock_outside_events", 0, n), ("clock_outside_games", 0, g), ("clock_pairs", 0, pairs),
            ("clock_median", 0, float(med)), ("clock_p99", 0, float(p99)), ("clock_big_share", 0, float(big))]


def live_unreconciled(cur):
    (n,) = _one(cur, "SELECT count(*) FILTER (WHERE NOT game_ok) FROM lineup_stint_games")
    return [("unrec_games", 0, n)]


def live_chart_gaps(cur):
    (n,) = _one(cur, """SELECT count(*) FROM lineup_stint_games g WHERE g.game_ok AND g.nba_game_id IS NOT NULL
                        AND NOT EXISTS (SELECT 1 FROM player_shots s WHERE s.game_id = g.nba_game_id)""")
    return [("chart_missing_games", 0, n)]


def _shots_by_season(cur):
    return _all(cur, """SELECT int4(left(season, 4)) + 1, count(*), count(*) FILTER (WHERE loc_x = 0 AND loc_y = 0),
                               count(*) FILTER (WHERE shot_type LIKE '3%%'),
                               count(*) FILTER (WHERE shot_type LIKE '3%%' AND shot_distance = 0)
                        FROM player_shots WHERE game_id LIKE '002%%' GROUP BY 1 ORDER BY 1""")


def live_zero_distance(cur):
    return [("zero_dist_threes", int(s), z / t) for s, _, _, t, z in _shots_by_season(cur)]


def live_unlocated(cur):
    return [("origin_shots", int(s), o / n) for s, n, o, _, _ in _shots_by_season(cur)]


def live_plus_minus(cur):
    games, bad, point, sign = _one(cur, """
        SELECT count(DISTINCT f.game_id), count(DISTINCT f.game_id) FILTER (WHERE abs(f.plus_minus - (s.pts_for - s.pts_against)) > 1e-6),
               count(DISTINCT f.game_id) FILTER (WHERE abs(f.plus_minus - (s.pts_for - s.pts_against)) >= 1),
               count(DISTINCT f.game_id) FILTER (WHERE f.plus_minus <> 0 AND (f.plus_minus > 0) <> f.win)
        FROM team_game_fatigue f JOIN game_scores s ON s.game_id = f.game_id AND s.team_abbreviation = f.team_abbreviation""")
    return [("pm_games", 0, games), ("pm_bad", 0, bad), ("pm_point", 0, point), ("pm_sign", 0, sign)]


def live_wrong_team(cur):
    rows = _all(cur, """SELECT s.season, count(*) FILTER (WHERE NOT EXISTS (
                                SELECT 1 FROM player_game_lines l WHERE l.player_id = s.player_id AND l.season = s.season
                                AND l.team_abbreviation = s.team_abbreviation))
                        FROM player_season_stats s
                        WHERE EXISTS (SELECT 1 FROM player_game_lines l WHERE l.player_id = s.player_id AND l.season = s.season)
                        GROUP BY 1 ORDER BY 1""")
    return [("wrong_team_rows", int(s), n) for s, n in rows] + [("wrong_team_rows", 0, sum(n for _, n in rows))]


def live_age_convention(cur):
    (share,) = _one(cur, """SELECT avg((s.age = date_part('year', age(make_date(s.season, 2, 1), b.birth_date)) + 1)::int::float8)
                            FROM player_season_stats s JOIN player_bio b ON b.player_id = s.player_id
                            WHERE s.season >= 2010 AND s.age IS NOT NULL AND b.birth_date IS NOT NULL""")
    return [("age_older", 0, share)]


LIVE = {
    "twin_copies": live_twin_copies, "cup_finals": live_cup_finals, "unidentified": live_unidentified,
    "teamless_sub": live_teamless_sub, "score_fields": live_score_fields, "last_score": live_last_score,
    "clock_offset": live_clock_offset, "unreconciled": live_unreconciled, "chart_gaps": live_chart_gaps,
    "zero_distance": live_zero_distance, "unlocated": live_unlocated, "plus_minus": live_plus_minus,
    "wrong_team": live_wrong_team, "age_convention": live_age_convention,
}
LIVE_NOTES = {
    "clock_offset": "Shot offsets from pbp_event_clock's chart-matched events, which carry the chart's own clock; the audit matches "
                    "the same shots itself. Agreement is at the precision the paper prints (whole seconds, 0.1%).",
    "chart_gaps": "Reconciled games with no chart rows; the share of attempts matched needs the full replay (build only).",
    "score_fields": "Per season, games whose summed positive score steps miss the real final; games whose score steps backwards.",
}


def compare(results, audit):
    """[{key, season, live, stored, fmt, ok}] for one live check's (key, season, value) rows against paper_data_audit
    ({(key, season): (value, fmt)})."""
    out = []
    for key, season, value in results:
        stored, fmt = audit.get((key, season), (None, None))
        live = None if value is None else float(value)
        out.append({"key": key, "season": season, "live": live, "stored": stored, "fmt": fmt,
                    "ok": stored is not None and agrees(live, stored, fmt)})
    return out
