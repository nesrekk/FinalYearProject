"""
build_player_profile_data.py
=============================
Three small tables for the player profile page (GET /player-profile/{id}),
all from the local Basketball-Reference export
(nba_data/kaggle_1947_present/), BAA/NBA only (ABA seasons and awards left
out, like the rest of the app):

  player_bio          one row per player: position, height, weight, birth
                      date, colleges, Hall of Fame flag, first/last season.
                      (Player Career Info.csv)
  player_awards       every award row: MVP, DPOY, ROY, Sixth Man, Most
                      Improved and Clutch Player of the Year voting (winner
                      flag, vote share and finish), All-NBA / All-Defense /
                      All-Rookie teams, and All-Star selections (detail
                      notes a selection he didn't play, a replacement named). Some early
                      winners (mostly 1950s-60s Rookie of the Year) have no
                      published vote count: winner, no share or finish.
                      (Player Award Shares.csv, End of Season Teams.csv,
                      All-Star Selections.csv)
  player_team_stints  a traded player's per-team games in a season, the rows
                      Basketball-Reference lists under its combined "2TM" /
                      "3TM" line. Only multi-team seasons are stored.
                      (Player Totals.csv)

Player ids are the NBA person ids from player_id_map (built by
load_kaggle_historical_seasons.py with the shared bref_nba_ids matcher), so
they join to player_season_stats. Players with no NBA id match are skipped
and counted. Team codes follow player_season_stats: from 2009-10 on
(NBA.com's codes) BRK/PHO/CHO become BKN/PHX/CHA; earlier seasons keep
Basketball-Reference's codes, as the pre-2010 season rows do.

Usage (after load_kaggle_historical_seasons.py):
    cd scripts && python3 build_player_profile_data.py
"""

import os

import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

KAGGLE = os.path.join(os.path.dirname(__file__), "..", "nba_data", "kaggle_1947_present")
LEAGUES = ["NBA", "BAA"]
TEAM_CODES = {"BRK": "BKN", "PHO": "PHX", "CHO": "CHA"}
NBA_CODES_FROM = 2010

VOTED_AWARDS = {
    "nba mvp": "MVP",
    "nba dpoy": "Defensive Player of the Year",
    "nba roy": "Rookie of the Year",
    "baa roy": "Rookie of the Year",
    "nba smoy": "Sixth Man of the Year",
    "nba mip": "Most Improved Player",
    "nba clutch_poy": "Clutch Player of the Year",
}


def team_code(team, season):
    return TEAM_CODES.get(team, team) if season >= NBA_CODES_FROM else team


def load_csv(name):
    return pd.read_csv(os.path.join(KAGGLE, name))


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT bbref_id, nba_player_id FROM player_id_map WHERE nba_player_id IS NOT NULL;")
    nba_id = dict(cur.fetchall())

    # ── Bio ──
    career = load_csv("Player Career Info.csv")
    totals = load_csv("Player Totals.csv")
    totals = totals[totals.lg.isin(LEAGUES)]
    span = totals.groupby("player_id").season.agg(["min", "max"])
    bio, bio_skipped = [], 0
    for r in career.itertuples(index=False):
        pid = nba_id.get(r.player_id)
        if pid is None or r.player_id not in span.index:
            bio_skipped += r.player_id in span.index
            continue
        bio.append((
            int(pid), r.player_id, r.player,
            None if pd.isna(r.pos) else r.pos,
            None if pd.isna(r.ht_in_in) else int(r.ht_in_in),
            None if pd.isna(r.wt) else int(r.wt),
            None if pd.isna(r.birth_date) else r.birth_date,
            None if pd.isna(r.colleges) else r.colleges,
            bool(r.hof) if not pd.isna(r.hof) else False,
            int(span.loc[r.player_id, "min"]), int(span.loc[r.player_id, "max"]),
        ))

    # ── Awards ──
    awards, aw_skipped = [], 0
    shares = load_csv("Player Award Shares.csv")
    shares = shares[shares.award.isin(VOTED_AWARDS) & shares.player_id.notna()].copy()
    shares["finish"] = shares.groupby(["season", "award"]).share.rank(ascending=False, method="min")
    for r in shares.itertuples(index=False):
        pid = nba_id.get(r.player_id)
        if pid is None:
            aw_skipped += 1
            continue
        awards.append((int(r.season), int(pid), r.player_id, r.player, VOTED_AWARDS[r.award], None,
                       bool(r.winner), None if pd.isna(r.share) else float(r.share),
                       None if pd.isna(r.finish) else int(r.finish)))

    teams = load_csv("End of Season Teams.csv")
    teams = teams[teams.lg.isin(LEAGUES)]
    for r in teams.itertuples(index=False):
        pid = nba_id.get(r.player_id)
        if pid is None:
            aw_skipped += 1
            continue
        awards.append((int(r.season), int(pid), r.player_id, r.player, r.type, f"{r.number_tm} team",
                       True, None, None))

    stars = load_csv("All-Star Selections.csv")
    stars = stars[stars.lg == "NBA"]
    for r in stars.itertuples(index=False):
        pid = nba_id.get(r.player_id)
        if pid is None:
            aw_skipped += 1
            continue
        awards.append((int(r.season), int(pid), r.player_id, r.player, "All-Star",
                       "selected, did not play (replacement named)" if r.replaced else None, True, None, None))

    # ── Team stints (multi-team seasons only) ──
    multi = totals.team.astype(str).str.match(r"^\dTM$")
    multi_keys = set(zip(totals[multi].player_id, totals[multi].season))
    per_team = totals[~multi & pd.Series([k in multi_keys for k in zip(totals.player_id, totals.season)],
                                         index=totals.index)]
    stints, st_skipped = [], 0
    for (bref, season), g in per_team.groupby(["player_id", "season"], sort=False):
        pid = nba_id.get(bref)
        if pid is None:
            st_skipped += 1
            continue
        # The CSV lists a season's teams in the order he played for them.
        for order, r in enumerate(g.itertuples(index=False), start=1):
            stints.append((int(season), int(pid), order, team_code(r.team, int(season)), int(r.g),
                           None if pd.isna(r.mp) else int(r.mp), None if pd.isna(r.pts) else int(r.pts)))

    cur.execute("DROP TABLE IF EXISTS player_bio, player_awards, player_team_stints;")
    cur.execute("""
        CREATE TABLE player_bio (
            player_id INTEGER PRIMARY KEY, bref_id TEXT NOT NULL, player_name TEXT NOT NULL,
            position TEXT, height_in INTEGER, weight_lb INTEGER, birth_date DATE, colleges TEXT,
            hall_of_fame BOOLEAN NOT NULL, first_season INTEGER NOT NULL, last_season INTEGER NOT NULL
        );
        CREATE TABLE player_awards (
            season INTEGER NOT NULL, player_id INTEGER NOT NULL, bref_id TEXT NOT NULL, player_name TEXT NOT NULL,
            award TEXT NOT NULL, detail TEXT, winner BOOLEAN NOT NULL, vote_share REAL, finish INTEGER
        );
        CREATE INDEX ON player_awards (player_id);
        CREATE TABLE player_team_stints (
            season INTEGER NOT NULL, player_id INTEGER NOT NULL, stint INTEGER NOT NULL, team TEXT NOT NULL,
            gp INTEGER NOT NULL, minutes INTEGER, pts INTEGER,
            PRIMARY KEY (player_id, season, stint)
        );
    """)
    psycopg2.extras.execute_values(cur, "INSERT INTO player_bio VALUES %s;", bio)
    psycopg2.extras.execute_values(cur, "INSERT INTO player_awards VALUES %s;", awards)
    psycopg2.extras.execute_values(cur, "INSERT INTO player_team_stints VALUES %s;", stints)

    # Sniff tests against well-known facts before committing.
    def count(pid, award, winner=True, detail=None):
        cur.execute("SELECT count(*) FROM player_awards WHERE player_id = %s AND award = %s AND winner = %s"
                    + (" AND detail = %s" if detail else ""),
                    (pid, award, winner, detail) if detail else (pid, award, winner))
        return cur.fetchone()[0]
    jordan, jokic, bird = 893, 203999, 1449
    assert count(jordan, "MVP") == 5 and count(jordan, "Defensive Player of the Year") == 1
    assert count(jordan, "All-NBA") == 11 and count(jordan, "All-NBA", detail="1st team") == 10
    assert count(jordan, "All-Star") == 14 and count(jordan, "Rookie of the Year") == 1
    assert count(jokic, "MVP") == 3 and count(bird, "MVP") == 3
    cur.execute("SELECT team, gp FROM player_team_stints WHERE player_id = 201935 AND season = 2022 ORDER BY stint;")
    assert cur.fetchall() == [("BKN", 44), ("PHI", 21)]  # Harden 2021-22
    cur.execute("SELECT team, gp FROM player_team_stints WHERE player_id = 201142 AND season = 2023 ORDER BY stint;")
    assert cur.fetchall() == [("BKN", 39), ("PHX", 8)]  # Durant 2022-23
    conn.commit()

    print(f"player_bio: {len(bio)} players ({bio_skipped} BAA/NBA players without an NBA id skipped)")
    print(f"player_awards: {len(awards)} rows ({aw_skipped} rows for players without an NBA id skipped)")
    print(f"player_team_stints: {len(stints)} team rows ({st_skipped} multi-team seasons without an NBA id skipped)")
    conn.close()


if __name__ == "__main__":
    main()
