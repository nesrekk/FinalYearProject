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
    and points.

How the play-by-play is read (ESPN text):
  - shots are events whose text says "makes"/"misses", or "X blocks Y's
    ..." (a blocked shot is a missed attempt for the shooter, a block for
    X). Assists and steals are the names in "(X assists)" / "(X steals)".
    Made shots are worth what the shooter's team score went up by; a miss
    is a three when the text says "three point", or, when it gives no
    shot type (common in 2020-21 and 2021-22), when the distance is 23
    feet or more;
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
    swaps them.

Checked against player_season_stats (NBA.com season totals) after the
build; the script prints the comparison. Names in descriptions are
matched to ids within the game first, then against player_season_stats
for that season and team.

Usage:
    cd scripts && python3 build_player_game_lines.py
"""

from collections import Counter

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from pbp_lineups import Game, load_espn, load_season_names

OWN = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov"]
ON = ["tm_fgm", "tm_fga", "tm_fta", "tm_oreb", "tm_dreb", "tm_tov", "tm_pts",
      "op_fgm", "op_fga", "op_fta", "op_oreb", "op_dreb", "op_tov", "op_pts"]


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    season_names, all_names = load_season_names(cur)
    games, grouped = load_espn(conn)
    print(f"{len(games)} games, {sum(len(v) for v in grouped.values())} events")

    out, unmatched, fixes, periods = [], Counter(), 0, 0
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None:
            continue
        game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
        rows, played = game.run(g.home_team)
        unmatched.update(game.unmatched)
        fixes += game.lineup_fixes
        periods += ev["period"].nunique()
        for pid, r in rows.items():
            if pid not in played and r.get("seconds", 0) < 1:
                continue
            team = game.team_of.get(pid)
            out.append([pid, g.game_id, int(g.season), g.game_date, team,
                        round(r.get("seconds", 0.0), 1)] + [int(r.get(k, 0)) for k in OWN] +
                       [int(r.get(k, 0)) for k in ON])
        if i % 1000 == 0:
            print(f"  {i} games")

    cols = ["player_id", "game_id", "season", "game_date", "team_abbreviation", "seconds"] + OWN + ON
    cur.execute("DROP TABLE IF EXISTS player_game_lines;")
    cur.execute(f"""CREATE TABLE player_game_lines (
        player_id BIGINT NOT NULL, game_id TEXT NOT NULL, season INTEGER NOT NULL, game_date DATE,
        team_abbreviation TEXT, seconds DOUBLE PRECISION,
        {', '.join(f'{c} INTEGER' for c in OWN + ON)},
        PRIMARY KEY (player_id, game_id));""")
    psycopg2.extras.execute_values(cur, f"INSERT INTO player_game_lines ({', '.join(cols)}) VALUES %s", out, page_size=5000)
    cur.execute("CREATE INDEX ON player_game_lines (season, player_id);")
    conn.commit()
    print(f"{len(out)} player-game rows; lineup periods needing a fill/trim: {fixes} of ~{periods * 2} team-periods")
    print("most common unmatched names:", unmatched.most_common(10))

    # Check against NBA.com season totals (players with 20+ games).
    cur.execute("""
        WITH l AS (SELECT player_id, season, COUNT(*) FILTER (WHERE seconds > 0) gp, SUM(seconds)/60 mins,
                          SUM(pts) pts, SUM(fga) fga, SUM(fg3a) fg3a, SUM(fta) fta, SUM(oreb) oreb, SUM(dreb) dreb,
                          SUM(ast) ast, SUM(stl) stl, SUM(blk) blk, SUM(tov) tov
                   FROM player_game_lines GROUP BY 1, 2)
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
        print(f"  {k:>16}: {float(v):.4f}" if k != "player-seasons" else f"  {k:>16}: {v}")
    conn.close()


if __name__ == "__main__":
    main()
