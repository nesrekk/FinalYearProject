"""
build_lineup_stints.py
=======================
Every five-on-five stint of every regular-season game 2020-21 to 2025-26,
rebuilt from ESPN play-by-play (pbp_events, source 'espn') with the shared
lineup parser in pbp_lineups.py (the same code that builds
player_game_lines, so the two can't disagree about who was on the floor).
Until now lineup and pair numbers came from `lineup_stats`, which stores
only each season's 2,000 most-used lineups (31-89% of a team's minutes);
these stints cover every minute of every game except the ones flagged
below.

A stint is a stretch of one period during which the same ten players were
on the floor. It ends at the substitution that changes either five. Each
carries, for each side, the points and the possession components (FGA,
FGM, 3PA, 3PM, FTA, FTM, offensive and defensive rebounds by a player,
turnovers including team turnovers) with the same credit rules as
`team_game_totals`, so stint sums equal the team-game totals, and an
inclusive `action_number` range so any event can be mapped to its stint.

Points: ESPN's score fields are stale in a few hundred games of 2020-21 to
2022-23 (the score drops, or jumps by two baskets at once), so crediting
points by positive score changes double-counts there (that is why the
player lines' on-court plus-minus doesn't add up in 29% of team-games).
Here points come from the made shots and free throws themselves; in games
where those don't add up to the real final score (game_scores, ~2% of
games: a shot with no type in the text judged a two instead of a three,
or the other way round) the running maximum of the score fields is used
instead; a game that adds up neither way is flagged.

Reconciliation, per game (`lineup_stint_games`): stint points must equal
the real final score for both teams (`points_ok`), stint seconds the game
length from game_scores.periods (`seconds_ok`), and stint FGA/FTA/OREB/TOV
the team-game totals for both teams (`poss_ok`); `game_ok` is all three.
Separately, a stint is complete when it has exactly five a side
(`lineup_ok` on the game = every stint is; `bad_lineup_stints` /
`bad_lineup_seconds` count the rest). A stint is `tracked_ok` when its
game is game_ok and it has five a side, and the aggregate tables use
only tracked_ok stints; a game is `tracked_ok` when all of its stints are.
Incomplete stints are almost always a player ESPN gives no id to (two-way
and 10-day players, 100-155 names a season) whose name isn't in
player_season_stats, the parser's only id source: 2020-21 to 2024-25 lose
2-6% of their minutes that way, 2025-26 almost none. The per-game rows say
which games are excluded or partial and why; nothing is hidden.
`actors_off_floor` also counts events whose actor wasn't on his team's
tracked floor (a stale or wrong lineup that still had five players), for
anyone who wants a stricter cut.

Tables written (all dropped and rebuilt):
  lineup_stints         one row per stint (~490,000): game, period, start/end
                        seconds since tip-off, both fives (sorted NBA ids),
                        per-side points, possession components and
                        possessions, event range, and the game's tracked_ok;
  lineup_stint_games    one row per game: reconciliation checks, which
                        point-crediting method was used, and the reason a
                        game isn't fully tracked;
  lineup_stint_seasons  per season: games, game_ok and tracked_ok counts,
                        stints, minutes and the tracked share of minutes;
  lineup_seasons        per team-season five-man lineup (tracked_ok stints):
                        games, stints, minutes, possessions, points,
                        ORtg/DRtg/net;
  pair_seasons          the same per pair of teammates.

Ratings use possessions averaged over the two sides (the on/off
convention: FGA + 0.44 FTA - OREB + TOV), so they sit ~3 points under
NBA.com's scale like player_on_off; differences don't depend on that.

Checks printed at the end (the README quotes them): reconciliation shares
per season; every player-game's stint seconds against player_game_lines
(the same parser, so they must agree); each team-season's stint minutes
against 48 x games + overtime; the most-used lineups' minutes against
lineup_stats for the same lineups; table sizes.

Usage:
    cd scripts && python3 build_lineup_stints.py            # full build
    cd scripts && python3 build_lineup_stints.py --dry-run  # one season into
                                                            # schema zz_phase1_dry,
                                                            # sizes scaled up
Rerun after build_player_game_lines.py (new play-by-play) or
fetch_game_scores.py.
"""

import sys
import time
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from pbp_lineups import Game, STINT_STATS, elapsed, game_seconds, load_espn, load_season_names

FT_POSS = 0.44
STAT_COLS = ["pts"] + STINT_STATS   # per side, prefixed home_/away_
DRY_SCHEMA = "zz_phase1_dry"


def poss(fga, fta, oreb, tov):
    return fga + FT_POSS * fta - oreb + tov


def load_reference(conn):
    finals = pd.read_sql_query(
        """SELECT 'espn_' || espn_id AS game_id, game_id AS nba_game_id, team_abbreviation AS team, pts_for, periods
           FROM game_scores WHERE espn_id IS NOT NULL""", conn)
    final_pts = {(r.game_id, r.team): int(r.pts_for) for r in finals.itertuples()}
    nba_ids = {r.game_id: r.nba_game_id for r in finals.itertuples()}
    periods = {r.game_id: int(r.periods) for r in finals.itertuples()}
    totals = pd.read_sql_query(
        "SELECT game_id, team_abbreviation AS team, fga, fta, oreb, tov FROM team_game_totals", conn)
    team_tot = {(r.game_id, r.team): (int(r.fga), int(r.fta), int(r.oreb), int(r.tov)) for r in totals.itertuples()}
    return final_pts, nba_ids, periods, team_tot


def build_game(g, ev, season_names, all_names, final_pts, nba_ids, periods, team_tot):
    """Stint rows and the reconciliation row for one game."""
    game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names)
    stints, diag = game.stints(g.home_team)
    home, away = g.home_team, g.away_team
    fh, fa = final_pts.get((g.game_id, home)), final_pts.get((g.game_id, away))
    n_periods = periods.get(g.game_id)
    sum_shots = (sum(s["h_pts_shots"] for s in stints), sum(s["a_pts_shots"] for s in stints))
    sum_score = (sum(s["h_pts_score"] for s in stints), sum(s["a_pts_score"] for s in stints))
    reasons = []
    if fh is None or fa is None:
        method, points_ok = "shots", False
        reasons.append("not in game_scores")
    elif sum_shots == (fh, fa):
        method, points_ok = "shots", True
    elif sum_score == (fh, fa):
        method, points_ok = "score", True
    else:
        method, points_ok = "shots", False
        reasons.append(f"points {sum_shots[0]}-{sum_shots[1]} by shots, {sum_score[0]}-{sum_score[1]} by score, "
                       f"final {fh}-{fa}")
    seconds = sum(s["t1"] - s["t0"] for s in stints)
    expected = game_seconds(n_periods) if n_periods else None
    seconds_ok = expected is not None and abs(seconds - expected) < 0.5
    if not seconds_ok:
        reasons.append(f"{seconds:.0f} stint seconds, game length {expected}")
    bad_lineups = sum(1 for s in stints if len(s["home"]) != 5 or len(s["away"]) != 5)
    lineup_ok = bad_lineups == 0
    if not lineup_ok:
        reasons.append(f"{bad_lineups} stints without five a side")
    poss_ok = True
    for team, p in ((home, "h_"), (away, "a_")):
        got = tuple(sum(s[p + k] for s in stints) for k in ("fga", "fta", "oreb", "tov"))
        want = team_tot.get((g.game_id, team))
        if want is None or got != want:
            poss_ok = False
            reasons.append(f"{team} FGA/FTA/OREB/TOV {got} vs team_game_totals {want}")
    game_ok = points_ok and seconds_ok and poss_ok
    tracked_ok = game_ok and lineup_ok
    bad_seconds = sum(s["t1"] - s["t0"] for s in stints if len(s["home"]) != 5 or len(s["away"]) != 5)
    pts_key = "pts_shots" if method == "shots" else "pts_score"
    rows = []
    score = [0, 0]
    for i, s in enumerate(stints):
        r = {"game_id": g.game_id, "nba_game_id": nba_ids.get(g.game_id), "season": int(g.season),
             "home_team": home, "away_team": away, "period": s["period"], "stint_no": i + 1,
             "start_elapsed": round(elapsed(s["period"], s["t0"]), 1), "end_elapsed": round(elapsed(s["period"], s["t1"]), 1),
             "seconds": round(s["t1"] - s["t0"], 1),
             "home_ids": s["home"], "away_ids": s["away"], "n_home": len(s["home"]), "n_away": len(s["away"]),
             "action_from": s["action_from"], "action_to": s["action_to"],
             "home_score": score[0], "away_score": score[1],
             "tracked_ok": game_ok and len(s["home"]) == 5 and len(s["away"]) == 5}
        for side, p in (("home", "h_"), ("away", "a_")):
            r[f"{side}_pts"] = s[p + pts_key]
            for k in STINT_STATS:
                r[f"{side}_{k}"] = s[p + k]
            r[f"{side}_poss"] = round(poss(s[p + "fga"], s[p + "fta"], s[p + "oreb"], s[p + "tov"]), 2)
        score[0] += r["home_pts"]
        score[1] += r["away_pts"]
        rows.append(r)
    game_row = {
        "game_id": g.game_id, "nba_game_id": nba_ids.get(g.game_id), "season": int(g.season), "game_date": g.game_date,
        "home_team": home, "away_team": away, "periods": n_periods, "stints": len(stints),
        "stint_seconds": round(seconds, 1), "game_length": expected, "points_method": method,
        "home_pts": score[0], "away_pts": score[1], "final_home": fh, "final_away": fa,
        "points_ok": points_ok, "seconds_ok": seconds_ok, "poss_ok": poss_ok, "game_ok": game_ok, "lineup_ok": lineup_ok,
        "bad_lineup_stints": bad_lineups, "bad_lineup_seconds": round(bad_seconds, 1),
        "actors_off_floor": int(diag.get("actor_off_floor", 0)),
        "events_no_team": int(diag.get("no_team", 0)), "tracked_ok": tracked_ok,
        "reason": "; ".join(reasons) if reasons else None,
    }
    return rows, game_row, game.unmatched


STINT_DDL = """CREATE TABLE {schema}lineup_stints (
    stint_id INTEGER PRIMARY KEY, game_id TEXT NOT NULL, nba_game_id TEXT, season INTEGER NOT NULL,
    home_team TEXT NOT NULL, away_team TEXT NOT NULL, period SMALLINT NOT NULL, stint_no SMALLINT NOT NULL,
    start_elapsed REAL NOT NULL, end_elapsed REAL NOT NULL, seconds REAL NOT NULL,
    home_ids INTEGER[] NOT NULL, away_ids INTEGER[] NOT NULL, n_home SMALLINT NOT NULL, n_away SMALLINT NOT NULL,
    action_from INTEGER, action_to INTEGER, home_score SMALLINT NOT NULL, away_score SMALLINT NOT NULL,
    {stats},
    home_poss REAL NOT NULL, away_poss REAL NOT NULL, tracked_ok BOOLEAN NOT NULL)"""

GAMES_DDL = """CREATE TABLE {schema}lineup_stint_games (
    game_id TEXT PRIMARY KEY, nba_game_id TEXT, season INTEGER NOT NULL, game_date DATE, home_team TEXT, away_team TEXT,
    periods SMALLINT, stints INTEGER, stint_seconds REAL, game_length INTEGER, points_method TEXT,
    home_pts SMALLINT, away_pts SMALLINT, final_home SMALLINT, final_away SMALLINT,
    points_ok BOOLEAN, seconds_ok BOOLEAN, poss_ok BOOLEAN, game_ok BOOLEAN NOT NULL, lineup_ok BOOLEAN,
    bad_lineup_stints INTEGER, bad_lineup_seconds REAL, actors_off_floor INTEGER, events_no_team INTEGER,
    tracked_ok BOOLEAN NOT NULL, reason TEXT)"""

SEASONS_DDL = """CREATE TABLE {schema}lineup_stint_seasons (
    season INTEGER PRIMARY KEY, games INTEGER, games_ok INTEGER, tracked_games INTEGER,
    tracked_share DOUBLE PRECISION, points_by_shots INTEGER, points_by_score INTEGER, points_failed INTEGER,
    seconds_failed INTEGER, poss_failed INTEGER, lineup_failed INTEGER, stints INTEGER, tracked_stints INTEGER,
    minutes DOUBLE PRECISION, tracked_minutes DOUBLE PRECISION, tracked_minutes_share DOUBLE PRECISION,
    bad_lineup_minutes DOUBLE PRECISION, actors_off_floor INTEGER)"""

# Per team-season lineup and pair aggregates, from tracked_ok games only.
SIDES_SQL = """
    WITH sides AS (
        SELECT season, home_team AS team, home_ids AS ids, game_id, seconds, home_pts AS pts_for, away_pts AS pts_against,
               home_poss AS poss_for, away_poss AS poss_against, home_fga AS fga, home_fgm AS fgm, home_fg3a AS fg3a,
               home_fg3m AS fg3m, home_fta AS fta, home_ftm AS ftm, home_oreb AS oreb, home_dreb AS dreb, home_tov AS tov
        FROM {schema}lineup_stints WHERE tracked_ok
        UNION ALL
        SELECT season, away_team, away_ids, game_id, seconds, away_pts, home_pts, away_poss, home_poss,
               away_fga, away_fgm, away_fg3a, away_fg3m, away_fta, away_ftm, away_oreb, away_dreb, away_tov
        FROM {schema}lineup_stints WHERE tracked_ok)
"""

LINEUPS_SQL = SIDES_SQL + """
    SELECT season, team AS team_abbreviation, ids AS player_ids, COUNT(DISTINCT game_id) AS games, COUNT(*) AS stints,
           ROUND((SUM(seconds) / 60)::numeric, 1)::double precision AS minutes,
           ROUND(SUM(poss_for)::numeric, 1)::double precision AS poss_for,
           ROUND(SUM(poss_against)::numeric, 1)::double precision AS poss_against,
           ROUND(((SUM(poss_for) + SUM(poss_against)) / 2)::numeric, 1)::double precision AS poss,
           SUM(pts_for)::integer AS pts_for, SUM(pts_against)::integer AS pts_against,
           SUM(fga)::integer AS fga, SUM(fgm)::integer AS fgm, SUM(fg3a)::integer AS fg3a, SUM(fg3m)::integer AS fg3m,
           SUM(fta)::integer AS fta, SUM(ftm)::integer AS ftm, SUM(oreb)::integer AS oreb, SUM(dreb)::integer AS dreb,
           SUM(tov)::integer AS tov,
           ROUND((100.0 * SUM(pts_for) / NULLIF((SUM(poss_for) + SUM(poss_against)) / 2, 0))::numeric, 2)::double precision AS off_rating,
           ROUND((100.0 * SUM(pts_against) / NULLIF((SUM(poss_for) + SUM(poss_against)) / 2, 0))::numeric, 2)::double precision AS def_rating,
           ROUND((100.0 * (SUM(pts_for) - SUM(pts_against)) / NULLIF((SUM(poss_for) + SUM(poss_against)) / 2, 0))::numeric, 2)::double precision AS net_rating
    FROM sides GROUP BY 1, 2, 3
"""

PAIRS_SQL = SIDES_SQL + """
    SELECT s.season, s.team AS team_abbreviation, a.pid AS player_a, b.pid AS player_b,
           COUNT(DISTINCT s.game_id) AS games, COUNT(*) AS stints, COUNT(DISTINCT s.ids) AS lineups,
           ROUND((SUM(s.seconds) / 60)::numeric, 1)::double precision AS minutes,
           ROUND(SUM(s.poss_for)::numeric, 1)::double precision AS poss_for,
           ROUND(SUM(s.poss_against)::numeric, 1)::double precision AS poss_against,
           ROUND(((SUM(s.poss_for) + SUM(s.poss_against)) / 2)::numeric, 1)::double precision AS poss,
           SUM(s.pts_for)::integer AS pts_for, SUM(s.pts_against)::integer AS pts_against,
           ROUND((100.0 * SUM(s.pts_for) / NULLIF((SUM(s.poss_for) + SUM(s.poss_against)) / 2, 0))::numeric, 2)::double precision AS off_rating,
           ROUND((100.0 * SUM(s.pts_against) / NULLIF((SUM(s.poss_for) + SUM(s.poss_against)) / 2, 0))::numeric, 2)::double precision AS def_rating,
           ROUND((100.0 * (SUM(s.pts_for) - SUM(s.pts_against)) / NULLIF((SUM(s.poss_for) + SUM(s.poss_against)) / 2, 0))::numeric, 2)::double precision AS net_rating
    FROM sides s, unnest(s.ids) AS a(pid), unnest(s.ids) AS b(pid)
    WHERE a.pid < b.pid
    GROUP BY 1, 2, 3, 4
"""


def clean(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    return v.item() if hasattr(v, "item") else v


def write_tables(cur, schema, stint_rows, game_rows, season_rows):
    for t in ("pair_seasons", "lineup_seasons", "lineup_stint_seasons", "lineup_stint_games", "lineup_stints"):
        cur.execute(f"DROP TABLE IF EXISTS {schema}{t};")
    stats = ", ".join(f"{side}_{k} SMALLINT NOT NULL" for side in ("home", "away") for k in STAT_COLS)
    cur.execute(STINT_DDL.format(schema=schema, stats=stats))
    scols = ["stint_id", "game_id", "nba_game_id", "season", "home_team", "away_team", "period", "stint_no",
             "start_elapsed", "end_elapsed", "seconds", "home_ids", "away_ids", "n_home", "n_away",
             "action_from", "action_to", "home_score", "away_score"] + \
            [f"{side}_{k}" for side in ("home", "away") for k in STAT_COLS] + ["home_poss", "away_poss", "tracked_ok"]
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO {schema}lineup_stints ({', '.join(scols)}) VALUES %s",
        [tuple(clean(r[c]) for c in scols) for r in stint_rows], page_size=5000)
    cur.execute(f"CREATE INDEX ON {schema}lineup_stints (game_id, stint_no);")
    cur.execute(f"CREATE INDEX ON {schema}lineup_stints (season, home_team);")
    cur.execute(f"CREATE INDEX ON {schema}lineup_stints (season, away_team);")
    cur.execute(f"CREATE INDEX ON {schema}lineup_stints USING GIN (home_ids);")
    cur.execute(f"CREATE INDEX ON {schema}lineup_stints USING GIN (away_ids);")

    cur.execute(GAMES_DDL.format(schema=schema))
    gcols = list(game_rows[0].keys())
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO {schema}lineup_stint_games ({', '.join(gcols)}) VALUES %s",
        [tuple(clean(r[c]) for c in gcols) for r in game_rows], page_size=2000)
    cur.execute(f"CREATE INDEX ON {schema}lineup_stint_games (season, home_team);")
    cur.execute(f"CREATE INDEX ON {schema}lineup_stint_games (season, away_team);")

    cur.execute(SEASONS_DDL.format(schema=schema))
    ccols = list(season_rows[0].keys())
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO {schema}lineup_stint_seasons ({', '.join(ccols)}) VALUES %s",
        [tuple(clean(r[c]) for c in ccols) for r in season_rows])

    cur.execute(f"CREATE TABLE {schema}lineup_seasons AS " + LINEUPS_SQL.format(schema=schema))
    cur.execute(f"CREATE INDEX ON {schema}lineup_seasons (season, team_abbreviation);")
    cur.execute(f"CREATE INDEX ON {schema}lineup_seasons USING GIN (player_ids);")
    cur.execute(f"CREATE TABLE {schema}pair_seasons AS " + PAIRS_SQL.format(schema=schema))
    cur.execute(f"ALTER TABLE {schema}pair_seasons ADD PRIMARY KEY (season, team_abbreviation, player_a, player_b);")
    cur.execute(f"CREATE INDEX ON {schema}pair_seasons (season, player_a);")
    cur.execute(f"CREATE INDEX ON {schema}pair_seasons (season, player_b);")


def season_summary(game_rows, stint_rows):
    by_season = defaultdict(list)
    for r in game_rows:
        by_season[r["season"]].append(r)
    st = defaultdict(lambda: [0, 0, 0.0, 0.0])
    for s in stint_rows:
        a = st[s["season"]]
        a[0] += 1
        a[2] += s["seconds"]
        if s["tracked_ok"]:
            a[1] += 1
            a[3] += s["seconds"]
    out = []
    for season in sorted(by_season):
        g = by_season[season]
        n = len(g)
        tracked = sum(r["tracked_ok"] for r in g)
        out.append({
            "season": season, "games": n, "games_ok": sum(r["game_ok"] for r in g), "tracked_games": tracked,
            "tracked_share": round(tracked / n, 4),
            "points_by_shots": sum(r["points_ok"] and r["points_method"] == "shots" for r in g),
            "points_by_score": sum(r["points_ok"] and r["points_method"] == "score" for r in g),
            "points_failed": sum(not r["points_ok"] for r in g),
            "seconds_failed": sum(not r["seconds_ok"] for r in g),
            "poss_failed": sum(not r["poss_ok"] for r in g),
            "lineup_failed": sum(not r["lineup_ok"] for r in g),
            "stints": st[season][0], "tracked_stints": st[season][1],
            "minutes": round(st[season][2] / 60, 1), "tracked_minutes": round(st[season][3] / 60, 1),
            "tracked_minutes_share": round(st[season][3] / st[season][2], 4) if st[season][2] else None,
            "bad_lineup_minutes": round(sum(r["bad_lineup_seconds"] for r in g) / 60, 1),
            "actors_off_floor": sum(r["actors_off_floor"] for r in g),
        })
    return out


def print_checks(cur, schema, conn):
    print("\nPer season (lineup_stint_seasons):")
    print(pd.read_sql_query(f"SELECT * FROM {schema}lineup_stint_seasons ORDER BY season", conn).to_string(index=False))

    # Stint seconds per player-game must equal the player lines (same parser).
    cur.execute(f"""
        WITH s AS (
            SELECT game_id, pid, SUM(seconds) secs FROM (
                SELECT game_id, unnest(home_ids) pid, seconds FROM {schema}lineup_stints
                UNION ALL SELECT game_id, unnest(away_ids), seconds FROM {schema}lineup_stints) x GROUP BY 1, 2)
        SELECT COUNT(*), COUNT(*) FILTER (WHERE ABS(s.secs - l.seconds) > 0.2), MAX(ABS(s.secs - l.seconds))
        FROM s JOIN player_game_lines l ON l.game_id = s.game_id AND l.player_id = s.pid""")
    n, off, mx = cur.fetchone()
    print(f"\nPlayer-games whose stint seconds differ from player_game_lines by > 0.2 s: {off} of {n} (max {mx:.1f} s)")
    cur.execute(f"""
        SELECT COUNT(*) FROM player_game_lines l WHERE l.seconds > 0 AND NOT EXISTS (
            SELECT 1 FROM {schema}lineup_stints s WHERE s.game_id = l.game_id
            AND (s.home_ids @> ARRAY[l.player_id::integer] OR s.away_ids @> ARRAY[l.player_id::integer]))""")
    print(f"player_game_lines rows with minutes but no stint: {cur.fetchone()[0]}")

    # Team-season minutes vs 48 x games + overtime (all games, then tracked only).
    cur.execute(f"""
        WITH g AS (SELECT season, home_team AS team, game_length, stint_seconds FROM {schema}lineup_stint_games
                   UNION ALL SELECT season, away_team, game_length, stint_seconds FROM {schema}lineup_stint_games),
             t AS (SELECT season, SUM(seconds) FILTER (WHERE tracked_ok) * 2 AS tracked FROM {schema}lineup_stints GROUP BY 1)
        SELECT g.season, COUNT(*) / 30.0 AS games_per_team, SUM(stint_seconds) / 60 AS stint_min, SUM(game_length) / 60.0 AS length_min,
               MAX(t.tracked) / 60 AS tracked_min
        FROM g JOIN t USING (season) GROUP BY 1 ORDER BY 1""")
    print("\nTeam-season minutes (league sums): stint minutes vs 48 x games + overtime")
    for season, gpt, sm, lm, tm in ((a, float(b), float(c), float(d), float(e)) for a, b, c, d, e in cur.fetchall()):
        print(f"  {season}: {gpt:.1f} games per team, stint minutes {sm:,.0f} vs game length {lm:,.0f} "
              f"(ratio {sm / lm:.4f}); tracked {tm:,.0f} ({tm / lm:.1%})")

    # Most-used lineups vs lineup_stats (the stored top-2,000 list), same lineups.
    cur.execute(f"""
        WITH top AS (
            SELECT season, team_abbreviation, player_ids, minutes, poss, net_rating,
                   ROW_NUMBER() OVER (PARTITION BY season, team_abbreviation ORDER BY minutes DESC) rn
            FROM lineup_stats WHERE season >= 2021)
        SELECT t.season, COUNT(*), CORR(t.minutes, l.minutes), AVG(l.minutes - t.minutes),
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ABS(l.minutes - t.minutes)),
               MAX(ABS(l.minutes - t.minutes)), CORR(t.net_rating, l.net_rating), COUNT(*) FILTER (WHERE l.minutes IS NULL)
        FROM top t LEFT JOIN {schema}lineup_seasons l
          ON l.season = t.season AND l.team_abbreviation = t.team_abbreviation
         AND l.player_ids = (SELECT array_agg(x ORDER BY x) FROM unnest(t.player_ids) x)::integer[]
        WHERE t.rn <= 5 GROUP BY 1 ORDER BY 1""")
    print("\nEach team's 5 most-used lineups in lineup_stats vs the same lineups here (minutes):")
    for season, n, corr, bias, med, mx, ncorr, missing in ((r[0], r[1], *[float(x) for x in r[2:7]], r[7]) for r in cur.fetchall()):
        print(f"  {season}: {n} lineups, r {corr:.3f}, mean diff {bias:+.1f} min, median |diff| {med:.1f}, "
              f"max |diff| {mx:.1f}; net rating r {ncorr:.3f}; not found here: {missing}")

    for t in ("lineup_stints", "lineup_stint_games", "lineup_stint_seasons", "lineup_seasons", "pair_seasons"):
        cur.execute(f"SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('{schema}{t}')), "
                    f"pg_total_relation_size('{schema}{t}') FROM {schema}{t}")
        n, pretty, raw = cur.fetchone()
        print(f"  {t}: {n:,} rows, {pretty}")


def main():
    dry = "--dry-run" in sys.argv
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    season_names, all_names = load_season_names(cur)
    final_pts, nba_ids, periods, team_tot = load_reference(conn)
    games, grouped = load_espn(conn)
    if dry:
        games = games[games.season == games.season.max()]
    print(f"{len(games)} games, {sum(len(grouped[g]) for g in games.game_id if g in grouped)} events "
          f"({time.time() - t0:.0f}s)")

    stint_rows, game_rows, unmatched = [], [], Counter()
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None:
            continue
        rows, game_row, um = build_game(g, ev, season_names, all_names, final_pts, nba_ids, periods, team_tot)
        for r in rows:
            r["stint_id"] = len(stint_rows) + 1
            stint_rows.append(r)
        game_rows.append(game_row)
        unmatched.update(um)
        if i % 1000 == 0:
            print(f"  {i} games, {len(stint_rows)} stints ({time.time() - t0:.0f}s)")
    season_rows = season_summary(game_rows, stint_rows)
    print(f"{len(game_rows)} games, {len(stint_rows)} stints; most common unmatched names: {unmatched.most_common(8)}")

    schema = f"{DRY_SCHEMA}." if dry else ""
    if dry:
        cur.execute(f"DROP SCHEMA IF EXISTS {DRY_SCHEMA} CASCADE; CREATE SCHEMA {DRY_SCHEMA};")
    write_tables(cur, schema, stint_rows, game_rows, season_rows)
    conn.commit()
    print(f"wrote {len(stint_rows)} stints, {len(game_rows)} games ({time.time() - t0:.0f}s)")
    print_checks(cur, schema, conn)
    if dry:
        cur.execute("SELECT COUNT(*) FROM pbp_games WHERE source = 'espn'")
        scale = cur.fetchone()[0] / len(game_rows)
        cur.execute(f"SELECT pg_total_relation_size('{schema}lineup_stints')")
        print(f"\nDry run: {len(stint_rows):,} stints for {len(game_rows)} games; all {scale * len(game_rows):.0f} games "
              f"would be ~{scale * len(stint_rows):,.0f} stints, lineup_stints ~{scale * cur.fetchone()[0] / 1e6:.0f} MB "
              f"with indexes. Tables are in schema {DRY_SCHEMA}; drop it when done.")
    conn.close()


if __name__ == "__main__":
    main()
