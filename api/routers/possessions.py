"""Possession Explorer: points per possession by how the possession began.

    GET /possessions/options          seasons (games, games that reconcile, possessions), teams, start types,
                                      the league by start type every season, the transition window and its basis
    GET /possessions/league?season=   one season: the league by start type, transition vs settled, and every
                                      team's offence and defence by start type with ranks and 95% intervals;
                                      how much of the spread between teams is more than chance, and how well it
                                      repeats the next season
    GET /possessions/team/{abbr}      one team's offence and defence by start type, every season
    GET /possessions/player/{id}?season=
                                      the team's possessions with this player on the floor when they began vs off it,
                                      by start type, offence and defence (live from possessions + lineup_stints)

All from scripts/build_possessions.py (possessions, possession_games, possession_seasons, possession_meta): every
regular-season game 2020-21 on, ESPN play-by-play cut into possessions on the shared lineup parser. Only games
whose possessions add up to the final score and the box score (possession_games.game_ok, 7,220 of 7,232) count,
the same set as possession_seasons. Start types describe how the possession began, from the offence's side: a
"steal" start is a possession that began with the offence stealing the ball, so on the defence view it is the
team's own live-ball turnover. Transition (first shot or free throw within 7 s of the start) is only timed after
made shots, made free throws and rebounds: ESPN stamps a turnover at about the time of the next play.

Intervals treat possessions as independent (points-per-possession variance pooled by season and start type over
the league); they ignore that possessions in one game share opponents and lineups, so they run a little narrow.
Nothing here is a model. Every read is cached per process: restart impact_api after rebuilding the tables.
"""

import json
import math
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query

from impact_core import get_db
from source_badge import make_source

router = APIRouter()

UPSTREAM = "ESPN play-by-play via sportsdataverse, cut into possessions by scripts/build_possessions.py"
SOURCE = ["possessions", "possession_games", "possession_seasons", "possession_meta"]
Z95 = 1.959964
# Start types in the order the page shows them; `other` and `jump_ball` are a few hundred a season.
START_TYPES = [
    ("made_fg", "After a made shot", "After make",
     "The other team scored a field goal (or an and-one ended on its free throw): the ball is inbounded under their basket."),
    ("dreb", "After a defensive rebound", "After def. rebound",
     "A player rebounded the other team's missed field goal."),
    ("steal", "After a steal", "After steal",
     "A live-ball turnover: the offence stole the ball. On the defence view this is the team's own turnover."),
    ("dead_tov", "After a dead-ball turnover", "After dead-ball TO",
     "Out of bounds, a violation, an offensive foul, a shot-clock turnover: the ball is inbounded."),
    ("made_ft", "After a made last free throw", "After made FT",
     "The other team made the last free throw of a trip."),
    ("dreb_ft", "After a rebounded free throw", "After FT rebound",
     "A player rebounded a missed last free throw."),
    ("team_dreb", "After a team rebound", "After team rebound",
     "The miss went out of bounds off the shooting side, or the shot clock or period ran out on it."),
    ("period_start", "Start of a period", "Period start",
     "The first possession of a quarter or overtime."),
    ("jump_ball", "After a held ball", "After held ball",
     "A held ball won by the other side (a few dozen a season)."),
    ("other", "Other", "Other",
     "The other team acted with no ending event logged before it (a gap in ESPN's log): a few hundred a season."),
]
START_KEYS = [k for k, *_ in START_TYPES]
MAIN_STARTS = ["made_fg", "dreb", "steal", "dead_tov", "made_ft", "dreb_ft", "team_dreb", "period_start"]
TIMED_STARTS = ["dreb", "made_fg", "made_ft", "team_dreb", "dreb_ft"]
PLAYER_MIN_POSS = 150   # fewer on-court (or off-court) possessions of a kind than this: shown greyed


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _f(v, d=4):
    if v is None:
        return None
    v = float(v)
    return None if math.isnan(v) else round(v, d)


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _require(cur):
    cur.execute("SELECT to_regclass('public.possession_seasons'), to_regclass('public.possessions')")
    if None in cur.fetchone():
        raise HTTPException(status_code=503, detail="No possession data: run scripts/build_possessions.py.")


@lru_cache(maxsize=1)
def _base():
    """Seasons, teams, every possession_seasons row, the per-possession points variance and the meta checks."""
    with get_db() as conn:
        cur = conn.cursor()
        _require(cur)
        seasons = _rows(cur, """SELECT season, COUNT(*) AS games, COUNT(*) FILTER (WHERE game_ok) AS games_ok,
                                       SUM(possessions) FILTER (WHERE game_ok) AS possessions,
                                       MIN(game_date) AS first_date, MAX(game_date) AS last_date
                                FROM possession_games GROUP BY season ORDER BY season""")
        rows = _rows(cur, "SELECT * FROM possession_seasons ORDER BY season, team, start_type")
        cur.execute("""SELECT p.season, COALESCE(p.start_type, 'all'), VAR_SAMP(p.pts)
                       FROM possessions p JOIN possession_games g USING (game_id) WHERE g.game_ok
                       GROUP BY GROUPING SETS ((p.season, p.start_type), (p.season))""")
        var = {(s, t): float(v) for s, t, v in cur.fetchall() if v is not None}
        meta = {k: v for k, v in _rows_kv(cur)}
    teams = sorted({r["team"] for r in rows if r["team"] != "ALL"})
    by_key = {(r["season"], r["team"], r["start_type"]): r for r in rows}
    return {"seasons": seasons, "teams": teams, "rows": by_key, "var": var, "meta": meta}


def _rows_kv(cur):
    cur.execute("SELECT key, value FROM possession_meta")
    return [(k, v if isinstance(v, (dict, list)) else json.loads(v)) for k, v in cur.fetchall()]


def _season_or_400(season):
    b = _base()
    known = [s["season"] for s in b["seasons"]]
    if season is None:
        return known[-1]
    if season not in known:
        raise HTTPException(status_code=400, detail=f"No possessions for season {season}: {label(known[0])} to {label(known[-1])} only.")
    return season


def _team_or_400(abbr):
    t = (abbr or "").strip().upper()
    if t not in _base()["teams"]:
        raise HTTPException(status_code=400, detail=f"Unknown team '{abbr}'.")
    return t


def _side(r, var, side):
    """One side (off / def) of a possession_seasons row, with the 95% interval of its points per possession."""
    if r is None:
        return None
    if side == "off":
        poss, pts = r["poss"], r["pts"]
        timed, trans, tppp = r["timed_poss"], r["trans_poss"], r["trans_ppp"]
    else:
        poss, pts = r["d_poss"], r["d_pts"]
        timed, trans, tppp = r["d_timed_poss"], r["d_trans_poss"], r["d_trans_ppp"]
    if not poss:
        return None
    ppp = pts / poss
    se = math.sqrt(var / poss) if var else None
    return {
        "poss": poss, "pts": pts, "ppp": _f(ppp), "se": _f(se),
        "lo": _f(ppp - Z95 * se) if se else None, "hi": _f(ppp + Z95 * se) if se else None,
        "timed_poss": timed, "trans_poss": trans,
        "trans_share": _f(trans / timed) if timed else None, "trans_ppp": _f(tppp),
    }


def _league(season):
    b = _base()
    out = []
    for key, name, short, desc in [("all", "Every possession", "All", "")] + START_TYPES:
        r = b["rows"].get((season, "ALL", key))
        if r is None:
            continue
        s = _side(r, b["var"].get((season, key)), "off")
        s.update({"start_type": key, "games": r["games"], "avg_seconds": _f(r["avg_seconds"], 2),
                  "second_chance_pts": r["second_chance_pts"], "oreb_poss": r["oreb_poss"],
                  "fga": r["fga"], "fg3a": r["fg3a"], "fta": r["fta"], "tov": r["tov"]})
        out.append(s)
    total = next(x["poss"] for x in out if x["start_type"] == "all")
    for x in out:
        x["share"] = _f(x["poss"] / total)
    return out


def _corr(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 5:
        return None
    n = len(pairs)
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    return sxy / math.sqrt(sxx * syy) if sxx and syy else None


@lru_cache(maxsize=1)
def _signal():
    """Per start type and side: how much of the between-team spread in points per possession is more than chance
    (1 - mean sampling variance / observed variance, per season, then averaged) and the year-to-year r of team
    values (every pair of consecutive seasons)."""
    b = _base()
    seasons = [s["season"] for s in b["seasons"]]
    out = {}
    for key in ["all"] + MAIN_STARTS:
        for side in ("off", "def"):
            shares, sds, ses = [], [], []
            vals = {}
            for s in seasons:
                v = b["var"].get((s, key))
                xs = [_side(b["rows"].get((s, t, key)), v, side) for t in b["teams"]]
                xs = [x for x in xs if x]
                if len(xs) < 20 or not v:
                    continue
                vals[s] = {t: x["ppp"] for t, x in zip(b["teams"], xs)}
                ppps = [x["ppp"] for x in xs]
                m = sum(ppps) / len(ppps)
                obs = sum((p - m) ** 2 for p in ppps) / (len(ppps) - 1)
                noise = sum(x["se"] ** 2 for x in xs) / len(xs)
                sds.append(math.sqrt(obs))
                ses.append(math.sqrt(noise))
                shares.append(max(0.0, 1 - noise / obs) if obs else 0.0)
            rs = []
            for s in seasons[:-1]:
                if s in vals and s + 1 in vals:
                    ts = b["teams"]
                    r = _corr([vals[s].get(t) for t in ts], [vals[s + 1].get(t) for t in ts])
                    if r is not None:
                        rs.append({"from": s, "to": s + 1, "r": _f(r, 3)})
            out[f"{key}:{side}"] = {
                "start_type": key, "side": side,
                "team_sd": _f(sum(sds) / len(sds)) if sds else None,
                "noise_sd": _f(sum(ses) / len(ses)) if ses else None,
                "real_share": _f(sum(shares) / len(shares), 3) if shares else None,
                "yty": rs, "yty_mean": _f(sum(x["r"] for x in rs) / len(rs), 3) if rs else None,
            }
    return out


@lru_cache(maxsize=8)
def _season_extras(season):
    """Transition vs settled possessions (league), and second-chance points allowed per team, for one season."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT p.start_type, p.transition, COUNT(*), SUM(p.pts)
                       FROM possessions p JOIN possession_games g USING (game_id)
                       WHERE g.game_ok AND p.season = %s AND p.transition IS NOT NULL AND p.start_type = ANY(%s)
                       GROUP BY 1, 2""", (season, TIMED_STARTS))
        tr = {}
        for st, is_tr, n, pts in cur.fetchall():
            tr.setdefault(st, {})["trans" if is_tr else "settled"] = {"poss": n, "pts": int(pts), "ppp": _f(pts / n)}
        # Every timed possession, as possession_seasons counts them (period starts and held balls are timed, never
        # transition), so the share matches the page's other transition numbers.
        cur.execute("""SELECT p.transition, COUNT(*), SUM(p.pts)
                       FROM possessions p JOIN possession_games g USING (game_id)
                       WHERE g.game_ok AND p.season = %s AND p.transition IS NOT NULL
                       GROUP BY 1""", (season,))
        for is_tr, n, pts in cur.fetchall():
            tr.setdefault("all", {})["trans" if is_tr else "settled"] = {"poss": n, "pts": int(pts), "ppp": _f(pts / n)}
        cur.execute("""SELECT p.defense, SUM(p.second_chance_pts), COUNT(*) FILTER (WHERE p.oreb + p.team_oreb > 0)
                       FROM possessions p JOIN possession_games g USING (game_id)
                       WHERE g.game_ok AND p.season = %s GROUP BY 1""", (season,))
        allowed = {t: {"second_chance_pts": int(s), "oreb_poss": int(o)} for t, s, o in cur.fetchall()}
    transition = [{"start_type": k, **tr[k]} for k in ["all"] + TIMED_STARTS if k in tr]
    return transition, allowed


def _team_season(season, team, allowed=None):
    """One team-season: offence and defence by start type, plus per-game totals."""
    b = _base()
    by = {}
    for key in ["all"] + START_KEYS:
        r = b["rows"].get((season, team, key))
        if r is None:
            continue
        v = b["var"].get((season, key))
        by[key] = {"off": _side(r, v, "off"), "def": _side(r, v, "def")}
    a = b["rows"].get((season, team, "all"))
    st = b["rows"].get((season, team, "steal"))
    games = a["games"]
    totals = {
        "games": games,
        "poss_per_game": _f(a["poss"] / games, 2),
        "second_chance_pg": _f(a["second_chance_pts"] / games, 2),
        "oreb_share": _f(a["oreb_poss"] / a["poss"]),
        "steal_poss_pg": _f(st["poss"] / games, 2) if st else None,
        "steal_pts_pg": _f(st["pts"] / games, 2) if st else None,
        "d_steal_poss_pg": _f(st["d_poss"] / games, 2) if st else None,
        "d_steal_pts_pg": _f(st["d_pts"] / games, 2) if st else None,
    }
    if allowed is not None and team in allowed:
        totals["d_second_chance_pg"] = _f(allowed[team]["second_chance_pts"] / games, 2)
        totals["d_oreb_share"] = _f(allowed[team]["oreb_poss"] / a["d_poss"])
    return {"team": team, "season": season, "by_start": by, "totals": totals}


def _ppp(team_row, key, side):
    cell = (team_row["by_start"].get(key) or {}).get(side)
    return cell["ppp"] if cell else None


def _rank(teams, getter, high_first):
    vals = [(t["team"], getter(t)) for t in teams]
    vals = [(t, v) for t, v in vals if v is not None]
    vals.sort(key=lambda x: -x[1] if high_first else x[1])
    return {t: i + 1 for i, (t, _) in enumerate(vals)}


@router.get("/possessions/options")
def possession_options():
    b = _base()
    seasons = [{**s, "label": label(s["season"]),
                "first_date": s["first_date"].isoformat() if s["first_date"] else None,
                "last_date": s["last_date"].isoformat() if s["last_date"] else None} for s in b["seasons"]]
    trend = {s["season"]: _league(s["season"]) for s in b["seasons"]}
    tc = b["meta"].get("transition_check", {})
    cc = b["meta"].get("clock_check", {}).get("all", {})
    pooled = {}
    for key in ["all"] + START_KEYS:
        rs = [b["rows"].get((s["season"], "ALL", key)) for s in b["seasons"]]
        rs = [r for r in rs if r]
        if rs:
            n = sum(r["poss"] for r in rs)
            pooled[key] = {"poss": n, "pts": sum(r["pts"] for r in rs), "ppp": _f(sum(r["pts"] for r in rs) / n)}
    return {
        "seasons": seasons,
        "teams": b["teams"],
        "start_types": [{"key": k, "label": n, "short": s, "description": d, "main": k in MAIN_STARTS,
                         "timed": k in TIMED_STARTS} for k, n, s, d in START_TYPES],
        "trend": [{"season": s, "label": label(s), "rows": rows} for s, rows in trend.items()],
        "pooled": pooled,
        "transition": {
            "window_seconds": tc.get("window_seconds"),
            "dreb_ppp_by_second": tc.get("dreb_ppp_by_first_attempt_second"),
            "dreb_n_by_second": tc.get("dreb_n_by_first_attempt_second"),
            "take_fouls": tc.get("take_fouls"), "take_fouls_within_window": tc.get("take_fouls_within_window"),
        },
        "clock": {"corrected_within_2s": cc.get("corrected_within_2s"), "espn_within_2s": cc.get("espn_within_2s")},
        "player_min_poss": PLAYER_MIN_POSS,
        "notes": {
            "coverage": ("Every regular-season game from 2020-21 on, cut into possessions from ESPN's play-by-play. Only "
                         "games whose possessions add up to the final score and the box score count "
                         f"({sum(s['games_ok'] for s in seasons):,} of {sum(s['games'] for s in seasons):,})."),
            "possession": ("A possession ends on a made shot (or the last free throw of an and-one), a defensive rebound, "
                           "a turnover or the end of the period; an offensive rebound continues it. Counted possessions "
                           "run about 2 a team-game under the box-score estimate (FGA + 0.44 FTA − OREB + TOV) because "
                           "team offensive rebounds continue a possession too."),
            "transition": ("Transition = the first shot or free throw within 7 seconds of the start, on a clock rebuilt "
                           "from the NBA shot chart (ESPN logs made shots a median 14 s late). It is timed only after made "
                           "shots, made free throws and rebounds: ESPN stamps a turnover at about the time of the next "
                           "play, so possessions after steals have no transition flag."),
            "intervals": ("95% intervals treat possessions as independent (variance of points per possession pooled over "
                          "the league by season and start type); possessions in one game share lineups and opponents, so "
                          "they run a little narrow."),
            "sides": ("Start types are named from the offence's side. On the defence view a 'steal' start is the team's "
                      "own live-ball turnover, and 'after a make' follows the team's own basket."),
        },
        "_source": make_source(SOURCE, UPSTREAM),
    }


@router.get("/possessions/league")
def possession_league(season: int = Query(None, ge=2000, le=2100)):
    season = _season_or_400(season)
    b = _base()
    transition, allowed = _season_extras(season)
    teams = [_team_season(season, t, allowed) for t in b["teams"] if (season, t, "all") in b["rows"]]
    ranks = {}
    for key in ["all"] + START_KEYS:
        for side in ("off", "def"):
            # Offence: most points first; defence: fewest allowed first.
            ranks[f"{key}:{side}"] = _rank(teams, lambda t, k=key, sd=side: _ppp(t, k, sd), side == "off")
    for t in teams:
        for key, sides in t["by_start"].items():
            for side in ("off", "def"):
                if sides[side]:
                    sides[side]["rank"] = ranks[f"{key}:{side}"].get(t["team"])
    return {
        "season": season, "label": label(season),
        "league": _league(season),
        "transition": transition,
        "teams": teams,
        "signal": list(_signal().values()),
        "_source": make_source(SOURCE, UPSTREAM),
    }


@router.get("/possessions/team/{abbr}")
def possession_team(abbr: str):
    team = _team_or_400(abbr)
    b = _base()
    seasons = []
    for s in b["seasons"]:
        season = s["season"]
        if (season, team, "all") not in b["rows"]:
            continue
        _, allowed = _season_extras(season)
        row = _team_season(season, team, allowed)
        row["label"] = label(season)
        seasons.append(row)
    if not seasons:
        raise HTTPException(status_code=404, detail=f"No possessions on file for {team}.")
    return {"team": team, "seasons": seasons, "_source": make_source(SOURCE, UPSTREAM)}


PLAYER_SQL = """
WITH my AS (
    SELECT game_id, stint_no, CASE WHEN %(pid)s = ANY(home_ids) THEN home_team ELSE away_team END AS team
    FROM lineup_stints
    WHERE season = %(season)s AND tracked_ok AND (%(pid)s = ANY(home_ids) OR %(pid)s = ANY(away_ids))),
games AS (SELECT DISTINCT game_id, team FROM my)
SELECT g.team, p.start_type, p.offense = g.team AS off, m.stint_no IS NOT NULL AS on_court,
       COUNT(*) AS poss, SUM(p.pts) AS pts, COUNT(*) FILTER (WHERE p.transition IS NOT NULL) AS timed,
       COUNT(*) FILTER (WHERE p.transition) AS trans, COUNT(DISTINCT p.game_id) AS games
FROM possessions p
JOIN games g ON g.game_id = p.game_id
LEFT JOIN my m ON m.game_id = p.game_id AND m.stint_no = p.stint_no
WHERE p.tracked_ok AND p.season = %(season)s
GROUP BY GROUPING SETS ((g.team, p.start_type, p.offense = g.team, m.stint_no IS NOT NULL),
                        (g.team, p.offense = g.team, m.stint_no IS NOT NULL))
"""


@lru_cache(maxsize=1)
def _player_seasons_all():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT MIN(season), MAX(season) FROM possessions")
        return cur.fetchone()


def player_possession_seasons(cur, player_id):
    """Seasons with this player on the floor for a tracked stint (the profile lists these; the block loads the rest)."""
    cur.execute("SELECT to_regclass('public.possessions')")
    if cur.fetchone()[0] is None:
        return []
    cur.execute("""SELECT season, COUNT(*) FROM lineup_stints
                   WHERE tracked_ok AND (%s = ANY(home_ids) OR %s = ANY(away_ids)) GROUP BY season ORDER BY season""",
                (player_id, player_id))
    lo, hi = _player_seasons_all()
    return [s for s, _ in cur.fetchall() if lo is not None and lo <= s <= hi]


@lru_cache(maxsize=256)
def _player(player_id, season):
    with get_db() as conn:
        cur = conn.cursor()
        _require(cur)
        seasons = player_possession_seasons(cur, player_id)
        if not seasons:
            return None
        if season is None:
            season = seasons[-1]
        if season not in seasons:
            raise HTTPException(status_code=404, detail=f"No tracked possessions with this player on the floor in {label(season)}.")
        cur.execute(PLAYER_SQL, {"pid": player_id, "season": season})
        raw = cur.fetchall()
        cur.execute("SELECT player_name FROM player_season_stats WHERE player_id = %s ORDER BY season DESC LIMIT 1",
                    (player_id,))
        nm = cur.fetchone()
    b = _base()
    cells = {}
    for team, st, off, on, poss, pts, timed, trans, games in raw:
        cells[(team, st or "all", "off" if off else "def", "on" if on else "off_court")] = (poss, int(pts), timed, trans, games)
    teams_out = []
    for team in sorted({k[0] for k in cells}, key=lambda t: -sum(v[0] for k, v in cells.items() if k[0] == t and k[1] == "all")):
        rows = []
        for key in ["all"] + START_KEYS:
            var = b["var"].get((season, key))
            row = {"start_type": key}
            for side in ("off", "def"):
                for where in ("on", "off_court"):
                    c = cells.get((team, key, side, where))
                    if not c:
                        row[f"{side}_{where}"] = None
                        continue
                    poss, pts, timed, trans, _ = c
                    ppp = pts / poss
                    row[f"{side}_{where}"] = {"poss": poss, "pts": pts, "ppp": _f(ppp),
                                              "trans_share": _f(trans / timed) if timed else None,
                                              "small": poss < PLAYER_MIN_POSS}
                on, offc = row[f"{side}_on"], row[f"{side}_off_court"]
                if on and offc and var:
                    diff = on["ppp"] - offc["ppp"]
                    se = math.sqrt(var / on["poss"] + var / offc["poss"])
                    row[f"{side}_diff"] = {"diff": _f(diff), "lo": _f(diff - Z95 * se), "hi": _f(diff + Z95 * se),
                                           "excludes_zero": bool(abs(diff) > Z95 * se)}
                else:
                    row[f"{side}_diff"] = None
            if any(row[f"{s}_{w}"] for s in ("off", "def") for w in ("on", "off_court")):
                rows.append(row)
        games = cells.get((team, "all", "off", "on"), (0, 0, 0, 0, 0))[4]
        teams_out.append({"team": team, "games": games, "rows": rows})
    return {
        "player_id": player_id, "player_name": nm[0] if nm else None,
        "season": season, "label": label(season), "seasons": seasons, "teams": teams_out,
        "min_poss": PLAYER_MIN_POSS,
        "league": {r["start_type"]: r["ppp"] for r in _league(season)},
    }


@router.get("/possessions/player/{player_id}")
def possession_player(player_id: int, season: int = Query(None, ge=2000, le=2100)):
    out = _player(player_id, season)
    if out is None:
        raise HTTPException(status_code=404, detail="No tracked possessions with this player on the floor (2020-21 on).")
    return {**out, "_source": make_source(SOURCE + ["lineup_stints"], UPSTREAM)}
