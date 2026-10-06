"""
build_player_game_lines.py
===========================
One row per player per game, rebuilt from ESPN play-by-play (pbp_events,
source = 'espn': every regular-season game 2020-21 to 2025-26). The project
has no box-score game logs, so this is the only per-game player data it has
besides shot charts. Used by build_stat_stability.py (split-half
reliability); written as its own table so later features can reuse it.

What each row holds:
  - the player's own counting stats: points, FGM/FGA, 3PM/3PA, FTM/FTA,
    offensive/defensive rebounds, assists, steals, blocks, turnovers;
  - seconds played, reconstructed from substitutions;
  - what happened while he was on the floor, for rate stats: his team's and
    the opponent's FGM/FGA/FTA/offensive and defensive rebounds/turnovers
    and points, with lineup_stints' credit rules (round 8 step 6a): free
    throws (attempts and points) go to the players on the floor at the
    foul, an event ESPN tagged to no team to nobody on the floor, and
    points come from the made shots and free throws, or, in a game where
    those don't add up to the real final score (game_scores) and the
    running maximum of ESPN's score fields does, from that
    (pbp_lineups.points_method(), the stints' own pick). So each
    player-game's on-court totals equal the sums over his stints
    (build_lineup_stints.py stops otherwise) and tm_pts - op_pts equals
    player_game_onfloor's plus-minus. Until then tm_pts/op_pts credited
    every positive step of the score fields, which double-counted wherever
    they were stale (summed on-court margin = 5 x the final margin in only
    75% of full-minute team-games).

How the play-by-play is read (ESPN text):
  - shots are events whose text says "makes"/"misses", or "X blocks Y's
    ..." (a blocked shot is a missed attempt for the shooter, a block for
    X). Assists and steals are the names in "(X assists)" / "(X steals)".
    Made shots are worth what the shooter's team score went up by. A miss
    is a three when the NBA shot chart (player_shots) calls the same shot
    a three (pbp_lineups.miss_three_calls: ~99% of attempts are matched,
    by order within game, shooter and period); for a miss it can't match,
    when the text says "three point", or, when it gives no shot type, when
    the distance is 23 feet or more. (Until 2026-09-29 the text alone
    decided, and ~8,700 of the chart's missed threes were counted as twos:
    ESPN often writes "misses 26-foot jumper" with no "three point".);
  - turnovers are events typed "... Turnover" or "Traveling" (not "No
    Turnover"); rebounds and turnovers with no player are team ones: they
    count for possessions but aren't anybody's rebound chance;
  - who is on the floor: scripts/pbp_lineups.py (shared with
    build_lineup_stints.py, the one lineup parser): in each period, a
    player starts it if the first thing he does in it is anything other
    than checking in (being checked out counts). Technical fouls and
    ejections don't count, bench players get those. A player who played a
    whole period without appearing in the play-by-play is found from the
    previous period's closing lineup. Then every "A enters the game for B"
    swaps them (a substitution tagged to no team: pbp_lineups).

Names in descriptions are matched to ids within the game first, then
against player_season_stats, then against player_bio (exact name, one
player active that season: pbp_lineups.load_season_names()). The script
prints every name the player_bio fallback answered and every one left
unmatched, by season and team.

Checked against player_season_stats (NBA.com season totals, players with
20+ games) after the build; the script prints the comparison.

Usage:
    cd scripts && python3 build_player_game_lines.py
    cd scripts && python3 build_player_game_lines.py --season 2027
        # round 9 step 3: only that season's rows are deleted and rebuilt, through
        # the same per-game code (the name index is the same one the full build
        # uses, every season's); needs the full build's table.
"""

from collections import Counter

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from pbp_lineups import Game, load_espn, load_season_names, miss_three_calls, points_method
import season_mode as SM

OWN = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov"]
ON = ["tm_fgm", "tm_fga", "tm_fta", "tm_oreb", "tm_dreb", "tm_tov", "tm_pts",
      "op_fgm", "op_fga", "op_fta", "op_oreb", "op_dreb", "op_tov", "op_pts"]


def final_scores(cur):
    """{(espn game id, team): final points} from game_scores (the stints' reference, build_lineup_stints.py)."""
    cur.execute("SELECT 'espn_' || espn_id, team_abbreviation, pts_for FROM game_scores WHERE espn_id IS NOT NULL")
    return {(gid, team): int(pts) for gid, team, pts in cur.fetchall()}


def main():
    season = SM.parse_season()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    season_names, all_names = load_season_names(cur)
    finals = final_scores(cur)
    games, grouped = load_espn(conn, season=season)
    print(f"{len(games)} games, {sum(len(v) for v in grouped.values())} events")
    calls, misses = miss_three_calls(conn, games, grouped, season_names, all_names, season=season)
    flipped = misses[misses.text_three != misses.nba_three]
    print(f"NBA shot chart's call on {len(misses):,} missed shots; differs from the text's on {len(flipped):,} "
          f"({int((flipped.nba_three).sum()):,} threes the text calls twos, {int((~flipped.nba_three).sum()):,} the other way)")

    out, unmatched, from_bio, teamless, methods, fixes, periods = [], Counter(), Counter(), Counter(), Counter(), 0, 0
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None:
            continue
        game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names,
                    miss_threes=calls.get(g.game_id))
        rows, played, totals = game.run(g.home_team)
        final = (finals.get((g.game_id, g.home_team)), finals.get((g.game_id, g.away_team)))
        method, ok = points_method(totals["shots"], totals["score"], final)
        methods[(method, ok)] += 1
        for (team, name), n in game.unmatched_at.items():
            unmatched[(int(g.season), team, name)] += n
        for (team, name, pid), n in game.from_bio.items():
            from_bio[(int(g.season), team, name, pid)] += n
        teamless.update(game.teamless_subs)
        fixes += game.lineup_fixes
        periods += ev["period"].nunique()
        for pid, r in rows.items():
            if pid not in played and r.get("seconds", 0) < 1:
                continue
            team = game.team_of.get(pid)
            r["tm_pts"], r["op_pts"] = r.get(f"tm_pts_{method}", 0), r.get(f"op_pts_{method}", 0)
            out.append([pid, g.game_id, int(g.season), g.game_date, team,
                        round(r.get("seconds", 0.0), 1)] + [int(r.get(k, 0)) for k in OWN] +
                       [int(r.get(k, 0)) for k in ON])
        if i % 1000 == 0:
            print(f"  {i} games")

    bad_team = [r for r in out if not isinstance(r[4], str)]
    if bad_team:
        raise SystemExit(f"{len(bad_team)} rows with no team, e.g. {bad_team[:3]}")
    cols = ["player_id", "game_id", "season", "game_date", "team_abbreviation", "seconds"] + OWN + ON
    if season is None:
        cur.execute("DROP TABLE IF EXISTS player_game_lines;")
        cur.execute(f"""CREATE TABLE player_game_lines (
            player_id BIGINT NOT NULL, game_id TEXT NOT NULL, season INTEGER NOT NULL, game_date DATE,
            team_abbreviation TEXT NOT NULL, seconds DOUBLE PRECISION,
            {', '.join(f'{c} INTEGER' for c in OWN + ON)},
            PRIMARY KEY (player_id, game_id));""")
    else:
        SM.require_tables(cur, ["player_game_lines"], season)
        print(f"--season {season}: {SM.delete_season(cur, 'player_game_lines', season):,} stored rows of the season deleted")
    psycopg2.extras.execute_values(cur, f"INSERT INTO player_game_lines ({', '.join(cols)}) VALUES %s", out, page_size=5000)
    if season is None:
        cur.execute("CREATE INDEX ON player_game_lines (season, player_id);")
    conn.commit()
    print(f"{len(out)} player-game rows; lineup periods needing a fill/trim: {fixes} of ~{periods * 2} team-periods")
    print(f"on-court points per game (points_method): {dict(methods)}")
    print(f"substitutions tagged to no team: {dict(teamless)}")
    print(f"\nNames the player_bio fallback answered: {len({k[:3] for k in from_bio})} name-team-seasons, "
          f"{sum(from_bio.values())} lookups (season, team hint, ESPN name, id: lookups)")
    for k, n in sorted(from_bio.items(), key=lambda x: (x[0][0], x[0][1] or "", x[0][2])):
        print(f"  {k}: {n}")
    print(f"\nNames left unmatched: {len(unmatched)} name-team-seasons, {sum(unmatched.values())} lookups")
    for k, n in sorted(unmatched.items(), key=lambda x: (-x[1], x[0][0], x[0][1] or "", x[0][2])):
        print(f"  {k}: {n}")

    # Check against NBA.com season totals (players with 20+ games; in --season mode that season only).
    season_where = "" if season is None else f" WHERE season = {int(season)}"
    cur.execute(f"""
        WITH l AS (SELECT player_id, season, COUNT(*) FILTER (WHERE seconds > 0) gp, SUM(seconds)/60 mins,
                          SUM(pts) pts, SUM(fga) fga, SUM(fg3a) fg3a, SUM(fta) fta, SUM(oreb) oreb, SUM(dreb) dreb,
                          SUM(ast) ast, SUM(stl) stl, SUM(blk) blk, SUM(tov) tov
                   FROM player_game_lines{season_where} GROUP BY 1, 2)
        SELECT COUNT(*),
               SUM(l.gp)/SUM(s.gp), SUM(l.mins)/SUM(s.min*s.gp), SUM(l.pts)/SUM(s.pts*s.gp),
               SUM(l.fga)/SUM(s.fga*s.gp), SUM(l.fg3a)/SUM(s.fg3a*s.gp), SUM(l.fta)/SUM(s.fta*s.gp),
               SUM(l.oreb)/SUM(s.oreb*s.gp), SUM(l.dreb)/SUM(s.dreb*s.gp), SUM(l.ast)/SUM(s.ast*s.gp),
               SUM(l.stl)/SUM(s.stl*s.gp), SUM(l.blk)/SUM(s.blk*s.gp), SUM(l.tov)/SUM(s.tov*s.gp),
               AVG(ABS(l.mins - s.min*s.gp) / NULLIF(s.min*s.gp, 0))
        FROM l JOIN player_season_stats s USING (player_id, season) WHERE s.gp >= 20;""")
    labels = ["player-seasons", "gp", "min", "pts", "fga", "fg3a", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov",
              "mean |min error|"]
    for k, v in zip(labels, cur.fetchone()):
        # early in a season nobody has 20 games yet: the sums are NULL
        print(f"  {k:>16}: {v}" if k == "player-seasons" else f"  {k:>16}: {'n/a' if v is None else f'{float(v):.4f}'}")
    conn.close()


if __name__ == "__main__":
    main()
