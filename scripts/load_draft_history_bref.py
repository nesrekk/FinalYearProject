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

  player_id  The NBA's person id, matched by normalised name against
             nba_api's bundled player list (5,103 NBA players, offline), and
             only for picks Basketball-Reference shows playing in the
             BAA/NBA. A match is accepted when the name is unique and no
             evidence contradicts it (the candidate's first season in
             player_season_stats must not be before the draft, and must be
             within two seasons of Basketball-Reference's first season).
             Duplicate names are settled by season overlap, then by NBA id
             era (ids 200000-599999 = entered 2006-07 or later; verified on
             every player in player_season_stats) when the careers fall
             clearly on opposite sides of that line.
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

Usage:
    cd scripts && python3 load_draft_history_bref.py
"""

import os
import re
import unicodedata

import pandas as pd
import psycopg2
import psycopg2.extras
from nba_api.stats.static import players as nba_players

from db_config import DB_CONFIG

KAGGLE = os.path.join(os.path.dirname(__file__), "..", "nba_data", "kaggle_1947_present")
TEAM_CODES = {"BRK": "BKN", "PHO": "PHX", "CHO": "CHA"}


def norm(name, keep_suffix=False):
    name = name.replace("ı", "i").replace("ß", "ss")  # letters NFKD doesn't decompose
    name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    name = re.sub(r"[.']", "", name.lower()).replace("-", " ")
    if not keep_suffix:
        name = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", name)
    return " ".join(name.split())


# First names that are the same person's formal/short form (checked against
# the unmatched list, not a general nickname dictionary).
FIRST_NAME_FORMS = [{"mike", "michael"}, {"steve", "steven"}, {"ron", "ronald"}, {"mel", "melvin"},
                    {"clar", "clarence"}, {"pearl", "dwayne"}, {"flip", "ronald"}, {"wang", "wang"}]


def first_names_compatible(a, b):
    if a == b or a.startswith(b) or b.startswith(a):
        return True
    return any(a in f and b in f for f in FIRST_NAME_FORMS)


def main():
    draft = pd.read_csv(os.path.join(KAGGLE, "Draft Pick History.csv"))
    draft = draft[draft.lg.isin(["NBA", "BAA"])].copy()
    career = pd.read_csv(os.path.join(KAGGLE, "Player Career Info.csv"))
    bref_from = dict(zip(career.player_id, career["from"]))

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

    nba_list = [(p["id"], p["full_name"]) for p in nba_players.get_players()]

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    # This project's own bbref -> NBA id map (load_kaggle_historical_seasons.py).
    # Ids the map gives to two different Basketball-Reference players (e.g.
    # the 1964 and 2004 Luke Jacksons) are not trusted.
    cur.execute("""SELECT bbref_id, nba_player_id FROM player_id_map
                   WHERE nba_player_id IN (SELECT nba_player_id FROM player_id_map WHERE nba_player_id IS NOT NULL
                                           GROUP BY 1 HAVING count(*) = 1);""")
    id_map = dict(cur.fetchall())
    cur.execute("""SELECT nba_player_id FROM player_id_map WHERE nba_player_id IS NOT NULL
                   GROUP BY 1 HAVING count(*) > 1;""")
    shared_ids = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT player_id, player_name, min(season), max(season) FROM player_season_stats GROUP BY 1, 2;")
    pss_span = {}
    for pid, name, lo, hi in cur.fetchall():
        pss_span[pid] = (lo, hi)
        nba_list.append((pid, name))  # current rookies missing from nba_api's bundled list
    nba_list = list({pid: name for pid, name in nba_list}.items())
    by_full = {}
    by_last = {}
    for pid, name in nba_list:
        by_full.setdefault(norm(name, keep_suffix=True), set()).add(pid)
        parts = norm(name).split()
        if parts:
            by_last.setdefault(parts[-1], set()).add((pid, parts[0]))
    names_of = dict(nba_list)
    bref_to = dict(zip(career.player_id, career["to"]))

    def fits_career(nba_id, bref_id):
        """True/False from season evidence, None when there is none."""
        span = pss_span.get(nba_id)
        lo, hi = bref_from.get(bref_id), bref_to.get(bref_id)
        if span is None or lo is None or pd.isna(lo):
            return None
        return span[0] >= lo - 1 and span[1] <= hi + 1

    def match(bref_id, name):
        if bref_id in id_map:
            return id_map[bref_id], "project_map"
        for key, how in ((norm(name, keep_suffix=True), "exact_name"), (norm(name), "name_without_suffix")):
            cands = by_full.get(key, set()) if how == "exact_name" else {
                pid for pid, n in nba_list if norm(n) == key}
            if not cands:
                continue
            cands = cands - shared_ids  # their season rows mix two players, so no evidence
            if not cands:
                return None, "only NBA id for this name is shared by two players"
            checked = {c: fits_career(c, bref_id) for c in cands}
            good = [c for c, ok in checked.items() if ok]
            if len(good) == 1:
                return good[0], how + "+seasons"
            if len(cands) == 1 and checked[next(iter(cands))] is None:
                return next(iter(cands)), how
            if len(cands) > 1:
                # Tie-break by ID era: NBA ids 200000-599999 were issued to
                # players entering 2006-07 or later (checked against every
                # player in player_season_stats: no exception; the 600000s are
                # a later block for old-era players). Only used when the
                # Basketball-Reference career is clearly on one side of that.
                lo, hi = bref_from.get(bref_id), bref_to.get(bref_id)
                pool = [c for c in cands if checked[c] is not False]
                if lo is not None and not pd.isna(lo):
                    if lo >= 2008:
                        pool = [c for c in pool if 200000 <= c < 600000]
                    elif hi <= 2005:
                        pool = [c for c in pool if not 200000 <= c < 600000]
                    else:
                        pool = []
                    if len(pool) == 1:
                        return pool[0], how + "+id_era"
                return None, f"{len(cands)} same-name players, seasons don't settle it"
        parts = norm(name).split()
        if len(parts) >= 2:
            cands = {pid for pid, first in by_last.get(parts[-1], ())
                     if first_names_compatible(first, parts[0])}
            cands = {c for c in cands - shared_ids if fits_career(c, bref_id) is not False}
            if len(cands) == 1:
                return next(iter(cands)), "first_name_form"
        return None, "no NBA name match"

    outcomes = []
    rows, stats, unmatched, how_counts = [], {"matched": 0, "never_played": 0, "played_unmatched": 0}, [], {}
    draft["round_pick"] = draft.groupby(["season", "round"]).overall_pick.rank(method="first").astype("Int64")
    draft = draft.sort_values(["season", "overall_pick"], na_position="last")
    draft["seq"] = draft.groupby("season").cumcount() + 1
    # Match each Basketball-Reference player once, then allow one person per
    # NBA id: if two players land on the same id (2006 and 2007 Marcus
    # Williams did), the strongest method keeps it and the other is unmatched.
    strength = ["project_map", "exact_name+seasons", "name_without_suffix+seasons", "exact_name+id_era",
                "name_without_suffix+id_era", "exact_name", "name_without_suffix", "first_name_form"]
    matches = {}
    for bref_id, name in draft[draft.player_id.isin(played_bref)][["player_id", "player"]].drop_duplicates("player_id").itertuples(index=False):
        matches[bref_id] = match(bref_id, name)
    holders = {}
    for bref_id, (pid, how) in matches.items():
        if pid is not None:
            holders.setdefault(pid, []).append((strength.index(how), bref_id))
    for pid, hs in holders.items():
        if len(hs) > 1:
            hs.sort()
            for _, loser in hs[1:]:
                matches[loser] = (None, f"NBA id {pid} already matched to {hs[0][1]} by a stronger rule")

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
                if how == "first_name_form":
                    print(f"  name-form match: {r.player} -> {names_of[pid]} ({pid})")
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
