"""
fetch_hustle_stats.py
========================
Bulk-fetches real hustle stats (deflections, contested shots, screen
assists, loose balls recovered, charges drawn, box outs) from nba_api's
LeagueHustleStatsPlayer endpoint — one real call per season, real
league-wide per-game rates.

Real fact verified directly against the live endpoint before writing
this script: hustle stats start at the 2015-16 season (season int
2016) — 2012-13 through 2014-15 return a real, empty 0-row response.

Usage:
    cd scripts && python3 fetch_hustle_stats.py
"""

import time

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

SEASON_START = 2016
SEASON_END = 2026


def ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_hustle (
            season INT NOT NULL,
            player_id BIGINT NOT NULL,
            player_name TEXT NOT NULL,
            team_abbreviation TEXT,
            gp INT,
            min DOUBLE PRECISION,
            deflections DOUBLE PRECISION,
            contested_shots DOUBLE PRECISION,
            contested_shots_2pt DOUBLE PRECISION,
            contested_shots_3pt DOUBLE PRECISION,
            screen_assists DOUBLE PRECISION,
            screen_ast_pts DOUBLE PRECISION,
            loose_balls_recovered DOUBLE PRECISION,
            charges_drawn DOUBLE PRECISION,
            box_outs DOUBLE PRECISION,
            PRIMARY KEY (season, player_id)
        );
    """)


def fetch_season(season: int):
    from nba_api.stats.endpoints import leaguehustlestatsplayer

    season_label = f"{season - 1}-{str(season)[-2:]}"
    endpoint = leaguehustlestatsplayer.LeagueHustleStatsPlayer(
        season=season_label,
        per_mode_time="PerGame",
        season_type_all_star="Regular Season",
        timeout=45,
    )
    return endpoint.get_data_frames()[0]


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    ensure_table(cursor)
    conn.commit()

    total_rows = 0
    for season in range(SEASON_START, SEASON_END + 1):
        try:
            df = fetch_season(season)
        except Exception as exc:
            print(f"  {season}: FAILED — {exc}")
            time.sleep(1.0)
            continue

        if df.empty:
            print(f"  {season}: no real hustle data returned, skipping.")
            time.sleep(0.5)
            continue

        rows = [
            (
                season, int(r["PLAYER_ID"]), r["PLAYER_NAME"], r["TEAM_ABBREVIATION"],
                int(r["G"]), float(r["MIN"]), float(r["DEFLECTIONS"]),
                float(r["CONTESTED_SHOTS"]), float(r["CONTESTED_SHOTS_2PT"]), float(r["CONTESTED_SHOTS_3PT"]),
                float(r["SCREEN_ASSISTS"]), float(r["SCREEN_AST_PTS"]),
                float(r["LOOSE_BALLS_RECOVERED"]), float(r["CHARGES_DRAWN"]), float(r["BOX_OUTS"]),
            )
            for _, r in df.iterrows()
        ]
        cursor.execute("DELETE FROM player_hustle WHERE season = %s;", (season,))
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO player_hustle
               (season, player_id, player_name, team_abbreviation, gp, min, deflections,
                contested_shots, contested_shots_2pt, contested_shots_3pt, screen_assists,
                screen_ast_pts, loose_balls_recovered, charges_drawn, box_outs)
               VALUES %s;""",
            rows,
        )
        conn.commit()
        total_rows += len(rows)
        print(f"  {season}: {len(rows)} real players")
        time.sleep(0.5)

    conn.close()
    print(f"\n✅ Done. {total_rows} real player-hustle rows, seasons {SEASON_START}-{SEASON_END}.")


if __name__ == "__main__":
    main()
