"""
add_personal_fouls.py
=======================
Adds personal fouls (pf) to player_season_stats — purely additive (ALTER
TABLE ADD COLUMN, never touching existing rows/columns), same pattern as
add_shooting_efficiency_stats.py.

Why: BPM (Box Plus/Minus) needs PF as a direct input (it's part of both
the position-estimation regression and the defense component of the BPM
formula itself), and this table never had it — only *_pct columns and
derived stats were kept from the original load. No new fetch needed: PF
is already sitting in the same nba_data/*_season.csv files this table
was built from (confirmed: the raw CSV header includes a PF column).

Usage:
    cd scripts && python3 add_personal_fouls.py
"""

import os
import warnings

import pandas as pd
import psycopg2
import psycopg2.extras

warnings.filterwarnings("ignore")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "nba_data")

from db_config import DB_CONFIG


def load_season(year):
    tag = f"{year}_{str(year + 1)[-2:]}"
    path = os.path.join(DATA_DIR, f"nba_{tag}_season.csv")
    if not os.path.exists(path):
        return None, None
    df = pd.read_csv(path)[["PLAYER_ID", "PF"]].rename(columns={"PLAYER_ID": "player_id", "PF": "pf"})
    df["season"] = year + 1
    return year + 1, df


def build_full_dataset():
    frames = []
    for year in range(2009, 2026):
        season_code, df = load_season(year)
        if df is None:
            continue
        frames.append(df)
        print(f"  season {season_code} ({year}-{str(year+1)[-2:]}): {len(df)} rows")
    return pd.concat(frames, ignore_index=True)


def main():
    print("Building PF dataset from local CSVs (no network calls)...")
    df = build_full_dataset()
    print(f"\nTotal rows across all seasons: {len(df)}")

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("ALTER TABLE player_season_stats ADD COLUMN IF NOT EXISTS pf DOUBLE PRECISION;")
    conn.commit()
    print("Confirmed pf column exists (added if missing, nothing dropped).")

    rows = [(r.player_id, int(r.season), r.pf) for r in df.itertuples(index=False)]
    psycopg2.extras.execute_values(
        cur,
        """
        UPDATE player_season_stats AS p SET pf = v.pf
        FROM (VALUES %s) AS v (player_id, season, pf)
        WHERE p.player_id = v.player_id AND p.season = v.season;
        """,
        rows,
    )
    conn.commit()
    print(f"Updated matching rows.")

    cur.execute("SELECT COUNT(*), COUNT(pf) FROM player_season_stats;")
    total, has_pf = cur.fetchone()
    print(f"\nCoverage: {has_pf}/{total} rows have pf populated.")

    cur.execute("SELECT player_name, pf FROM player_season_stats WHERE player_name = 'LeBron James' AND season = 2025;")
    print(f"Spot check (LeBron James, 2024-25): {cur.fetchone()}")

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
