"""
build_first_nba_season.py
==========================
Each player's first NBA season, for deciding who is a rookie.

The ROY model and endpoint used to call a player a rookie in his first
season in player_season_stats. That table misses many short early stints
(its 2009-10+ rows come from nba_api's season pool), so 240 "rookie"
player-seasons 2010-11 to 2025-26 had played in the NBA before, 53 of them
in 2025-26 (Bronny James, Daniss Jenkins, Alondes Williams...).

Here the first season is the earlier of:
  * the first NBA or BAA season on Basketball-Reference (the Kaggle export
    in nba_data/kaggle_1947_present/, linked through player_id_map), and
  * the first season in player_season_stats (covers players the map can't
    link).
ABA seasons don't count (an ABA veteran's first NBA season is still his
first NBA season).

Writes player_first_season (player_id, first_season, first_season_bref,
first_season_table). Rerun after load_kaggle_historical_seasons.py or any
new season load; then rebuild the ROY model (build_dpoy_roy_models.py ->
backtest_models.py -> calibrate_award_chances.py).

Usage:
    cd scripts && python3 build_first_nba_season.py
"""

import os

import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

KAGGLE = os.path.join(os.path.dirname(__file__), "..", "nba_data", "kaggle_1947_present")


def main():
    totals = pd.read_csv(os.path.join(KAGGLE, "Player Totals.csv"), usecols=["season", "lg", "player_id"])
    bref_first = totals[totals.lg.isin(["NBA", "BAA"])].groupby("player_id").season.min()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT bbref_id, nba_player_id FROM player_id_map WHERE nba_player_id IS NOT NULL;")
    nba_to_bref = {int(n): b for b, n in cur.fetchall()}
    cur.execute("SELECT player_id, MIN(season) FROM player_season_stats GROUP BY player_id;")
    table_first = dict(cur.fetchall())

    rows = []
    for pid, t_first in table_first.items():
        b = nba_to_bref.get(int(pid))
        b_first = int(bref_first[b]) if b in bref_first.index else None
        first = min(t_first, b_first) if b_first is not None else t_first
        rows.append((int(pid), int(first), b_first, int(t_first)))

    earlier = [r for r in rows if r[2] is not None and r[2] < r[3]]
    later = [r for r in rows if r[2] is not None and r[2] > r[3]]
    print(f"{len(rows)} players; {sum(r[2] is not None for r in rows)} linked to Basketball-Reference")
    print(f"  first NBA season earlier on Basketball-Reference than in player_season_stats: {len(earlier)}")
    print(f"  later on Basketball-Reference (table kept; should be ~0): {len(later)}")
    for r in later[:10]:
        print(f"    player_id {r[0]}: table {r[3]}, Basketball-Reference {r[2]}")

    cur.execute("DROP TABLE IF EXISTS player_first_season;")
    cur.execute("""
        CREATE TABLE player_first_season (
            player_id BIGINT PRIMARY KEY,
            first_season INT NOT NULL,        -- earlier of the two below
            first_season_bref INT,            -- first NBA/BAA season on Basketball-Reference (NULL if unlinked)
            first_season_table INT NOT NULL   -- first season in player_season_stats
        );""")
    psycopg2.extras.execute_values(
        cur, "INSERT INTO player_first_season (player_id, first_season, first_season_bref, first_season_table) VALUES %s",
        rows)
    conn.commit()

    cur.execute("""SELECT p.player_name, f.first_season, f.first_season_table
                   FROM player_first_season f JOIN player_season_stats p
                     ON p.player_id = f.player_id AND p.season = f.first_season_table
                   WHERE p.player_name IN ('Bronny James', 'Victor Wembanyama', 'Alondes Williams', 'LeBron James')
                   ORDER BY 1;""")
    for name, first, t_first in cur.fetchall():
        print(f"  {name}: first NBA season {first} (first in table {t_first})")
    conn.close()


if __name__ == "__main__":
    main()
