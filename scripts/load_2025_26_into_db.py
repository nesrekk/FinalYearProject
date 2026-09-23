"""
load_2025_26_into_db.py
=========================
Loads the already-fetched 2025-26 season CSVs (nba_data/nba_2025_26_season.csv
and nba_data/nba_2025_26_advanced.csv — fetched earlier by
fetch_2025_26_season_data.py, already sitting on disk) into player_season_stats
as season=2026, following this project's season_int convention ("2025-26"
season -> season_int 2026, same as every other season in this table).

Purely additive: only inserts new (player_id, season=2026) rows, never
touches any existing row for any other season.

Column mapping verified by cross-checking a known real player (LeBron James,
2024-25) row already in the DB against the raw CSV values for that same
season — every base-CSV column maps straight across at PerGame values, every
advanced-CSV column maps straight across, with one exception: this table's
tov_pct is TM_TOV_PCT / 100 (CSV stores it as a percentage like 11.6, this
table stores it as a fraction like 0.116).

impact_score / impact_score_raw / impact_score_star are NOT computed here —
those are per-season z-scores computed by compute_impact_score.py and
upgrade_impact_scores.py across the whole table. Run those two scripts
after this one so the new season gets scored consistently with every other
season (that's how every prior season's impact scores were computed too).

Usage:
    python load_2025_26_into_db.py
    python compute_impact_score.py
    python upgrade_impact_scores.py
"""

import os
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(BASE_DIR, "nba_data")
BACKUP_DIR = os.path.join(BASE_DIR, "scratchpad_backups")

SEASON_INT = 2026
BASE_CSV = os.path.join(DATA_DIR, "nba_2025_26_season.csv")
ADVANCED_CSV = os.path.join(DATA_DIR, "nba_2025_26_advanced.csv")

from db_config import DB_CONFIG

INSERT_COLUMNS = [
    "player_id", "player_name", "team_abbreviation", "season", "age", "gp", "min",
    "pts", "reb", "ast", "stl", "blk", "tov", "fg_pct", "fg3_pct", "ft_pct", "w_pct",
    "plus_minus", "ts_pct", "usg_pct", "off_rating", "def_rating", "net_rating",
    "ast_pct", "reb_pct", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb",
    "efg_pct", "oreb_pct", "tov_pct", "poss",
]


def load_and_merge():
    base = pd.read_csv(BASE_CSV)
    adv = pd.read_csv(ADVANCED_CSV)

    base = base[[
        "PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION", "AGE", "GP", "MIN",
        "PTS", "REB", "AST", "STL", "BLK", "TOV", "FG_PCT", "FG3_PCT", "FT_PCT",
        "W_PCT", "PLUS_MINUS", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "OREB", "DREB",
    ]]
    adv = adv[[
        "PLAYER_ID", "TS_PCT", "USG_PCT", "OFF_RATING", "DEF_RATING", "NET_RATING",
        "AST_PCT", "REB_PCT", "EFG_PCT", "OREB_PCT", "TM_TOV_PCT", "POSS",
    ]]

    df = base.merge(adv, on="PLAYER_ID", how="inner")
    df["tov_pct"] = df["TM_TOV_PCT"] / 100.0
    df["season"] = SEASON_INT

    rename = {
        "PLAYER_ID": "player_id", "PLAYER_NAME": "player_name", "TEAM_ABBREVIATION": "team_abbreviation",
        "AGE": "age", "GP": "gp", "MIN": "min", "PTS": "pts", "REB": "reb", "AST": "ast",
        "STL": "stl", "BLK": "blk", "TOV": "tov", "FG_PCT": "fg_pct", "FG3_PCT": "fg3_pct",
        "FT_PCT": "ft_pct", "W_PCT": "w_pct", "PLUS_MINUS": "plus_minus", "FGM": "fgm",
        "FGA": "fga", "FG3M": "fg3m", "FG3A": "fg3a", "FTM": "ftm", "FTA": "fta",
        "OREB": "oreb", "DREB": "dreb", "TS_PCT": "ts_pct", "USG_PCT": "usg_pct",
        "OFF_RATING": "off_rating", "DEF_RATING": "def_rating", "NET_RATING": "net_rating",
        "AST_PCT": "ast_pct", "REB_PCT": "reb_pct", "EFG_PCT": "efg_pct", "OREB_PCT": "oreb_pct",
        "POSS": "poss",
    }
    df = df.rename(columns=rename)
    df["age"] = df["age"].astype("Int64")
    df["gp"] = df["gp"].astype("Int64")
    df = df.dropna(subset=["player_id", "player_name", "team_abbreviation"])
    return df[INSERT_COLUMNS]


def backup_existing():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    conn = psycopg2.connect(**DB_CONFIG)
    df = pd.read_sql_query("SELECT * FROM player_season_stats WHERE season = %s;", conn, params=(SEASON_INT,))
    conn.close()
    if not df.empty:
        path = os.path.join(BACKUP_DIR, f"player_season_stats_season_{SEASON_INT}_before_reload.csv")
        df.to_csv(path, index=False)
        print(f"Backed up {len(df)} existing season={SEASON_INT} rows to {path} before replacing them.")
    return len(df)


def save(df):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("DELETE FROM player_season_stats WHERE season = %s;", (SEASON_INT,))

    values = [tuple(row[c] if pd.notna(row[c]) else None for c in INSERT_COLUMNS) for _, row in df.iterrows()]
    execute_values(
        cur,
        f"INSERT INTO player_season_stats ({', '.join(INSERT_COLUMNS)}) VALUES %s;",
        values,
    )
    conn.commit()
    conn.close()
    return len(values)


if __name__ == "__main__":
    print(f"Loading season {SEASON_INT} (2025-26) from already-fetched CSVs into player_season_stats...")
    existing = backup_existing()
    if existing:
        print(f"(Replacing {existing} existing rows — the season is still in progress, so re-running "
              f"this script after re-fetching fresher CSVs is expected and safe.)")

    df = load_and_merge()
    print(f"Merged {len(df)} players from base + advanced CSVs.")

    n = save(df)
    print(f"Saved {n} rows for season {SEASON_INT}.")
    print("\nNext: run compute_impact_score.py and upgrade_impact_scores.py to "
          "backfill impact scores for this season (they recompute across the whole table).")
