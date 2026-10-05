"""
Game Log + Game Finder: every regular-season player-game, 2020-21 on.

    GET /games/player-log/{player_id}?season=2026   one player's games in a season
    GET /games/finder/options                       stats, seasons, teams, accuracy
    GET /games/finder/players?q=jok                 players who have game lines
    GET /games/finder?f=pts:gte:30,fga:lt:15&...    matching games (mode=games)
                                                    or longest runs (mode=streaks)

Data: player_game_lines (scripts/build_player_game_lines.py), one row per
player-game rebuilt from ESPN play-by-play, including minutes from
substitutions. The project has no box-score game logs, so this is the only
per-game source, and it starts in 2020-21.

Opponent, home/away, result and rest come from team_game_fatigue, joined on
(team, date): ESPN game ids (espn_...) aren't the NBA ids used there. The
margin is the real final score from game_scores (scripts/fetch_game_scores.py),
not team_game_fatigue.plus_minus (summed player +/- / 5, wrong in 160 games). The
join also defines which games count: only games in the standings. The lines
that don't match are the three NBA Cup finals (Dec 2023, 2024, 2025), which
don't count in regular-season stats, and one 2021-22 line with no team.
Games a player sat out have no line, so streaks count games he played.

Plus-minus (since round 8 step 5) is on the floor, from player_game_onfloor
(scripts/build_player_game_onfloor.py: the five-man stints with free throws
credited at the foul, 98% exact against ESPN's box score), and None in the 12
games whose play-by-play doesn't reconcile with the final score (game_ok
false). Never player_game_lines' tm_pts - op_pts (stale ESPN score fields).

Filters never become SQL text: stat keys, operators, sort keys and teams are
looked up in the whitelists below, values are bound parameters.

The name and player lists are lru-cached per process: restart impact_api
after rebuilding player_game_lines.
"""

import math
import unicodedata
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query

from impact_core import get_db
from source_badge import make_source
from workbench_catalogue import (ONFLOOR_JOIN, PLAYER_GAME_FROM, PLAYER_GAME_WHERE, TEAM_MARGIN_SQL,
                                 game_finder_stats)

router = APIRouter()

MAX_CONDITIONS = 8
MAX_LIMIT = 200
MIN_STREAK = 2
# Rows in every query: a line he played, in a game that counts in the standings
# (the Workbench's player_game dataset is the same rows: api/workbench_catalogue.py).
BASE_FROM = PLAYER_GAME_FROM + ONFLOOR_JOIN
MARGIN_SQL = TEAM_MARGIN_SQL
BASE_WHERE = list(PLAYER_GAME_WHERE)

# key -> (label, per-game SQL expression over l, format), built from the
# player_game columns of api/workbench_catalogue.py (round 7): add or change a
# stat there. Shooting % are shares (0.6 = 60%); a game with no attempts has
# none and never matches.
STATS = game_finder_stats()
OPS = {"gte": ">=", "gt": ">", "lte": "<=", "lt": "<", "eq": "="}
SORTS = {**{k: v[1] for k, v in STATS.items()}, "date": "l.game_date", "margin": MARGIN_SQL}
RAW = ["pts", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov"]
PM_SQL = STATS["plus_minus"][1]  # on-floor plus-minus, NULL where the game doesn't reconcile
ROW_SQL = ("l.player_id, l.season, l.game_date, l.team_abbreviation, f.opponent, f.is_home, f.win, "
           f"{MARGIN_SQL}, f.rest_days, f.is_b2b, f.game_id, l.seconds, " + ", ".join(f"l.{c}" for c in RAW)
           + f", {PM_SQL}")
ROW_KEYS = ["player_id", "season", "date", "team", "opponent", "home", "win", "margin", "rest_days", "b2b",
            "nba_game_id", "seconds"] + RAW + ["plus_minus"]


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _fold(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _derived(t, games):
    """Per-game averages and shooting % from summed raw columns; plus-minus over
    the games that have one (t["pm"], t["pm_games"])."""
    def ratio(a, b):
        return round(a / b, 4) if b else None
    out = {k: round(t[k] / games, 2) if games else None for k in RAW}
    out["reb"] = round((t["oreb"] + t["dreb"]) / games, 2) if games else None
    out["min"] = round(t["seconds"] / 60 / games, 2) if games else None
    out["fg_pct"] = ratio(t["fgm"], t["fga"])
    out["fg3_pct"] = ratio(t["fg3m"], t["fg3a"])
    out["ft_pct"] = ratio(t["ftm"], t["fta"])
    out["ts_pct"] = ratio(t["pts"], 2 * (t["fga"] + 0.44 * t["fta"]))
    out["plus_minus"] = round(t["pm"] / t["pm_games"], 2) if t.get("pm_games") else None
    out["plus_minus_games"] = int(t.get("pm_games") or 0)
    return out


def _game_row(r, names):
    g = dict(zip(ROW_KEYS, r))
    g["player_name"] = names.get(g["player_id"])
    g["date"] = g["date"].isoformat()
    g["min"] = round(g.pop("seconds") / 60, 1)
    g["reb"] = g["oreb"] + g["dreb"]
    g["margin"] = None if g["margin"] is None else int(g["margin"])
    g["plus_minus"] = None if g["plus_minus"] is None else int(g["plus_minus"])
    g["ts_pct"] = round(g["pts"] / (2 * (g["fga"] + 0.44 * g["fta"])), 4) if g["fga"] + g["fta"] else None
    return g


@lru_cache(maxsize=1)
def _names():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT DISTINCT ON (player_id) player_id, player_name FROM player_season_stats
                       ORDER BY player_id, season DESC""")
        return dict(cur.fetchall())


@lru_cache(maxsize=1)
def _meta():
    """Seasons, teams, what's left out, and how close the rebuild is to NBA.com."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT min(l.season), max(l.season), max(l.game_date), count(*) {BASE_FROM} WHERE l.seconds > 0")
        lo, hi, last_date, n_lines = cur.fetchone()
        cur.execute(f"SELECT DISTINCT l.team_abbreviation {BASE_FROM} ORDER BY 1")
        teams = [t for (t,) in cur.fetchall()]
        cur.execute("""SELECT l.season, l.game_date, array_agg(DISTINCT l.team_abbreviation ORDER BY l.team_abbreviation)
                       FROM player_game_lines l LEFT JOIN team_game_fatigue f
                         ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
                       WHERE f.game_id IS NULL AND l.team_abbreviation IN (SELECT DISTINCT team_abbreviation FROM team_game_fatigue)
                       GROUP BY 1, 2 ORDER BY 2""")
        left_out = [{"season": s, "date": d.isoformat(), "teams": t} for s, d, t in cur.fetchall()]
        # Season totals from the lines vs NBA.com's (player_season_stats), players with 20+ games.
        cur.execute("""
            WITH l AS (SELECT player_id, season, SUM(seconds) / 60 mins, SUM(pts) pts, SUM(fg3a) fg3a
                       FROM player_game_lines GROUP BY 1, 2)
            SELECT count(*), SUM(l.pts) / SUM(s.pts * s.gp), SUM(l.fg3a) / SUM(s.fg3a * s.gp),
                   SUM(l.mins) / SUM(s.min * s.gp),
                   AVG(ABS(l.pts - s.pts * s.gp) / NULLIF(s.pts * s.gp, 0)),
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY ABS(l.pts - s.pts * s.gp) / NULLIF(s.pts * s.gp, 0))
            FROM l JOIN player_season_stats s USING (player_id, season) WHERE s.gp >= 20""")
        n, pts_ratio, fg3a_ratio, min_ratio, pts_mae, pts_p95 = cur.fetchone()
    return {
        "seasons": {"from": lo, "to": hi}, "last_date": last_date.isoformat(), "lines": n_lines, "teams": teams,
        "left_out": left_out,
        "accuracy": {
            "player_seasons": n, "points_total_ratio": round(float(pts_ratio), 4),
            "fg3a_total_ratio": round(float(fg3a_ratio), 4), "minutes_total_ratio": round(float(min_ratio), 4),
            "points_mean_abs_error": round(float(pts_mae), 4), "points_p95_abs_error": round(float(pts_p95), 4),
        },
    }


@lru_cache(maxsize=1)
def _players():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"""SELECT l.player_id, min(l.season), max(l.season), count(*) {BASE_FROM}
                        WHERE l.seconds > 0 GROUP BY 1""")
        rows = cur.fetchall()
    names = _names()
    out = [{"player_id": p, "player_name": names.get(p, str(p)), "from": a, "to": b, "games": g} for p, a, b, g in rows]
    return [(_fold(p["player_name"]), p) for p in out]


def _source():
    return make_source(["player_game_lines", "team_game_fatigue", "game_scores", "player_season_stats"],
                       "ESPN play-by-play (lines rebuilt from it) and scoreboard (final scores), "
                       "nba_api (stats.nba.com) schedule")


def _notes(meta):
    a = meta["accuracy"]
    return {
        "coverage": (f"Regular season {label(meta['seasons']['from'])} to {label(meta['seasons']['to'])} only "
                     f"(through {meta['last_date']}): the lines are rebuilt from ESPN play-by-play, which the "
                     "project has for those seasons and no earlier."),
        "plus_minus": ("+/- is on the floor: the team's points minus the opponent's while he played, from the "
                       "five-man stints with free throws credited to the players on the floor at the foul, as the "
                       "box score does (equals ESPN's box-score +/- in 98% of player-games). Blank in the 12 games "
                       "whose play-by-play doesn't add up to the final score."),
        "accuracy": (f"Rebuilt season totals match NBA.com's within {a['points_mean_abs_error'] * 100:.1f}% "
                     f"on points for the average player-season (95% within {a['points_p95_abs_error'] * 100:.1f}%; "
                     f"{a['player_seasons']:,} player-seasons with 20+ games). Three-point attempts total "
                     f"{a['fg3a_total_ratio'] * 100:.2f}% of NBA.com's: a missed shot is a two or a three as the "
                     "NBA shot chart calls the same shot (ESPN's text often doesn't say), else as the text does."),
        "left_out": ("NBA Cup finals are left out: they don't count in regular-season stats or the standings "
                     f"({', '.join(f'{x['date']} {'-'.join(x['teams'])}' for x in meta['left_out'])})."),
    }


@router.get("/games/finder/options")
def finder_options():
    meta = _meta()
    return {
        "stats": [{"key": k, "label": v[0], "format": v[2]} for k, v in STATS.items()],
        "ops": OPS, "max_conditions": MAX_CONDITIONS, "max_limit": MAX_LIMIT, "min_streak": MIN_STREAK,
        "sorts": list(SORTS), **meta, "notes": _notes(meta), "_source": _source(),
    }


@router.get("/games/finder/players")
def finder_players(q: str, limit: int = Query(12, ge=1, le=30)):
    query = _fold(q).strip()
    if len(query) < 2:
        return {"query": q, "results": []}
    hits = [p for folded, p in _players() if query in folded]
    hits.sort(key=lambda p: (not _fold(p["player_name"]).startswith(query), -p["games"], p["player_name"]))
    return {"query": q, "results": hits[:limit]}


def _parse_conditions(f):
    """'pts:gte:30,fga:lt:15' -> [(key, op, value)]; 400 on anything unknown."""
    if not f or not f.strip():
        return []
    out = []
    for part in f.split(","):
        bits = part.strip().split(":")
        if len(bits) != 3 or bits[0] not in STATS or bits[1] not in OPS:
            raise HTTPException(status_code=400, detail=(
                f"Bad condition '{part}'. Use stat:op:value with stat in {', '.join(STATS)} "
                f"and op in {', '.join(OPS)}."))
        try:
            value = float(bits[2])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"'{bits[2]}' in '{part}' isn't a number.")
        if not math.isfinite(value):
            raise HTTPException(status_code=400, detail=f"'{bits[2]}' in '{part}' isn't a number.")
        out.append((bits[0], bits[1], value))
    if len(out) > MAX_CONDITIONS:
        raise HTTPException(status_code=400, detail=f"At most {MAX_CONDITIONS} conditions.")
    return out


@router.get("/games/finder")
def game_finder(
    f: str = Query("", description="Conditions, e.g. pts:gte:30,fga:lt:15 (shooting % as shares: ts_pct:gte:0.7)"),
    mode: str = Query("games", pattern="^(games|streaks)$"),
    season_from: int | None = None,
    season_to: int | None = None,
    team: str | None = None,
    home: str | None = Query(None, pattern="^(home|away)$"),
    result: str | None = Query(None, pattern="^(W|L)$"),
    min_minutes: float = Query(0, ge=0, le=60),
    player_id: int | None = None,
    sort: str = "pts",
    order: str = Query("desc", pattern="^(asc|desc)$"),
    one_per_player: bool = True,
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
):
    meta = _meta()
    names = _names()
    conds = _parse_conditions(f)
    lo, hi = meta["seasons"]["from"], meta["seasons"]["to"]
    s_from = max(season_from or lo, lo)
    s_to = min(season_to or hi, hi)
    if s_from > s_to:
        raise HTTPException(status_code=400, detail="season_from is after season_to.")
    if team is not None and team not in meta["teams"]:
        raise HTTPException(status_code=400, detail=f"Unknown team '{team}'.")
    if sort not in SORTS:
        raise HTTPException(status_code=400, detail=f"Can't sort by '{sort}'. Use one of {', '.join(SORTS)}.")

    # Which games are in the sequence at all (for streaks: games that don't break a run).
    where, params = list(BASE_WHERE), []
    where.append("l.season BETWEEN %s AND %s")
    params += [s_from, s_to]
    if team:
        where.append("l.team_abbreviation = %s")
        params.append(team)
    if home:
        where.append("f.is_home" if home == "home" else "NOT f.is_home")
    if result:
        where.append("f.win" if result == "W" else "NOT f.win")
    if min_minutes:
        where.append("l.seconds >= %s")
        params.append(min_minutes * 60)
    if player_id is not None:
        where.append("l.player_id = %s")
        params.append(player_id)
    # The stat conditions.
    cond_sql = [f"{STATS[k][1]} {OPS[op]} %s" for k, op, _ in conds]
    cond_params = [v for _, _, v in conds]
    filters = {
        "conditions": [{"stat": k, "label": STATS[k][0], "op": op, "value": v} for k, op, v in conds],
        "season_from": s_from, "season_to": s_to, "team": team, "home": home, "result": result,
        "min_minutes": min_minutes, "player_id": player_id,
        "player_name": names.get(player_id) if player_id is not None else None,
    }
    common = {"mode": mode, "filters": filters, "notes": _notes(meta), "_source": _source()}

    with get_db() as conn:
        cur = conn.cursor()
        if mode == "games":
            all_where = " AND ".join(where + cond_sql)
            all_params = params + cond_params
            nulls = "NULLS LAST"
            cur.execute(f"""SELECT {ROW_SQL}, count(*) OVER () {BASE_FROM} WHERE {all_where}
                            ORDER BY {SORTS[sort]} {order.upper()} {nulls}, l.game_date DESC, l.player_id
                            LIMIT %s OFFSET %s""", all_params + [limit, offset])
            rows = cur.fetchall()
            total = rows[0][-1] if rows else 0
            if not rows and offset:
                cur.execute(f"SELECT count(*) {BASE_FROM} WHERE {all_where}", all_params)
                total = cur.fetchone()[0]
            cur.execute(f"""SELECT l.player_id, count(*) {BASE_FROM} WHERE {all_where}
                            GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10""", all_params)
            top = [{"player_id": p, "player_name": names.get(p), "games": n} for p, n in cur.fetchall()]
            cur.execute(f"SELECT count(DISTINCT l.player_id), count(*) {BASE_FROM} WHERE {' AND '.join(where)}", params)
            pool_players, pool_games = cur.fetchone()
            return {**common, "total": total, "offset": offset, "limit": limit, "sort": sort, "order": order,
                    "pool_games": pool_games, "pool_players": pool_players, "most_games": top,
                    "results": [_game_row(r[:-1], names) for r in rows]}

        # Streaks: runs of consecutive games in the sequence that all meet the
        # conditions (gaps-and-islands on each player's game order).
        if not conds:
            raise HTTPException(status_code=400, detail="Streak mode needs at least one condition.")
        hit = " AND ".join(f"COALESCE({c}, FALSE)" for c in cond_sql)
        cur.execute(f"""
            WITH seq AS (
                SELECT l.player_id, l.season, l.game_date, l.team_abbreviation, l.seconds,
                       {', '.join(f'l.{c}' for c in RAW)}, {PM_SQL} AS pm, ({hit}) AS hit,
                       row_number() OVER (PARTITION BY l.player_id ORDER BY l.game_date) AS rn,
                       count(*) OVER (PARTITION BY l.player_id) AS n_games
                {BASE_FROM} WHERE {' AND '.join(where)}
            ), runs AS (
                SELECT *, rn - row_number() OVER (PARTITION BY player_id ORDER BY game_date) AS grp
                FROM seq WHERE hit
            ), streaks AS (
                SELECT player_id, grp, count(*) AS games, min(game_date) AS start_date, max(game_date) AS end_date,
                       min(season) AS season_from, max(season) AS season_to,
                       array_agg(DISTINCT team_abbreviation ORDER BY team_abbreviation) AS teams,
                       max(rn) = max(n_games) AS reaches_last_game, SUM(seconds) AS seconds,
                       SUM(pm) AS pm, COUNT(pm) AS pm_games,
                       {', '.join(f'SUM({c}) AS {c}' for c in RAW)}
                FROM runs GROUP BY player_id, grp HAVING count(*) >= %s
            ), ranked AS (
                SELECT *, row_number() OVER (PARTITION BY player_id ORDER BY games DESC, start_date) AS best
                FROM streaks
            )
            SELECT *, count(*) OVER () FROM ranked WHERE (%s OR best = 1)
            ORDER BY games DESC, start_date LIMIT %s OFFSET %s""",
                    cond_params + params + [MIN_STREAK, not one_per_player, limit, offset])
        cols = [d[0] for d in cur.description]
        out = []
        for r in cur.fetchall():
            s = dict(zip(cols, r))
            totals = {k: float(s[k]) for k in RAW + ["seconds"]}
            totals.update(pm=float(s["pm"] or 0), pm_games=s["pm_games"])
            out.append({
                "player_id": s["player_id"], "player_name": names.get(s["player_id"]), "games": s["games"],
                "start_date": s["start_date"].isoformat(), "end_date": s["end_date"].isoformat(),
                "season_from": s["season_from"], "season_to": s["season_to"], "teams": s["teams"],
                # Still going at his last game in the chosen range, and that range reaches the latest data.
                "active": bool(s["reaches_last_game"]) and s_to == hi,
                "averages": _derived(totals, s["games"]),
                "total": s["count"],
            })
        total = out[0].pop("total") if out else 0
        for o in out:
            o.pop("total", None)
        return {**common, "total": total, "offset": offset, "limit": limit, "one_per_player": one_per_player,
                "min_streak": MIN_STREAK, "results": out}


@router.get("/games/player-log/{player_id}")
def player_game_log(player_id: int, season: int | None = None):
    meta = _meta()
    names = _names()
    if player_id not in names:
        raise HTTPException(status_code=404, detail=f"No NBA seasons on file for player id {player_id}.")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"""SELECT l.season, count(*) {BASE_FROM} WHERE l.player_id = %s AND l.seconds > 0
                        GROUP BY 1 ORDER BY 1""", (player_id,))
        seasons = [{"season": s, "games": n} for s, n in cur.fetchall()]
        if not seasons:
            raise HTTPException(status_code=404, detail=(
                f"No game lines for {names[player_id]}: they cover regular seasons "
                f"{label(meta['seasons']['from'])} to {label(meta['seasons']['to'])}."))
        have = [s["season"] for s in seasons]
        season = season if season is not None else have[-1]
        if season not in have:
            raise HTTPException(status_code=404, detail=f"No game lines for {names[player_id]} in {label(season)}.")
        cur.execute(f"""SELECT {ROW_SQL} {BASE_FROM} WHERE l.player_id = %s AND l.season = %s AND l.seconds > 0
                        ORDER BY l.game_date""", (player_id, season))
        raw = cur.fetchall()
        rows = [_game_row(r, names) for r in raw]
        totals = {k: float(sum(r[ROW_KEYS.index(k)] for r in raw)) for k in RAW + ["seconds"]}
        pms = [g["plus_minus"] for g in rows if g["plus_minus"] is not None]
        totals.update(pm=float(sum(pms)), pm_games=len(pms))
        cur.execute("SELECT gp FROM player_season_stats WHERE player_id = %s AND season = %s", (player_id, season))
        g = cur.fetchone()
        nba_gp = int(g[0]) if g and g[0] is not None else None
        cur.execute("""SELECT count(*) FROM player_game_lines l LEFT JOIN team_game_fatigue f
                         ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
                       WHERE l.player_id = %s AND l.season = %s AND l.seconds > 0 AND f.game_id IS NULL""",
                    (player_id, season))
        left_out = cur.fetchone()[0]
    return {
        "player_id": player_id, "player_name": names[player_id], "season": season, "seasons": seasons,
        "games": len(rows), "nba_gp": nba_gp, "cup_final_games": left_out,
        "averages": _derived(totals, len(rows)), "rows": rows,
        "notes": _notes(meta), "_source": _source(),
    }
