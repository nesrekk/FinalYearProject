"""
paper_freeze.py
================
The paper is frozen on the 2025-26 season: MAX_PAPER_SEASON = 2026 (end year), the paper's test season.
From round 9 (the live 2026-27 season, planned 2026-10-06) the database gains 2026-27 rows every day
while the paper's inputs must not move, so every paper-stage script reads the tables the season adds to
through this module, and scripts/paper_manifest.py hashes only the paper's rows of each table.

    from paper_freeze import MAX_PAPER_SEASON, F
    pd.read_sql(f"SELECT ... FROM {F('lineup_stints', 's')} JOIN {F('pbp_games', 'g')} ON ...", conn)

F(table, alias=None) is the table restricted to the paper's rows, as a FROM-item:
    (SELECT * FROM lineup_stints WHERE season <= 2026) AS s
Postgres pulls the subquery up into the outer query, so the plan is the plain table's plus one filter.
The alias is the query's own, or the table's name when it has none (so `lineup_stints.season` still
resolves). paper_rows(table) is the bare predicate for a WHERE clause of your own.

Which rows are the paper's, per table (paper_predicate):
  * a `season` column of an integer type: season <= 2026 (2026 = 2025-26; 0 or NULL = a pooled row, kept);
  * a `season` column of text ('2025-26'): season <= '2025-26' (lexicographic order is season order);
  * tables keyed by game or event, no season column: pbp_events through pbp_games; pbp_event_clock and
    play_finder_events through the event's game; game_officials / game_officials_fetch_log by the NBA
    game id (digits 4-5 are the season's start year); pregame_availability_players through its odds table;
  * player_projections: season is the season projected, so last_season (the data's season) <= 2026;
  * the Forecast Ledger's locked tables (kind ledger) are all 2026-27 and never capped: the lock is
    frozen on its own terms; its live log (paper_manifest.LIVE) is left out of the digest instead;
  * a table with none of these has no season dimension and is read whole. Such a table must not change
    during the season if the paper reads it (docs/LIVE_SEASON.md lists them: pooled fits are fitted on
    seasons <= 2026 and only applied to 2026-27).

The rule for new code: a paper-stage script (scripts/rebuild_all.sh's paper and paper-inputs stages,
plus api/data_quality_lib.py) names a table the season adds to only through F(); a shared loader it calls
(build_rapm.load_rows, compute_wpa.load_events, build_hot_streak_persistence.load, pbp_lineups.load_espn)
gets the bound as an argument. api/tests/test_paper_frozen.py checks both ways: statically (no SQL
literal in those files names such a table after FROM/JOIN) and dynamically (a schema of views that add
one fake 2026-27 row to every such table changes nothing a paper-stage loader returns).

Pure Python, no database connection needed; import-safe from api/ and scripts/.
"""

MAX_PAPER_SEASON = 2026            # end year: 2025-26, the paper's test season (paper_eval.TEST)
MAX_PAPER_SEASON_LABEL = "2025-26"

# Tables whose `season` column is text in the 'YYYY-YY' form (every other `season` column is an integer end year).
TEXT_SEASON_TABLES = frozenset({"player_shots", "league_zone_mix", "team_zone_mix", "league_shot_zones",
                                "zone_classifier_check"})

# Tables with no `season` column whose rows still belong to a season (explicit predicates); a table's own
# alias isn't needed inside: F() wraps the predicate in `SELECT * FROM <table> WHERE ...`.
_EVENT_OF_PAPER_GAME = ("event_id IN (SELECT e.id FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id "
                        "WHERE g.season <= {cap})")
_NBA_GAME_ID = ("(CASE WHEN substr(game_id, 4, 2)::int < 50 THEN 2001 ELSE 1901 END) + substr(game_id, 4, 2)::int "
                "<= {cap}")
EXPLICIT_PREDICATES = {
    "pbp_events": "game_id IN (SELECT game_id FROM pbp_games WHERE season <= {cap})",
    "pbp_event_clock": _EVENT_OF_PAPER_GAME,
    "play_finder_events": _EVENT_OF_PAPER_GAME,
    "game_officials": _NBA_GAME_ID,
    "game_officials_fetch_log": _NBA_GAME_ID,
    "pregame_availability_players": "game_id IN (SELECT game_id FROM pregame_availability_odds WHERE season <= {cap})",
    "player_projections": "(last_season IS NULL OR last_season <= {cap})",
    # the shot cache's bookkeeping (no season dimension, read by no paper script): the rows that existed at the freeze.
    # The daily update adds one for each new player whose shots it stores (rookies); without this the manifest's row
    # count, and with it paper_numbers.py's manifest claim, failed after the first live run (round 9 step 5, R9-037)
    "player_shots_cache_status": "updated_at < TIMESTAMP '2026-10-07'",
}

# The Forecast Ledger (scripts/ledger_lock.py, ledger_update.py): 2026-27 by design, never capped.
LEDGER_TABLES = frozenset({"ledger_meta", "ledger_schedule", "ledger_rosters", "ledger_hindcast", "ledger_forecasts",
                           "ledger_lock", "ledger_results", "ledger_game_log", "ledger_team_log", "ledger_runs",
                           "ledger_tests"})


def season_label(end_year):
    """2026 -> '2025-26'."""
    return f"{end_year - 1}-{str(end_year)[-2:]}"


def paper_predicate(table, columns=None, cap=MAX_PAPER_SEASON):
    """The SQL predicate that keeps the paper's rows of `table`, or None when the table has no season dimension.
    `columns` = the table's column names (any iterable); without them a `season` column is assumed, which is
    what F() does in the scripts (a wrong assumption fails loudly at the first query)."""
    if table in LEDGER_TABLES:
        return None
    if table in EXPLICIT_PREDICATES:
        return EXPLICIT_PREDICATES[table].format(cap=cap)
    if columns is not None and "season" not in set(columns):
        return None
    # A NULL season is a pooled row (e.g. pregame_availability_fit's all-season constants): the paper's.
    if table in TEXT_SEASON_TABLES:
        return f"(season IS NULL OR season <= '{season_label(cap)}')"
    return f"(season IS NULL OR season <= {cap})"


def paper_rows(table, cap=MAX_PAPER_SEASON):
    """The predicate for a WHERE clause (never None: a table without a season dimension raises)."""
    p = paper_predicate(table, cap=cap)
    if p is None:
        raise ValueError(f"{table} has no paper-rows predicate (a ledger table?)")
    return p


def F(table, alias=None, cap=MAX_PAPER_SEASON):
    """`table` restricted to the paper's rows, as a FROM-item with the alias the query uses (default: the
    table's own name, so unqualified `table.column` references keep working)."""
    return f'(SELECT * FROM "{table}" WHERE {paper_rows(table, cap)}) AS {alias or table}'


def paper_game_ids(conn, cap=MAX_PAPER_SEASON, source="espn"):
    """The ESPN play-by-play game ids of the paper's seasons, for pbp_lineups.load_espn(conn, game_ids=...)."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT game_id FROM pbp_games WHERE source = %s AND season <= {int(cap)} ORDER BY game_date, game_id",
                    (source,))
        return [r[0] for r in cur.fetchall()]
