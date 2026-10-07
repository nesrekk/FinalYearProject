"""
build_team_zone_mix.py
=======================
Every team's shot mix per regular season, 1996-97 to 2025-26, for its own
shots and for its opponents' shots against it: attempts and makes in each of
the five zones of api/shots_lib.classify_zone() (the same classifier as
league_zone_mix and every player shot feature, so team and league shares are
directly comparable).

player_shots has no team column, so each shot's team is worked out:
  * a game's two teams come from team_game_fatigue from 2009-10 on (exact);
    before that, the two teams with the most single-team shooters in the game
    (per player_season_stats), if they hold 80%+ of them;
  * a player's team in a game is whichever of its two teams he appears with in
    more of his games that season (his own team is in every game, an opponent
    in a handful), so traded players are handled and a season row that lists
    a later team (some 2024-25 rows do, e.g. Desmond Bane under ORL) doesn't
    matter; ties (a player with a game or two) fall back to his listed team;
  * the other team in the game is the defence.
Checks printed (checked 2026-09-28): 99.98% of 5.93M regular-season shots
placed; the assigned team agrees with the play-by-play lines' team in 99.82%
of 144,834 player-games 2020-21 on; every team-season's placed attempts are
97.3-100% of Basketball-Reference's FGA (own and opponent).

Table written (dropped and rebuilt):
  team_zone_mix(season text, team_abbreviation text, side text ('team' or
  'opponent'), zone text, fgm int, fga int)
Season is TEXT like player_shots ('2024-25'); team codes are the ones the
database uses in that season (see api/teams_lib.py).

Rerun after reloading player_shots, player_season_stats or player_team_stints.
Takes about 15 seconds (~5.9M regular-season shots).

Usage:
    cd scripts && python3 build_team_zone_mix.py
    cd scripts && python3 build_team_zone_mix.py --season 2027      # one season's rows (round 9 step 4)

--season N (round 9 step 4; scripts/season_mode.py): the same team
assignment and aggregation over that season's shots only (a player's team in
a game is decided within the season, so the rows are what the full build
gives the season) and only its rows replaced, no DDL. The
Basketball-Reference check is printed for the seasons it has (n/a for a live
season).
"""

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from db_config import DB_CONFIG
import season_mode as SM

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
from shots_lib import ZONES, classify_zone  # noqa: E402

KAGGLE = Path(__file__).resolve().parent.parent / "nba_data" / "kaggle_1947_present"

SETUP = """
CREATE TEMP TABLE single AS
    SELECT p.season, p.player_id, p.team_abbreviation AS team FROM player_season_stats p
    WHERE p.team_abbreviation !~ '^([0-9]TM|TOT)$'
      AND NOT EXISTS (SELECT 1 FROM player_team_stints s WHERE s.season = p.season AND s.player_id = p.player_id);
CREATE INDEX ON single (season, player_id);
CREATE TEMP TABLE pg AS
    SELECT DISTINCT LEFT(season, 4)::int + 1 AS s_int, game_id, player_id
    FROM player_shots WHERE game_id LIKE '002%'{only};
-- A game's two teams: team_game_fatigue from 2009-10 on (exact); before that the
-- two teams with the most single-team shooters, if they hold 80%+ of them.
CREATE TEMP TABLE game_pair AS
    SELECT game_id, MIN(team_abbreviation) AS a, MAX(team_abbreviation) AS b
    FROM team_game_fatigue GROUP BY game_id;
INSERT INTO game_pair
    SELECT game_id, MIN(team), MAX(team) FROM (
        SELECT game_id, team, n, SUM(n) OVER (PARTITION BY game_id) AS total,
               ROW_NUMBER() OVER (PARTITION BY game_id ORDER BY n DESC, team) AS rk
        FROM (SELECT pg.game_id, si.team, COUNT(*) AS n FROM pg
              JOIN single si ON si.season = pg.s_int AND si.player_id = pg.player_id
              WHERE pg.s_int < 2010 GROUP BY 1, 2) x) y
    WHERE rk <= 2 GROUP BY game_id HAVING COUNT(*) = 2 AND SUM(n) >= 0.8 * MAX(total);
CREATE INDEX ON game_pair (game_id);
"""


def assign_teams(cur):
    """(player_id, game_id) -> (team, opponent). A player's team in a game is
    whichever of the game's two teams he appears with in more games that
    season: his own team is in every game he plays, an opponent in a handful,
    so this holds for traded players too and doesn't depend on which team a
    season row lists (some 2024-25 rows list a later team). Ties (a player
    with a game or two) fall back to his listed team if it played that game."""
    cur.execute("""SELECT pg.s_int, pg.player_id, pg.game_id, gp.a, gp.b, si.team
                   FROM pg JOIN game_pair gp USING (game_id)
                   LEFT JOIN single si ON si.season = pg.s_int AND si.player_id = pg.player_id""")
    d = pd.DataFrame(cur.fetchall(), columns=["s_int", "player_id", "game_id", "a", "b", "listed"])
    freq = pd.concat([d[["s_int", "player_id", "a"]].rename(columns={"a": "t"}),
                      d[["s_int", "player_id", "b"]].rename(columns={"b": "t"})]).value_counts()
    fa = freq.reindex(pd.MultiIndex.from_frame(d[["s_int", "player_id", "a"]])).to_numpy()
    fb = freq.reindex(pd.MultiIndex.from_frame(d[["s_int", "player_id", "b"]])).to_numpy()
    team = np.where(fa > fb, d.a, np.where(fb > fa, d.b,
                    np.where(d.listed == d.a, d.a, np.where(d.listed == d.b, d.b, None))))
    d["team"] = team
    d["opp"] = np.where(d.team == d.a, d.b, d.a)
    ok = d[d.team.notna()]
    print(f"player-games: {len(d):,} in placed games, {len(ok):,} assigned, {len(d) - len(ok):,} ties left out")
    return {(p, g): (t, o) for p, g, t, o in zip(ok.player_id, ok.game_id, ok.team, ok.opp)}, ok


def check_against_lines(cur, ok):
    """From 2020-21 on the play-by-play lines record each player's team per game."""
    cur.execute("""SELECT gs.game_id, l.player_id, l.team_abbreviation
                   FROM player_game_lines l JOIN game_scores gs ON 'espn_' || gs.espn_id = l.game_id
                    AND gs.team_abbreviation = l.team_abbreviation""")
    lines = pd.DataFrame(cur.fetchall(), columns=["game_id", "player_id", "line_team"])
    m = ok.merge(lines, on=["game_id", "player_id"])
    agree = (m.team == m.line_team).mean() if len(m) else float("nan")
    print(f"vs play-by-play lines (2020-21+): {len(m):,} player-games compared, team agrees in {agree:.4%}")


def main():
    t0 = time.time()
    season = SM.parse_season()
    label = None if season is None else SM.season_label(season)
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(SETUP.format(only="" if label is None else f" AND season = '{label}'"))
    cur.execute("SELECT COUNT(DISTINCT game_id), (SELECT COUNT(*) FROM game_pair) FROM pg")
    games, pairs = cur.fetchone()
    print(f"{games:,} regular-season games with shots; {pairs:,} with two known teams ({time.time() - t0:.0f}s)")
    team_of, ok = assign_teams(cur)
    check_against_lines(cur, ok)

    agg = defaultdict(lambda: [0, 0])   # (season, team, side, zone) -> [fgm, fga]
    total = placed = unclassified = 0
    with conn.cursor(name="team_zone_stream") as s:
        s.itersize = 200_000
        s.execute("""SELECT season, game_id, player_id, loc_x, loc_y, shot_distance, shot_type,
                            shot_zone_basic, shot_made_flag
                     FROM player_shots WHERE game_id LIKE '002%%'""" + ("" if label is None else " AND season = %s"),
                  None if label is None else (label,))
        for season, game_id, player_id, x, y, dist, stype, zbasic, made in s:
            total += 1
            to = team_of.get((player_id, game_id))
            if to is None:
                continue
            zone = classify_zone(x, y, dist, stype, zbasic)
            if zone is None:
                unclassified += 1
                continue
            placed += 1
            for key in ((season, to[0], "team", zone), (season, to[1], "opponent", zone)):
                cell = agg[key]
                cell[0] += made or 0
                cell[1] += 1
    print(f"placed and classified: {placed:,} of {total:,} ({placed / total:.2%}); "
          f"unclassifiable: {unclassified:,} ({time.time() - t0:.0f}s)" if total else f"no regular-season shot of {label} on file yet")

    rows = [(s, t, side, z, fgm, fga) for (s, t, side, z), (fgm, fga) in sorted(agg.items())]
    if label is None:
        cur.execute("DROP TABLE IF EXISTS team_zone_mix")
        cur.execute("""CREATE TABLE team_zone_mix (
            season TEXT NOT NULL, team_abbreviation TEXT NOT NULL, side TEXT NOT NULL, zone TEXT NOT NULL,
            fgm INTEGER NOT NULL, fga INTEGER NOT NULL, PRIMARY KEY (season, team_abbreviation, side, zone))""")
    else:
        SM.require_tables(cur, ["team_zone_mix"], season)
        n_del = SM.delete_season(cur, "team_zone_mix", label)
        print(f"--season {season}: {n_del} stored rows of {label} replaced with {len(rows)}; every other season untouched")
    execute_values(cur, "INSERT INTO team_zone_mix VALUES %s", rows)
    conn.commit()
    if not rows:
        conn.close()
        return

    # Check: placed attempts against Basketball-Reference's team and opponent FGA.
    df = pd.DataFrame(rows, columns=["season", "team_abbreviation", "side", "zone", "fgm", "fga"])
    tot = df.pivot_table(index=["season", "team_abbreviation"], columns="side", values="fga", aggfunc="sum").reset_index()
    tot["s_int"] = tot.season.str[:4].astype(int) + 1
    cur.execute("SELECT season, abbreviation, bref_abbreviation FROM team_seasons WHERE NOT is_league_avg")
    codes = pd.DataFrame(cur.fetchall(), columns=["s_int", "team_abbreviation", "bref"])
    tt = pd.read_csv(KAGGLE / "Team Totals.csv")[["season", "abbreviation", "fga"]]
    ot = pd.read_csv(KAGGLE / "Opponent Totals.csv")[["season", "abbreviation", "opp_fga"]]
    br = tt.merge(ot, on=["season", "abbreviation"]).rename(columns={"season": "s_int", "abbreviation": "bref"})
    chk = tot.merge(codes.merge(br, on=["s_int", "bref"]), on=["s_int", "team_abbreviation"], how="left")
    chk["own_share"] = chk["team"] / chk["fga"]
    chk["opp_share"] = chk["opponent"] / chk["opp_fga"]
    print(f"team-seasons: {len(chk)}, unmatched to Basketball-Reference: {chk.fga.isna().sum()}"
          + (" (n/a: the export has no row for this season yet)" if chk.fga.isna().all() else ""))
    by = chk.groupby("s_int")[["own_share", "opp_share"]].agg(["min", "median"])
    print("share of Basketball-Reference FGA placed, by season (min / median):")
    print(by.round(3).to_string())
    conn.close()


if __name__ == "__main__":
    main()
