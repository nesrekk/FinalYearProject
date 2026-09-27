"""
load_draft_history_bref.py
===========================
Fills draft_history from Basketball-Reference's draft history (the local
Kaggle export, nba_data/kaggle_1947_present/Draft Pick History.csv, BAA
1947-49 + NBA 1950-2025) when stats.nba.com is unreachable.
fetch_draft_history.py (the NBA's own DraftHistory endpoint) remains the
preferred source: on 2026-09-26 stats.nba.com timed out on every request
from this machine (full history, a single year, and an unrelated endpoint).

Same table and columns as fetch_draft_history.py, so the Draft Value Guide
endpoints work unchanged. What differs, and how:

  player_id  The NBA's person id from player_id_map (built by
             load_kaggle_historical_seasons.py with bref_nba_ids.py, the
             matcher both scripts share: names, first-name forms, checked
             nicknames, season overlap, NBA id eras, one person per id), for
             picks Basketball-Reference shows playing in the BAA/NBA.
             Picks who never played in the NBA have no NBA id at all; they
             get a placeholder id -(draft_year * 1000 + order within that
             draft), which can't collide with a real id, so they still count as zero
             career value instead of disappearing. Players who did play but
             couldn't be matched safely get the same kind of placeholder and
             are reported.
  organization / organization_type
             College from Basketball-Reference (type 'College/University');
             NULL when none is listed (international, high school, G League:
             Basketball-Reference's file doesn't say which).
  team_abbreviation
             Basketball-Reference's code for the drafting team, with the
             three current teams whose codes differ mapped to NBA's
             (BRK->BKN, PHO->PHX, CHO->CHA); historical codes are kept as is.
  overall_pick / round_pick
             Overall pick as listed; NULL for the 428 early picks (1947-1960s
             territorial and unnumbered picks) the file has no number for,
             which the Draft Value endpoints already skip. Round pick is the
             order within its round.
  draft_type Always 'Draft' (the file has no undrafted entries).
  nba_id_status
             'matched', 'never_played' (placeholder id, zero NBA career) or
             'played_unmatched' (played, but no NBA id could be matched
             safely; placeholder id, so it must be left OUT of career-value
             stats rather than counted as zero). Rows from the NBA's own
             endpoint (fetch_draft_history.py) get 'nba_api'.

Also builds draft_pick_outcomes (same player_id + draft_year keys), the
career outcome of every pick from Basketball-Reference's own tables, so
Draft Value doesn't depend on player_season_stats (which is missing some
pre-2010 players): Win Shares in the first five NBA seasons after the
draft, career Win Shares, NBA seasons and games, and NBA All-Star
selections. Zero for picks who never played.

Usage (after load_kaggle_historical_seasons.py, which builds player_id_map):
    cd scripts && python3 load_draft_history_bref.py
"""

import os

import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

KAGGLE = os.path.join(os.path.dirname(__file__), "..", "nba_data", "kaggle_1947_present")
TEAM_CODES = {"BRK": "BKN", "PHO": "PHX", "CHO": "CHA"}


def main():
    draft = pd.read_csv(os.path.join(KAGGLE, "Draft Pick History.csv"))
    draft = draft[draft.lg.isin(["NBA", "BAA"])].copy()

    adv = pd.read_csv(os.path.join(KAGGLE, "Advanced.csv"))
    adv = adv[adv.lg.isin(["NBA", "BAA"])]
    played_bref = set(adv.player_id)
    # One row per player-season: a traded player's combined "2TM"/"3TM" row,
    # never the team rows on top of it.
    is_multi = adv.team.astype(str).str.match(r"^\dTM$")
    multi_keys = set(zip(adv[is_multi].player_id, adv[is_multi].season))
    keep = is_multi | ~pd.Series([k in multi_keys for k in zip(adv.player_id, adv.season)], index=adv.index)
    seasons = adv[keep].groupby(["player_id", "season"], as_index=False)[["ws", "g"]].sum()
    seasons_by = {pid: g for pid, g in seasons.groupby("player_id")}
    allstar = pd.read_csv(os.path.join(KAGGLE, "All-Star Selections.csv"))
    allstar_n = allstar[allstar.lg == "NBA"].groupby("player_id").size().to_dict()

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    # NBA ids from this project's player_id_map, built by
    # load_kaggle_historical_seasons.py with the shared bref_nba_ids
    # matcher, so draft picks and season stats agree on who is who.
    cur.execute("SELECT bbref_id, nba_player_id, match_method FROM player_id_map;")
    id_map = {bid: (pid, how) for bid, pid, how in cur.fetchall()}
    if not id_map:
        raise SystemExit("player_id_map is empty: run load_kaggle_historical_seasons.py first.")

    outcomes = []
    rows, stats, unmatched, how_counts = [], {"matched": 0, "never_played": 0, "played_unmatched": 0}, [], {}
    draft["round_pick"] = draft.groupby(["season", "round"]).overall_pick.rank(method="first").astype("Int64")
    draft = draft.sort_values(["season", "overall_pick"], na_position="last")
    draft["seq"] = draft.groupby("season").cumcount() + 1
    matches = {bid: id_map.get(bid, (None, "not in player_id_map")) for bid in played_bref}

    for r in draft.itertuples(index=False):
        draft_year = int(r.season)
        pick = None if pd.isna(r.overall_pick) else int(r.overall_pick)
        placeholder = -(draft_year * 1000 + int(r.seq))
        pid, status = None, "never_played"
        if r.player_id in played_bref:
            pid, how = matches[r.player_id]
            if pid is None:
                status = "played_unmatched"
                stats["played_unmatched"] += 1
                unmatched.append(f"{draft_year} #{pick or '-'} {r.player} ({how})")
            else:
                status = "matched"
                stats["matched"] += 1
                how_counts[how] = how_counts.get(how, 0) + 1
        else:
            stats["never_played"] += 1
        g = seasons_by.get(r.player_id)
        if g is None:
            outcomes.append((pid if pid is not None else placeholder, draft_year, r.player_id, 0.0, 0.0, 0, 0, 0))
        else:
            first5 = g[g.season.between(draft_year + 1, draft_year + 5)]
            outcomes.append((pid if pid is not None else placeholder, draft_year, r.player_id,
                             round(float(first5.ws.sum()), 1), round(float(g.ws.sum()), 1),
                             int((g.g > 0).sum()), int(g.g.sum()), int(allstar_n.get(r.player_id, 0))))
        college = r.college if isinstance(r.college, str) and r.college.strip() else None
        rows.append((
            pid if pid is not None else placeholder, r.player, draft_year, draft_year + 1,
            int(r.round) if not pd.isna(r.round) else None,
            None if pd.isna(r.round_pick) else int(r.round_pick), pick, "Draft",
            TEAM_CODES.get(r.tm, r.tm), college, "College/University" if college else None, status,
        ))

    print(f"{len(rows)} picks {draft.season.min()}-{draft.season.max()}: {stats}")
    print("How matched:", how_counts)
    print("Played in the NBA but not safely matched (placeholder id):")
    for u in unmatched:
        print("  ", u)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS draft_history (
            player_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            draft_year INTEGER NOT NULL,
            rookie_season_int INTEGER NOT NULL,
            round_number INTEGER,
            round_pick INTEGER,
            overall_pick INTEGER,
            draft_type TEXT,
            team_abbreviation TEXT,
            organization TEXT,
            organization_type TEXT,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (player_id, draft_year)
        );
    """)
    cur.execute("ALTER TABLE draft_history ADD COLUMN IF NOT EXISTS nba_id_status TEXT NOT NULL DEFAULT 'nba_api';")
    cur.execute("DELETE FROM draft_history;")
    psycopg2.extras.execute_values(cur, """
        INSERT INTO draft_history
            (player_id, player_name, draft_year, rookie_season_int, round_number, round_pick,
             overall_pick, draft_type, team_abbreviation, organization, organization_type, nba_id_status)
        VALUES %s;""", rows)
    cur.execute("DROP TABLE IF EXISTS draft_pick_outcomes;")
    cur.execute("""
        CREATE TABLE draft_pick_outcomes (
            player_id INTEGER NOT NULL,
            draft_year INTEGER NOT NULL,
            bref_id TEXT NOT NULL,
            ws_first5 REAL NOT NULL,
            ws_career REAL NOT NULL,
            nba_seasons INTEGER NOT NULL,
            nba_games INTEGER NOT NULL,
            all_star_selections INTEGER NOT NULL,
            PRIMARY KEY (player_id, draft_year)
        );
    """)
    psycopg2.extras.execute_values(cur, "INSERT INTO draft_pick_outcomes VALUES %s;", outcomes)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
