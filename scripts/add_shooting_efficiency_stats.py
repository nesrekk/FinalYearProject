"""
add_shooting_efficiency_stats.py
=================================
Adds raw shooting/rebounding counting stats to player_season_stats —
purely additive (ALTER TABLE ADD COLUMN, never DROP/recreate) so nothing
already built on this table (MVP/DPOY/ROY models, clustering, trade
analyzer, win model, radar, trends...) is touched.

Why: several CraftedNBA-style metrics (Shooting Proficiency, Spacing, Four
Factors) have fully disclosed formulas but need raw attempt counts (FGA,
FG3A, FTA, OREB) and a couple of advanced percentages (EFG%, OREB%, TOV%)
that were never stored — only *_pct columns and combined REB were kept from
the original load. No new network fetch needed: this data was already
sitting on disk the whole time, in the same nba_data/*.csv files the table
was originally built from (confirmed by diffing player_seasons_master.csv's
columns against the DB schema — it's the exact source file, missing only
these columns).

New columns added (all per-game except poss, which is a season total —
needed for per-100-possession rate stats):
    fgm, fga, fg3m, fg3a, ftm, fta, oreb, dreb   (raw counts, per game)
    efg_pct, oreb_pct, tov_pct                    (0-1 fractions)
    poss                                           (season total)

Usage:
    cd scripts && python3 add_shooting_efficiency_stats.py
"""

import os
import warnings

import pandas as pd
import psycopg2
import psycopg2.extras

warnings.filterwarnings("ignore")

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "nba_data")

DB_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "user": "postgres",
    "password": "meinkampf:)",
    "dbname": "nba_analytics",
}

NEW_COLUMNS = [
    ("fgm", "DOUBLE PRECISION"), ("fga", "DOUBLE PRECISION"),
    ("fg3m", "DOUBLE PRECISION"), ("fg3a", "DOUBLE PRECISION"),
    ("ftm", "DOUBLE PRECISION"), ("fta", "DOUBLE PRECISION"),
    ("oreb", "DOUBLE PRECISION"), ("dreb", "DOUBLE PRECISION"),
    ("efg_pct", "DOUBLE PRECISION"), ("oreb_pct", "DOUBLE PRECISION"),
    ("tov_pct", "DOUBLE PRECISION"), ("poss", "DOUBLE PRECISION"),
]


def load_season_pair(year):
    """year=2009 -> reads nba_2009_10_season.csv + nba_2009_10_advanced.csv,
    returns (season_code, merged_df) where season_code=2010 (matches the
    project-wide convention: season 2010 == "2009-10")."""
    tag = f"{year}_{str(year + 1)[-2:]}"
    season_path = os.path.join(DATA_DIR, f"nba_{tag}_season.csv")
    adv_path = os.path.join(DATA_DIR, f"nba_{tag}_advanced.csv")
    if not (os.path.exists(season_path) and os.path.exists(adv_path)):
        return None, None

    season_df = pd.read_csv(season_path)[["PLAYER_ID", "GP", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB"]]
    adv_df = pd.read_csv(adv_path)[["PLAYER_ID", "EFG_PCT", "OREB_PCT", "TM_TOV_PCT", "POSS"]]

    merged = season_df.merge(adv_df, on="PLAYER_ID", how="inner")
    merged["season"] = year + 1
    return year + 1, merged


def build_full_dataset():
    frames = []
    for year in range(2009, 2026):
        season_code, df = load_season_pair(year)
        if df is None:
            continue
        frames.append(df)
        print(f"  season {season_code} ({year}-{str(year+1)[-2:]}): {len(df)} rows")
    full = pd.concat(frames, ignore_index=True)

    full = full.rename(columns={
        "PLAYER_ID": "player_id", "TM_TOV_PCT": "tov_pct_raw",
        "EFG_PCT": "efg_pct", "OREB_PCT": "oreb_pct", "POSS": "poss",
        "FGM": "fgm", "FGA": "fga", "FG3M": "fg3m", "FG3A": "fg3a",
        "FTM": "ftm", "FTA": "fta", "OREB": "oreb", "DREB": "dreb",
    })
    full["tov_pct"] = full["tov_pct_raw"] / 100.0  # nba_api returns this as e.g. 10.8, not 0.108
    return full


def ensure_columns(conn):
    cur = conn.cursor()
    for col, coltype in NEW_COLUMNS:
        cur.execute(f"ALTER TABLE player_season_stats ADD COLUMN IF NOT EXISTS {col} {coltype};")
    conn.commit()
    print(f"  Confirmed {len(NEW_COLUMNS)} columns exist (added if missing, nothing dropped).")


def update_rows(conn, df):
    cur = conn.cursor()
    cols = ["fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "efg_pct", "oreb_pct", "tov_pct", "poss"]
    rows = [
        (r.player_id, int(r.season), r.fgm, r.fga, r.fg3m, r.fg3a, r.ftm, r.fta,
         r.oreb, r.dreb, r.efg_pct, r.oreb_pct, r.tov_pct, r.poss)
        for r in df.itertuples(index=False)
    ]
    psycopg2.extras.execute_values(
        cur,
        f"""
        UPDATE player_season_stats AS p SET
            {', '.join(f'{c} = v.{c}' for c in cols)}
        FROM (VALUES %s) AS v (player_id, season, {', '.join(cols)})
        WHERE p.player_id = v.player_id AND p.season = v.season;
        """,
        rows,
    )
    conn.commit()
    print(f"  Updated up to {cur.rowcount if cur.rowcount != -1 else len(rows)} matching rows "
          f"(rows in these CSVs that don't have a matching player_id+season row in the DB are silently skipped).")


def verify(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COUNT(fga), COUNT(efg_pct) FROM player_season_stats;")
    total, has_fga, has_efg = cur.fetchone()
    print(f"\n  Coverage: {has_fga}/{total} rows have fga populated, {has_efg}/{total} have efg_pct.")

    # Spot check against a known value (verified earlier from the raw CSV directly).
    cur.execute(
        "SELECT player_name, fga, fg3a, efg_pct, ts_pct FROM player_season_stats "
        "WHERE player_name = 'AJ Price' AND season = 2010;"
    )
    row = cur.fetchone()
    print(f"  Spot check (AJ Price, 2009-10): {row}  — expect fga=6.3, fg3a=3.1, efg_pct=0.494")


def main():
    print("Building merged dataset from local CSVs (no network calls)...")
    df = build_full_dataset()
    print(f"\nTotal rows across all seasons: {len(df)}")

    conn = psycopg2.connect(**DB_CONFIG)
    print("\nEnsuring new columns exist (ADD COLUMN IF NOT EXISTS — additive only)...")
    ensure_columns(conn)

    print("\nUpdating matching rows...")
    update_rows(conn, df)

    print("\nVerifying...")
    verify(conn)

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
