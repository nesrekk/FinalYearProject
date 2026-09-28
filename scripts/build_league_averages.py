"""
build_league_averages.py
=========================
One row per NBA season (BAA 1946-47 to 1948-49 included): the league's
pace and what an average team produced per game, for the Era Translator
(api/routers/era.py).

Source: Basketball-Reference's "League Average" rows in
nba_data/kaggle_1947_present/Team Summaries.csv (pace, offensive rating,
TS%, eFG%) and Team Stats Per Game.csv (points, rebounds, assists, steals,
blocks, turnovers, threes, free throws and shooting % per team game). ABA
rows are left out: player_season_stats is NBA only.

Pace = possessions per 48 minutes. The standard possession estimate needs
offensive rebounds and turnovers, which the league average rows only have
from 1973-74 on, so Basketball-Reference's paces for 1950-51 to 1972-73 are
estimates (pace_source 'bref_estimate'). 1949-50 and the BAA seasons have
no pace at all; this script estimates them the way 1950-51 to 1952-53
relate pace to shots: pace = (FGA + 0.44 x FTA) per team game x the average
ratio of Basketball-Reference's pace to that sum in those three seasons
(0.994). pace_source 'estimated_here'.

A league average that wasn't recorded (steals before 1973-74, threes before
1979-80) stays NULL.

Usage:
    cd scripts && python3 build_league_averages.py
"""

import os

import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

KAGGLE = os.path.join(os.path.dirname(__file__), "..", "nba_data", "kaggle_1947_present")
FIRST_FULL_PACE = 1974          # offensive rebounds and turnovers recorded from 1973-74
RATIO_SEASONS = (1951, 1952, 1953)

PER_GAME = {  # column here -> Team Stats Per Game.csv column
    "pts": "pts_per_game", "fga": "fga_per_game", "fta": "fta_per_game",
    "fg3m": "x3p_per_game", "fg3a": "x3pa_per_game", "reb": "trb_per_game",
    "oreb": "orb_per_game", "ast": "ast_per_game", "stl": "stl_per_game",
    "blk": "blk_per_game", "tov": "tov_per_game", "pf": "pf_per_game",
    "fg_pct": "fg_percent", "fg3_pct": "x3p_percent", "ft_pct": "ft_percent",
}
SUMMARY = {"pace": "pace", "ortg": "o_rtg", "ts_pct": "ts_percent", "efg_pct": "e_fg_percent"}


def main():
    summ = pd.read_csv(os.path.join(KAGGLE, "Team Summaries.csv"))
    pg = pd.read_csv(os.path.join(KAGGLE, "Team Stats Per Game.csv"))
    summ = summ[(summ.team == "League Average") & (summ.lg != "ABA")]
    pg = pg[(pg.team == "League Average") & (pg.lg != "ABA")]
    df = pg.merge(summ, on=["season", "lg"], how="inner", suffixes=("", "_s"))
    assert df.season.is_unique, "more than one league-average row for a season"
    df = df.rename(columns={**{v: k for k, v in PER_GAME.items()}, **{v: k for k, v in SUMMARY.items()}})
    df["games"] = df["g"]

    shots = df.fga + 0.44 * df.fta
    ratio = (df.pace / shots)[df.season.isin(RATIO_SEASONS)]
    assert len(ratio) == len(RATIO_SEASONS) and ratio.notna().all()
    ratio = ratio.mean()
    df["pace_source"] = "bref"
    df.loc[df.season < FIRST_FULL_PACE, "pace_source"] = "bref_estimate"
    missing = df.pace.isna()
    df.loc[missing, "pace"] = (shots[missing] * ratio).round(1)
    df.loc[missing, "pace_source"] = "estimated_here"
    # Offensive rating wasn't published where pace wasn't; derive it the same way.
    no_ortg = df.ortg.isna()
    df.loc[no_ortg, "ortg"] = (df.pts[no_ortg] / df.pace[no_ortg] * 100).round(1)
    print(f"Pace/(FGA + 0.44 FTA) in {RATIO_SEASONS}: {ratio:.4f}; estimated here for "
          f"{', '.join(str(s) for s in sorted(df.season[missing]))}")

    cols = ["season", "lg", "games", "pace", "pace_source", "ortg", *PER_GAME, "ts_pct", "efg_pct"]
    df = df[cols].sort_values("season")

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS league_season_averages;")
    cur.execute(f"""
        CREATE TABLE league_season_averages (
            season INTEGER PRIMARY KEY,
            lg TEXT NOT NULL,
            games INTEGER,
            pace DOUBLE PRECISION NOT NULL,
            pace_source TEXT NOT NULL,
            ortg DOUBLE PRECISION,
            {', '.join(f'{c} DOUBLE PRECISION' for c in PER_GAME)},
            ts_pct DOUBLE PRECISION,
            efg_pct DOUBLE PRECISION
        );""")
    rows = [tuple(None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in r)
            for r in df.itertuples(index=False)]
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO league_season_averages ({', '.join(cols)}) VALUES %s;", rows)
    conn.commit()
    print(f"league_season_averages: {len(rows)} seasons, {df.season.min()}-{df.season.max()}")
    print(df.pace_source.value_counts().to_string())
    conn.close()


if __name__ == "__main__":
    main()
