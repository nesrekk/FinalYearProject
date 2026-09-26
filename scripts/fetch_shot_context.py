"""
fetch_shot_context.py
=======================
Real shot-context splits for Scouting Report v2 (build_scouting_reports.py),
fetched league-wide from nba_api's LeagueDashPlayerPtShot — one real call
per filter value per season (12 per season), not one per player:

  close_def   closest defender: 0-2 ft very tight / 2-4 tight / 4-6 open /
              6+ wide open
  touch       touch time before the shot: < 2 s / 2-6 s / 6+ s
  dribbles    dribbles before the shot: 0 / 1 / 2 / 3-6 / 7+

Each row stores real 2PT and 3PT makes/attempts separately, so a split can
be compared like-for-like (a band that mixed twos and threes would make a
high-volume 3-point shooter look like a poor shooter in it from shot mix
alone). Filter values verified live before writing this: each dimension's
buckets sum to 98-100% of the league's real FGA for 2024-25.

Mid-season-traded players can come back as one row per stint; stints are
summed (all stored fields are counts, so summing is exact).

Tracking starts in 2013-14 (season int 2014).

Usage:
    cd scripts && python3 fetch_shot_context.py
"""

import time

import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

SEASON_START = 2014
SEASON_END = 2026
DIMENSIONS = {
    "close_def": ("close_def_dist_range_nullable", {
        "0-2 ft": "0-2 Feet - Very Tight", "2-4 ft": "2-4 Feet - Tight",
        "4-6 ft": "4-6 Feet - Open", "6+ ft": "6+ Feet - Wide Open"}),
    "touch": ("touch_time_range_nullable", {
        "< 2 s": "Touch < 2 Seconds", "2-6 s": "Touch 2-6 Seconds", "6+ s": "Touch 6+ Seconds"}),
    "dribbles": ("dribble_range_nullable", {
        "0": "0 Dribbles", "1": "1 Dribble", "2": "2 Dribbles", "3-6": "3-6 Dribbles", "7+": "7+ Dribbles"}),
}


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def call(fn, retries=3):
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            print(f"    attempt {attempt + 1} failed — {exc}")
            time.sleep(5 * (attempt + 1))
    return None


def main():
    from nba_api.stats.endpoints import leaguedashplayerptshot

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS player_shot_context (
            season INT NOT NULL,
            player_id BIGINT NOT NULL,
            dimension TEXT NOT NULL,
            bucket TEXT NOT NULL,
            fg2m INT, fg2a INT, fg3m INT, fg3a INT,
            PRIMARY KEY (season, player_id, dimension, bucket)
        );
    """)
    conn.commit()

    for season in range(SEASON_START, SEASON_END + 1):
        rows, ok = [], True
        for dim, (param, buckets) in DIMENSIONS.items():
            for bucket, value in buckets.items():
                df = call(lambda: leaguedashplayerptshot.LeagueDashPlayerPtShot(
                    season=label(season), per_mode_simple="Totals", season_type_all_star="Regular Season",
                    timeout=60, **{param: value}).get_data_frames()[0])
                time.sleep(1.0)
                if df is None:
                    ok = False
                    break
                g = df.groupby("PLAYER_ID")[["FG2M", "FG2A", "FG3M", "FG3A"]].sum()
                rows += [(season, int(pid), dim, bucket, int(r.FG2M), int(r.FG2A), int(r.FG3M), int(r.FG3A))
                         for pid, r in g.iterrows()]
            if not ok:
                break
        if not ok:
            print(f"  {season}: a call failed, skipping season (nothing written).")
            continue
        cur.execute("DELETE FROM player_shot_context WHERE season = %s;", (season,))
        psycopg2.extras.execute_values(cur, "INSERT INTO player_shot_context VALUES %s", rows)
        conn.commit()
        print(f"  {season}: {len(rows)} real player-bucket rows")

    conn.close()
    print("\n✅ Done.")


if __name__ == "__main__":
    main()
