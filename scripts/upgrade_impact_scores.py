"""
upgrade_impact_scores.py
=========================
Add two normalized impact score systems to player_season_stats:

  - impact_score_raw  — all players, efficiency-weighted composite
  - impact_score_star — star-caliber only (min >= 28 & usg_pct >= 0.22),
                        production-weighted composite

Both are z-score normalized per season.

Usage:
    python upgrade_impact_scores.py
"""

import sys
import numpy as np
import pandas as pd
import psycopg2

# ─── Configuration ──────────────────────────────────────────────────────────

from db_config import DB_CONFIG

REQUIRED_COLS = [
    "player_id", "player_name", "season",
    "pts", "ts_pct", "usg_pct", "net_rating",
    "ast_pct", "reb_pct", "w_pct", "def_rating", "min",
]


# ─── Step 1: Add columns ───────────────────────────────────────────────────

def add_columns():
    print("=" * 60)
    print("Step 1: Adding impact_score_raw & impact_score_star columns")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute("""
            ALTER TABLE player_season_stats
            ADD COLUMN IF NOT EXISTS impact_score_raw FLOAT;
        """)
        cursor.execute("""
            ALTER TABLE player_season_stats
            ADD COLUMN IF NOT EXISTS impact_score_star FLOAT;
        """)
        print("  ✅ Columns ready")
    except Exception as e:
        print(f"  ❌ Error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 2: Load data ──────────────────────────────────────────────────────

def load_data():
    print("\n" + "=" * 60)
    print("Step 2: Loading data from PostgreSQL")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cols = ", ".join(REQUIRED_COLS)
        df = pd.read_sql_query(f"SELECT {cols} FROM player_season_stats;", conn)

        feature_cols = [c for c in REQUIRED_COLS if c not in ("player_id", "player_name", "season")]
        before = len(df)
        df = df.dropna(subset=feature_cols).reset_index(drop=True)
        dropped = before - len(df)
        if dropped > 0:
            print(f"  Dropped {dropped} rows with NULLs")
        print(f"  Loaded {len(df):,} rows")
        return df
    except Exception as e:
        print(f"  ❌ Error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 3–4: Compute raw impact score ─────────────────────────────────────

def compute_raw_score(df):
    print("\n" + "=" * 60)
    print("Step 3: Computing RAW impact score (all players)")
    print("=" * 60)

    df["raw_impact"] = (
        (df["ts_pct"] * 2.0)
        + (df["net_rating"] * 0.5)
        + (df["usg_pct"] * 0.3)
        + (df["ast_pct"] * 0.3)
        + (df["reb_pct"] * 0.2)
        + (df["w_pct"] * 1.5)
        - (df["def_rating"] * 0.2)
    )

    # Z-score normalize per season
    season_stats = df.groupby("season")["raw_impact"].agg(["mean", "std"])
    df["impact_score_raw"] = df.apply(
        lambda r: (r["raw_impact"] - season_stats.loc[r["season"], "mean"])
                  / season_stats.loc[r["season"], "std"]
        if season_stats.loc[r["season"], "std"] > 0 else 0.0,
        axis=1,
    )

    print(f"  ✅ Computed for {len(df):,} players")
    print(f"  Range: {df['impact_score_raw'].min():.3f} to {df['impact_score_raw'].max():.3f}")
    return df


# ─── Step 5: Compute star impact score ──────────────────────────────────────

def compute_star_score(df):
    print("\n" + "=" * 60)
    print("Step 4: Computing STAR impact score (min >= 28, usg >= 0.22)")
    print("=" * 60)

    # Star filter
    star_mask = (df["min"] >= 28) & (df["usg_pct"] >= 0.22)
    star_count = star_mask.sum()
    print(f"  Star-qualified players: {star_count:,} / {len(df):,}")

    # Initialize as NaN (non-stars stay NULL)
    df["star_raw"] = np.nan
    df["impact_score_star"] = np.nan

    # Compute raw star score only for qualified players
    df.loc[star_mask, "star_raw"] = (
        (df.loc[star_mask, "pts"] * 0.6)
        + (df.loc[star_mask, "ts_pct"] * 1.8)
        + (df.loc[star_mask, "net_rating"] * 0.5)
        + (df.loc[star_mask, "usg_pct"] * 0.5)
        + (df.loc[star_mask, "ast_pct"] * 0.3)
        + (df.loc[star_mask, "reb_pct"] * 0.2)
        + (df.loc[star_mask, "w_pct"] * 1.0)
        - (df.loc[star_mask, "def_rating"] * 0.2)
    )

    # Z-score normalize per season (only among star-qualified)
    star_df = df[star_mask].copy()
    season_stats = star_df.groupby("season")["star_raw"].agg(["mean", "std"])

    for season in season_stats.index:
        mean = season_stats.loc[season, "mean"]
        std = season_stats.loc[season, "std"]
        season_mask = star_mask & (df["season"] == season)
        if std > 0:
            df.loc[season_mask, "impact_score_star"] = (
                (df.loc[season_mask, "star_raw"] - mean) / std
            )
        else:
            df.loc[season_mask, "impact_score_star"] = 0.0

    valid = df["impact_score_star"].notna().sum()
    print(f"  ✅ Computed for {valid:,} star players")
    print(f"  Range: {df['impact_score_star'].min():.3f} to {df['impact_score_star'].max():.3f}")

    return df


# ─── Step 6: Update table ──────────────────────────────────────────────────

def update_table(df):
    print("\n" + "=" * 60)
    print("Step 5: Updating player_season_stats table")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        # First set all to NULL (clean slate)
        cursor.execute("UPDATE player_season_stats SET impact_score_raw = NULL, impact_score_star = NULL;")
        conn.commit()

        update_query = """
            UPDATE player_season_stats
            SET impact_score_raw = %s, impact_score_star = %s
            WHERE player_id = %s AND season = %s;
        """

        data = []
        for _, row in df.iterrows():
            raw_val = float(row["impact_score_raw"])
            star_val = float(row["impact_score_star"]) if pd.notna(row["impact_score_star"]) else None
            data.append((raw_val, star_val, int(row["player_id"]), int(row["season"])))

        batch_size = 1000
        total = len(data)
        for start in range(0, total, batch_size):
            batch = data[start:start + batch_size]
            cursor.executemany(update_query, batch)
            conn.commit()
            print(f"    Updated {min(start + batch_size, total):,} / {total:,}", end="\r")

        print(f"\n  ✅ Updated {total:,} rows")

    except Exception as e:
        print(f"  ❌ Error: {e}")
        if conn:
            conn.rollback()
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 7: Print top 10s ──────────────────────────────────────────────────

def print_results():
    print("\n" + "=" * 60)
    print("Step 6: Results (Latest Season)")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("SELECT MAX(season) FROM player_season_stats;")
        latest = cursor.fetchone()[0]

        # Top 10 RAW
        cursor.execute("""
            SELECT player_name, impact_score_raw, pts, min, w_pct
            FROM player_season_stats
            WHERE season = %s AND impact_score_raw IS NOT NULL
            ORDER BY impact_score_raw DESC LIMIT 10;
        """, (latest,))
        rows = cursor.fetchall()

        print(f"\n  📊 Top 10 RAW Impact — Season {latest}:")
        print(f"  {'Rank':<6} {'Player':<28} {'Score':>8} {'PTS':>6} {'MIN':>6} {'W%':>6}")
        print(f"  {'─' * 6} {'─' * 28} {'─' * 8} {'─' * 6} {'─' * 6} {'─' * 6}")
        for i, r in enumerate(rows, 1):
            print(f"  {i:<6} {r[0]:<28} {r[1]:>+7.3f} {r[2]:>6.1f} {r[3]:>6.1f} {r[4]:>6.3f}")

        # Top 10 STAR
        cursor.execute("""
            SELECT player_name, impact_score_star, pts, min, w_pct
            FROM player_season_stats
            WHERE season = %s AND impact_score_star IS NOT NULL
            ORDER BY impact_score_star DESC LIMIT 10;
        """, (latest,))
        rows = cursor.fetchall()

        print(f"\n  ⭐ Top 10 STAR Impact — Season {latest}:")
        print(f"  {'Rank':<6} {'Player':<28} {'Score':>8} {'PTS':>6} {'MIN':>6} {'W%':>6}")
        print(f"  {'─' * 6} {'─' * 28} {'─' * 8} {'─' * 6} {'─' * 6} {'─' * 6}")
        for i, r in enumerate(rows, 1):
            print(f"  {i:<6} {r[0]:<28} {r[1]:>+7.3f} {r[2]:>6.1f} {r[3]:>6.1f} {r[4]:>6.3f}")

    except Exception as e:
        print(f"  ❌ Error: {e}")
    finally:
        if conn:
            conn.close()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("🏀 Upgrade Impact Scores (Raw + Star)")
    print("=" * 60)

    add_columns()
    df = load_data()
    df = compute_raw_score(df)
    df = compute_star_score(df)
    update_table(df)
    print_results()

    print("\n" + "=" * 60)
    print("🎉 Done! Both impact_score_raw and impact_score_star updated.")
    print("=" * 60)


if __name__ == "__main__":
    main()
