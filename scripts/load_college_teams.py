"""
load_college_teams.py
======================
Loads the Kaggle "College Basketball Dataset" (team-level D1 ratings from
Bart Torvik's T-Rank, one CSV per season) into Postgres as
college_team_seasons.

Source files (gitignored, third-party): nba_data/college_teams/cbb13.csv ..
cbb26.csv. The combined cbb.csv in the same download was checked against
the per-season files (identical ADJOE/ADJDE/G/W/POSTSEASON for every team
in all 12 seasons it covers), so the per-season files are the one source.

What each season file is:
  - 2013-2025 (no 2020 tournament): END-OF-SEASON ratings, tournament games
    included. Checked: 2024-25 Florida shows 40 games, 36-4 (its real final
    record, six NCAA wins included). Fine for describing how good a team
    was; NOT usable as pre-tournament inputs to a bracket model (that model
    uses CollegeBasketballData.com stats cut off before the tournament).
  - 2020: no POSTSEASON/SEED columns (tournament cancelled).
  - 2026: pre-tournament snapshot (Duke 34 games), SEED filled, no results.

Season ints are end year (2013 = 2012-13), same as the rest of the project.
Also adds adj_margin = ADJOE - ADJDE (points per 100 possessions better than
an average D1 team, opponent-adjusted) and each team's rank by BARTHAG
within its season.

Usage:
    cd scripts && python3 load_college_teams.py
"""

import glob
import os
import re

import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "nba_data", "college_teams")

COLUMNS = {
    "TEAM": "team", "CONF": "conf", "G": "g", "W": "w", "ADJOE": "adjoe", "ADJDE": "adjde",
    "BARTHAG": "barthag", "EFG_O": "efg_o", "EFG_D": "efg_d", "TOR": "tor", "TORD": "tord",
    "ORB": "orb", "DRB": "drb", "FTR": "ftr", "FTRD": "ftrd", "2P_O": "two_p_o", "2P_D": "two_p_d",
    "3P_O": "three_p_o", "3P_D": "three_p_d", "ADJ_T": "adj_t", "WAB": "wab",
    "POSTSEASON": "postseason", "SEED": "seed",
}


def load_frames():
    frames = []
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "cbb[0-9][0-9].csv"))):
        season = 2000 + int(re.search(r"cbb(\d\d)\.csv", path).group(1))
        df = pd.read_csv(path, encoding="utf-8-sig")
        df = df.rename(columns=COLUMNS)[[c for c in COLUMNS.values() if c in df.rename(columns=COLUMNS).columns]]
        for col in ("postseason", "seed"):
            if col not in df.columns:
                df[col] = None
        df["season"] = season
        df["adj_margin"] = (df["adjoe"] - df["adjde"]).round(1)
        df["barthag_rank"] = df["barthag"].rank(ascending=False, method="min").astype(int)
        df["n_teams"] = len(df)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def main():
    df = load_frames()
    dupes = df.duplicated(["season", "team"]).sum()
    assert dupes == 0, f"{dupes} duplicate (season, team) rows"
    print(f"{len(df)} team-seasons, {df.season.min()}-{df.season.max()}, "
          f"{df.season.nunique()} seasons")

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS college_team_seasons;")
    cur.execute("""
        CREATE TABLE college_team_seasons (
            season INTEGER NOT NULL,
            team TEXT NOT NULL,
            conf TEXT,
            g INTEGER, w INTEGER,
            adjoe REAL, adjde REAL, adj_margin REAL, barthag REAL,
            barthag_rank INTEGER, n_teams INTEGER,
            efg_o REAL, efg_d REAL, tor REAL, tord REAL, orb REAL, drb REAL,
            ftr REAL, ftrd REAL, two_p_o REAL, two_p_d REAL, three_p_o REAL, three_p_d REAL,
            adj_t REAL, wab REAL,
            postseason TEXT, seed INTEGER,
            PRIMARY KEY (season, team)
        );
    """)
    cols = ["season", "team", "conf", "g", "w", "adjoe", "adjde", "adj_margin", "barthag",
            "barthag_rank", "n_teams", "efg_o", "efg_d", "tor", "tord", "orb", "drb", "ftr", "ftrd",
            "two_p_o", "two_p_d", "three_p_o", "three_p_d", "adj_t", "wab", "postseason", "seed"]
    rows = [
        tuple(None if pd.isna(v) else (int(v) if c in ("season", "g", "w", "barthag_rank", "n_teams", "seed") else v)
              for c, v in zip(cols, rec))
        for rec in df[cols].itertuples(index=False)
    ]
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO college_team_seasons ({', '.join(cols)}) VALUES %s;", rows
    )
    conn.commit()

    cur.execute("SELECT season, team FROM college_team_seasons WHERE postseason = 'Champions' ORDER BY season;")
    print("Champions:", ", ".join(f"{s} {t}" for s, t in cur.fetchall()))
    conn.close()


if __name__ == "__main__":
    main()
