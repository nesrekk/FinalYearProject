"""
fetch_spacing_data.py
=======================
Real data for the Gravity Index & Spacing Lab (build_gravity_index.py),
fetched league-wide — one real call per filter per season rather than one
per player (the owner's plan assumed PlayerDashPtShots per player, ~450
calls a season; LeagueDashPlayerPtShot returns the same real tracking
splits for every player at once, verified live before writing this):

  * LeagueDashPlayerPtShot, general_range='Catch and Shoot' — real
    catch-and-shoot 3PM/3PA per player.
  * LeagueDashPlayerPtShot, close_def_dist_range in the NBA's own 4 bands
    (0-2 ft very tight, 2-4 ft tight, 4-6 ft open, 6+ ft wide open) —
    real 3PA per player by closest-defender distance.
  * LeagueDashLineups, group_quantity=5, Advanced, Totals — real 5-man
    lineups: possessions, off/def/net rating. The endpoint returns at most
    2,000 lineups per season, sorted by minutes; checked live for 2013-14
    and 2024-25 that the 2,000th still has only ~20 possessions, so every
    lineup with 100+ possessions (what the validation uses) is included.

Mid-season-traded players can come back as one row per stint (seen live
on LeagueDashPtDefend); stints are summed here (all stored fields are
counts, so summing is exact).

Tracking data starts in 2013-14 (season int 2014).

Usage:
    cd scripts && python3 fetch_spacing_data.py
"""

import time

import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

SEASON_START = 2014
SEASON_END = 2026
DEF_RANGES = {
    "def_0_2": "0-2 Feet - Very Tight",
    "def_2_4": "2-4 Feet - Tight",
    "def_4_6": "4-6 Feet - Open",
    "def_6_plus": "6+ Feet - Wide Open",
}


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def call(fn, retries=3):
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            print(f"    attempt {attempt + 1} failed — {exc}")
            time.sleep(5 * (attempt + 1))
    return None


def ensure_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS player_shot_tracking (
            season INT NOT NULL,
            player_id BIGINT NOT NULL,
            player_name TEXT NOT NULL,
            team_abbreviation TEXT,
            cs_fg3m INT, cs_fg3a INT,
            fg3a_def_0_2 INT, fg3a_def_2_4 INT, fg3a_def_4_6 INT, fg3a_def_6_plus INT,
            PRIMARY KEY (season, player_id)
        );
        CREATE TABLE IF NOT EXISTS lineup_stats (
            season INT NOT NULL,
            group_id TEXT NOT NULL,
            team_abbreviation TEXT,
            player_ids BIGINT[] NOT NULL,
            group_name TEXT,
            gp INT, minutes DOUBLE PRECISION, poss INT,
            off_rating DOUBLE PRECISION, def_rating DOUBLE PRECISION, net_rating DOUBLE PRECISION,
            PRIMARY KEY (season, group_id)
        );
    """)


def main():
    from nba_api.stats.endpoints import leaguedashlineups, leaguedashplayerptshot

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    ensure_tables(cur)
    conn.commit()

    for season in range(SEASON_START, SEASON_END + 1):
        s = label(season)
        cs = call(lambda: leaguedashplayerptshot.LeagueDashPlayerPtShot(
            season=s, general_range_nullable="Catch and Shoot", per_mode_simple="Totals",
            season_type_all_star="Regular Season", timeout=60).get_data_frames()[0])
        time.sleep(1.0)
        if cs is None or cs.empty:
            print(f"  {season}: no real catch-and-shoot data, skipping season.")
            continue
        base = cs.groupby("PLAYER_ID").agg(
            player_name=("PLAYER_NAME", "last"), team=("PLAYER_LAST_TEAM_ABBREVIATION", "last"),
            cs_fg3m=("FG3M", "sum"), cs_fg3a=("FG3A", "sum"))

        ok = True
        for col, rng in DEF_RANGES.items():
            d = call(lambda: leaguedashplayerptshot.LeagueDashPlayerPtShot(
                season=s, close_def_dist_range_nullable=rng, per_mode_simple="Totals",
                season_type_all_star="Regular Season", timeout=60).get_data_frames()[0])
            time.sleep(1.0)
            if d is None:
                ok = False
                break
            base = base.join(d.groupby("PLAYER_ID")["FG3A"].sum().rename(col), how="outer")
            names = d.groupby("PLAYER_ID").agg(n=("PLAYER_NAME", "last"), t=("PLAYER_LAST_TEAM_ABBREVIATION", "last"))
            base["player_name"] = base["player_name"].fillna(names["n"])
            base["team"] = base["team"].fillna(names["t"])
        if not ok:
            print(f"  {season}: a defender-distance call failed, skipping season (nothing written).")
            continue
        base = base.fillna({c: 0 for c in ["cs_fg3m", "cs_fg3a", *DEF_RANGES]})

        lu = call(lambda: leaguedashlineups.LeagueDashLineups(
            season=s, group_quantity=5, measure_type_detailed_defense="Advanced", per_mode_detailed="Totals",
            season_type_all_star="Regular Season", timeout=90).get_data_frames()[0])
        time.sleep(1.0)
        if lu is None or lu.empty:
            print(f"  {season}: no real lineup data, skipping season (nothing written).")
            continue

        cur.execute("DELETE FROM player_shot_tracking WHERE season = %s;", (season,))
        psycopg2.extras.execute_values(cur, "INSERT INTO player_shot_tracking VALUES %s", [
            (season, int(pid), r.player_name, None if pd.isna(r.team) else r.team,
             int(r.cs_fg3m), int(r.cs_fg3a), int(r.def_0_2), int(r.def_2_4), int(r.def_4_6), int(r.def_6_plus))
            for pid, r in base.iterrows()
        ])
        cur.execute("DELETE FROM lineup_stats WHERE season = %s;", (season,))
        psycopg2.extras.execute_values(cur, "INSERT INTO lineup_stats VALUES %s", [
            (season, r.GROUP_ID, r.TEAM_ABBREVIATION,
             [int(x) for x in r.GROUP_ID.strip("-").split("-")], r.GROUP_NAME,
             int(r.GP), float(r.MIN), int(r.POSS), float(r.OFF_RATING), float(r.DEF_RATING), float(r.NET_RATING))
            for r in lu.itertuples()
        ])
        conn.commit()
        print(f"  {season}: {len(base)} real players tracked, {len(lu)} real lineups "
              f"({int((lu['POSS'] >= 100).sum())} with 100+ poss; smallest returned {int(lu['POSS'].min())} poss)")

    conn.close()
    print("\n✅ Done.")


if __name__ == "__main__":
    main()
