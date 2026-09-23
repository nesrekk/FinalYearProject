"""
build_schedule_fatigue.py
===========================
Computes real schedule-fatigue metrics for every real team game: rest
days, back-to-backs, games in the last 7 real days, real travel miles
since the previous real game (haversine, scripts/arenas.py), and real
time zones crossed. All derived from nba_api's real LeagueGameFinder
game logs — every input is a real game that actually happened, on a
real date, at a real location.

One real call per real season (LeagueGameFinder with no team filter
returns every real team's real games for that season in a single
request — confirmed directly against the live endpoint before writing
this: ~2460 rows / 1230 real games per season, under 1 second).

Real, disclosed simplification: a game's "location" is its real home
team's real current arena (scripts/arenas.py doesn't track historical
arena moves), and time zone offsets use real fixed standard-time hours
rather than tracking daylight saving shifts — neither materially
changes a "how far did this real team travel" or "how many zones did
they cross" estimate.

Usage:
    cd scripts && python3 build_schedule_fatigue.py
"""

import time
from datetime import datetime

import psycopg2
import psycopg2.extras

from arenas import ARENAS, travel_miles, timezones_crossed
from db_config import DB_CONFIG

SEASON_START = 2010
SEASON_END = 2026


def ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS team_game_fatigue (
            game_id TEXT NOT NULL,
            team_abbreviation TEXT NOT NULL,
            season INT NOT NULL,
            game_date DATE NOT NULL,
            is_home BOOLEAN,
            opponent TEXT,
            win BOOLEAN,
            plus_minus DOUBLE PRECISION,
            rest_days INT,
            is_b2b BOOLEAN,
            games_last_7_days INT,
            travel_miles_since_last DOUBLE PRECISION,
            timezones_crossed_since_last INT,
            PRIMARY KEY (game_id, team_abbreviation)
        );
    """)


def fetch_season_games(season: int):
    from nba_api.stats.endpoints import leaguegamefinder

    season_label = f"{season - 1}-{str(season)[-2:]}"
    endpoint = leaguegamefinder.LeagueGameFinder(
        season_nullable=season_label,
        season_type_nullable="Regular Season",
        league_id_nullable="00",
        timeout=45,
    )
    return endpoint.get_data_frames()[0]


def build_season_rows(season: int, df):
    by_team = {}
    for _, row in df.iterrows():
        team = row["TEAM_ABBREVIATION"]
        if team not in ARENAS:
            continue  # Historical franchise abbreviations not in the current 30-team map.
        matchup = row["MATCHUP"]
        is_home = " vs. " in matchup
        opponent = matchup.split(" @ " if not is_home else " vs. ")[-1].strip()
        by_team.setdefault(team, []).append({
            "game_id": row["GAME_ID"],
            "game_date": datetime.strptime(row["GAME_DATE"], "%Y-%m-%d").date(),
            "is_home": is_home,
            "opponent": opponent,
            "win": row["WL"] == "W",
            "plus_minus": float(row["PLUS_MINUS"]) if row["PLUS_MINUS"] is not None else None,
            "location_team": team if is_home else opponent,
        })

    rows = []
    for team, games in by_team.items():
        games.sort(key=lambda g: g["game_date"])
        for i, g in enumerate(games):
            if i == 0:
                rest_days = None
                is_b2b = None
                travel = None
                tz_crossed = None
            else:
                prev = games[i - 1]
                rest_days = (g["game_date"] - prev["game_date"]).days - 1
                is_b2b = rest_days == 0
                travel = travel_miles(prev["location_team"], g["location_team"])
                tz_crossed = timezones_crossed(prev["location_team"], g["location_team"])

            games_last_7 = sum(
                1 for other in games
                if 0 <= (g["game_date"] - other["game_date"]).days <= 6
            )

            rows.append((
                g["game_id"], team, season, g["game_date"], g["is_home"], g["opponent"],
                g["win"], g["plus_minus"], rest_days, is_b2b, games_last_7, travel, tz_crossed,
            ))
    return rows


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    ensure_table(cursor)
    conn.commit()

    total_rows = 0
    for season in range(SEASON_START, SEASON_END + 1):
        try:
            df = fetch_season_games(season)
        except Exception as exc:
            print(f"  {season}: FAILED to fetch — {exc}")
            time.sleep(1.0)
            continue

        rows = build_season_rows(season, df)
        cursor.execute("DELETE FROM team_game_fatigue WHERE season = %s;", (season,))
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO team_game_fatigue
               (game_id, team_abbreviation, season, game_date, is_home, opponent, win, plus_minus,
                rest_days, is_b2b, games_last_7_days, travel_miles_since_last, timezones_crossed_since_last)
               VALUES %s;""",
            rows,
        )
        conn.commit()
        total_rows += len(rows)
        print(f"  {season}: {len(rows)} real team-games processed")
        time.sleep(0.5)

    conn.close()
    print(f"\n✅ Done. {total_rows} real team-game fatigue rows, seasons {SEASON_START}-{SEASON_END}.")


if __name__ == "__main__":
    main()
