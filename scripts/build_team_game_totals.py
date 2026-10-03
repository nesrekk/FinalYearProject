"""
build_team_game_totals.py
=========================
One row per team-game, every regular-season game 2020-21 to 2025-26, from
the ESPN play-by-play (`pbp_events`, source 'espn') with the same event
rules as build_player_game_lines.py: field-goal and free-throw attempts and
turnovers (including team turnovers) counted per team from the events,
offensive rebounds = player rebounds only (team rebounds aren't credited to
anyone in the lines), for both sides; possessions = FGA + 0.44 FTA - OREB +
TOV averaged over the two sides; game seconds (overtime included); the
share of player-seconds the rebuilt lineups tracked; points for/against =
the play-by-play's last score, which differs from the real final
(`game_scores`) in 86 of 14,458 team-games (2026-10-03; the data-quality
audit's last_score class compares the two, so keep it the pbp's own).
Two free throws in six seasons carry no team in the ESPN feed and are left out.

Read by build_lineup_stints.py and build_possessions.py (their stint and
possession sums must equal these components) and build_player_on_off.py
(possessions off the floor). Split out of build_player_on_off.py on
2026-10-03, when on/off began reading player_game_onfloor, which is built
from the stints: in one script the rebuild order was a cycle. The table is
byte-identical to what that script wrote.

Table written (dropped and rebuilt): team_game_totals.

Usage:
    cd scripts && python3 build_team_game_totals.py      (~10 s; after build_player_game_lines.py)
"""

import time

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

FT_POSS = 0.44

# Same event rules as build_player_game_lines.py (order matters there:
# substitution, then free throw, then field goal, then rebound, then turnover).
FG_SQL = ("action_type NOT LIKE 'Free Throw%%' AND action_type <> 'Substitution' "
          "AND (description LIKE '%% blocks %%' OR description ~ ' (makes|misses) ')")
FT_SQL = "action_type LIKE 'Free Throw%%'"
TOV_SQL = (f"NOT ({FG_SQL}) AND NOT ({FT_SQL}) AND action_type NOT LIKE '%%Rebound%%' "
           "AND action_type <> 'No Turnover' AND (action_type LIKE '%%Turnover%%' OR action_type = 'Traveling')")

TEAM_TOTALS_SQL = f"""
WITH ev AS (
    SELECT e.game_id, g.season, g.game_date, g.home_team, g.away_team, e.team_tricode AS team,
           e.action_type, COALESCE(e.description, '') AS description, e.period, e.score_home, e.score_away
    FROM pbp_events e JOIN pbp_games g USING (game_id)
    WHERE g.source = 'espn'
),
per_team AS (
    SELECT game_id, season, game_date, home_team, away_team, team,
           COUNT(*) FILTER (WHERE {FG_SQL}) AS fga,
           COUNT(*) FILTER (WHERE {FT_SQL}) AS fta,
           COUNT(*) FILTER (WHERE {TOV_SQL}) AS tov
    FROM ev WHERE team IS NOT NULL
    GROUP BY 1, 2, 3, 4, 5, 6
),
per_game AS (
    SELECT game_id, MAX(score_home) AS score_home, MAX(score_away) AS score_away,
           2880 + 300 * GREATEST(MAX(period) - 4, 0) AS game_seconds
    FROM ev GROUP BY 1
)
SELECT t.game_id, t.season, t.game_date, t.team,
       CASE WHEN t.team = t.home_team THEN t.away_team ELSE t.home_team END AS opponent,
       t.team = t.home_team AS is_home,
       CASE WHEN t.team = t.home_team THEN g.score_home ELSE g.score_away END AS pts_for,
       CASE WHEN t.team = t.home_team THEN g.score_away ELSE g.score_home END AS pts_against,
       t.fga, t.fta, t.tov, g.game_seconds
FROM per_team t JOIN per_game g USING (game_id)
WHERE t.team IN (t.home_team, t.away_team)
"""


def poss(fga, fta, oreb, tov):
    return fga + FT_POSS * fta - oreb + tov


def team_totals(conn):
    """Team-game totals from the play-by-play, joined with the lines' own
    offensive-rebound and tracked-seconds sums."""
    t = pd.read_sql_query(TEAM_TOTALS_SQL, conn)
    lines_by_team = pd.read_sql_query(
        """SELECT game_id, team_abbreviation AS team, SUM(oreb) AS oreb, SUM(seconds) AS tracked_seconds
           FROM player_game_lines GROUP BY 1, 2""", conn)
    t = t.merge(lines_by_team, on=["game_id", "team"], how="left")
    t["oreb"] = t["oreb"].fillna(0).astype(int)
    t["tracked_seconds"] = t["tracked_seconds"].fillna(0.0)
    # Opponent side of the same game.
    opp = t[["game_id", "team", "fga", "fta", "oreb", "tov"]].rename(
        columns={"team": "opponent", "fga": "opp_fga", "fta": "opp_fta", "oreb": "opp_oreb", "tov": "opp_tov"})
    t = t.merge(opp, on=["game_id", "opponent"], how="inner")
    t["poss"] = (poss(t.fga, t.fta, t.oreb, t.tov) + poss(t.opp_fga, t.opp_fta, t.opp_oreb, t.opp_tov)) / 2
    t["tracked_share"] = t["tracked_seconds"] / (5 * t["game_seconds"])
    t["win"] = t["pts_for"] > t["pts_against"]
    return t




def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    teams = team_totals(conn)
    cur.execute("DROP TABLE IF EXISTS team_game_totals;")
    cur.execute("""CREATE TABLE team_game_totals (
        game_id TEXT NOT NULL, season INTEGER NOT NULL, game_date DATE, team_abbreviation TEXT NOT NULL,
        opponent TEXT, is_home BOOLEAN, win BOOLEAN, pts_for INTEGER, pts_against INTEGER,
        fga INTEGER, fta INTEGER, oreb INTEGER, tov INTEGER,
        opp_fga INTEGER, opp_fta INTEGER, opp_oreb INTEGER, opp_tov INTEGER,
        poss DOUBLE PRECISION, game_seconds INTEGER, tracked_seconds DOUBLE PRECISION, tracked_share DOUBLE PRECISION,
        PRIMARY KEY (game_id, team_abbreviation));""")
    tcols = ["game_id", "season", "game_date", "team", "opponent", "is_home", "win", "pts_for", "pts_against",
             "fga", "fta", "oreb", "tov", "opp_fga", "opp_fta", "opp_oreb", "opp_tov", "poss", "game_seconds",
             "tracked_seconds", "tracked_share"]
    trecs = [tuple(None if (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v)
                   for v in r) for r in teams[tcols].itertuples(index=False)]
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO team_game_totals ({', '.join(c if c != 'team' else 'team_abbreviation' for c in tcols)}) VALUES %s",
        trecs, page_size=2000)
    cur.execute("CREATE INDEX ON team_game_totals (season, team_abbreviation);")
    conn.commit()
    print(f"wrote {len(teams)} team-games, {teams.game_id.nunique()} games ({time.time() - t0:.0f}s)")
    conn.close()


if __name__ == "__main__":
    main()
