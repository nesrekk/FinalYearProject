"""The team shown for a player-season (round 8 step 5, R8-020).

player_season_stats.team_abbreviation is NBA.com's team for the season row,
read when the row was loaded. For a player who changed teams afterwards it can
be a team he never played for that season: 22 rows from 2020-21 to 2025-26
(Desmond Bane ORL and Kentavious Caldwell-Pope MEM in 2024-25, Anthony Davis
WAS in 2025-26 ...). Where the play-by-play game lines exist (2020-21 on) and
the row's team isn't one he played for, the pages show the last team he played
for in the lines instead (NBA.com's own convention for a traded player's row).

The table itself is left as loaded: the lineup parser (pbp_lineups.py) and the
paper's data audit read it as is. Pages that show or filter by a player's
season team put season_team_sql() where they read the column.

Cached per process (the 22 pairs): restart the service after rebuilding
player_game_lines or reloading player_season_stats.
"""

import re

_FIXES = None

FIX_SQL = """
    WITH pbp AS (
        SELECT player_id, season, array_agg(DISTINCT team_abbreviation) AS teams,
               (array_agg(team_abbreviation ORDER BY game_date DESC, game_id DESC))[1] AS last_team
        FROM player_game_lines WHERE seconds > 0 AND team_abbreviation <> 'NaN'
        GROUP BY player_id, season
    )
    SELECT s.player_id, s.season, s.team_abbreviation, p.last_team
    FROM player_season_stats s JOIN pbp p USING (player_id, season)
    WHERE s.team_abbreviation IS NOT NULL AND NOT (s.team_abbreviation = ANY(p.teams))
    ORDER BY s.season, s.player_id
"""


def season_team_fixes(cur):
    """{(player_id, season): (season row's team, team shown)} for the rows whose team he never played for."""
    global _FIXES
    if _FIXES is None:
        cur.execute(FIX_SQL)
        fixes = {}
        for pid, season, nba_team, team in cur.fetchall():
            if not re.fullmatch(r"[A-Z]{2,4}", team or ""):
                continue
            fixes[(int(pid), int(season))] = (nba_team, team)
        _FIXES = fixes
    return _FIXES


def season_team_sql(cur, prefix=""):
    """SQL for the team shown for a player_season_stats row: the column, except for the
    rows in season_team_fixes(). `prefix` is the table alias with its dot ("s.")."""
    fixes = season_team_fixes(cur)
    col = f"{prefix}team_abbreviation"
    if not fixes:
        return col
    whens = " ".join(f"WHEN {prefix}player_id = {pid} AND {prefix}season = {season} THEN '{team}'"
                     for (pid, season), (_, team) in fixes.items())
    return f"(CASE {whens} ELSE {col} END)"


def shown_team(cur, player_id, season, team):
    """The team to show for one row read without season_team_sql()."""
    if player_id is None or season is None:
        return team
    fix = season_team_fixes(cur).get((int(player_id), int(season)))
    return fix[1] if fix else team


def select_list(cur, cols, prefix=""):
    """A SELECT list (a list or a comma-separated string of column names) with the team column swapped for
    season_team_sql(). Works on any table keyed by player_id + season that copied the season row's team
    (defender_dad, player_gravity, player_hustle, player_shot_making, shot_value_added, contract_value ...)."""
    names = [c.strip() for c in cols.split(",")] if isinstance(cols, str) else list(cols)
    team = season_team_sql(cur, prefix)
    return ", ".join(f"{team} AS team_abbreviation" if c == "team_abbreviation" else f"{prefix}{c}" for c in names)
