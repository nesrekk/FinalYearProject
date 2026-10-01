"""
build_possessions.py
====================
Every regular-season game 2020-21 to 2025-26 cut into possessions, from
ESPN play-by-play through the shared parser (pbp_lineups.Game.walk(); the
possession rules are in pbp_possessions.py). Feeds the Possession Explorer
(round 6 step 4), Coaching Decisions (step 5), availability-aware odds
(step 6) and the Lineup Predictor (step 9), and lets RAPM be scored per
possession (step 10).

The clock. ESPN's clock is late by event type, measured here against
NBA.com's own play-by-play of the same games (the 418 games of 2024-25
kept in pbp_events as nba_api twins): made shots and made last free throws
a median 14 s late, rebounds and turnovers 6 s, misses 2 s; fouls and the
first free throw of a trip are on time (turnovers: 5 s for steals, 10 s
for dead-ball ones). Possession times use
pbp_possessions.corrected_clock(): every field goal the NBA shot chart can
be matched to (match_coordinates(), ~99%) takes the chart's clock, free
throws their trip's first free throw (or the foul / made shot before a
single one), a rebound 2 s after its miss, a turnover its offensive foul
or ESPN's time less 6 s. The lags were measured on all 418 twin games;
`possession_meta` stores them measured separately on the odd and even
games, and the corrected clock's error against NBA.com on each half. The
stints, player minutes and every other table keep ESPN's clock (substitutions
happen at dead balls, where ESPN's clock is on time); possessions map to
stints by event number, never by time.

Judgment calls (also on possession_meta and in README):
  - possession rules: pbp_possessions.py docstring (and-ones, kept-ball
    free throws, technicals outside `pts`, dead-ball team rebounds between
    free throws, empty end-of-period possessions with >= 1 s left);
  - transition: the first field-goal attempt or free throw comes within
    TRANSITION_SECONDS (7) of the start, judged only where the start and the
    attempt are on the anchored clock (after a made shot, a made free throw
    or a rebound; after a turnover `transition` and `first_attempt_sec` are
    NULL, because ESPN stamps a turnover at about the time of the next play
    and the real moment can't be recovered). There is no trough in the
    time-to-first-attempt distribution to cut at; the window is where the
    early-shot premium has faded: after a player's defensive rebound,
    possessions whose first attempt comes in the first 0-3 s score ~1.55,
    falling to the ~1.31 plateau by 7-8 s (the curve is stored in
    possession_meta). Checked against "Transition Take Foul" events (2022-23
    on; by rule a foul that stops a fast break): 99% fall inside the window.
    ESPN's text has no fast-break label (0 events say "fast break").
  - points: from the made shots and free throws themselves; in games where
    those don't add up to the real final score (game_scores) the running
    maximum of ESPN's score fields is used, as lineup_stints does (a
    possession then takes the score steps logged while it was in progress,
    which can sit a possession late where the fields were stale); a game
    that adds up neither way is flagged.
  - lineups: `stint_no` is the lineup_stints stint holding the possession's
    first event, `stint_no_end` its last; they differ when a substitution
    came mid-possession (almost always between two free throws).
  - `team_oreb` keeps the team offensive rebounds that continue a
    possession (ball out of bounds off the defence), which the lines'
    possession estimate (FGA + 0.44 FTA - OREB + TOV, player OREB only)
    doesn't subtract: that is why counted possessions run ~3 a team-game
    under it.

Reconciliation per game (`possession_games`): offence points plus
technicals add up to the final score for both teams (`points_ok`); FGA,
FTA (with technicals), player OREB and TOV equal team_game_totals
(`comp_ok`); the stints rebuilt here are lineup_stints' (same count and
event ranges, `stints_ok`); `game_ok` is all three. Also counted: possessions
per side vs the estimate, possessions spanning two stints, starts with no
logged ending event, and the parser's oddities (pbp_possessions diag).

Tables written (all dropped and rebuilt):
  possessions        one row per possession (~1.4M);
  possession_games   one row per game: reconciliation and counts;
  possession_seasons per season x team (and 'ALL') x start type (and 'all'):
                     possessions, points, points per possession, transition,
                     second-chance points, and the same allowed (game_ok games);
  possession_meta    rules, constants, the clock check and the
                     transition-window check (key / JSON value).

Usage:
    cd scripts && python3 build_possessions.py              # full build (~3.5 min)
    cd scripts && python3 build_possessions.py --dry-run    # 2024-25 only, into schema zz_poss_dry
Rerun after build_lineup_stints.py (it reads lineup_stints and
team_game_totals) or a player_shots reload.
"""

import json
import sys
import time
from collections import Counter, defaultdict

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from pbp_lineups import Game, chart_matches, elapsed, load_espn, load_season_names, miss_three_calls, period_bounds
import pbp_possessions as PP

TRANSITION_SECONDS = 7.0
# Starts that can be transition; after the last two the start time is unknown, so `transition` is NULL there.
TRANSITION_STARTS = ("made_fg", "made_ft", "dreb", "dreb_ft", "team_dreb", "steal", "dead_tov")
FT_POSS = 0.44
DRY_SCHEMA = "zz_poss_dry"
TWIN_SEASON = 2025
STAT_COLS = ["fga", "fgm", "fg3a", "fg3m", "fta", "ftm", "oreb", "team_oreb", "tov"]


def load_reference(conn):
    finals = pd.read_sql_query(
        """SELECT 'espn_' || espn_id AS game_id, game_id AS nba_game_id, team_abbreviation AS team, pts_for, periods
           FROM game_scores WHERE espn_id IS NOT NULL""", conn)
    final_pts = {(r.game_id, r.team): int(r.pts_for) for r in finals.itertuples()}
    nba_ids = {r.game_id: r.nba_game_id for r in finals.itertuples()}
    totals = pd.read_sql_query("SELECT game_id, team_abbreviation AS team, fga, fta, oreb, tov FROM team_game_totals", conn)
    team_tot = {(r.game_id, r.team): (int(r.fga), int(r.fta), int(r.oreb), int(r.tov)) for r in totals.itertuples()}
    st = pd.read_sql_query(
        "SELECT game_id, stint_no, action_from, action_to, tracked_ok FROM lineup_stints ORDER BY game_id, stint_no", conn)
    stints = {}
    for gid, grp in st.groupby("game_id", sort=False):
        stints[gid] = [(int(r.stint_no), None if pd.isna(r.action_from) else int(r.action_from),
                        None if pd.isna(r.action_to) else int(r.action_to), bool(r.tracked_ok)) for r in grp.itertuples()]
    return final_pts, nba_ids, team_tot, stints


def chart_clock(conn, matched):
    """{game_id: {action_number: seconds into the period}} by the NBA shot chart's clock."""
    clocks = pd.read_sql_query(
        """SELECT id AS nba_shot_id, period AS chart_period, minutes_remaining * 60 + seconds_remaining AS clock
           FROM player_shots WHERE game_id LIKE '002%%' AND season >= '2020-21'""", conn)
    m = matched[matched.nba_shot_id.notna()][["game_id", "action_number", "period", "nba_shot_id"]].copy()
    m["nba_shot_id"] = m.nba_shot_id.astype("int64")
    m = m.merge(clocks, on="nba_shot_id")
    m = m[m.period == m.chart_period]
    m["t"] = [period_bounds(int(p))[1] - c for p, c in zip(m.period, m.clock)]
    out = defaultdict(dict)
    for gid, n, t in m[["game_id", "action_number", "t"]].itertuples(index=False):
        out[gid][int(n)] = float(t)
    return out


def build_game(g, ev, season_names, all_names, calls, chart_t, final_pts, nba_ids, team_tot, stored_stints):
    """Possession rows and the reconciliation row for one game."""
    home, away = g.home_team, g.away_team
    game = Game(g.game_id, int(g.season), g.game_date, ev, season_names[int(g.season)], all_names,
                miss_threes=calls.get(g.game_id))
    game.home = home
    events = game.parse()
    clock, anchored = PP.corrected_clock(events, chart_t.get(g.game_id, {}))
    n_counted = sum(1 for e in events if e["kind"] in PP.COUNT_KINDS)
    n_chart = sum(1 for e in events if e["kind"] == "fg" and e["action_number"] in chart_t.get(g.game_id, {}))
    n_fg = sum(1 for e in events if e["kind"] == "fg")
    rows, diag = PP.possessions(game, home, clock)
    stints, _ = game.stints(home)

    # Stints rebuilt here must be lineup_stints' own (same parser, same game).
    stored = stored_stints.get(g.game_id, [])
    mine = [(i + 1, s["action_from"], s["action_to"]) for i, s in enumerate(stints)]
    stints_ok = len(stored) == len(mine) and all(a[:3] == b[:3] for a, b in zip(stored, mine))
    tracked = {no: ok for no, _, _, ok in stored}
    ranges = [(a, b, no) for no, a, b in mine if a is not None]
    last_in_period = {}
    for i, s in enumerate(stints):
        last_in_period[s["period"]] = i + 1

    def stint_of(n):
        """The stint whose event range holds `n`; an event outside every range (a team rebound at the buzzer, in a
        zero-length stint lineup_stints drops) takes the last stint that started before it."""
        before = None
        for a, b, no in ranges:
            if a <= n <= b:
                return no
            if a <= n:
                before = no
        return before

    # Points: shots and free throws, else score steps.
    fh, fa = final_pts.get((g.game_id, home)), final_pts.get((g.game_id, away))

    def team_points(key_off, key_def_tech):
        tot = {home: 0, away: 0}
        for r in rows:
            d = away if r["offense"] == home else home
            tot[r["offense"]] += r[key_off] + r["off_tech_pts"]
            tot[d] += r[key_def_tech]
        return tot[home], tot[away]

    for r in rows:
        own, opp = ("score_h", "score_a") if r["offense"] == home else ("score_a", "score_h")
        r["pts_score"] = r[own] - r["off_tech_pts"]
        r["def_score"] = r[opp]
    reasons = []
    by_shots = team_points("pts_shots", "def_tech_pts")
    by_score = team_points("pts_score", "def_score")
    if fh is None or fa is None:
        method, points_ok = "shots", False
        reasons.append("not in game_scores")
    elif by_shots == (fh, fa):
        method, points_ok = "shots", True
    elif by_score == (fh, fa):
        method, points_ok = "score", True
    else:
        method, points_ok = "shots", False
        reasons.append(f"points {by_shots[0]}-{by_shots[1]} by shots, {by_score[0]}-{by_score[1]} by score, final {fh}-{fa}")
    negative = 0
    for r in rows:
        if method == "score":
            r["pts"], r["def_tech_pts"] = r["pts_score"], r["def_score"]
            negative += r["pts"] < 0
        else:
            r["pts"] = r["pts_shots"]
    if negative:
        reasons.append(f"{negative} possessions with negative points by score steps")

    comp_ok = True
    for team in (home, away):
        mine_r = [r for r in rows if r["offense"] == team]
        theirs = [r for r in rows if r["offense"] != team]
        got = (sum(r["fga"] for r in mine_r),
               sum(r["fta"] + r["off_tech_fta"] for r in mine_r) + sum(r["def_tech_fta"] for r in theirs),
               sum(r["oreb"] for r in mine_r), sum(r["tov"] for r in mine_r))
        want = team_tot.get((g.game_id, team))
        if want is None or got != want:
            comp_ok = False
            reasons.append(f"{team} FGA/FTA/OREB/TOV {got} vs team_game_totals {want}")
    if not stints_ok:
        reasons.append(f"{len(mine)} stints rebuilt vs {len(stored)} in lineup_stints")
    game_ok = points_ok and comp_ok and stints_ok

    out = []
    score = {home: 0, away: 0}
    multi = 0
    for i, r in enumerate(rows):
        d = away if r["offense"] == home else home
        if r["action_from"] is not None:
            s0, s1 = stint_of(r["action_from"]), stint_of(r["action_to"])
        else:
            s0 = s1 = last_in_period.get(r["period"])
        multi += s0 != s1
        start_ok = r["start_type"] == "period_start" or r["start_action"] in anchored
        end_ok = r["end_type"] == "period_end" or r["end_action"] in anchored
        fa_sec = r["first_attempt_sec"] if start_ok and r["first_attempt_action"] in anchored else None
        if r["start_type"] not in TRANSITION_STARTS:
            transition = False
        elif not start_ok:
            transition = None                      # after a turnover: ESPN can't say when it happened
        elif r["first_attempt_action"] is None:
            transition = False                     # no attempt
        else:
            transition = None if fa_sec is None else fa_sec <= TRANSITION_SECONDS
        row = {
            "game_id": g.game_id, "season": int(g.season), "period": r["period"], "poss_no": i + 1,
            "offense": r["offense"], "defense": d, "off_home": r["offense"] == home,
            "start_type": r["start_type"], "end_type": r["end_type"],
            "start_elapsed": round(elapsed(r["period"], r["t0"]), 1), "end_elapsed": round(elapsed(r["period"], r["t1"]), 1),
            "seconds": round(r["t1"] - r["t0"], 1),
            "first_attempt_sec": None if fa_sec is None else round(fa_sec, 1), "transition": transition,
            "clock_ok": start_ok and end_ok,
            "action_from": r["action_from"], "action_to": r["action_to"], "stint_no": s0, "stint_no_end": s1,
            "off_score": score[r["offense"]], "def_score": score[d],
            "pts": r["pts"], **{k: r[k] for k in STAT_COLS},
            "second_chance_pts": r["second_chance_pts"], "off_tech_pts": r["off_tech_pts"], "def_tech_pts": r["def_tech_pts"],
            "and_one": r["and_one"], "empty": r["empty"],
            "tracked_ok": game_ok and s0 is not None and tracked.get(s0, False),
            "_take": r.get("take_foul_sec") if start_ok else None,
        }
        score[r["offense"]] += r["pts"] + r["off_tech_pts"]
        score[d] += r["def_tech_pts"]
        out.append(row)

    def est(team):
        t = team_tot.get((g.game_id, team))
        return round(t[0] + FT_POSS * t[1] - t[2] + t[3], 2) if t else None

    counts = Counter(r["offense"] for r in out)
    starts = Counter(r["start_type"] for r in out)
    game_row = {
        "game_id": g.game_id, "nba_game_id": nba_ids.get(g.game_id), "season": int(g.season), "game_date": g.game_date,
        "home_team": home, "away_team": away, "possessions": len(out),
        "home_poss": counts[home], "away_poss": counts[away], "empty_poss": sum(r["empty"] for r in out),
        "home_est": est(home), "away_est": est(away),
        "home_team_oreb": sum(r["team_oreb"] for r in out if r["offense"] == home),
        "away_team_oreb": sum(r["team_oreb"] for r in out if r["offense"] == away),
        "points_method": method, "home_pts": score[home], "away_pts": score[away], "final_home": fh, "final_away": fa,
        "points_ok": points_ok, "comp_ok": comp_ok, "stints_ok": stints_ok, "game_ok": game_ok,
        "multi_stint": multi, "other_starts": starts.get("other", 0),
        "chart_clock_share": round(n_chart / n_fg, 4) if n_fg else None, "counted_events": n_counted,
        "diag": json.dumps(dict(sorted(diag.items()))) if diag else None,
        "reason": "; ".join(reasons) if reasons else None,
    }
    return out, game_row, diag


# --------------------------------------------------------------------------------------------- clock check
def clock_check(conn, season_names, all_names, chart_t, grouped):
    """ESPN's lag and the corrected clock's error against NBA.com's play-by-play of the same games (the nba_api
    twins), by event class, on all twin games and on the odd / even halves separately."""
    link = pd.read_sql_query(
        """SELECT DISTINCT n.game_id AS nba, 'espn_' || s.espn_id AS espn, g.home_team
           FROM pbp_games n JOIN game_scores s ON s.game_id = n.game_id JOIN pbp_games g ON g.game_id = 'espn_' || s.espn_id
           WHERE n.source = 'nba_api' AND s.espn_id IS NOT NULL ORDER BY 1""", conn)
    nba = pd.read_sql_query(
        """SELECT e.game_id, e.period, e.seconds_remaining, e.person_id, e.action_type
           FROM pbp_events e JOIN pbp_games g USING (game_id) WHERE g.source = 'nba_api' AND e.person_id IS NOT NULL
           AND e.action_type IN ('Made Shot', 'Missed Shot', 'Free Throw', 'Rebound', 'Turnover')
           ORDER BY e.game_id, e.action_number, e.id""", conn)
    keymap = {"Made Shot": "fg", "Missed Shot": "fg", "Free Throw": "ft", "Rebound": "reb", "Turnover": "tov"}
    nba["key"] = nba.action_type.map(keymap)
    nba["t"] = [period_bounds(int(p))[0] - s for p, s in zip(nba.period, nba.seconds_remaining)]
    nba["pid"] = nba.person_id.astype("int64")
    nba["half"] = nba.game_id.map({r.nba: i % 2 for i, r in enumerate(link.itertuples())})
    rows = []
    for i, l in enumerate(link.itertuples()):
        ev = grouped.get(l.espn)
        if ev is None:
            continue
        game = Game(l.espn, TWIN_SEASON, None, ev, season_names[TWIN_SEASON], all_names)
        game.home = l.home_team
        events = game.parse()
        corr, _ = PP.corrected_clock(events, chart_t.get(l.espn, {}))
        for e in events:
            k = e["kind"]
            if k not in PP.COUNT_KINDS or not e["pid"]:
                continue
            if k == "ft":
                kk, n, tech, _ = PP.ft_trip(e["action"])
                if tech:
                    continue
                cls = ("ft_first" if kk == 1 and n > 1 else "ft_later" if kk > 1 else "ft_single") + \
                      ("_made" if e["made"] else "_miss")
            elif k == "fg":
                cls = "fg_made" if e["made"] else "fg_miss"
            elif k == "tov":
                cls = "tov_steal" if e.get("steal") else "tov_dead"
            else:
                cls = "reb"
            key = {"fg": "fg", "ft": "ft", "oreb": "reb", "dreb": "reb", "tov": "tov"}[k]
            rows.append((l.nba, i % 2, e["period"], int(e["pid"]), key, cls, PP.espn_t(e), corr[e["action_number"]]))
    d = pd.DataFrame(rows, columns=["game_id", "half", "period", "pid", "key", "cls", "t_espn", "t_corr"])
    d["k"] = d.groupby(["game_id", "period", "pid", "key"]).cumcount()
    nba["k"] = nba.groupby(["game_id", "period", "pid", "key"]).cumcount()
    m = d.merge(nba[["game_id", "period", "pid", "key", "k", "t"]], on=["game_id", "period", "pid", "key", "k"])
    m["lag"] = m.t_espn - m.t
    m["err"] = (m.t_corr - m.t).abs()
    m["err_espn"] = m.lag.abs()
    out = {"twin_games": int(link.shape[0]), "matched_events": int(len(m)), "classes": {}}
    for cls, grp in m.groupby("cls"):
        out["classes"][cls] = {
            "n": int(len(grp)),
            "espn_lag_median": float(grp.lag.median()),
            "espn_lag_median_odd": float(grp[grp.half == 1].lag.median()),
            "espn_lag_median_even": float(grp[grp.half == 0].lag.median()),
            "espn_abs_err_median": float(grp.err_espn.median()),
            "corrected_abs_err_median": float(grp.err.median()),
            "corrected_abs_err_median_even": float(grp[grp.half == 0].err.median()),
            "espn_within_2s": round(float((grp.err_espn <= 2).mean()), 4),
            "corrected_within_2s": round(float((grp.err <= 2).mean()), 4),
            "corrected_within_2s_even": round(float((grp[grp.half == 0].err <= 2).mean()), 4),
            "espn_within_5s": round(float((grp.err_espn <= 5).mean()), 4),
            "corrected_within_5s": round(float((grp.err <= 5).mean()), 4),
        }
    out["all"] = {"espn_within_2s": round(float((m.err_espn <= 2).mean()), 4),
                  "corrected_within_2s": round(float((m.err <= 2).mean()), 4),
                  "espn_within_5s": round(float((m.err_espn <= 5).mean()), 4),
                  "corrected_within_5s": round(float((m.err <= 5).mean()), 4),
                  "espn_abs_err_median": float(m.err_espn.median()), "corrected_abs_err_median": float(m.err.median())}
    return out


# --------------------------------------------------------------------------------------------- tables
POSS_DDL = """CREATE TABLE {schema}possessions (
    game_id TEXT NOT NULL, poss_no SMALLINT NOT NULL, season SMALLINT NOT NULL, period SMALLINT NOT NULL, offense TEXT NOT NULL, defense TEXT NOT NULL, off_home BOOLEAN NOT NULL,
    start_type TEXT NOT NULL, end_type TEXT NOT NULL, start_elapsed REAL NOT NULL, end_elapsed REAL NOT NULL,
    seconds REAL NOT NULL, clock_ok BOOLEAN NOT NULL, first_attempt_sec REAL, transition BOOLEAN,
    action_from INTEGER, action_to INTEGER, stint_no SMALLINT, stint_no_end SMALLINT,
    off_score SMALLINT NOT NULL, def_score SMALLINT NOT NULL, pts SMALLINT NOT NULL,
    {stats}, second_chance_pts SMALLINT NOT NULL, off_tech_pts SMALLINT NOT NULL, def_tech_pts SMALLINT NOT NULL,
    and_one BOOLEAN NOT NULL, empty BOOLEAN NOT NULL, tracked_ok BOOLEAN NOT NULL)"""

GAMES_DDL = """CREATE TABLE {schema}possession_games (
    game_id TEXT PRIMARY KEY, nba_game_id TEXT, season SMALLINT NOT NULL, game_date DATE, home_team TEXT, away_team TEXT,
    possessions SMALLINT, home_poss SMALLINT, away_poss SMALLINT, empty_poss SMALLINT,
    home_est REAL, away_est REAL, home_team_oreb SMALLINT, away_team_oreb SMALLINT,
    points_method TEXT, home_pts SMALLINT, away_pts SMALLINT, final_home SMALLINT, final_away SMALLINT,
    points_ok BOOLEAN, comp_ok BOOLEAN, stints_ok BOOLEAN, game_ok BOOLEAN NOT NULL,
    multi_stint SMALLINT, other_starts SMALLINT, chart_clock_share REAL, counted_events INTEGER,
    diag TEXT, reason TEXT)"""

# Per season x team x start type, offence and defence, from game_ok games; team 'ALL' and start_type 'all' are totals.
SEASONS_SQL = """
    WITH p AS (SELECT * FROM {schema}possessions WHERE game_id IN (SELECT game_id FROM {schema}possession_games WHERE game_ok)),
    sides AS (
        SELECT season, offense AS team, 'off' AS side, start_type, game_id, pts, seconds, clock_ok, transition,
               (oreb + team_oreb) > 0 AS had_oreb, second_chance_pts, fga, fg3a, fta, tov FROM p
        UNION ALL
        SELECT season, defense, 'def', start_type, game_id, pts, seconds, clock_ok, transition,
               (oreb + team_oreb) > 0, second_chance_pts, fga, fg3a, fta, tov FROM p),
    agg AS (
        SELECT season, COALESCE(team, 'ALL') AS team, COALESCE(start_type, 'all') AS start_type, side,
               COUNT(DISTINCT game_id) AS games, COUNT(*) AS poss, SUM(pts) AS pts,
               AVG(seconds) FILTER (WHERE clock_ok) AS avg_seconds, COUNT(*) FILTER (WHERE transition IS NOT NULL) AS timed_poss,
               COUNT(*) FILTER (WHERE transition) AS trans_poss, SUM(pts) FILTER (WHERE transition) AS trans_pts,
               COUNT(*) FILTER (WHERE had_oreb) AS oreb_poss, SUM(second_chance_pts) AS second_chance_pts,
               SUM(fga) AS fga, SUM(fg3a) AS fg3a, SUM(fta) AS fta, SUM(tov) AS tov
        FROM sides GROUP BY GROUPING SETS ((season, side, team, start_type), (season, side, team),
                                           (season, side, start_type), (season, side)))
    SELECT o.season, o.team, o.start_type, o.games,
           o.poss, o.pts::integer AS pts, ROUND(o.pts::numeric / o.poss, 4)::double precision AS ppp,
           ROUND(o.avg_seconds::numeric, 2)::double precision AS avg_seconds,
           o.timed_poss, o.trans_poss, COALESCE(o.trans_pts, 0)::integer AS trans_pts,
           ROUND(o.trans_poss::numeric / NULLIF(o.timed_poss, 0), 4)::double precision AS trans_share,
           ROUND(o.trans_pts::numeric / NULLIF(o.trans_poss, 0), 4)::double precision AS trans_ppp,
           o.oreb_poss, o.second_chance_pts::integer AS second_chance_pts,
           o.fga::integer AS fga, o.fg3a::integer AS fg3a, o.fta::integer AS fta, o.tov::integer AS tov,
           d.poss AS d_poss, d.pts::integer AS d_pts, ROUND(d.pts::numeric / NULLIF(d.poss, 0), 4)::double precision AS d_ppp,
           d.timed_poss AS d_timed_poss, d.trans_poss AS d_trans_poss,
           ROUND(d.trans_pts::numeric / NULLIF(d.trans_poss, 0), 4)::double precision AS d_trans_ppp
    FROM agg o LEFT JOIN agg d ON d.season = o.season AND d.team = o.team AND d.start_type = o.start_type AND d.side = 'def'
    WHERE o.side = 'off'
"""


def clean(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    return v.item() if hasattr(v, "item") else v


POSS_COLS = ["game_id", "poss_no", "season", "period", "offense", "defense", "off_home", "start_type",
             "end_type", "start_elapsed", "end_elapsed", "seconds", "clock_ok", "first_attempt_sec", "transition",
             "action_from", "action_to", "stint_no", "stint_no_end", "off_score", "def_score", "pts"] + STAT_COLS + \
            ["second_chance_pts", "off_tech_pts", "def_tech_pts", "and_one", "empty", "tracked_ok"]


def create_tables(cur, schema):
    for t in ("possession_seasons", "possession_meta", "possession_games", "possessions"):
        cur.execute(f"DROP TABLE IF EXISTS {schema}{t};")
    stats = ", ".join(f"{k} SMALLINT NOT NULL" for k in STAT_COLS)
    cur.execute(POSS_DDL.format(schema=schema, stats=stats))


def insert_possessions(cur, schema, rows):
    """Rows go to the table as they are built (1.5M dicts would not fit in this machine's memory)."""
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO {schema}possessions ({', '.join(POSS_COLS)}) VALUES %s",
        [tuple(clean(r[c]) for c in POSS_COLS) for r in rows], page_size=10000)


def finish_tables(cur, schema, game_rows, meta):
    cur.execute(f"ALTER TABLE {schema}possessions ADD PRIMARY KEY (game_id, poss_no);")
    cur.execute(f"CREATE INDEX ON {schema}possessions (season, offense);")
    cur.execute(f"CREATE INDEX ON {schema}possessions (season, defense);")

    cur.execute(GAMES_DDL.format(schema=schema))
    gcols = list(game_rows[0].keys())
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO {schema}possession_games ({', '.join(gcols)}) VALUES %s",
        [tuple(clean(r[c]) for c in gcols) for r in game_rows], page_size=2000)
    cur.execute(f"CREATE INDEX ON {schema}possession_games (season);")

    cur.execute(f"CREATE TABLE {schema}possession_seasons AS " + SEASONS_SQL.format(schema=schema))
    cur.execute(f"ALTER TABLE {schema}possession_seasons ADD PRIMARY KEY (season, team, start_type);")

    cur.execute(f"CREATE TABLE {schema}possession_meta (key TEXT PRIMARY KEY, value JSONB NOT NULL)")
    psycopg2.extras.execute_values(cur, f"INSERT INTO {schema}possession_meta (key, value) VALUES %s",
                                   [(k, json.dumps(v)) for k, v in meta.items()])


def transition_check(timing):
    """Time to the first attempt (corrected clock) by start type, and the take-foul check. `timing`: (start_type,
    first_attempt_sec, pts, take-foul seconds, season) per possession."""
    df = pd.DataFrame(timing, columns=["start_type", "fa", "pts", "take_sec", "season"])
    live = df[df.start_type.isin(TRANSITION_STARTS) & df.fa.notna()]       # fa is NULL unless the start is anchored
    hist = np.histogram(live.fa.clip(upper=24), bins=np.arange(0, 25, 1))[0]
    dr = live[live.start_type == "dreb"]
    take = df.take_sec.dropna()
    return {
        "window_seconds": TRANSITION_SECONDS,
        "first_attempt_hist_0_24s": [int(x) for x in hist],
        "ppp_by_first_attempt_second": [round(float(live[(live.fa >= a) & (live.fa < a + 1)].pts.mean()), 3)
                                        for a in range(0, 24)],
        # the window's basis: after a player's defensive rebound, the early-shot premium fades by ~7 s
        "dreb_ppp_by_first_attempt_second": [round(float(dr[(dr.fa >= a) & (dr.fa < a + 1)].pts.mean()), 3)
                                             for a in range(0, 24)],
        "dreb_n_by_first_attempt_second": [int(((dr.fa >= a) & (dr.fa < a + 1)).sum()) for a in range(0, 24)],
        "take_fouls": int(len(take)),
        "take_fouls_within_window": round(float((take <= TRANSITION_SECONDS).mean()), 4) if len(take) else None,
        "take_foul_sec_quartiles": [round(float(x), 1) for x in take.quantile([0.25, 0.5, 0.75])] if len(take) else None,
    }


def print_checks(cur, schema, conn):
    print("\nPer season (game_ok games):")
    print(pd.read_sql_query(f"""
        SELECT season, COUNT(*) games, SUM(game_ok::int) ok, SUM(points_ok::int) pts_ok, SUM(comp_ok::int) comp_ok,
               SUM(stints_ok::int) stints_ok, SUM((points_method = 'score')::int) by_score,
               ROUND(AVG((home_poss + away_poss) / 2.0), 2) poss_per_team,
               ROUND(AVG((home_poss + away_poss - home_est - away_est) / 2.0)::numeric, 2) vs_est,
               ROUND(AVG((home_poss + away_poss - home_est - away_est + home_team_oreb + away_team_oreb) / 2.0)::numeric, 2) vs_est_team_oreb,
               ROUND(CORR(home_poss, home_est)::numeric, 3) r_est,
               ROUND(AVG(chart_clock_share)::numeric, 4) chart_clock
        FROM {schema}possession_games GROUP BY 1 ORDER BY 1""", conn).to_string(index=False))
    print(pd.read_sql_query(f"""
        SELECT season, COUNT(*) poss, ROUND(AVG(empty::int), 4) empty, ROUND(AVG((stint_no <> stint_no_end)::int), 4) multi_stint,
               ROUND(AVG(transition::int), 4) transition, ROUND(AVG((transition IS NULL)::int), 4) untimed,
               ROUND(AVG(clock_ok::int), 4) clock_ok, ROUND(AVG(tracked_ok::int), 4) tracked,
               ROUND(SUM(pts)::numeric / COUNT(*), 4) ppp, ROUND(AVG(seconds)::numeric, 2) secs
        FROM {schema}possessions GROUP BY 1 ORDER BY 1""", conn).to_string(index=False))
    print("\nLeague points per possession by start type (all seasons, game_ok):")
    print(pd.read_sql_query(f"""
        SELECT start_type, SUM(poss) poss, ROUND(SUM(pts)::numeric / SUM(poss), 3) ppp, ROUND(AVG(avg_seconds)::numeric, 1) secs,
               ROUND(SUM(trans_poss)::numeric / NULLIF(SUM(timed_poss), 0), 3) trans_share, ROUND(SUM(trans_pts)::numeric / NULLIF(SUM(trans_poss), 0), 3) trans_ppp
        FROM {schema}possession_seasons WHERE team = 'ALL' GROUP BY 1 ORDER BY 2 DESC""", conn).to_string(index=False))
    # Possessions inside one stint, and what lies between when they're not.
    cur.execute(f"""SELECT COUNT(*) FILTER (WHERE stint_no = stint_no_end), COUNT(*),
                           COUNT(*) FILTER (WHERE stint_no <> stint_no_end AND fta > 0) FROM {schema}possessions""")
    one, n, ft = cur.fetchone()
    print(f"\nPossessions inside one stint: {one:,} of {n:,} ({one / n:.2%}); of the rest, {ft:,} have free throws")
    for t in ("possessions", "possession_games", "possession_seasons", "possession_meta"):
        cur.execute(f"SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('{schema}{t}')) FROM {schema}{t}")
        c, pretty = cur.fetchone()
        print(f"  {t}: {c:,} rows, {pretty}")


def main():
    dry = "--dry-run" in sys.argv
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    season_names, all_names = load_season_names(cur)
    final_pts, nba_ids, team_tot, stored_stints = load_reference(conn)
    games, grouped = load_espn(conn)
    if dry:
        games = games[games.season == TWIN_SEASON]
    matched = chart_matches(conn, games, grouped, season_names, all_names)
    calls, _ = miss_three_calls(conn, games, grouped, season_names, all_names, matched=matched)
    chart_t = chart_clock(conn, matched)
    print(f"{len(games)} games; {matched.nba_shot_id.notna().mean():.2%} of field goals matched to the shot chart "
          f"({time.time() - t0:.0f}s)")

    schema = f"{DRY_SCHEMA}." if dry else ""
    if dry:
        cur.execute(f"DROP SCHEMA IF EXISTS {DRY_SCHEMA} CASCADE; CREATE SCHEMA {DRY_SCHEMA};")
    create_tables(cur, schema)
    n_poss, game_rows, diag, timing, batch = 0, [], Counter(), [], []
    for i, g in enumerate(games.itertuples(index=False)):
        ev = grouped.get(g.game_id)
        if ev is None:
            continue
        rows, game_row, d = build_game(g, ev, season_names, all_names, calls, chart_t, final_pts, nba_ids, team_tot,
                                       stored_stints)
        for r in rows:
            n_poss += 1
            timing.append((r["start_type"], r["first_attempt_sec"], r["pts"], r["_take"], r["season"]))
        batch += rows
        game_rows.append(game_row)
        diag.update(d)
        if len(batch) >= 50000:
            insert_possessions(cur, schema, batch)
            batch = []
        if i % 1000 == 0:
            print(f"  {i} games, {n_poss:,} possessions ({time.time() - t0:.0f}s)")
    insert_possessions(cur, schema, batch)
    print(f"{len(game_rows)} games, {n_poss:,} possessions; parser diag {dict(diag)} ({time.time() - t0:.0f}s)")

    clock = clock_check(conn, season_names, all_names, chart_t, grouped)
    print("\nClock check against NBA.com's play-by-play (twin games):")
    print(pd.DataFrame(clock["classes"]).T[["n", "espn_lag_median", "espn_lag_median_odd", "espn_lag_median_even",
                                            "espn_within_2s", "corrected_within_2s", "corrected_within_2s_even",
                                            "espn_within_5s", "corrected_within_5s"]].to_string())
    print(clock["all"])
    trans = transition_check(timing)
    print(f"\nTransition window {TRANSITION_SECONDS:.0f} s: first-attempt histogram {trans['first_attempt_hist_0_24s']}")
    print(f"  points per possession by first-attempt second: {trans['ppp_by_first_attempt_second']}")
    print(f"  after a defensive rebound: {trans['dreb_ppp_by_first_attempt_second']}")
    print(f"  take fouls inside the window: {trans['take_fouls_within_window']} of {trans['take_fouls']} "
          f"(quartiles {trans['take_foul_sec_quartiles']} s)")
    meta = {
        "rules": {"transition_seconds": TRANSITION_SECONDS, "transition_starts": list(TRANSITION_STARTS),
                  "empty_min_seconds": PP.EMPTY_MIN_SECONDS, "retain_fouls": sorted(PP.RETAIN_FOULS),
                  "start_types": PP.START_TYPES, "end_types": PP.END_TYPES},
        "clock_constants": {"lag_fg_made": PP.LAG_FG_MADE, "lag_fg_miss": PP.LAG_FG_MISS,
                            "lag_tov_steal": PP.LAG_TOV_STEAL, "lag_tov_dead": PP.LAG_TOV_DEAD,
                            "lag_reb": PP.LAG_REB, "reb_gap": PP.REB_GAP},
        "clock_check": clock, "transition_check": trans, "parser_diag": dict(diag),
    }

    finish_tables(cur, schema, game_rows, meta)
    conn.commit()
    print(f"\nwrote {n_poss:,} possessions, {len(game_rows)} games ({time.time() - t0:.0f}s)")
    print_checks(cur, schema, conn)
    if dry:
        print(f"\nDry run: tables are in schema {DRY_SCHEMA}; drop it when done.")
    conn.close()


if __name__ == "__main__":
    main()
