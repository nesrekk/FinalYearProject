"""Best games and biggest upsets.

    GET /best-games/options        seasons, teams, the excitement formula and how much its weights matter,
                                   coverage notes, and the pre-game model's tail calibration
    GET /best-games                the regular-season games since 2020-21 ranked by excitement (or comeback,
                                   lead changes, closest finish, overtimes), filtered by season and team
    GET /upsets                    every regular-season game since 2010-11 with the winner's pre-game odds,
                                   lowest first, filtered by season, team and games played so far

Best games: scripts/build_best_games.py -> best_games (one row per game: win-probability swing, lead changes,
comeback, overtime, excitement), from ESPN play-by-play, so 2020-21 on only; api/best_games.py holds the formula
the page shows. Upsets: game_pregame_odds (the Season Simulator's held-out pre-game win chances, 2010-11 on) joined
to the real result. Regular season only in both (no playoff play-by-play or pre-game odds is on file). Filters never
become SQL text: sorts and teams are looked up, every value is a bound parameter. The option and calibration reads
are cached per process: restart impact_api after rebuilding.
"""

from functools import lru_cache

import current_season
from fastapi import APIRouter, HTTPException, Query

from best_games import FORMULA, LEAD_CHANGE_W, MARGIN_W, OVERTIME_W, SORT_LABELS, SORTS
from impact_core import get_db
from luck_lib import FRANCHISE
from source_badge import make_source
from teams_lib import lookup_codes

router = APIRouter()

MAX_LIMIT = 100
MAX_OFFSET = 2_000
UPSET_SORTS = {
    "chance": "winner_p, o.game_date DESC, o.game_id",
    "newest": "o.game_date DESC, winner_p, o.game_id",
    "margin": "abs(o.margin) DESC, winner_p, o.game_id",
}
UPSET_SORT_LABELS = {"chance": "Lowest pre-game win chance", "newest": "Newest", "margin": "Biggest winning margin"}
CAL_BINS = [(0.0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.30), (0.30, 0.40), (0.40, 0.50)]
# Franchise -> every code it played under in the odds table (NJN = the Nets, NOH = the Pelicans then).
CODES_OF = {}
for _old, _new in FRANCHISE.items():
    CODES_OF.setdefault(_new, {_new}).add(_old)
BEST_SOURCE = ["best_games", "best_games_meta", "play_finder_games", "play_finder_events", "game_pregame_odds"]
UPSET_SOURCE = ["game_pregame_odds", "game_scores", "best_games"]
UPSTREAM = "ESPN play-by-play and final scores via sportsdataverse (scripts/build_play_finder.py, scripts/fetch_game_scores.py)"


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _require(cur, table, script):
    cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
    if cur.fetchone()[0] is None:
        raise HTTPException(status_code=503, detail=f"No {table} data: run {script}.")


def _codes(team):
    """Every code a team abbreviation can appear under (PHX/PHO, BKN/NJN, NOP/NOH), or 400."""
    if not team:
        return None
    t = team.strip().upper()
    codes = set(lookup_codes(t)) | CODES_OF.get(FRANCHISE.get(t, t), {t})
    if not codes & _team_set():
        raise HTTPException(status_code=400, detail=f"Unknown team '{team}'.")
    return sorted(codes)


@lru_cache(maxsize=1)
def _team_set():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT home FROM game_pregame_odds UNION SELECT away FROM game_pregame_odds "
                    "UNION SELECT home_team FROM best_games UNION SELECT away_team FROM best_games")
        return {r[0] for r in cur.fetchall()}


@lru_cache(maxsize=1)
def _options():
    with get_db() as conn:
        cur = conn.cursor()
        _require(cur, "best_games", "scripts/build_best_games.py")
        _require(cur, "game_pregame_odds", "scripts/build_season_sim.py")
        best = _rows(cur, """SELECT season, COUNT(*) AS games, COUNT(*) FILTER (WHERE score_ok) AS ranked,
                                    COUNT(*) FILTER (WHERE periods > 4) AS overtime_games,
                                    MIN(game_date) AS first_date, MAX(game_date) AS last_date,
                                    AVG(excitement) FILTER (WHERE score_ok) AS mean_excitement
                             FROM best_games GROUP BY season ORDER BY season""")
        odds = _rows(cur, """SELECT season, COUNT(*) AS games,
                                    AVG((LEAST(p_home, 1 - p_home)) ) AS mean_underdog_chance,
                                    AVG(((home_won AND p_home < 0.5) OR (NOT home_won AND p_home > 0.5))::int) AS upset_rate,
                                    MIN(game_date) AS first_date, MAX(game_date) AS last_date
                             FROM game_pregame_odds GROUP BY season ORDER BY season""")
        meta = {r["name"]: r for r in _rows(cur, "SELECT * FROM best_games_meta")}
        # the calibration and the favourites' record pool complete seasons (a season being played would make them
        # drift daily; round 9 step 5)
        last = current_season.latest_complete_season(cur)
        cal = []
        for lo, hi in CAL_BINS:
            cur.execute("""SELECT COUNT(*), AVG(LEAST(p_home, 1 - p_home)),
                                  AVG(((home_won AND p_home < 0.5) OR (NOT home_won AND p_home > 0.5))::int)
                           FROM game_pregame_odds WHERE LEAST(p_home, 1 - p_home) > %s AND LEAST(p_home, 1 - p_home) <= %s
                             AND season <= %s""",
                        (lo, hi, last))
            n, expected, actual = cur.fetchone()
            cal.append({"lo": lo, "hi": hi, "games": n, "expected": expected, "actual": actual})
        cur.execute("""SELECT AVG(GREATEST(p_home, 1 - p_home)),
                              AVG(((home_won AND p_home >= 0.5) OR (NOT home_won AND p_home < 0.5))::int),
                              AVG(home_won::int) FILTER (WHERE season < 2020 OR season > 2021), COUNT(*)
                       FROM game_pregame_odds WHERE season <= %s""", (last,))
        fav_mean, fav_win, home_win, total = cur.fetchone()
    teams = sorted({FRANCHISE.get(t, t) for t in _team_set()})
    return {
        "best": best, "odds": odds, "meta": meta, "calibration": cal, "teams": teams,
        "favourites": {"mean_chance": fav_mean, "won": fav_win, "home_win_rate": home_win, "games": total},
    }


def _notes(o):
    m = o["meta"]
    ranked, games = int(m["games_ranked"]["value"]), int(m["games"]["value"])
    lo, hi = o["best"][0], o["best"][-1]
    ots = sum(s["overtime_games"] for s in o["best"])
    fav = o["favourites"]
    return {
        "coverage": (f"Best games: every regular-season game {label(lo['season'])} to {label(hi['season'])} "
                     f"({games:,} games, through {hi['last_date'].isoformat()}), the years ESPN's play-by-play is on file "
                     "for; the three NBA Cup finals are left out (they don't count in regular-season stats). "
                     "Upsets: every regular-season game 2010-11 on. Playoff and play-in games are in neither: no "
                     "playoff play-by-play or pre-game odds are on file."),
        "reconciliation": (f"{games - ranked} of {games:,} games have play-by-play whose score doesn't reconcile to the "
                           "real final; they keep their numbers but are left out of every ranking."),
        "formula": FORMULA + f". {ots} of {games:,} games went to overtime.",
        "weights": (f"The three weights are a judgment call, not fitted: nothing on file says which games fans found "
                    f"exciting. Ranking games by the plain win-probability swing instead gives a rank correlation of "
                    f"{m['rank_corr_swing']['value']:.3f} and {int(m['top50_overlap_swing']['value'])} of the same top 50."),
        "upsets": (f"Pre-game win chances are the Season Simulator's, each from ratings, home court and rest as of that "
                   f"morning and a model fitted without the season it scores; the favourite won {fav['won']:.1%} of "
                   f"{fav['games']:,} games. The model doesn't know who is injured or resting, so a favourite missing a "
                   "star is still a favourite here, which explains some of the biggest upsets."),
    }


@router.get("/best-games/options")
def best_games_options():
    o = _options()
    m = o["meta"]
    return {
        "seasons": {
            "best": [{"season": s["season"], "label": label(s["season"]), "games": s["games"], "ranked": s["ranked"],
                      "overtime_games": s["overtime_games"], "mean_excitement": round(s["mean_excitement"], 2)}
                     for s in o["best"]],
            "upsets": [{"season": s["season"], "label": label(s["season"]), "games": s["games"],
                        "upset_rate": round(s["upset_rate"], 4), "mean_underdog_chance": round(s["mean_underdog_chance"], 4)}
                       for s in o["odds"]],
        },
        "teams": o["teams"],
        "sorts": [{"key": k, "label": SORT_LABELS[k]} for k in SORTS],
        "upset_sorts": [{"key": k, "label": UPSET_SORT_LABELS[k]} for k in UPSET_SORTS],
        "formula": {
            "text": FORMULA, "lead_change_w": LEAD_CHANGE_W, "overtime_w": OVERTIME_W, "margin_w": MARGIN_W,
            "rank_corr_swing": m["rank_corr_swing"]["value"], "top50_overlap_swing": int(m["top50_overlap_swing"]["value"]),
        },
        "calibration": [{**c, "expected": round(c["expected"], 4), "actual": round(c["actual"], 4)}
                        if c["games"] else c for c in o["calibration"]],
        "favourites": o["favourites"],
        "max_limit": MAX_LIMIT, "max_offset": MAX_OFFSET,
        "notes": _notes(o), "_source": make_source(BEST_SOURCE, UPSTREAM),
    }


def _season_check(season, table_key):
    if season is None:
        return
    have = {s["season"] for s in _options()[table_key]}
    if season not in have:
        raise HTTPException(status_code=404, detail=f"No games on file for {season}; seasons: {min(have)}-{max(have)}.")


@router.get("/best-games")
def best_games(
    season: int | None = None,
    team: str | None = Query(None, max_length=4),
    sort: str = "excitement",
    ot: bool = False,
    limit: int = Query(25, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
):
    if sort not in SORTS:
        raise HTTPException(status_code=400, detail=f"Unknown sort '{sort}'. Use one of: {', '.join(SORTS)}.")
    _season_check(season, "best")
    codes = _codes(team)
    where, args = ["b.score_ok"], []
    if season is not None:
        where.append("b.season = %s")
        args.append(season)
    if codes:
        where.append("(b.home_team = ANY(%s) OR b.away_team = ANY(%s))")
        args += [codes, codes]
    if ot:
        where.append("b.periods > 4")
    where_sql = "WHERE " + " AND ".join(where)
    order = SORTS[sort]
    with get_db() as conn:
        cur = conn.cursor()
        _require(cur, "best_games", "scripts/build_best_games.py")
        cur.execute(f"""SELECT COUNT(*), AVG(excitement), AVG(swing), AVG(lead_changes::float), AVG((periods > 4)::int),
                               AVG(final_margin::float) FROM best_games b {where_sql}""", args)
        total, mean_x, mean_s, mean_lc, ot_share, mean_margin = cur.fetchone()
        # Rank among every ranked game in the same seasons, so a team filter still shows where a game stands.
        pool = "WHERE b.score_ok" + (" AND b.season = %s" if season is not None else "")
        pool_args = [season] if season is not None else []
        rows = _rows(cur, f"""
            WITH pool AS (
              SELECT b.game_id, RANK() OVER (ORDER BY b.excitement DESC) AS rank_all,
                     COUNT(*) OVER () AS n_all
              FROM best_games b {pool}
            )
            SELECT b.*, pool.rank_all, pool.n_all, o.p_home,
                   e.description AS peak_description, e.period AS peak_period, e.player_name AS peak_player
            FROM best_games b
            JOIN pool ON pool.game_id = b.game_id
            LEFT JOIN game_pregame_odds o ON o.game_id = b.nba_game_id
            LEFT JOIN pbp_events e ON e.id = b.peak_event_id
            {where_sql}
            ORDER BY {order}, b.game_id LIMIT %s OFFSET %s""", pool_args + args + [limit, offset])
    results = []
    for i, r in enumerate(rows):
        home_won = r["pts_home"] > r["pts_away"]
        winner, loser = (r["home_team"], r["away_team"]) if home_won else (r["away_team"], r["home_team"])
        p = r.pop("p_home")
        results.append({
            "rank": offset + i + 1, "rank_all": r["rank_all"], "of_all": r["n_all"],
            "game_id": r["game_id"], "nba_game_id": r["nba_game_id"], "season": r["season"], "date": r["game_date"].isoformat(),
            "home": r["home_team"], "away": r["away_team"], "pts_home": r["pts_home"], "pts_away": r["pts_away"],
            "winner": winner, "loser": loser, "periods": r["periods"], "overtimes": r["periods"] - 4,
            "excitement": round(r["excitement"], 2), "swing": round(r["swing"], 2), "lead_changes": r["lead_changes"],
            "ties": r["ties"], "largest_lead": r["largest_lead"], "comeback": r["comeback"],
            "win_min_wp": round(r["win_min_wp"], 4), "final_margin": r["final_margin"], "events": r["events"],
            "winner_pregame": None if p is None else round(p if home_won else 1 - p, 4),
            "peak": None if r["peak_event_id"] is None else {
                "event_id": r["peak_event_id"], "seconds_elapsed": r["peak_t"], "swing": round(r["peak_dwp"], 3),
                "description": r["peak_description"], "period": r["peak_period"], "player": r["peak_player"],
            },
        })
    return {
        "total": total, "limit": limit, "offset": offset, "max_offset": MAX_OFFSET,
        "summary": None if not total else {
            "mean_excitement": round(mean_x, 2), "mean_swing": round(mean_s, 2), "mean_lead_changes": round(mean_lc, 2),
            "overtime_share": round(ot_share, 4), "mean_margin": round(mean_margin, 1),
        },
        "filters": {"season": season, "team": team.upper() if team else None, "sort": sort, "ot": ot},
        "results": results, "notes": _notes(_options()), "_source": make_source(BEST_SOURCE, UPSTREAM),
    }


@router.get("/upsets")
def upsets(
    season: int | None = None,
    team: str | None = Query(None, max_length=4),
    side: str | None = Query(None, pattern="^(won|lost)$"),
    min_games: int = Query(0, ge=0, le=60),
    sort: str = "chance",
    limit: int = Query(25, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
):
    """`team` filters to that team's games; with `side` it is the team that won the upset ("won") or lost it as the
    favourite ("lost"). `min_games`: both teams have played at least that many games that season, so the ratings
    aren't mostly last year's."""
    if sort not in UPSET_SORTS:
        raise HTTPException(status_code=400, detail=f"Unknown sort '{sort}'. Use one of: {', '.join(UPSET_SORTS)}.")
    _season_check(season, "odds")
    codes = _codes(team)
    if side and not codes:
        raise HTTPException(status_code=400, detail="side needs a team.")
    upset = "((o.home_won AND o.p_home < 0.5) OR (NOT o.home_won AND o.p_home > 0.5))"
    base, base_args = [], []
    if season is not None:
        base.append("o.season = %s")
        base_args.append(season)
    if codes:
        base.append("(o.home = ANY(%s) OR o.away = ANY(%s))")
        base_args += [codes, codes]
    if min_games:
        base.append("o.home_games >= %s AND o.away_games >= %s")
        base_args += [min_games, min_games]
    where, args = [upset] + base, list(base_args)
    if side:
        winner_side = "((o.home_won AND o.home = ANY(%s)) OR (NOT o.home_won AND o.away = ANY(%s)))"
        where.append(winner_side if side == "won" else "NOT " + winner_side)
        args += [codes, codes]
    where_sql = "WHERE " + " AND ".join(where)
    winner_p = "(CASE WHEN o.home_won THEN o.p_home ELSE 1 - o.p_home END)"
    order = UPSET_SORTS[sort].replace("winner_p", winner_p)
    with get_db() as conn:
        cur = conn.cursor()
        _require(cur, "game_pregame_odds", "scripts/build_season_sim.py")
        # every game the season / team / games-played filters pick, upset or not
        base_sql = ("WHERE " + " AND ".join(base)) if base else ""
        cur.execute(f"SELECT COUNT(*) FROM game_pregame_odds o {base_sql}", base_args)
        games_all = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*), AVG({winner_p}) FROM game_pregame_odds o {where_sql}", args)
        total, mean_p = cur.fetchone()
        rows = _rows(cur, f"""
            SELECT o.game_id, o.season, o.game_date, o.home, o.away, o.p_home, o.home_won, o.margin, o.pts_home, o.pts_away,
                   o.home_games, o.away_games, o.r_home, o.r_away, o.home_b2b, o.away_b2b, o.exp_margin,
                   {winner_p} AS winner_p, s.periods, b.game_id AS replay_id, b.excitement, b.comeback,
                   b.lead_changes, b.score_ok
            FROM game_pregame_odds o
            LEFT JOIN game_scores s ON s.game_id = o.game_id AND s.team_abbreviation = o.home
            LEFT JOIN best_games b ON b.nba_game_id = o.game_id
            {where_sql} ORDER BY {order} LIMIT %s OFFSET %s""", args + [limit, offset])
    results = []
    for i, r in enumerate(rows):
        home_won = r["home_won"]
        results.append({
            "rank": offset + i + 1, "game_id": r["game_id"], "replay_id": r["replay_id"], "season": r["season"],
            "date": r["game_date"].isoformat(), "home": r["home"], "away": r["away"],
            "pts_home": r["pts_home"], "pts_away": r["pts_away"], "margin": abs(r["margin"]),
            "winner": r["home"] if home_won else r["away"], "loser": r["away"] if home_won else r["home"],
            "winner_home": bool(home_won), "winner_chance": round(r["winner_p"], 4),
            "winner_rating": round(r["r_home"] if home_won else r["r_away"], 1),
            "loser_rating": round(r["r_away"] if home_won else r["r_home"], 1),
            "games_played": [r["home_games"], r["away_games"]],
            "loser_back_to_back": bool(r["away_b2b"] if home_won else r["home_b2b"]),
            "winner_back_to_back": bool(r["home_b2b"] if home_won else r["away_b2b"]),
            "expected_margin": round(r["exp_margin"] if home_won else -r["exp_margin"], 1),
            "overtimes": None if r["periods"] is None else max(0, r["periods"] - 4),
            "excitement": None if r["excitement"] is None or not r["score_ok"] else round(r["excitement"], 2),
            "comeback": r["comeback"], "lead_changes": r["lead_changes"],
        })
    return {
        "total": total, "games": games_all, "upset_rate": None if not games_all else round(total / games_all, 4),
        "mean_winner_chance": None if not total else round(mean_p, 4),
        "limit": limit, "offset": offset, "max_offset": MAX_OFFSET,
        "filters": {"season": season, "team": team.upper() if team else None, "side": side, "min_games": min_games,
                    "sort": sort},
        "results": results, "notes": _notes(_options()), "_source": make_source(UPSET_SOURCE, UPSTREAM),
    }
