"""
Play Finder: every play of every regular-season game, 2020-21 on.

    GET /plays/finder/options     categories, seasons, dates, teams, coverage
    GET /plays/finder?player_id=1628389&date_from=2026-03-10&date_to=2026-03-10
        &cat=made3,ftm&team=&opp=&home=&period=4,ot&clock_min=&clock_max=
        &margin_min=&margin_max=&clutch=1&dist_min=&dist_max=&game=
        &sort=newest|oldest|dist&limit=&offset=

Data: play_finder_events / play_finder_games (scripts/build_play_finder.py):
one row per player per play, from the shared play-by-play parser, so a
player's shots, free throws, rebounds, assists, steals, blocks and turnovers
here equal his player_game_lines line in every game (checked at build time,
stored in play_finder_seasons). The description, action type and name come
from pbp_events by id.

Filter meanings:
  margin   the player's team's lead just before the play (negative = behind),
           from the reconciled score (the build script's per-game choice);
  clutch   wpa_lib's definition (final 5 minutes of the fourth or overtime,
           within 5 points before the play), as the Clutch WPA leaderboard;
  clock    seconds left in the period;
  cat      made/missed 2s and 3s: makes as the parser scores them, misses by
           the NBA shot chart's shot type where matched (the parser's
           miss_threes input, the same as player_game_lines);
  dist     feet, shot rows only (made/missed shots and the assists and blocks
           on them): the NBA shot chart's coordinates where the two feeds
           match, else ESPN's text; shots with neither never match a distance.

Filters never become SQL text: categories, teams, periods and sorts are
looked up in whitelists, every value is a bound parameter. The games and
seasons tables are lru-cached: restart impact_api after rebuilding.
"""

from datetime import date
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query

from impact_core import get_db
from pbp_lineups import ASSIST_RE, BLOCK_RE, STEAL_RE
from play_finder import CATS, FILTERS, SHOT_CODES
from routers.game_log import _names
from source_badge import make_source
from wpa_lib import CLUTCH_MARGIN, CLUTCH_SECONDS

router = APIRouter()

MAX_LIMIT = 200
MAX_OFFSET = 10_000
TOP_PLAYERS = 10
SORTS = {
    "newest": "p.game_no DESC, p.event_id, p.cat",
    "oldest": "p.game_no, p.event_id, p.cat",
    "dist": "p.dist DESC NULLS LAST, p.game_no DESC, p.event_id, p.cat",
}
PERIODS = {"1": "p.period = 1", "2": "p.period = 2", "3": "p.period = 3", "4": "p.period = 4", "ot": "p.period >= 5"}
TEAM_SQL = "(CASE WHEN p.is_home THEN gm.home_team ELSE gm.away_team END)"
OPP_SQL = "(CASE WHEN p.is_home THEN gm.away_team ELSE gm.home_team END)"
MARGIN_SQL = "(p.score_for - p.score_against)"
CLUTCH_SQL = f"(p.period >= 4 AND p.clock <= {CLUTCH_SECONDS * 10} AND abs({MARGIN_SQL}) <= {CLUTCH_MARGIN})"
FROM_SQL = "FROM play_finder_events p JOIN play_finder_games gm ON gm.game_no = p.game_no"
TEXT_NAME = {5: ASSIST_RE, 6: ASSIST_RE, 7: BLOCK_RE, 8: STEAL_RE}


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


@lru_cache(maxsize=1)
def _games():
    """game_no ranges by season and date (game_no runs in date order), teams, ESPN/NBA id lookups."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.play_finder_events');")
        if cur.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="Play Finder data not built yet: run scripts/build_play_finder.py.")
        cur.execute("""SELECT game_no, game_id, nba_game_id, season, game_date, home_team, away_team
                       FROM play_finder_games ORDER BY game_no""")
        rows = cur.fetchall()
        cur.execute("SELECT * FROM play_finder_seasons ORDER BY season")
        cols = [d[0] for d in cur.description]
        seasons = [dict(zip(cols, r)) for r in cur.fetchall()]
    by_id = {}
    for no, gid, nba, *_ in rows:
        by_id[gid] = by_id[nba] = no
    return {
        "rows": rows, "by_id": by_id, "seasons": seasons,
        "teams": sorted({r[5] for r in rows} | {r[6] for r in rows}),
        "first": rows[0][4], "last": rows[-1][4],
        "season_range": (rows[0][3], rows[-1][3]),
    }


def _no_range(season_from, season_to, date_from, date_to):
    """The game_no span a season/date window covers (None if it's empty)."""
    rows = _games()["rows"]
    keep = [r[0] for r in rows
            if (season_from is None or r[3] >= season_from) and (season_to is None or r[3] <= season_to)
            and (date_from is None or r[4] >= date_from) and (date_to is None or r[4] <= date_to)]
    return (keep[0], keep[-1]) if keep else None


def _notes():
    g = _games()
    s = g["seasons"]
    shots = sum(x["shots_coords"] + x["shots_text"] + x["shots_no_dist"] for x in s)
    coords = sum(x["shots_coords"] for x in s)
    none = sum(x["shots_no_dist"] for x in s)
    games = sum(x["games"] for x in s)
    bad = games - sum(x["games_score_ok"] for x in s)
    checked = sum(x["lines_checked"] for x in s)
    differ = sum(x["lines_differ"] for x in s)
    unid = sum(x["unidentified_rows"] for x in s)
    retyped = sum(x["misses_retyped"] for x in s)
    rows = sum(x["rows"] for x in s)
    lo, hi = g["season_range"]
    return {
        "coverage": (f"Every regular-season game {label(lo)} to {label(hi)} ({games:,} games, {rows:,} player-plays, "
                     f"through {g['last'].isoformat()}), from ESPN's play-by-play: the project has none earlier. "
                     "The three NBA Cup finals are left out (they don't count in regular-season stats)."),
        "accuracy": (f"Same parser as the game logs: in {checked - differ:,} of {checked:,} player-games "
                     "every made and missed shot, two and three, free throw, rebound, assist, steal, block and turnover "
                     "here adds up to his Game Log line. A missed shot is a two or a three as the NBA shot chart calls "
                     f"it where it has the shot ({retyped:,} misses where ESPN's text says otherwise, mostly 25-27 ft "
                     "threes the text doesn't call threes). The score before each play is reconciled to the real final "
                     f"in {games - bad:,} of {games:,} games ({bad} with bad play-by-play keep ESPN's own)."),
        "distance": (f"Distances: the NBA shot chart's coordinates for {coords / shots:.1%} of shots (matched to the "
                     f"same shot), ESPN's text for most of the rest; {none:,} shots ({none / shots:.2%}) have neither "
                     "and never match a distance filter."),
        "unidentified": (f"{unid:,} rows ({unid / rows:.2%}) name a player ESPN gives no id and the project can't "
                         "match; they show the name from the text and can't be found by player."),
        "definitions": (f"Clutch: the final {CLUTCH_SECONDS // 60} minutes of the fourth quarter or overtime with the "
                        f"score within {CLUTCH_MARGIN} before the play (the NBA's definition, as on the Clutch WPA "
                        "leaderboard). Margin: the player's team's lead before the play. Rebounds are players' own "
                        "(team rebounds aren't anybody's play); turnovers and fouls include the team's."),
    }


def _source():
    return make_source(["play_finder_events", "play_finder_games", "pbp_events", "player_shots"],
                       "ESPN play-by-play via sportsdataverse; NBA shot chart (stats.nba.com) for distances")


@router.get("/plays/finder/options")
def play_finder_options():
    g = _games()
    lo, hi = g["season_range"]
    return {
        "categories": [{"key": k, "label": v[0]} for k, v in FILTERS.items()],
        "seasons": {"from": lo, "to": hi}, "dates": {"from": g["first"].isoformat(), "to": g["last"].isoformat()},
        "teams": g["teams"], "periods": list(PERIODS), "sorts": list(SORTS), "max_limit": MAX_LIMIT,
        "max_offset": MAX_OFFSET, "clutch": {"seconds": CLUTCH_SECONDS, "margin": CLUTCH_MARGIN},
        "notes": _notes(), "_source": _source(),
    }


def _list(raw, allowed, what):
    if not raw or not raw.strip():
        return []
    out = [x.strip().lower() for x in raw.split(",") if x.strip()]
    bad = [x for x in out if x not in allowed]
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown {what} '{bad[0]}'. Use one of: {', '.join(allowed)}.")
    return list(dict.fromkeys(out))


def _clock_text(period, tenths):
    s = tenths / 10
    m, sec = int(s // 60), s % 60
    txt = f"{m}:{int(sec):02d}" if s >= 60 else f"0:{sec:04.1f}"
    name = f"Q{period}" if period <= 4 else ("OT" if period == 5 else f"{period - 4}OT")
    return f"{name} {txt}"


def _elapsed(period, tenths):
    """Seconds since tip-off: Game Replay's x-axis (wpa_lib.seconds_elapsed)."""
    left = tenths / 10
    if period <= 4:
        return round((period - 1) * 720 + 720 - left, 1)
    return round(2880 + (period - 5) * 300 + 300 - left, 1)


@router.get("/plays/finder")
def play_finder(
    player_id: int | None = Query(None, ge=1),
    cat: str | None = None,
    season_from: int | None = None,
    season_to: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    game: str | None = Query(None, max_length=40),
    team: str | None = None,
    opp: str | None = None,
    home: str | None = Query(None, pattern="^(home|away)$"),
    period: str | None = None,
    clock_min: float | None = Query(None, ge=0, le=720),
    clock_max: float | None = Query(None, ge=0, le=720),
    margin_min: int | None = Query(None, ge=-80, le=80),
    margin_max: int | None = Query(None, ge=-80, le=80),
    clutch: bool = False,
    dist_min: int | None = Query(None, ge=0, le=94),
    dist_max: int | None = Query(None, ge=0, le=94),
    sort: str = "newest",
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
):
    g = _games()
    if sort not in SORTS:
        raise HTTPException(status_code=400, detail=f"Unknown sort '{sort}'. Use one of: {', '.join(SORTS)}.")
    for name, value in (("team", team), ("opp", opp)):
        if value and value not in g["teams"]:
            raise HTTPException(status_code=400, detail=f"Unknown {name} '{value}'.")
    cats = _list(cat, list(FILTERS), "category")
    periods = _list(period, list(PERIODS), "period")

    where, args = [], []
    codes = sorted({c for k in cats for c in FILTERS[k][1]})
    if codes:
        where.append("p.cat = ANY(%s)")
        args.append(codes)
    if player_id:
        where.append("p.player_id = %s")
        args.append(player_id)
    span = _no_range(season_from, season_to, date_from, date_to)
    if span is None:
        span = (0, -1)  # empty window: no rows, but the response keeps its shape
    if span != (g["rows"][0][0], g["rows"][-1][0]):
        where.append("p.game_no BETWEEN %s AND %s")
        args += list(span)
    if game:
        no = g["by_id"].get(game.strip())
        if no is None:
            raise HTTPException(status_code=404, detail=f"No play-by-play on file for game '{game}'.")
        where.append("p.game_no = %s")
        args.append(no)
    if team:
        where.append(f"{TEAM_SQL} = %s")
        args.append(team)
    if opp:
        where.append(f"{OPP_SQL} = %s")
        args.append(opp)
    if home:
        where.append("p.is_home" if home == "home" else "NOT p.is_home")
    if periods and len(periods) < len(PERIODS):
        where.append("(" + " OR ".join(PERIODS[x] for x in periods) + ")")
    if clock_min is not None:
        where.append("p.clock >= %s")
        args.append(round(clock_min * 10))
    if clock_max is not None:
        where.append("p.clock <= %s")
        args.append(round(clock_max * 10))
    if margin_min is not None:
        where.append(f"{MARGIN_SQL} >= %s")
        args.append(margin_min)
    if margin_max is not None:
        where.append(f"{MARGIN_SQL} <= %s")
        args.append(margin_max)
    if clutch:
        where.append(CLUTCH_SQL)
    if dist_min is not None or dist_max is not None:
        where.append("p.cat = ANY(%s)")
        args.append(list(SHOT_CODES))
        if dist_min is not None:
            where.append("p.dist >= %s")
            args.append(dist_min)
        if dist_max is not None:
            where.append("p.dist <= %s")
            args.append(dist_max)
    if sort == "dist":
        where.append("p.dist IS NOT NULL")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT p.cat, COUNT(*) {FROM_SQL} {where_sql} GROUP BY 1", args)
        by_cat = dict(cur.fetchall())
        total = sum(by_cat.values())
        most = []
        if not player_id and total:
            cur.execute(f"""SELECT p.player_id, COUNT(*) n {FROM_SQL} {where_sql}
                            {'AND' if where else 'WHERE'} p.player_id IS NOT NULL
                            GROUP BY 1 ORDER BY n DESC, p.player_id LIMIT {TOP_PLAYERS}""", args)
            most = cur.fetchall()
        cur.execute(f"""
            SELECT p.event_id, p.player_id, p.cat, p.period, p.clock, p.score_for, p.score_against, p.dist, p.is_home,
                   gm.game_id, gm.nba_game_id, gm.season, gm.game_date, gm.home_team, gm.away_team,
                   e.description, e.action_type, e.player_name
            {FROM_SQL} JOIN pbp_events e ON e.id = p.event_id
            {where_sql} ORDER BY {SORTS[sort]} LIMIT %s OFFSET %s""", args + [limit, offset])
        rows = cur.fetchall()

    names = _names()
    results = []
    for (eid, pid, code, per, clock, sf, sa, dist, is_home, gid, nba, season, gdate, h, a, desc, action,
         pname) in rows:
        key, cat_label, pts = CATS[code]
        if pid:
            name, identified = names.get(pid) or pname or f"Player {pid}", True
        elif code in TEXT_NAME:
            m = TEXT_NAME[code].search(desc or "")
            name, identified = (m.group(1).strip() if m else None), False
        else:
            name, identified = pname, False
        team_abbr, opp_abbr = (h, a) if is_home else (a, h)
        results.append({
            "event_id": eid, "game_id": gid, "nba_game_id": nba, "season": season, "date": gdate.isoformat(),
            "team": team_abbr, "opponent": opp_abbr, "home": is_home,
            "player_id": pid, "player_name": name or ("Team" if code in (9, 10) else None), "identified": identified,
            "team_play": pid is None and not name,
            "cat": key, "cat_label": cat_label, "points": pts,
            "period": per, "clock": _clock_text(per, clock), "seconds_left": clock / 10,
            "seconds_elapsed": _elapsed(per, clock),
            "score_before": [sf, sa], "score_after": [sf + pts, sa], "margin_before": sf - sa,
            "clutch": per >= 4 and clock <= CLUTCH_SECONDS * 10 and abs(sf - sa) <= CLUTCH_MARGIN,
            "dist": dist, "description": desc, "action_type": (action or "").replace("\n", " "),
        })
    return {
        "total": total, "limit": limit, "offset": offset, "max_offset": MAX_OFFSET,
        "by_cat": [{"key": CATS[c][0], "label": CATS[c][1], "n": by_cat[c]} for c in sorted(by_cat)],
        "points": sum(CATS[c][2] * n for c, n in by_cat.items() if c in (1, 2, 11)),
        "most": [{"player_id": p, "player_name": names.get(p, f"Player {p}"), "n": n} for p, n in most],
        "filters": {
            "player_id": player_id, "player_name": names.get(player_id) if player_id else None, "cat": cats,
            "season_from": season_from, "season_to": season_to,
            "date_from": date_from.isoformat() if date_from else None, "date_to": date_to.isoformat() if date_to else None,
            "game": game, "team": team, "opp": opp, "home": home, "period": periods, "clock_min": clock_min,
            "clock_max": clock_max, "margin_min": margin_min, "margin_max": margin_max, "clutch": clutch,
            "dist_min": dist_min, "dist_max": dist_max, "sort": sort,
        },
        "results": results, "notes": _notes(), "_source": _source(),
    }
