"""
Team profile page: the team twin of the player profile, one aggregating call.

    GET /team-profile/{abbr}?season=     every block below for one team-season

`abbr` can be any code the team has used (PHX or PHO, BKN or BRK or NJN) or
today's code of its franchise; with a season, the franchise's team in that
season is shown (OKC + 2005 opens the 2004-05 Seattle SuperSonics, with a
note). Without a season, its latest one.

Blocks, each with the seasons it covers and a reason when it has nothing:
  summary     record, Pythagorean wins, margin, SRS/SOS, ratings, pace, four
              factors on both ends, playoffs, ranks among that season's
              teams (team_seasons, Basketball-Reference; every season)
  franchise   every season of the franchise (team_seasons)
  games       game-by-game results and home/away and rest records
              (team_game_fatigue + game_scores, 2009-10 on)
  luck        expected wins, luck, close games, SRS/SOS (team_luck_schedule)
  roster      who played and how much, with roles (player_season_stats,
              player_team_stints, player_game_lines from 2020-21, player_roles)
  payroll     salary and surplus value (contract_value, covered seasons only)
  lineups     best and worst five-man units: every play-by-play stint from
              2020-21 (lineup_seasons, tracked stints only, with the games
              excluded or partial), the stored top-2,000 before (lineup_stats)
  on_off      biggest on-minus-off gaps and the top-usage player (player_on_off)
  shot_mix    where the team shot and where opponents shot against it, vs.
              the league (team_zone_mix + league_zone_mix, 1996-97 on)

Every block reads the same stored tables as the page it comes from, so the
team page never disagrees with Luck & Schedule, On/Off or Contract Value.
Coverage spans are cached per process: restart impact_api after a rebuild.
"""

import math
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from lineups_lib import lineup_sources, team_stint_coverage
from routers.on_off import DEFAULT_MIN_MINUTES as ON_OFF_MIN, STAR_MINUTES as ON_OFF_STAR_MIN
from source_badge import make_source
from teams_lib import lookup_codes

router = APIRouter()

LINEUP_MIN_MINUTES = 100
LINEUP_SHOW = 5
ON_OFF_SHOW = 3
PAYROLL_SHOW = 3
SMALL_GP = 10

TABLES = ["team_seasons", "team_game_fatigue", "game_scores", "team_luck_schedule", "player_season_stats",
          "player_team_stints", "player_game_lines", "player_roles", "contract_value", "lineup_stats",
          "lineup_seasons", "lineup_stint_games", "player_on_off", "team_zone_mix", "league_zone_mix"]
UPSTREAM = "Stored tables only (Basketball-Reference team summaries, ESPN scores and play-by-play, nba_api)"

SUMMARY_COLS = ["season", "abbreviation", "bref_abbreviation", "franchise", "team_name", "playoffs", "g", "w", "l",
                "pw", "pl", "mov", "sos", "srs", "o_rtg", "d_rtg", "n_rtg", "pace", "age", "f_tr", "x3p_ar",
                "ts_percent", "e_fg_percent", "tov_percent", "orb_percent", "ft_fga", "opp_e_fg_percent",
                "opp_tov_percent", "drb_percent", "opp_ft_fga", "arena", "attend", "attend_g", "mp_per_game",
                "pts_per_game", "opp_pts_per_game"]
# Stat -> True when higher is better (for ranks among the season's teams).
RANKED = {"w_pct": True, "srs": True, "mov": True, "o_rtg": True, "d_rtg": False, "n_rtg": True, "pace": True,
          "e_fg_percent": True, "tov_percent": False, "orb_percent": True, "ft_fga": True,
          "opp_e_fg_percent": False, "opp_tov_percent": True, "drb_percent": True, "opp_ft_fga": False,
          "x3p_ar": True, "ts_percent": True}


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _num(v, d=3):
    if v is None:
        return None
    if isinstance(v, (int, bool, str)):
        return v
    v = float(v)
    return None if math.isnan(v) else round(v, d)


def _span_label(lo, hi):
    return None if lo is None else f"{label(lo)} to {label(hi)}"


def _seasons_label(seasons):
    """[2006, 2007, 2008, 2019, 2020, 2025] -> '2005-06 to 2007-08, 2018-19 to 2019-20, 2024-25'."""
    runs, out = [], []
    for s in sorted(seasons):
        if runs and s == runs[-1][1] + 1:
            runs[-1][1] = s
        else:
            runs.append([s, s])
    for a, b in runs:
        out.append(label(a) if a == b else f"{label(a)} to {label(b)}")
    return ", ".join(out)


def _missing(reason, coverage=None):
    return {"available": False, "reason": reason, "coverage": coverage}


@lru_cache(maxsize=1)
def _coverage():
    spans = {
        "summary": "SELECT min(season), max(season) FROM team_seasons WHERE NOT is_league_avg",
        "games": "SELECT min(season), max(season) FROM game_scores",
        "luck": "SELECT min(season), max(season) FROM team_luck_schedule",
        "roster": "SELECT min(season), max(season) FROM player_season_stats",
        "game_lines": "SELECT min(season), max(season) FROM player_game_lines",
        "roles": "SELECT min(season), max(season) FROM player_roles",
        "lineups": "SELECT min(season), max(season) FROM lineup_stats",
        "stints": "SELECT min(season), max(season) FROM lineup_stint_seasons",
        "on_off": "SELECT min(season), max(season) FROM player_on_off",
        "shot_mix": "SELECT min(LEFT(season, 4)::int + 1), max(LEFT(season, 4)::int + 1) FROM team_zone_mix",
    }
    out = {}
    with get_db() as conn:
        cur = conn.cursor()
        for k, sql in spans.items():
            cur.execute(sql)
            lo, hi = cur.fetchone()
            out[k] = {"from": lo, "to": hi, "label": _span_label(lo, hi)}
        cur.execute("SELECT season, included FROM contract_value_seasons ORDER BY season")
        rows = cur.fetchall()
        out["payroll"] = {"included": [s for s, inc in rows if inc], "excluded": [s for s, inc in rows if not inc]}
    return out


def _in(cov, season):
    return cov["from"] is not None and cov["from"] <= season <= cov["to"]


# ─── Identity ────────────────────────────────────────────────────────────
def _resolve(cur, abbr, season):
    codes = sorted(lookup_codes(abbr))
    cur.execute("""SELECT season, abbreviation, franchise FROM team_seasons
                   WHERE NOT is_league_avg AND (abbreviation = ANY(%s) OR bref_abbreviation = ANY(%s))
                   ORDER BY season DESC""", (codes, codes))
    exact = cur.fetchall()
    if exact:
        franchise = exact[0][2]
    else:
        cur.execute("SELECT DISTINCT franchise FROM team_seasons WHERE franchise = ANY(%s)", (codes,))
        r = cur.fetchone()
        if not r:
            raise HTTPException(status_code=404, detail=f"No team uses the code {abbr.upper()}.")
        franchise = r[0]
    cur.execute("""SELECT season, abbreviation, team_name FROM team_seasons
                   WHERE NOT is_league_avg AND franchise = %s ORDER BY season""", (franchise,))
    fr_rows = cur.fetchall()
    if season is None:
        season = exact[0][0] if exact else fr_rows[-1][0]
    this = next((r for r in fr_rows if r[0] == season), None)
    if this is None:
        raise HTTPException(status_code=404, detail=(
            f"The {fr_rows[-1][2]} franchise has no {label(season)} season on file "
            f"(it played {label(fr_rows[0][0])} to {label(fr_rows[-1][0])})."))
    note = None
    if this[1] not in codes:
        note = f"In {label(season)} this franchise played as the {this[2]} ({this[1]})."
    return season, this[1], franchise, note


# ─── Blocks ──────────────────────────────────────────────────────────────
def _summary(cur, season, abbr):
    cur.execute(f"""SELECT {', '.join(SUMMARY_COLS)}, is_league_avg FROM team_seasons
                    WHERE season = %s AND (abbreviation = %s OR is_league_avg)""", (season, abbr))
    rows = [dict(zip(SUMMARY_COLS + ["is_league_avg"], r)) for r in cur.fetchall()]
    team = next(r for r in rows if not r["is_league_avg"])
    league = next((r for r in rows if r["is_league_avg"]), None)
    cur.execute(f"""SELECT abbreviation, w, l, {', '.join(k for k in RANKED if k != 'w_pct')}
                    FROM team_seasons WHERE season = %s AND NOT is_league_avg""", (season,))
    cols = ["abbreviation", "w", "l"] + [k for k in RANKED if k != "w_pct"]
    all_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in all_rows:
        r["w_pct"] = r["w"] / (r["w"] + r["l"]) if (r["w"] or 0) + (r["l"] or 0) else None
    team["w_pct"] = team["w"] / (team["w"] + team["l"]) if team["w"] is not None else None
    ranks = {}
    for k, higher in RANKED.items():
        vals = [r[k] for r in all_rows if r[k] is not None]
        if team.get(k) is None or not vals:
            continue
        better = sum(1 for v in vals if (v > team[k] if higher else v < team[k]))
        ranks[k] = better + 1
    out = {k: _num(v) for k, v in team.items() if k != "is_league_avg"}
    for k in ("abbreviation", "bref_abbreviation", "franchise", "team_name", "arena", "playoffs"):
        out[k] = team[k]
    return {
        "available": True, "coverage": _coverage()["summary"]["label"], "reason": None,
        "team": out, "ranks": ranks, "n_teams": len(all_rows),
        "league": {k: _num(v) for k, v in league.items() if k not in ("abbreviation", "bref_abbreviation",
                                                                          "franchise", "team_name", "arena",
                                                                          "is_league_avg", "playoffs")} if league else None,
        "ratings_note": ("Ratings, pace and four factors are Basketball-Reference's (points per 100 possessions); "
                         "ranks are among that season's teams."),
    }


def _franchise(cur, franchise):
    cur.execute("""SELECT season, abbreviation, team_name, w, l, srs, playoffs FROM team_seasons
                   WHERE franchise = %s AND NOT is_league_avg ORDER BY season""", (franchise,))
    return [{"season": s, "abbreviation": a, "team_name": n, "w": _num(w, 0), "l": _num(l, 0), "srs": _num(srs, 2),
             "playoffs": p} for s, a, n, w, l, srs, p in cur.fetchall()]


def _games(cur, season, abbr):
    cov = _coverage()["games"]
    if not _in(cov, season):
        return _missing(f"Game-by-game scores and schedule start in {label(cov['from'])} (team_game_fatigue, from "
                        "the NBA schedule, and final scores from ESPN); earlier seasons have season totals only.",
                        cov["label"])
    cur.execute("""SELECT g.game_date, g.opponent, g.is_home, g.neutral_site, g.pts_for, g.pts_against, g.periods,
                          f.rest_days, f.is_b2b
                   FROM game_scores g JOIN team_game_fatigue f USING (game_id, team_abbreviation)
                   WHERE g.season = %s AND g.team_abbreviation = %s ORDER BY g.game_date""", (season, abbr))
    games = [{"date": d.isoformat(), "opponent": o, "home": h, "neutral": n, "pts_for": pf, "pts_against": pa,
              "ot": per > 4, "rest_days": rd, "b2b": b2b} for d, o, h, n, pf, pa, per, rd, b2b in cur.fetchall()]
    if not games:
        return _missing("No games on file for this team-season.", cov["label"])

    def rec(gs):
        w = sum(1 for g in gs if g["pts_for"] > g["pts_against"])
        m = sum(g["pts_for"] - g["pts_against"] for g in gs) / len(gs) if gs else None
        return {"w": w, "l": len(gs) - w, "n": len(gs), "margin": _num(m, 2)}

    splits = [
        {"key": "home", "label": "Home", **rec([g for g in games if g["home"] and not g["neutral"]])},
        {"key": "away", "label": "Away", **rec([g for g in games if not g["home"] and not g["neutral"]])},
        {"key": "b2b", "label": "Second night of a back-to-back", **rec([g for g in games if g["rest_days"] == 0])},
        {"key": "rest1", "label": "One day of rest", **rec([g for g in games if g["rest_days"] == 1])},
        {"key": "rest2", "label": "Two or more days of rest", **rec([g for g in games if (g["rest_days"] or 0) >= 2])},
    ]
    neutral = [g for g in games if g["neutral"]]
    if neutral:
        splits.insert(2, {"key": "neutral", "label": "Neutral site", **rec(neutral)})
    return {"available": True, "coverage": cov["label"], "reason": None, "games": games, "splits": splits,
            "small_split_games": SMALL_GP,
            "note": ("Margins are real final scores (game_scores). Rest counts days off since the team's previous "
                     "game; the season opener has none and is left out of the rest rows.")}


def _luck(cur, season, abbr):
    cov = _coverage()["luck"]
    if not _in(cov, season):
        return _missing(f"Luck and SRS need every game's final score, on file from {label(cov['from'])}.",
                        cov["label"])
    cur.execute("""SELECT wins, losses, exp_wins, luck, luck_rank, close3_w, close3_l, close5_w, close5_l, ot_w, ot_l,
                          srs, sos, srs_rank, mov, games,
                          (SELECT COUNT(*) FROM team_luck_schedule t2 WHERE t2.season = t.season)
                   FROM team_luck_schedule t WHERE season = %s AND team_abbreviation = %s""", (season, abbr))
    r = cur.fetchone()
    if not r:
        return _missing("No row for this team-season in team_luck_schedule.", cov["label"])
    keys = ["wins", "losses", "exp_wins", "luck", "luck_rank", "close3_w", "close3_l", "close5_w", "close5_l", "ot_w",
            "ot_l", "srs", "sos", "srs_rank", "mov", "games", "n_teams"]
    d = {k: _num(v, 2) for k, v in zip(keys, r)}
    cur.execute("""SELECT COUNT(*) + 1 FROM team_luck_schedule WHERE season = %s
                   AND sos > (SELECT sos FROM team_luck_schedule WHERE season = %s AND team_abbreviation = %s)""",
                (season, season, abbr))
    d["sos_rank"] = cur.fetchone()[0]
    return {"available": True, "coverage": cov["label"], "reason": None, **d,
            "note": "Luck = actual minus expected wins (Pythagorean from points). SOS rank 1 = hardest schedule."}


def _names(cur, ids):
    if not ids:
        return {}
    cur.execute("""SELECT DISTINCT ON (player_id) player_id, player_name FROM player_season_stats
                   WHERE player_id = ANY(%s) ORDER BY player_id, season DESC""", (list(ids),))
    return dict(cur.fetchall())


def _roster(cur, season, abbr):
    cov = _coverage()
    stat_cols = ["gp", "min", "pts", "reb", "ast", "ts_pct", "usg_pct", "bpm", "age"]
    cur.execute(f"""SELECT player_id, player_name, team_abbreviation, {', '.join(stat_cols)}
                    FROM player_season_stats WHERE season = %s""", (season,))
    pss = {r[0]: dict(zip(["player_id", "player_name", "team"] + stat_cols, r)) for r in cur.fetchall()}
    cur.execute("SELECT player_id, role, family FROM player_roles WHERE season = %s", (season,))
    roles = {p: (role, fam) for p, role, fam in cur.fetchall()}
    rows, left_out = [], []
    uses_lines = _in(cov["game_lines"], season)
    if uses_lines:
        # The play-by-play records each player's team game by game: the truth for who played here.
        cur.execute("""SELECT player_id, team_abbreviation, COUNT(*) FILTER (WHERE seconds > 0),
                              SUM(seconds) / 60.0, SUM(pts)
                       FROM player_game_lines WHERE season = %s GROUP BY 1, 2""", (season,))
        teams_of = {}
        for pid, team, gp, mins, pts in cur.fetchall():
            teams_of.setdefault(pid, {})[team] = (gp, float(mins or 0), pts)
        for pid, teams in teams_of.items():
            if abbr not in teams or not teams[abbr][0]:
                continue
            gp, mins, pts = teams[abbr]
            played = {t for t, v in teams.items() if v[0]}
            base = pss.get(pid)
            if played == {abbr} and base:
                rows.append({**base, "split": False, "total_min": (base["gp"] or 0) * (base["min"] or 0)})
            else:
                rows.append({"player_id": pid, "player_name": base["player_name"] if base else None, "gp": gp,
                             "min": mins / gp, "pts": pts / gp, "split": True, "total_min": mins,
                             "other_teams": sorted(played - {abbr})})
        for pid, base in pss.items():
            if base["team"] == abbr and abbr not in {t for t, v in teams_of.get(pid, {}).items() if v[0]} \
                    and pid in teams_of:
                left_out.append({"player_id": pid, "player_name": base["player_name"],
                                 "played_for": sorted(t for t, v in teams_of[pid].items() if v[0])})
    else:
        cur.execute("SELECT player_id, gp, minutes, pts FROM player_team_stints WHERE season = %s AND team = %s",
                    (season, abbr))
        stints = {p: (gp, mins, pts) for p, gp, mins, pts in cur.fetchall()}
        cur.execute("SELECT DISTINCT player_id FROM player_team_stints WHERE season = %s", (season,))
        traded = {r[0] for r in cur.fetchall()}
        for pid, base in pss.items():
            if base["team"] == abbr and pid not in traded:
                rows.append({**base, "split": False, "total_min": (base["gp"] or 0) * (base["min"] or 0)})
        for pid, (gp, mins, pts) in stints.items():
            base = pss.get(pid, {})
            rows.append({"player_id": pid, "player_name": base.get("player_name"), "gp": gp,
                         "min": (mins / gp) if gp and mins is not None else None,
                         "pts": (pts / gp) if gp and pts is not None else None, "split": True,
                         "total_min": mins or 0})
    if not rows:
        return _missing("No player rows on file for this team-season (the earliest BAA seasons have gaps).",
                        cov["roster"]["label"])
    missing_names = {r["player_id"] for r in rows if not r.get("player_name")}
    names = _names(cur, missing_names)
    for r in rows:
        r["player_name"] = r.get("player_name") or names.get(r["player_id"])
        r.pop("team", None)
        role = roles.get(r["player_id"])
        r["role"], r["role_family"] = (role if role else (None, None))
        r["small_sample"] = (r.get("gp") or 0) < SMALL_GP
        for k in list(r):
            if isinstance(r[k], float):
                r[k] = _num(r[k], 3)
    rows.sort(key=lambda r: -(r["total_min"] or 0))
    roles_cov = cov["roles"]
    return {
        "available": True, "coverage": cov["roster"]["label"], "reason": None, "players": rows,
        "source": "play-by-play game lines" if uses_lines else "season rows and traded-player stints",
        "left_out": left_out,
        "roles_note": (None if _in(roles_cov, season) else
                       f"Player roles cover {roles_cov['label']} (15+ minutes a game in 20+ games)."),
        "note": ("A player traded during the season shows only his games, minutes and points here (per game); his "
                 "full-season line is on his profile."),
    }


def _payroll(cur, season, abbr):
    cov = _coverage()["payroll"]
    label_cov = _seasons_label(cov["included"])
    if season not in cov["included"]:
        why = ("its salary file covers too few of that season's minutes" if season in cov["excluded"]
               else "there's no salary file for it")
        return _missing(f"Salaries are on file for {label_cov} only; not {label(season)}: {why}.", label_cov)
    # From 2020-21 on a player's team is the one he played the most games for in the play-by-play,
    # since a few season rows list a later team; before that, contract_value's own team.
    cur.execute("""WITH lt AS (SELECT DISTINCT ON (player_id) player_id, team_abbreviation AS team
                               FROM player_game_lines WHERE season = %s AND seconds > 0
                               GROUP BY player_id, team_abbreviation ORDER BY player_id, COUNT(*) DESC)
                   SELECT c.player_id, c.player_name, c.salary, c.fair_value, c.surplus, c.war, c.minutes,
                          COALESCE(lt.team, c.team_abbreviation) AS team
                   FROM contract_value c LEFT JOIN lt USING (player_id) WHERE c.season = %s""", (season, season))
    allrows = [dict(zip(["player_id", "player_name", "salary", "fair_value", "surplus", "war", "minutes", "team"], r))
               for r in cur.fetchall()]
    rows = [r for r in allrows if r["team"] == abbr]
    paid = [r for r in rows if r["salary"] is not None]
    if not paid:
        return _missing("No salaried player is attributed to this team in contract_value.", label_cov)
    team_tot = {}
    for r in allrows:
        if r["salary"] is not None and not str(r["team"]).endswith("TM"):
            team_tot[r["team"]] = team_tot.get(r["team"], 0.0) + float(r["salary"])
    payrolls = sorted(team_tot.values(), reverse=True)
    unattributed = sum(1 for r in allrows if str(r["team"]).endswith("TM"))
    total = sum(float(r["salary"]) for r in paid)
    by_surplus = sorted([r for r in paid if r["surplus"] is not None], key=lambda r: -float(r["surplus"]))
    clean = lambda r: {k: (_num(v, 1) if isinstance(v, float) else v) for k, v in r.items()}  # noqa: E731
    return {
        "available": True, "coverage": label_cov, "reason": None,
        "payroll": round(total), "players": len(paid), "payroll_rank": 1 + sum(1 for p in payrolls if p > total),
        "n_teams": len(payrolls),
        "surplus": round(sum(float(r["surplus"] or 0) for r in paid)),
        "fair_value": round(sum(float(r["fair_value"] or 0) for r in paid)),
        "best": [clean(r) for r in by_surplus[:PAYROLL_SHOW]],
        "worst": [clean(r) for r in by_surplus[::-1][:PAYROLL_SHOW]],
        "unattributed_players": unattributed,
        "note": ("Third-party salary files (see Contract Value): payroll here is the season salaries of the players "
                 "who played for the team, each counted once with one team (from 2020-21 the team he played the most "
                 "games for, earlier the team his season row lists; before 2009-10 traded players aren't attributed). "
                 "Surplus = the value of his wins above replacement minus his salary."),
    }


def _lineups(cur, season, abbr, team_minutes):
    sources = lineup_sources()
    cov = _coverage()["lineups"]
    if season not in sources:
        return _missing(f"Five-man lineups are stored from {label(cov['from'])} on (stats.nba.com's lineup "
                        "endpoint).", cov["label"])
    source = sources[season]
    if source == "stints":
        cur.execute("""SELECT player_ids, games, minutes, poss, off_rating, def_rating, net_rating FROM lineup_seasons
                       WHERE season = %s AND team_abbreviation = %s""", (season, abbr))
    else:
        cur.execute("""SELECT player_ids, gp, minutes, poss, off_rating, def_rating, net_rating FROM lineup_stats
                       WHERE season = %s AND team_abbreviation = %s""", (season, abbr))
    rows = [dict(zip(["player_ids", "gp", "minutes", "poss", "off_rating", "def_rating", "net_rating"], r))
            for r in cur.fetchall()]
    if not rows:
        return _missing("No stint of this team-season is tracked." if source == "stints"
                        else "None of this team's lineups made the season's 2,000 most-used.", cov["label"])
    stored = sum(float(r["minutes"] or 0) for r in rows)
    qualified = [r for r in rows if (r["minutes"] or 0) >= LINEUP_MIN_MINUTES]
    names = _names(cur, {p for r in qualified for p in r["player_ids"]})
    for r in qualified:
        r["players"] = [{"player_id": p, "player_name": names.get(p)} for p in r.pop("player_ids")]
        for k in ("minutes", "off_rating", "def_rating", "net_rating"):
            r[k] = _num(r[k], 1)
    qualified.sort(key=lambda r: -(r["net_rating"] or 0))
    if source == "stints":
        tc = team_stint_coverage(cur, season, abbr)
        share = tc["share"]
        note = ("Every stint of every game rebuilt from play-by-play; descriptive ratings, not adjusted for "
                "opponents, on possessions averaged over both sides (about 3 points under NBA.com's scale).")
        coverage = _coverage()["stints"]["label"]
    else:
        tc = None
        share = _num(stored / team_minutes, 3) if team_minutes else None
        note = ("Only the league's 2,000 most-used lineups a season are stored, so bench units are thin; "
                "descriptive ratings, not adjusted for opponents.")
        coverage = cov["label"]
    return {
        "available": True, "coverage": coverage, "reason": None, "source": source,
        "stored": len(rows), "qualified": len(qualified), "min_minutes": LINEUP_MIN_MINUTES,
        "stored_minutes": round(stored), "coverage_share": share, "stint_coverage": tc,
        "best": qualified[:LINEUP_SHOW],
        "worst": qualified[::-1][:LINEUP_SHOW] if len(qualified) > LINEUP_SHOW else [],
        "note": note,
    }


def _on_off(cur, season, abbr):
    cov = _coverage()["on_off"]
    if not _in(cov, season):
        return _missing(f"On/off needs every minute rebuilt from play-by-play, on file from {label(cov['from'])}.",
                        cov["label"])
    cur.execute("""SELECT player_id, minutes_on, minutes_off, net_on, net_off, on_off_net, on_off_ci_low,
                          on_off_ci_high, usg_pct
                   FROM player_on_off WHERE season = %s AND team_abbreviation = %s""", (season, abbr))
    keys = ["player_id", "minutes_on", "minutes_off", "net_on", "net_off", "on_off_net", "ci_low", "ci_high", "usg_pct"]
    rows = [dict(zip(keys, r)) for r in cur.fetchall()]
    qual = [r for r in rows if (r["minutes_on"] or 0) >= ON_OFF_MIN and r["on_off_net"] is not None]
    if not qual:
        return _missing(f"No player on this team reached {ON_OFF_MIN} minutes.", cov["label"])
    names = _names(cur, {r["player_id"] for r in qual})
    for r in qual:
        r["player_name"] = names.get(r["player_id"])
        r["ci_excludes_zero"] = bool(r["ci_low"] > 0 or r["ci_high"] < 0) if r["ci_low"] is not None else None
        for k in keys[1:]:
            r[k] = _num(r[k], 4 if k == "usg_pct" else 1)
    qual.sort(key=lambda r: -r["on_off_net"])
    stars = [r for r in qual if (r["minutes_on"] or 0) >= ON_OFF_STAR_MIN and r["usg_pct"] is not None]
    star = max(stars, key=lambda r: r["usg_pct"]) if stars else None
    return {
        "available": True, "coverage": cov["label"], "reason": None, "min_minutes": ON_OFF_MIN,
        "qualified": len(qual), "clear_of_zero": sum(1 for r in qual if r["ci_excludes_zero"]),
        "top": qual[:ON_OFF_SHOW], "bottom": qual[::-1][:ON_OFF_SHOW] if len(qual) > ON_OFF_SHOW else [],
        "star": star, "star_minutes": ON_OFF_STAR_MIN,
        "note": "On minus off net rating per 100 possessions with a 95% interval; descriptive, not RAPM.",
    }


def _shot_mix(cur, season, abbr):
    cov = _coverage()["shot_mix"]
    if not _in(cov, season):
        return _missing(f"Shot locations are on file from {label(cov['from'])} (nba_api shot charts).", cov["label"])
    sl = label(season)
    cur.execute("SELECT side, zone, fgm, fga FROM team_zone_mix WHERE season = %s AND team_abbreviation = %s",
                (sl, abbr))
    mine = {(s, z): (m, a) for s, z, m, a in cur.fetchall()}
    if not mine:
        return _missing("No located shots for this team-season.", cov["label"])
    cur.execute("SELECT zone, fgm, fga FROM league_zone_mix WHERE season = %s", (sl,))
    league = {z: (m, a) for z, m, a in cur.fetchall()}
    tot = {side: sum(a for (s, _), (m, a) in mine.items() if s == side) for side in ("team", "opponent")}
    ltot = sum(a for _, a in league.values())
    zones = []
    for z in ["Restricted Area", "In The Paint (Non-RA)", "Mid-Range", "Corner 3", "Above the Break 3"]:
        tm, ta = mine.get(("team", z), (0, 0))
        om, oa = mine.get(("opponent", z), (0, 0))
        lm, la = league.get(z, (0, 0))
        zones.append({
            "zone": z, "fga": ta, "share": _num(ta / tot["team"], 4) if tot["team"] else None,
            "fg_pct": _num(tm / ta, 4) if ta else None,
            "opp_fga": oa, "opp_share": _num(oa / tot["opponent"], 4) if tot["opponent"] else None,
            "opp_fg_pct": _num(om / oa, 4) if oa else None,
            "league_share": _num(la / ltot, 4) if ltot else None, "league_fg_pct": _num(lm / la, 4) if la else None,
        })
    return {"available": True, "coverage": cov["label"], "reason": None, "fga": tot["team"],
            "opp_fga": tot["opponent"], "zones": zones,
            "note": ("Every located regular-season shot, placed on a team game by game (99.98% placed; see "
                     "build_team_zone_mix.py). League = every team's shots that season.")}


@router.get("/team-profile/{abbr}")
def team_profile(abbr: str, season: Optional[int] = None):
    with get_db() as conn:
        cur = conn.cursor()
        season, code, franchise, note = _resolve(cur, abbr, season)
        summary = _summary(cur, season, code)
        t = summary["team"]
        team_minutes = (t["g"] or 0) * (t["mp_per_game"] or 0) / 5 if t.get("mp_per_game") else None
        pair_source = lineup_sources().get(season)
        if pair_source == "stints":
            cur.execute("SELECT 1 FROM pair_seasons WHERE season = %s AND team_abbreviation = %s LIMIT 1", (season, code))
        elif pair_source:
            cur.execute("SELECT 1 FROM lineup_stats WHERE season = %s AND team_abbreviation = %s LIMIT 1", (season, code))
        has_pairs = pair_source is not None and cur.fetchone() is not None
        profile = {
            "abbreviation": code, "season": season, "season_label": label(season), "franchise": franchise,
            "team_name": t["team_name"], "note": note,
            "summary": summary,
            "franchise_history": _franchise(cur, franchise),
            "games": _games(cur, season, code),
            "luck": _luck(cur, season, code),
            "roster": _roster(cur, season, code),
            "payroll": _payroll(cur, season, code),
            "lineups": _lineups(cur, season, code, team_minutes),
            "pairs": {"available": has_pairs, "source": pair_source,
                      "coverage": _coverage()["stints" if pair_source == "stints" else "lineups"]["label"],
                      "reason": None if has_pairs else "Pair Chemistry is built from the stored lineups, which "
                                                       f"start in {label(_coverage()['lineups']['from'])}."},
            "on_off": _on_off(cur, season, code),
            "shot_mix": _shot_mix(cur, season, code),
        }
    profile["_source"] = make_source(TABLES, UPSTREAM)
    return profile
