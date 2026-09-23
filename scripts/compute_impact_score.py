"""
compute_impact_score.py
========================
Compute and normalize a composite impact score for every player-season
and update the player_season_stats table in PostgreSQL.

Formula (raw):
    impact_score = (ts_pct * 2.0) + (net_rating * 0.5) + (usg_pct * 0.3)
                 + (ast_pct * 0.3) + (reb_pct * 0.2) + (w_pct * 1.5)
                 - (def_rating * 0.2)

Normalization (per season):
    z = (raw - season_mean) / season_std

Usage:
    python compute_impact_score.py
"""

import sys
import pandas as pd
import psycopg2

# ─── Configuration ──────────────────────────────────────────────────────────

from db_config import DB_CONFIG


# ─── Step 1: Add column if not exists ───────────────────────────────────────

def add_column():
    print("=" * 60)
    print("Step 1: Adding impact_score column (if not exists)")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cursor = conn.cursor()
        cursor.execute("""
            ALTER TABLE player_season_stats
            ADD COLUMN IF NOT EXISTS impact_score FLOAT;
        """)
        print("  ✅ Column ready")
    except Exception as e:
        print(f"  ❌ Error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 2–3: Compute raw + normalized impact scores ───────────────────────

def compute_scores():
    print("\n" + "=" * 60)
    print("Step 2–3: Computing and normalizing impact scores")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)

        # Pull required columns
        query = """
            SELECT player_id, season,
                   ts_pct, net_rating, usg_pct, ast_pct,
                   reb_pct, w_pct, def_rating
            FROM player_season_stats;
        """
        df = pd.read_sql_query(query, conn)
        print(f"  Loaded {len(df):,} rows")

        # Drop rows with NULLs in any component
        components = ["ts_pct", "net_rating", "usg_pct", "ast_pct",
                       "reb_pct", "w_pct", "def_rating"]
        before = len(df)
        df = df.dropna(subset=components)
        dropped = before - len(df)
        if dropped > 0:
            print(f"  Dropped {dropped} rows with NULL values")

        # Raw impact score
        df["raw_score"] = (
            (df["ts_pct"] * 2.0)
            + (df["net_rating"] * 0.5)
            + (df["usg_pct"] * 0.3)
            + (df["ast_pct"] * 0.3)
            + (df["reb_pct"] * 0.2)
            + (df["w_pct"] * 1.5)
            - (df["def_rating"] * 0.2)
        )

        # Normalize per season (z-score)
        season_stats = df.groupby("season")["raw_score"].agg(["mean", "std"])
        df["impact_score"] = df.apply(
            lambda row: (
                (row["raw_score"] - season_stats.loc[row["season"], "mean"])
                / season_stats.loc[row["season"], "std"]
            ) if season_stats.loc[row["season"], "std"] > 0 else 0.0,
            axis=1,
        )

        print(f"  ✅ Computed normalized impact scores for {len(df):,} rows")
        print(f"  Score range: {df['impact_score'].min():.3f} to {df['impact_score'].max():.3f}")

        return df[["player_id", "season", "impact_score"]]

    except Exception as e:
        print(f"  ❌ Error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Step 4: Update table ───────────────────────────────────────────────────

def update_table(scores_df):
    print("\n" + "=" * 60)
    print("Step 4: Updating player_season_stats table")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        update_query = """
            UPDATE player_season_stats
            SET impact_score = %s
            WHERE player_id = %s AND season = %s;
        """

        data = [
            (float(row["impact_score"]), int(row["player_id"]), int(row["season"]))
            for _, row in scores_df.iterrows()
        ]

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


# ─── Step 5: Print top 10 for latest season ─────────────────────────────────

def print_top_scores():
    print("\n" + "=" * 60)
    print("Step 5: Top 10 Impact Scores (Latest Season)")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT player_name, season, impact_score, pts, ts_pct, net_rating, w_pct
            FROM player_season_stats
            WHERE season = (SELECT MAX(season) FROM player_season_stats)
              AND impact_score IS NOT NULL
            ORDER BY impact_score DESC
            LIMIT 10;
        """)
        rows = cursor.fetchall()

        if rows:
            season = rows[0][1]
            print(f"\n  Season: {season}")
            print(f"  {'Rank':<6} {'Player':<28} {'Impact':>8} {'PTS':>6} {'TS%':>6} {'NET':>6} {'W%':>6}")
            print(f"  {'─' * 6} {'─' * 28} {'─' * 8} {'─' * 6} {'─' * 6} {'─' * 6} {'─' * 6}")
            for i, r in enumerate(rows, 1):
                print(f"  {i:<6} {r[0]:<28} {r[2]:>+7.3f} {r[3]:>6.1f} {r[4]:>6.3f} {r[5]:>6.1f} {r[6]:>6.3f}")

    except Exception as e:
        print(f"  ❌ Error: {e}")
    finally:
        if conn:
            conn.close()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("🏀 Compute Player Impact Scores")
    print("=" * 60)

    add_column()
    scores_df = compute_scores()
    update_table(scores_df)
    print_top_scores()

    print("\n" + "=" * 60)
    print("🎉 Done! impact_score column updated in player_season_stats.")
    print("=" * 60)


if __name__ == "__main__":
    main()
