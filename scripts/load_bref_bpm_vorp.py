"""
load_bref_bpm_vorp.py
======================
Puts Basketball-Reference's published BPM, OBPM, DBPM and VORP into
player_season_stats for every 2009-10+ season (pre-2010 rows already come
from Basketball-Reference, via load_kaggle_historical_seasons.py).

Why: this project's own BPM 2.0 reproduction (build_bpm_vorp.py) ran hot
and loose: 2025-26 SGA at BPM 22.0 / VORP 13.8 against the published 11.7 /
7.8, and only 0.76 correlation with the published 2024-25 BPM. DAD Index,
Spacing Lab, Contract Value, the BPM leaderboard and several pages read
these columns, so they now use the real numbers.

Source: nba_data/kaggle_1947_present/Advanced.csv (Basketball-Reference),
one row per player-season (a traded player's combined "2TM"/"3TM" row),
linked to NBA ids through player_id_map (bref_nba_ids.py). Checked on load:
games played agree with this table's own gp (from nba_api) within one game
on every linked row. Rows that can't be linked are set to NULL rather than
left holding the reproduction, so the two scales are never mixed.

Before overwriting, the reproduction is copied to bpm_repro/obpm_repro/
dbpm_repro/vorp_repro if those are still empty (Pair Synergy's trained model
uses dbpm_repro).

Usage (after build_bpm_vorp.py and load_kaggle_historical_seasons.py):
    cd scripts && python3 load_bref_bpm_vorp.py
"""

import os

import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

KAGGLE = os.path.join(os.path.dirname(__file__), "..", "nba_data", "kaggle_1947_present")
FIRST_SEASON = 2010
COLS = ["bpm", "obpm", "dbpm", "vorp"]


def main():
    adv = pd.read_csv(os.path.join(KAGGLE, "Advanced.csv"))
    adv = adv[(adv.lg == "NBA") & (adv.season >= FIRST_SEASON)].copy()
    adv["_combined"] = adv.team.astype(str).str.match(r"^\dTM$")
    adv = adv.sort_values("_combined", ascending=False).drop_duplicates(["player_id", "season"])

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT bbref_id, nba_player_id FROM player_id_map WHERE nba_player_id IS NOT NULL;")
    id_map = dict(cur.fetchall())
    adv["nba_id"] = adv.player_id.map(id_map)
    adv = adv.dropna(subset=["nba_id"])

    cur.execute("SELECT player_id, season, gp FROM player_season_stats WHERE season >= %s;", (FIRST_SEASON,))
    pss = pd.DataFrame(cur.fetchall(), columns=["nba_id", "season", "gp"])
    m = pss.merge(adv[["nba_id", "season", "g", *COLS]], on=["nba_id", "season"], how="left")
    linked = m.bpm.notna()
    gp_ok = ((m.gp - m.g).abs() <= 1)[linked]
    print(f"{len(m)} rows since {FIRST_SEASON - 1}-{str(FIRST_SEASON)[-2:]}: {linked.sum()} linked, "
          f"{(~linked).sum()} not (set to NULL); games agree on {gp_ok.mean():.1%} of linked rows")
    assert gp_ok.all(), "games played disagree: the id link is wrong somewhere"
    for r in m[~linked].itertuples():
        print(f"  no Basketball-Reference link: player_id {r.nba_id}, season {r.season}")

    for col in COLS:
        cur.execute(f"ALTER TABLE player_season_stats ADD COLUMN IF NOT EXISTS {col}_repro DOUBLE PRECISION;")
    # Keep the reproduction, all four columns together, the first time only
    # (a rerun must not copy the published values over it).
    cur.execute("SELECT count(*) FROM player_season_stats WHERE season >= %s AND bpm_repro IS NOT NULL;",
                (FIRST_SEASON,))
    if cur.fetchone()[0] == 0:
        cur.execute("""UPDATE player_season_stats
                       SET bpm_repro = bpm, obpm_repro = obpm, dbpm_repro = dbpm, vorp_repro = vorp
                       WHERE season >= %s;""", (FIRST_SEASON,))
        print(f"Copied the reproduction to *_repro for {cur.rowcount} rows")

    rows = [(int(r.nba_id), int(r.season), *[None if pd.isna(getattr(r, c)) else float(getattr(r, c)) for c in COLS])
            for r in m.itertuples()]
    psycopg2.extras.execute_values(cur, """
        UPDATE player_season_stats AS p SET bpm = v.bpm, obpm = v.obpm, dbpm = v.dbpm, vorp = v.vorp
        FROM (VALUES %s) AS v (player_id, season, bpm, obpm, dbpm, vorp)
        WHERE p.player_id = v.player_id AND p.season = v.season;""", rows,
        template="(%s, %s, %s::float, %s::float, %s::float, %s::float)")
    conn.commit()

    cur.execute("""SELECT player_name, bpm, vorp, bpm_repro, vorp_repro FROM player_season_stats
                   WHERE season = 2026 AND player_name IN ('Shai Gilgeous-Alexander', 'Nikola Jokić')""")
    for name, bpm, vorp, bpm_r, vorp_r in cur.fetchall():
        print(f"  2025-26 {name}: BPM {bpm} (was {bpm_r:.1f}), VORP {vorp} (was {vorp_r:.1f})")
    conn.close()


if __name__ == "__main__":
    main()
