"""
fetch_dpoy_roy_stats.py
========================
Build DPOY and ROY winner datasets from existing PostgreSQL data
and advanced CSVs (already on disk). No API calls needed.

Outputs:
    nba_data/dpoy_seasons.csv
    nba_data/roy_seasons.csv
    PostgreSQL tables: dpoy_seasons, roy_seasons

Usage:
    python fetch_dpoy_roy_stats.py
"""

import os
import sys
import warnings
import pandas as pd
import psycopg2

warnings.filterwarnings("ignore")

# ─── Configuration ──────────────────────────────────────────────────────────

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "nba_data")

from db_config import DB_CONFIG

# ─── Award Winners (2009-10 to 2023-24) ─────────────────────────────────────

DPOY_WINNERS = [
    {"season": "2009-10", "season_int": 2010, "player": "Dwight Howard"},
    {"season": "2010-11", "season_int": 2011, "player": "Dwight Howard"},
    {"season": "2011-12", "season_int": 2012, "player": "Tyson Chandler"},
    {"season": "2012-13", "season_int": 2013, "player": "Marc Gasol"},
    {"season": "2013-14", "season_int": 2014, "player": "Joakim Noah"},
    {"season": "2014-15", "season_int": 2015, "player": "Kawhi Leonard"},
    {"season": "2015-16", "season_int": 2016, "player": "Kawhi Leonard"},
    {"season": "2016-17", "season_int": 2017, "player": "Draymond Green"},
    {"season": "2017-18", "season_int": 2018, "player": "Rudy Gobert"},
    {"season": "2018-19", "season_int": 2019, "player": "Rudy Gobert"},
    {"season": "2019-20", "season_int": 2020, "player": "Giannis Antetokounmpo"},
    {"season": "2020-21", "season_int": 2021, "player": "Rudy Gobert"},
    {"season": "2021-22", "season_int": 2022, "player": "Marcus Smart"},
    {"season": "2022-23", "season_int": 2023, "player": "Jaren Jackson Jr."},
    {"season": "2023-24", "season_int": 2024, "player": "Rudy Gobert"},
]

ROY_WINNERS = [
    {"season": "2009-10", "season_int": 2010, "player": "Tyreke Evans"},
    {"season": "2010-11", "season_int": 2011, "player": "Blake Griffin"},
    {"season": "2011-12", "season_int": 2012, "player": "Kyrie Irving"},
    {"season": "2012-13", "season_int": 2013, "player": "Damian Lillard"},
    {"season": "2013-14", "season_int": 2014, "player": "Michael Carter-Williams"},
    {"season": "2014-15", "season_int": 2015, "player": "Andrew Wiggins"},
    {"season": "2015-16", "season_int": 2016, "player": "Karl-Anthony Towns"},
    {"season": "2016-17", "season_int": 2017, "player": "Malcolm Brogdon"},
    {"season": "2017-18", "season_int": 2018, "player": "Ben Simmons"},
    {"season": "2018-19", "season_int": 2019, "player": "Luka Dončić"},
    {"season": "2019-20", "season_int": 2020, "player": "Ja Morant"},
    {"season": "2020-21", "season_int": 2021, "player": "LaMelo Ball"},
    {"season": "2021-22", "season_int": 2022, "player": "Scottie Barnes"},
    {"season": "2022-23", "season_int": 2023, "player": "Paolo Banchero"},
    {"season": "2023-24", "season_int": 2024, "player": "Chet Holmgren"},
]


# ─── Load base stats from PostgreSQL ────────────────────────────────────────

def load_base_stats():
    """Pull all player season stats from the database."""
    print("=" * 60)
    print("Loading base stats from PostgreSQL")
    print("=" * 60)

    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        df = pd.read_sql_query(
            "SELECT * FROM player_season_stats;", conn
        )
        print(f"  ✅ {len(df):,} rows loaded")
        return df
    except Exception as e:
        print(f"  ❌ Error: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()


# ─── Load advanced stats from CSVs ──────────────────────────────────────────

def load_advanced_stats():
    """Load advanced stats from existing CSV files for all seasons."""
    print("\n" + "=" * 60)
    print("Loading advanced stats from CSVs")
    print("=" * 60)

    all_adv = []
    for year in range(2009, 2025):
        tag = f"{year}_{str(year+1)[-2:]}"
        path = os.path.join(DATA_DIR, f"nba_{tag}_advanced.csv")
        if os.path.exists(path):
            df = pd.read_csv(path)
            df["season_int"] = year + 1  # ending year
            all_adv.append(df)
        else:
            print(f"  ⚠ Missing: {path}")

    if all_adv:
        adv_df = pd.concat(all_adv, ignore_index=True)
        print(f"  ✅ {len(adv_df):,} advanced stat rows across {len(all_adv)} seasons")
        return adv_df
    else:
        print("  ❌ No advanced CSVs found!")
        return pd.DataFrame()


# ─── Build award dataset ────────────────────────────────────────────────────

def build_award_dataset(winners, award_name, base_df, adv_df):
    """Build a dataset for award winners by merging base + advanced stats."""
    print(f"\n{'=' * 60}")
    print(f"Building {award_name} dataset")
    print(f"{'=' * 60}")

    rows = []
    for entry in winners:
        season = entry["season"]
        season_int = entry["season_int"]
        player_name = entry["player"]

        # Find player in base stats
        mask = (base_df["player_name"] == player_name) & (base_df["season"] == season_int)
        base_match = base_df[mask]

        if base_match.empty:
            # Try partial match on last name
            last = player_name.split()[-1]
            mask = (
                base_df["player_name"].str.contains(last, case=False, na=False)
                & (base_df["season"] == season_int)
            )
            base_match = base_df[mask]

        if base_match.empty:
            print(f"  ✗ {season} {player_name} — NOT FOUND in database")
            continue

        base_row = base_match.iloc[0]
        player_id = int(base_row["player_id"])

        # Build row with base stats
        row = {
            f"{award_name}_SEASON": season,
            "PLAYER_NAME": player_name,
            "PLAYER_ID": player_id,
            "TEAM_ABBREVIATION": base_row.get("team_abbreviation"),
            "PLAYER_AGE": base_row.get("age"),
            "GP": base_row.get("gp"),
            "MIN": base_row.get("min"),
            "PTS": base_row.get("pts"),
            "REB": base_row.get("reb"),
            "AST": base_row.get("ast"),
            "STL": base_row.get("stl"),
            "BLK": base_row.get("blk"),
            "TOV": base_row.get("tov"),
            "FG_PCT": base_row.get("fg_pct"),
            "FG3_PCT": base_row.get("fg3_pct"),
            "FT_PCT": base_row.get("ft_pct"),
            "W_PCT": base_row.get("w_pct"),
            "PLUS_MINUS": base_row.get("plus_minus"),
            "TS_PCT": base_row.get("ts_pct"),
            "USG_PCT": base_row.get("usg_pct"),
            "OFF_RATING": base_row.get("off_rating"),
            "DEF_RATING": base_row.get("def_rating"),
            "NET_RATING": base_row.get("net_rating"),
            "AST_PCT": base_row.get("ast_pct"),
            "REB_PCT": base_row.get("reb_pct"),
        }

        # Merge advanced stats from CSV
        if not adv_df.empty:
            adv_mask = (adv_df["PLAYER_ID"] == player_id) & (adv_df["season_int"] == season_int)
            adv_match = adv_df[adv_mask]
            if not adv_match.empty:
                adv_row = adv_match.iloc[0]
                for col in ["OFF_RATING", "DEF_RATING", "NET_RATING",
                             "AST_PCT", "AST_TO", "AST_RATIO",
                             "OREB_PCT", "DREB_PCT", "REB_PCT",
                             "EFG_PCT", "TS_PCT", "USG_PCT",
                             "PACE", "PIE", "TM_TOV_PCT"]:
                    if col in adv_row.index:
                        row[f"ADV_{col}"] = adv_row[col]

        rows.append(row)
        print(f"  ✓ {season} {player_name:<28} "
              f"{row['PTS']:.1f} PPG, {row['STL']:.1f} SPG, {row['BLK']:.1f} BPG")

    df = pd.DataFrame(rows)
    print(f"\n  Total: {len(df)} {award_name} seasons collected")
    return df


# ─── Save CSV and load to PostgreSQL ────────────────────────────────────────

def save_and_load(df, csv_name, table_name, award_name):
    """Save as CSV and load into PostgreSQL."""
    # CSV
    csv_path = os.path.join(DATA_DIR, csv_name)
    df.to_csv(csv_path, index=False)
    print(f"\n  ✅ Saved {csv_path}")

    # PostgreSQL
    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cursor = conn.cursor()

        cursor.execute(f"DROP TABLE IF EXISTS {table_name};")

        col_defs = []
        for col in df.columns:
            dtype = str(df[col].dtype)
            if "int" in dtype:
                col_defs.append(f"{col.lower()} INTEGER")
            elif "float" in dtype:
                col_defs.append(f"{col.lower()} REAL")
            else:
                col_defs.append(f"{col.lower()} TEXT")

        cursor.execute(f"CREATE TABLE {table_name} ({', '.join(col_defs)});")

        placeholders = ", ".join(["%s"] * len(df.columns))
        cols_lower = ", ".join(c.lower() for c in df.columns)
        insert_q = f"INSERT INTO {table_name} ({cols_lower}) VALUES ({placeholders})"

        for _, row in df.iterrows():
            values = [None if pd.isna(v) else v for v in row.values]
            cursor.execute(insert_q, values)

        print(f"  ✅ Loaded {len(df)} rows into PostgreSQL table: {table_name}")

    except Exception as e:
        print(f"  ❌ DB Error: {e}")
    finally:
        if conn:
            conn.close()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("🏀 Build DPOY & ROY Winner Datasets")
    print("=" * 60)
    print(f"  DPOY winners: {len(DPOY_WINNERS)} seasons")
    print(f"  ROY  winners: {len(ROY_WINNERS)} seasons")
    print(f"  Source: PostgreSQL + advanced CSVs (zero API calls)")

    os.makedirs(DATA_DIR, exist_ok=True)

    # Load existing data
    base_df = load_base_stats()
    adv_df = load_advanced_stats()

    # Build DPOY dataset
    dpoy_df = build_award_dataset(DPOY_WINNERS, "DPOY", base_df, adv_df)
    save_and_load(dpoy_df, "dpoy_seasons.csv", "dpoy_seasons", "DPOY")

    # Build ROY dataset
    roy_df = build_award_dataset(ROY_WINNERS, "ROY", base_df, adv_df)
    save_and_load(roy_df, "roy_seasons.csv", "roy_seasons", "ROY")

    # Summary
    print("\n" + "=" * 60)
    print("📊 Summary")
    print("=" * 60)

    season_col_dpoy = "DPOY_SEASON"
    if not dpoy_df.empty:
        print(f"\n  DPOY Winners ({len(dpoy_df)} seasons):")
        print(f"  {'Season':<10} {'Player':<28} {'PTS':>6} {'STL':>6} {'BLK':>6} {'DEF_RTG':>8}")
        print(f"  {'─' * 10} {'─' * 28} {'─' * 6} {'─' * 6} {'─' * 6} {'─' * 8}")
        for _, r in dpoy_df.iterrows():
            print(f"  {r[season_col_dpoy]:<10} {r['PLAYER_NAME']:<28} "
                  f"{r['PTS']:>6.1f} {r['STL']:>6.1f} {r['BLK']:>6.1f} "
                  f"{r['DEF_RATING']:>8.1f}")

    if not roy_df.empty:
        print(f"\n  ROY Winners ({len(roy_df)} seasons):")
        print(f"  {'Season':<10} {'Player':<28} {'PTS':>6} {'REB':>6} {'AST':>6} {'AGE':>5}")
        print(f"  {'─' * 10} {'─' * 28} {'─' * 6} {'─' * 6} {'─' * 6} {'─' * 5}")
        for _, r in roy_df.iterrows():
            print(f"  {r['ROY_SEASON']:<10} {r['PLAYER_NAME']:<28} "
                  f"{r['PTS']:>6.1f} {r['REB']:>6.1f} {r['AST']:>6.1f} "
                  f"{r['PLAYER_AGE']:>5.0f}")

    print("\n" + "=" * 60)
    print("🎉 Done!")
    print(f"   CSVs:   dpoy_seasons.csv, roy_seasons.csv")
    print(f"   Tables: dpoy_seasons, roy_seasons")
    print("=" * 60)


if __name__ == "__main__":
    main()
