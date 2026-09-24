"""
fetch_referee_officials.py
============================
Real referee/official assignments and real per-team-game box stats
(fouls, FTA, pace inputs), the data foundation for Referee Tendencies.

Two real nba_api sources, deliberately split because their cost is
very different:

  1. LeagueGameFinder — one real bulk call per season, already returns
     real per-team-per-game PF (personal fouls committed by that team,
     i.e. fouls called on them) and FTA, plus FGA/OREB/TOV to derive a
     real per-game possessions estimate (pace proxy). Fast.

  2. BoxScoreSummaryV2 — real officials assigned to a game are only
     available per-game, no bulk endpoint exists. This is the slow
     part: one real call per real game. Resumable — every attempted
     game_id (even ones that came back with zero officials) is logged
     to game_officials_fetch_log, so a re-run skips everything already
     tried instead of re-fetching from scratch.

Known real data gap, verified directly against the live endpoint:
nba_api's own BoxScoreSummaryV2 warns it may be missing officials for
games on/after 2025-04-10. Those games are still logged as attempted
(so we don't retry them forever) but simply contribute zero officials
rows — a real, disclosed gap, not an error.

Usage:
    cd scripts && python3 fetch_referee_officials.py [season_start] [season_end]
    python3 fetch_referee_officials.py 2021 2026
"""

import sys
import time

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

SEASON_START_DEFAULT = 2021
SEASON_END_DEFAULT = 2026


def ensure_tables(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_team_box (
            game_id TEXT NOT NULL,
            season INTEGER NOT NULL,
            game_date DATE,
            team_id BIGINT,
            team_abbreviation TEXT,
            fta INTEGER,
            pf INTEGER,
            fga INTEGER,
            oreb INTEGER,
            tov INTEGER,
            poss_est DOUBLE PRECISION,
            PRIMARY KEY (game_id, team_abbreviation)
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_officials (
            game_id TEXT NOT NULL,
            official_id BIGINT NOT NULL,
            official_name TEXT NOT NULL,
            PRIMARY KEY (game_id, official_id)
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_officials_fetch_log (
            game_id TEXT PRIMARY KEY,
            fetched_at TIMESTAMP DEFAULT NOW(),
            n_officials INTEGER
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_officials_game ON game_officials(game_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_game_team_box_season ON game_team_box(season);")


def fetch_season_box(season_end_year: int):
    """One real bulk call: every real team-game row for this season."""
    from nba_api.stats.endpoints import leaguegamefinder

    season_label = f"{season_end_year - 1}-{str(season_end_year)[-2:]}"
    endpoint = leaguegamefinder.LeagueGameFinder(
        season_nullable=season_label, season_type_nullable="Regular Season",
        league_id_nullable="00", timeout=30,
    )
    return endpoint.get_data_frames()[0]


def possessions_estimate(fga, oreb, tov, fta):
    # Standard real possessions-estimate formula (one team's side of the game).
    return fga - oreb + tov + 0.44 * fta


def fetch_game_officials(game_id: str):
    from nba_api.stats.endpoints import boxscoresummaryv2

    endpoint = boxscoresummaryv2.BoxScoreSummaryV2(game_id=game_id, timeout=30)
    return endpoint.officials.get_data_frame()


def main():
    season_start = int(sys.argv[1]) if len(sys.argv) > 1 else SEASON_START_DEFAULT
    season_end = int(sys.argv[2]) if len(sys.argv) > 2 else SEASON_END_DEFAULT

    conn = psycopg2.connect(**DB_CONFIG)
    cursor = conn.cursor()
    ensure_tables(cursor)
    conn.commit()

    # ─── Step 1: real per-team-game box stats, bulk per season ─────────────
    all_game_ids_by_season = {}
    for season in range(season_start, season_end + 1):
        cursor.execute("SELECT COUNT(*) FROM game_team_box WHERE season = %s;", (season,))
        if cursor.fetchone()[0] > 0:
            cursor.execute("SELECT DISTINCT game_id FROM game_team_box WHERE season = %s;", (season,))
            all_game_ids_by_season[season] = [r[0] for r in cursor.fetchall()]
            print(f"  season {season}: real box stats already loaded, skipping bulk fetch.")
            continue
        try:
            df = fetch_season_box(season)
        except Exception as exc:
            print(f"  season {season}: box-stats bulk fetch FAILED — {exc}")
            continue
        if df.empty:
            print(f"  season {season}: no real box-stats rows returned.")
            all_game_ids_by_season[season] = []
            continue

        rows = []
        for _, r in df.iterrows():
            fga, oreb, tov, fta = int(r["FGA"]), int(r["OREB"]), int(r["TOV"]), int(r["FTA"])
            rows.append((
                r["GAME_ID"], season, r["GAME_DATE"], int(r["TEAM_ID"]), r["TEAM_ABBREVIATION"],
                fta, int(r["PF"]), fga, oreb, tov, possessions_estimate(fga, oreb, tov, fta),
            ))
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO game_team_box
               (game_id, season, game_date, team_id, team_abbreviation, fta, pf, fga, oreb, tov, poss_est)
               VALUES %s ON CONFLICT (game_id, team_abbreviation) DO NOTHING;""",
            rows,
        )
        conn.commit()
        all_game_ids_by_season[season] = sorted(df["GAME_ID"].unique().tolist())
        print(f"  season {season}: {len(df)} real team-game rows loaded ({len(all_game_ids_by_season[season])} real games).")
        time.sleep(0.5)

    # ─── Step 2: real officials, one real call per real game (slow) ────────
    all_game_ids = sorted({g for gs in all_game_ids_by_season.values() for g in gs})
    cursor.execute("SELECT game_id FROM game_officials_fetch_log;")
    already_attempted = {r[0] for r in cursor.fetchall()}
    remaining = [g for g in all_game_ids if g not in already_attempted]
    print(f"\n{len(all_game_ids)} real games in scope, {len(already_attempted)} already attempted, "
          f"{len(remaining)} left to fetch officials for.\n")

    fetched, empty, failed = 0, 0, 0
    for i, game_id in enumerate(remaining):
        try:
            off_df = fetch_game_officials(game_id)
            n = len(off_df)
            if n:
                rows = [(game_id, int(r["OFFICIAL_ID"]), f"{r['FIRST_NAME']} {r['LAST_NAME']}".strip()) for _, r in off_df.iterrows()]
                psycopg2.extras.execute_values(
                    cursor,
                    "INSERT INTO game_officials (game_id, official_id, official_name) VALUES %s ON CONFLICT DO NOTHING;",
                    rows,
                )
                fetched += 1
            else:
                empty += 1
            cursor.execute(
                "INSERT INTO game_officials_fetch_log (game_id, n_officials) VALUES (%s, %s) ON CONFLICT (game_id) DO NOTHING;",
                (game_id, n),
            )
            conn.commit()
            if (i + 1) % 50 == 0:
                print(f"  [{i + 1}/{len(remaining)}] fetched={fetched} empty={empty} failed={failed}")
            time.sleep(0.45)
        except Exception as e:
            failed += 1
            print(f"  ⚠️  {game_id} failed: {e}")
            time.sleep(1.0)

    cursor.execute("SELECT COUNT(DISTINCT game_id) FROM game_officials;")
    n_games_with_officials = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT official_id) FROM game_officials;")
    n_officials = cursor.fetchone()[0]
    print(f"\n✅ Done this run. fetched={fetched} empty={empty} failed={failed}. "
          f"Totals: {n_games_with_officials} real games with officials data, {n_officials} distinct real officials.")
    conn.close()


if __name__ == "__main__":
    main()
