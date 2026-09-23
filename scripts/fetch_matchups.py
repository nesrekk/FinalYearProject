"""
fetch_matchups.py
========================
Bulk-fetches real player-vs-player defensive matchup data from nba_api's
LeagueSeasonMatchups endpoint — one real call per season, returning every
real (offensive player, defensive player) pair that has actually guarded
each other that season, with real partial possessions, points allowed,
FG%, assists/turnovers forced, etc. This is the real data source for the
"Kryptonite" matchup finder: a scorer's real toughest/easiest matchups,
and reversible to "who does this defender actually shut down."

Real facts verified directly against the live endpoint before writing
this script:
- Matchup tracking data is real but sparse/partial for 2016-17 (only
  3,515 real rows vs. ~130k+ in every full season after) — real full
  league-wide coverage starts at the 2017-18 season. 2012-13 through
  2015-16 return a real, empty 0-row response. 2016-17 is skipped here
  rather than stored as a misleadingly tiny "season."
- No duplicate (season, off_player_id, def_player_id) pairs even for
  players traded mid-season — the endpoint already aggregates a pair's
  real matchup minutes across both players' team stints that season.
- Real PARTIAL_POSS (partial possessions matched up) ranges from 0 to
  ~230 within a season; the median pair has only ~4.6 real possessions
  together. Rows below MIN_PARTIAL_POSS are dropped at fetch time as
  real noise (a career's worth of one-possession incidental matchups
  would otherwise dwarf the meaningful rivalries) — the app applies a
  stricter, disclosed threshold on top of this for "reliable" matchups.

Usage:
    cd scripts && python3 fetch_matchups.py
"""

import time

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

SEASON_START = 2018
SEASON_END = 2026
MIN_PARTIAL_POSS = 5.0


def ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_matchups (
            season INT NOT NULL,
            off_player_id BIGINT NOT NULL,
            off_player_name TEXT NOT NULL,
            def_player_id BIGINT NOT NULL,
            def_player_name TEXT NOT NULL,
            gp INT,
            matchup_min DOUBLE PRECISION,
            partial_poss DOUBLE PRECISION,
            player_pts INT,
            team_pts INT,
            matchup_ast INT,
            matchup_tov INT,
            matchup_blk INT,
            matchup_fgm INT,
            matchup_fga INT,
            matchup_fg_pct DOUBLE PRECISION,
            matchup_fg3m INT,
            matchup_fg3a INT,
            matchup_fg3_pct DOUBLE PRECISION,
            matchup_ftm INT,
            matchup_fta INT,
            sfl INT,
            PRIMARY KEY (season, off_player_id, def_player_id)
        );
        CREATE INDEX IF NOT EXISTS idx_player_matchups_off ON player_matchups (season, off_player_id);
        CREATE INDEX IF NOT EXISTS idx_player_matchups_def ON player_matchups (season, def_player_id);
    """)


def _to_min(x):
    # MATCHUP_MIN comes back as "MM:SS" string, real minutes-and-seconds of real overlap.
    if x is None:
        return None
    try:
        m, s = str(x).split(":")
        return int(m) + int(s) / 60.0
    except (ValueError, AttributeError):
        return None


def fetch_season(season: int):
    from nba_api.stats.endpoints import leagueseasonmatchups

    season_label = f"{season - 1}-{str(season)[-2:]}"
    endpoint = leagueseasonmatchups.LeagueSeasonMatchups(
        season=season_label,
        season_type_playoffs="Regular Season",
        timeout=60,
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
            print(f"  {season}: no real matchup data returned, skipping.")
            time.sleep(0.5)
            continue

        df = df[df["PARTIAL_POSS"] >= MIN_PARTIAL_POSS]

        rows = [
            (
                season, int(r["OFF_PLAYER_ID"]), r["OFF_PLAYER_NAME"],
                int(r["DEF_PLAYER_ID"]), r["DEF_PLAYER_NAME"],
                int(r["GP"]), _to_min(r["MATCHUP_MIN"]), float(r["PARTIAL_POSS"]),
                int(r["PLAYER_PTS"]), int(r["TEAM_PTS"]),
                int(r["MATCHUP_AST"]), int(r["MATCHUP_TOV"]), int(r["MATCHUP_BLK"]),
                int(r["MATCHUP_FGM"]), int(r["MATCHUP_FGA"]), float(r["MATCHUP_FG_PCT"]) if r["MATCHUP_FG_PCT"] is not None else None,
                int(r["MATCHUP_FG3M"]), int(r["MATCHUP_FG3A"]), float(r["MATCHUP_FG3_PCT"]) if r["MATCHUP_FG3_PCT"] is not None else None,
                int(r["MATCHUP_FTM"]), int(r["MATCHUP_FTA"]), int(r["SFL"]),
            )
            for _, r in df.iterrows()
        ]
        cursor.execute("DELETE FROM player_matchups WHERE season = %s;", (season,))
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO player_matchups
               (season, off_player_id, off_player_name, def_player_id, def_player_name,
                gp, matchup_min, partial_poss, player_pts, team_pts, matchup_ast, matchup_tov,
                matchup_blk, matchup_fgm, matchup_fga, matchup_fg_pct, matchup_fg3m, matchup_fg3a,
                matchup_fg3_pct, matchup_ftm, matchup_fta, sfl)
               VALUES %s;""",
            rows,
        )
        conn.commit()
        total_rows += len(rows)
        print(f"  {season}: {len(rows)} real matchup pairs (>= {MIN_PARTIAL_POSS} partial poss)")
        time.sleep(0.5)

    conn.close()
    print(f"\n✅ Done. {total_rows} real player-matchup rows, seasons {SEASON_START}-{SEASON_END}.")


if __name__ == "__main__":
    main()
