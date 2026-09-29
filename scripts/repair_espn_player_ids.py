"""
repair_espn_player_ids.py
==========================
Puts the right NBA id back on ESPN play-by-play events that fetch_pbp_espn.py
gave to the wrong player.

How it happened: the fetch matched ESPN's player name to player_season_stats
for that season, exact first, then fuzzy (rapidfuzz WRatio >= 90), and stored
the matched name in place of ESPN's. A player with no row that season fell
through to the fuzzy step and could land on someone else with the same last
name: Keon Johnson (Nets 2023-24, no season row) became Keldon Johnson, and
Jalen McDaniels (Wizards 2024-25) became Jaden McDaniels. The event text
keeps ESPN's own wording, so the two disagree.

Rule, per (game, team, stored id): if none of those events' descriptions
contain the stored name, and exactly one other player name from
player_season_stats (any season, a name only one player has had) appears in
every one of them and is a fuzzy match for the stored name (WRatio >= 90,
the fetch's own floor), the events belong to that player. Single events
where ESPN's player tag and text disagree (about 65, usually two teammates)
are left alone: one of the two is wrong and nothing says which.

The fetch now tries an exact name in any season before fuzzy matching, so a
re-fetch won't repeat this.

Usage:
    cd scripts && python3 repair_espn_player_ids.py           # report only
    cd scripts && python3 repair_espn_player_ids.py --apply   # update pbp_events
Rerun everything built on the ESPN play-by-play afterwards (README, Known
real gaps, lists the order).
"""

import re
import sys
import unicodedata
from collections import defaultdict

import pandas as pd
import psycopg2
from rapidfuzz import fuzz

from db_config import DB_CONFIG

FUZZY_FLOOR = 90  # fetch_pbp_espn.NAME_MATCH_FLOOR


def fold(s):
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[.'`]", "", s)).strip()


def unique_names(cur):
    """Folded name -> (id, name) for names only one player in player_season_stats has had."""
    cur.execute("SELECT DISTINCT player_id, player_name FROM player_season_stats WHERE player_name IS NOT NULL;")
    ids, shown = defaultdict(set), {}
    for pid, name in cur.fetchall():
        ids[fold(name)].add(int(pid))
        shown[fold(name)] = name
    return {n: (next(iter(p)), shown[n]) for n, p in ids.items() if len(p) == 1}


def find(conn):
    """One row per event to fix: id, game_id, team, old/new person_id and name."""
    ev = pd.read_sql_query(
        """SELECT e.id, e.game_id, e.team_tricode, e.person_id, e.player_name, e.description
           FROM pbp_events e JOIN pbp_games g ON g.game_id = e.game_id
           WHERE g.source = 'espn' AND e.person_id IS NOT NULL;""", conn)
    ev["pn"] = ev.player_name.map(fold)
    ev["dn"] = ev.description.map(fold)
    ev["hit"] = [p in d for p, d in zip(ev.pn, ev.dn)]
    names = unique_names(conn.cursor())
    fixes = []
    for (gid, team, pid), g in ev.groupby(["game_id", "team_tricode", "person_id"]):
        if g.hit.any():
            continue
        stored = g.pn.iloc[0]
        cand = [n for n in names if n != stored and fuzz.WRatio(n, stored) >= FUZZY_FLOOR
                and all(n in d for d in g.dn)]
        if len(cand) != 1:
            continue
        new_id, new_name = names[cand[0]]
        if new_id == pid:
            continue
        for eid in g.id:
            fixes.append((int(eid), gid, team, int(pid), g.player_name.iloc[0], new_id, new_name))
    return pd.DataFrame(fixes, columns=["id", "game_id", "team", "old_id", "old_name", "new_id", "new_name"])


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    fixes = find(conn)
    if fixes.empty:
        print("nothing to fix")
        return
    summary = fixes.groupby(["old_name", "new_name", "team", "old_id", "new_id"]).agg(
        events=("id", "size"), games=("game_id", "nunique")).reset_index()
    print(summary.to_string(index=False))
    if "--apply" not in sys.argv:
        print(f"{len(fixes)} events would change; rerun with --apply to write them")
        return
    cur = conn.cursor()
    for r in fixes.itertuples(index=False):
        cur.execute("UPDATE pbp_events SET person_id = %s, player_name = %s WHERE id = %s AND person_id = %s;",
                    (r.new_id, r.new_name, r.id, r.old_id))
        assert cur.rowcount == 1, r
    conn.commit()
    print(f"{len(fixes)} events updated")


if __name__ == "__main__":
    main()
