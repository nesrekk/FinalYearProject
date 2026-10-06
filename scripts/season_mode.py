"""
season_mode.py
==============
The `--season N` mode of the season-level builds (round 9 step 3, 2026-10-06): what every build does the same way
when it rebuilds one season's rows instead of every season's. A build in this mode

  * reads its inputs for that season only where the output is per game, or everything where one computation
    covers every season (one seeded random stream, pooled constants; each build's docstring says which);
  * deletes only that season's rows of each table it writes (`DELETE ... WHERE season = N`, or the season's games
    or events for tables keyed by game or event) and inserts the season's new rows through the same code as the
    full build: no DDL, no new index, no second implementation;
  * leaves alone every table without a season dimension (the `*_meta` check tables, `best_games_meta`,
    `leverage_index_grid`: round 9 issue R9-008) and every other season's rows;
  * needs the tables the full build creates: it never creates them (require_tables()).

api/tests/test_season_rebuild.py runs each build with `--season 2026` into copies of its tables and proves the
rows equal the stored full build's 2025-26 rows; scripts/daily_update.py runs them for the live season.
"""

import sys


def season_label(season):
    """2027 -> '2026-27' (player_shots' season column)."""
    return f"{int(season) - 1}-{str(int(season))[-2:]}"


def parse_season(argv=None):
    """The `--season N` argument (an end year) of a build's command line, or None (the full build)."""
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--season" not in argv:
        return None
    i = argv.index("--season")
    try:
        return int(argv[i + 1])
    except (IndexError, ValueError):
        raise SystemExit("--season needs an end year, e.g. --season 2027") from None


def require_tables(cur, tables, season):
    """A --season run needs the tables the full build creates: stop, naming the one missing, instead of creating it."""
    for t in tables:
        cur.execute("SELECT to_regclass(%s)", (t,))
        if cur.fetchone()[0] is None:
            raise SystemExit(f"--season {season}: {t} does not exist yet; run the full build first")


def delete_season(cur, table, season, where="season = %s"):
    """Delete one season's rows of `table` (`where` names the season column or the rows of its games); returns the count."""
    cur.execute(f"DELETE FROM {table} WHERE {where}", (season,) * where.count("%s"))
    return cur.rowcount


def next_id(cur, table, col, season, n_new):
    """The first value of a sequential id (`lineup_stints.stint_id`, `play_finder_games.game_no`: the full build numbers
    games in date order, so one season's ids follow the previous season's) for a season's `n_new` rows: one past the
    greatest id of an earlier season. Stops when a later season's ids are in the way (then only a full build can
    renumber)."""
    cur.execute(f"SELECT coalesce(max({col}), 0) FROM {table} WHERE season < %s", (season,))
    start = int(cur.fetchone()[0]) + 1
    cur.execute(f"SELECT min({col}) FROM {table} WHERE season > %s", (season,))
    later = cur.fetchone()[0]
    if later is not None and start + n_new > int(later):
        raise SystemExit(f"--season {season}: {n_new} rows would need {col} {start}..{start + n_new - 1} but a later "
                         f"season starts at {later}; run the full build")
    return start
