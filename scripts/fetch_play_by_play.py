"""
fetch_play_by_play.py
======================
Bulk-fetches real NBA play-by-play (nba_api's PlayByPlayV3) for a real
sample of games from a season, storing every real event — period, real game
clock, real running score, real player attribution — in Postgres. This is
the real data foundation for the Clutch-Time WPA Tracker: a win-probability
model needs many real (time, score, final outcome) triples to fit against,
and this table is where those come from.

Real games list comes from nba_api's LeagueGameFinder (one fast bulk call
covers a whole season's real schedule + real W/L outcomes, no per-game
lookup needed for that part). Play-by-play itself is inherently per-game;
this fetches a real, disclosed SAMPLE of games (not the full season, to
keep this a tractable single run) spread evenly across the season by date,
not cherry-picked.

Usage:
    cd scripts && python3 fetch_play_by_play.py [season_end_year] [n_games]
    python3 fetch_play_by_play.py 2025 300
"""

import sys
import time

import psycopg2
import psycopg2.extras

DB_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "user": "postgres",
    "password": "meinkampf:)",
    "dbname": "nba_analytics",
}

PERIOD_SECONDS = 12 * 60
OT_SECONDS = 5 * 60


def clock_to_seconds(clock_str: str) -> float:
    """'PT11M32.10S' -> seconds remaining in that period, as a float."""
    try:
        body = clock_str[2:]  # strip 'PT'
        minutes_part, rest = body.split("M")
        seconds_part = rest.rstrip("S")
        return float(minutes_part) * 60 + float(seconds_part)
    except Exception:
        return 0.0


def seconds_remaining_in_game(period: int, clock_seconds: float) -> float:
    """Total real seconds left in the game from this point, across periods."""
    if period <= 4:
        periods_left_after_this = 4 - period
        return clock_seconds + periods_left_after_this * PERIOD_SECONDS
    # overtime
    return clock_seconds


def fetch_season_games(season_end_year: int):
    from nba_api.stats.endpoints import leaguegamefinder

    season_label = f"{season_end_year - 1}-{str(season_end_year)[-2:]}"
    endpoint = leaguegamefinder.LeagueGameFinder(
        season_nullable=season_label, season_type_nullable="Regular Season",
        league_id_nullable="00", timeout=30,
    )
    data = endpoint.get_dict()
    rs = data["resultSets"][0]
    headers = rs["headers"]
    idx = {h: i for i, h in enumerate(headers)}
    rows = rs["rowSet"]

    games = {}
    for row in rows:
        game_id = row[idx["GAME_ID"]]
        team_abbr = row[idx["TEAM_ABBREVIATION"]]
        matchup = row[idx["MATCHUP"]]
        wl = row[idx["WL"]]
        game_date = row[idx["GAME_DATE"]]
        is_home = "vs." in matchup
        g = games.setdefault(game_id, {"game_id": game_id, "date": game_date})
        if is_home:
            g["home_team"] = team_abbr
            g["home_win"] = (wl == "W")
        else:
            g["away_team"] = team_abbr
    return sorted(games.values(), key=lambda g: g["date"])


def fetch_game_pbp(game_id: str):
    from nba_api.stats.endpoints import playbyplayv3

    endpoint = playbyplayv3.PlayByPlayV3(game_id=game_id, timeout=30)
    data = endpoint.get_dict()
    return data.get("game", {}).get("actions", [])


def main():
    season_end_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2025
    n_games = int(sys.argv[2]) if len(sys.argv) > 2 else 300

    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pbp_games (
            game_id TEXT PRIMARY KEY,
            season INTEGER NOT NULL,
            game_date DATE,
            home_team TEXT,
            away_team TEXT,
            home_win BOOLEAN
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pbp_events (
            id SERIAL PRIMARY KEY,
            game_id TEXT NOT NULL REFERENCES pbp_games(game_id) ON DELETE CASCADE,
            action_number INTEGER,
            period INTEGER,
            seconds_remaining DOUBLE PRECISION,
            score_home INTEGER,
            score_away INTEGER,
            team_id BIGINT,
            team_tricode TEXT,
            person_id BIGINT,
            player_name TEXT,
            action_type TEXT,
            sub_type TEXT,
            description TEXT
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pbp_events_game ON pbp_events(game_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pbp_events_player ON pbp_events(person_id);")
    conn.commit()

    print(f"Fetching real schedule for season {season_end_year}...")
    all_games = fetch_season_games(season_end_year)
    print(f"  Found {len(all_games)} real games this season.")

    # Evenly-spaced real sample across the season, not cherry-picked.
    if len(all_games) > n_games:
        step = len(all_games) / n_games
        sample = [all_games[int(i * step)] for i in range(n_games)]
    else:
        sample = all_games
    print(f"  Sampling {len(sample)} real games spread across the season.\n")

    cursor.execute("SELECT game_id FROM pbp_games;")
    already_have = {r[0] for r in cursor.fetchall()}

    fetched, skipped, failed = 0, 0, 0
    for i, g in enumerate(sample):
        game_id = g["game_id"]
        if game_id in already_have:
            skipped += 1
            continue
        try:
            actions = fetch_game_pbp(game_id)
            if not actions:
                failed += 1
                continue

            # The raw feed only populates scoreHome/scoreAway on the action
            # that actually changed the score — every other action (fouls,
            # rebounds, violations, jump balls...) has them as empty
            # strings, not "the score is 0". Forward-fill from the last
            # real score seen, or this would insert a fake 0-0 into the
            # middle of nearly every real game.
            last_home, last_away = 0, 0
            rows = []
            for a in actions:
                period = a.get("period", 1)
                clock_sec = clock_to_seconds(a.get("clock", "PT00M00.00S"))
                raw_home, raw_away = a.get("scoreHome"), a.get("scoreAway")
                if raw_home not in (None, ""):
                    last_home = int(raw_home)
                if raw_away not in (None, ""):
                    last_away = int(raw_away)
                rows.append((
                    game_id, a.get("actionNumber"), period, seconds_remaining_in_game(period, clock_sec),
                    last_home, last_away,
                    a.get("teamId") or None, a.get("teamTricode") or None,
                    a.get("personId") or None, a.get("playerName") or None,
                    a.get("actionType"), a.get("subType"), a.get("description"),
                ))

            home_win = g.get("home_win")
            if home_win is None:
                home_win = last_home > last_away

            cursor.execute(
                """INSERT INTO pbp_games (game_id, season, game_date, home_team, away_team, home_win)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (game_id) DO NOTHING;""",
                (game_id, season_end_year, g["date"], g.get("home_team"), g.get("away_team"), home_win),
            )
            psycopg2.extras.execute_values(
                cursor,
                """INSERT INTO pbp_events (game_id, action_number, period, seconds_remaining, score_home,
                       score_away, team_id, team_tricode, person_id, player_name, action_type, sub_type, description)
                   VALUES %s;""",
                rows,
            )
            conn.commit()
            fetched += 1
            if (i + 1) % 25 == 0:
                print(f"  [{i + 1}/{len(sample)}] fetched={fetched} skipped={skipped} failed={failed}")
            time.sleep(0.4)
        except Exception as e:
            failed += 1
            print(f"  ⚠️  {game_id} failed: {e}")
            time.sleep(1.0)

    cursor.execute("SELECT COUNT(*) FROM pbp_games;")
    n_games_total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM pbp_events;")
    n_events_total = cursor.fetchone()[0]
    print(f"\n✅ Done. {n_games_total} real games / {n_events_total} real events in the DB "
          f"(this run: fetched={fetched}, skipped={skipped}, failed={failed}).")
    conn.close()


if __name__ == "__main__":
    main()
