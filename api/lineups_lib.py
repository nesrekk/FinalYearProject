"""
lineups_lib.py
===============
Which stored five-man lineup source covers a season, and how much of a team's
season the play-by-play stints track. Shared by the Pair Chemistry, Lineup
Chemistry and team-page endpoints so they name the same source the same way.

Two sources exist:
  stints        `lineup_stints` and its aggregates `lineup_seasons` /
                `pair_seasons` (scripts/build_lineup_stints.py): every stint
                of every regular-season game 2020-21 on, rebuilt from ESPN
                play-by-play, minus the games whose play-by-play didn't
                reconcile (`lineup_stint_games.tracked_ok`);
  lineup_stats  stats.nba.com's LeagueDashLineups as stored by
                scripts/fetch_spacing_data.py, 2013-14 on: only the 2,000
                most-used lineups a season (31-89% of a team's minutes).

A season is served from the stints when they cover it, else from
lineup_stats. Season lists are cached per process: restart impact_api after
rebuilding either table.
"""

from functools import lru_cache

from impact_core import get_db

STINT_TABLES = ["lineup_stints", "lineup_stint_games", "lineup_stint_seasons", "lineup_seasons", "pair_seasons"]
STINT_UPSTREAM = "ESPN play-by-play (pbp_events), rebuilt into five-man stints by scripts/build_lineup_stints.py"
STORED_UPSTREAM = "nba_api (stats.nba.com LeagueDashLineups, stored by scripts/fetch_spacing_data.py)"

SOURCE_LABEL = {
    "stints": "every stint from play-by-play",
    "lineup_stats": "stats.nba.com's 2,000 most-used lineups",
}

STINTS_METHOD = (
    "Built from every five-on-five stint of the season, rebuilt from ESPN play-by-play (scripts/build_lineup_stints.py): "
    "a stint is a stretch of one period with the same ten players on the floor, and it carries each side's points and "
    "possession components (FGA + 0.44 FTA - OREB + TOV, the same credit rules as the on/off tables). Every game is "
    "reconciled against the real final score (game_scores), the game's length and the team-game totals; games that "
    "fail are excluded here and listed, never silently dropped. Stints with fewer or more than five identified "
    "players a side (almost always a player ESPN gives no id to) are left out and their minutes are stated. "
    "Points come from the made shots and free throws themselves; in games where those don't add up to the real final "
    "(a shot with no type in the text), the running maximum of ESPN's score fields is used instead. A pair's or "
    "lineup's minutes and possessions are summed over its stints; ratings are points per 100 possessions, with "
    "possessions averaged over the two sides (so they sit about 3 points under NBA.com's scale, like On/Off). This "
    "describes what happened; it isn't adjusted for opponents or for the other players on the floor."
)

STORED_METHOD = (
    "Built from the stored 5-man lineups (the 2,000 with the most minutes each regular season, the most the "
    "NBA's lineup endpoint returns). A pair's minutes and possessions are summed over every stored lineup that "
    "contains both players; its offensive and defensive ratings are those lineups' ratings weighted by "
    "possessions (points per 100 possessions, so this equals the pair's total points over total possessions in "
    "those lineups). Net = offense minus defence. Lineups outside the top 2,000, mostly short bench and "
    "garbage-time units, are missing, so starters are covered better than reserves, and the team's figure over "
    "the same lineups is usually better than its full-season net rating. Colours compare each pair to that "
    "same-lineup team figure. This describes what happened; it isn't adjusted for opponents or for the other "
    "three players on the floor."
)

SEASON_KEYS = ["season", "games", "games_ok", "tracked_games", "tracked_share", "points_by_shots", "points_by_score",
               "points_failed", "seconds_failed", "poss_failed", "lineup_failed", "stints", "tracked_stints",
               "minutes", "tracked_minutes", "tracked_minutes_share", "bad_lineup_minutes", "actors_off_floor"]


def _exists(cur, table):
    cur.execute("SELECT to_regclass(%s)", (table,))
    return cur.fetchone()[0] is not None


@lru_cache(maxsize=1)
def stint_seasons():
    """season -> its lineup_stint_seasons row (empty if the table isn't built)."""
    with get_db() as conn:
        cur = conn.cursor()
        if not _exists(cur, "lineup_stint_seasons"):
            return {}
        cur.execute(f"SELECT {', '.join(SEASON_KEYS)} FROM lineup_stint_seasons ORDER BY season")
        return {r[0]: dict(zip(SEASON_KEYS, r)) for r in cur.fetchall()}


@lru_cache(maxsize=1)
def stored_seasons():
    with get_db() as conn:
        cur = conn.cursor()
        if not _exists(cur, "lineup_stats"):
            return []
        cur.execute("SELECT DISTINCT season FROM lineup_stats ORDER BY season")
        return [r[0] for r in cur.fetchall()]


def lineup_sources():
    """season -> 'stints' | 'lineup_stats', for every season either source covers."""
    out = {s: "lineup_stats" for s in stored_seasons()}
    out.update({s: "stints" for s in stint_seasons()})
    return dict(sorted(out.items()))


def season_label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def team_stint_coverage(cur, season, team):
    """How much of a team's season the stints track: its games; the excluded
    games (play-by-play that didn't reconcile with the real final score,
    the game length or the team totals) with the reason; the partial games
    (reconciled, but some minutes had fewer or more than five identified
    players a side, almost always a player ESPN gives no id to); and the
    minutes: the game length from game_scores (the stint total when a game
    isn't in game_scores) against the minutes of tracked stints."""
    cur.execute("""SELECT game_id, nba_game_id, game_date, home_team, away_team, game_ok, tracked_ok, reason,
                          COALESCE(game_length, stint_seconds) AS length, bad_lineup_seconds
                   FROM lineup_stint_games WHERE season = %s AND (home_team = %s OR away_team = %s)
                   ORDER BY game_date, game_id""", (season, team, team))
    rows = cur.fetchall()
    excluded, partial = [], []
    total = tracked = 0.0
    for gid, nba_id, date, home, away, game_ok, ok, reason, length, bad_secs in rows:
        total += float(length or 0)
        entry = {"game_id": gid, "nba_game_id": nba_id, "date": date.isoformat() if date else None,
                 "opponent": away if home == team else home, "home": home == team, "reason": reason}
        if not game_ok:
            excluded.append(entry)
        elif not ok:
            tracked += float(length or 0) - float(bad_secs or 0)
            partial.append({**entry, "minutes_lost": round(float(bad_secs or 0) / 60, 1)})
        else:
            tracked += float(length or 0)
    return {
        "games": len(rows), "tracked_games": len(rows) - len(excluded) - len(partial),
        "excluded": excluded, "partial": partial,
        "partial_minutes": round(sum(p["minutes_lost"] for p in partial), 1),
        "minutes": round(total / 60, 1), "tracked_minutes": round(tracked / 60, 1),
        "share": round(tracked / total, 4) if total else None,
    }
