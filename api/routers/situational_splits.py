"""Situational splits: home/away, back-to-backs, long trips, strong vs. weak opponents.

    GET /splits/situational/options                       seasons, splits, stats, floors
    GET /splits/situational/leaderboard?season=&split=&stat=&sort=&order=
                                                          one split + stat for every player-season,
                                                          with the league effect and the chance check
    GET /splits/situational/player/{player_id}?season=    every split and stat for one player-season

Reads `player_situational_splits` and `situational_split_league`
(scripts/build_situational_splits.py, definitions in api/situational_splits.py):
regular season 2020-21 to 2025-26, from the play-by-play game lines joined to
the schedule. Each effect is side A minus side B with a game-clustered 95%
interval; "vs. league" subtracts that season's average player's effect. The
league rows carry the honest check: how many players land outside 95% of the
league effect, how many do when each player's own games are shuffled between
the two sides (pure chance, same formula), and whether a player's effect one
season predicts the next (year-to-year r).

Tables are small and read per request; the player-name map is game_log's
(lru-cached: restart impact_api after rebuilding player_game_lines).
"""

from fastapi import APIRouter, HTTPException, Query

from impact_core import get_db
from routers.game_log import _names
from situational_splits import LONG_TRIP, MIN_GAMES, SHORT_TRIP, SPLITS, STATS
from source_badge import make_source

router = APIRouter()

MAX_LIMIT = 200
SORTS = {"vs_league": "vs_league", "z": "z", "diff": "diff", "games": "LEAST(games_a, games_b)"}

PLAYER_COLS = ["player_id", "season", "split", "stat", "teams", "games_a", "games_b", "minutes_a", "minutes_b",
               "den_a", "den_b", "value_a", "value_b", "diff", "se", "ci_low", "ci_high", "league_diff",
               "vs_league", "z", "qualified"]
LEAGUE_COLS = ["season", "split", "stat", "players", "league_value_a", "league_value_b", "league_diff",
               "league_se", "league_ci_low", "league_ci_high", "fe_diff", "fe_se", "venue_adj_diff",
               "venue_adj_se", "games_a", "games_b", "home_share_a", "home_share_b", "outside_95",
               "expected_outside_95", "chance_outside_95", "outside_high", "outside_low", "yoy_r", "yoy_n"]

METHOD = (
    "Regular season 2020-21 to 2025-26 (game-by-game lines are rebuilt from ESPN play-by-play, which starts there), "
    "joined to the schedule on team and date. Each side's value is the ratio of its sums (points per 36 = 36 x points "
    "/ minutes over the side's games). The effect is side A minus side B; its 95% interval treats each game as one "
    "draw. The league effect is the average qualified player's effect, weighted by his playing time on the smaller "
    "side, with an interval that resamples whole team-seasons. Vs. league = his effect minus that. Rest and travel "
    "are the team's schedule; back-to-backs count only second nights he also played the first night of. Opponent "
    "strength is the opponent's average final-score margin over that whole season (the game itself included)."
)


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _source():
    return make_source(["player_situational_splits", "situational_split_league", "player_game_lines",
                        "team_game_fatigue", "game_scores"],
                       "ESPN play-by-play (lines rebuilt from it) and scoreboard (final scores), "
                       "nba_api (stats.nba.com) schedule")


def _f(v, d=4):
    return None if v is None else round(float(v), d)


def _league(cur, season, split=None, stat=None):
    where, params = ["season = %s"], [season]
    if split:
        where.append("split = %s")
        params.append(split)
    if stat:
        where.append("stat = %s")
        params.append(stat)
    cur.execute(f"SELECT {', '.join(LEAGUE_COLS)} FROM situational_split_league WHERE {' AND '.join(where)}", params)
    out = {}
    for r in cur.fetchall():
        d = {k: (_f(v) if isinstance(v, float) else v) for k, v in zip(LEAGUE_COLS, r)}
        out[(d["split"], d["stat"])] = d
    return out


def _seasons(cur):
    cur.execute("SELECT DISTINCT season FROM situational_split_league WHERE season > 0 ORDER BY 1")
    seasons = [s for (s,) in cur.fetchall()]
    if not seasons:
        raise HTTPException(status_code=503, detail="No situational splits — run scripts/build_situational_splits.py.")
    return seasons


def _row(r, names):
    d = dict(zip(PLAYER_COLS, r))
    d["player_name"] = names.get(d["player_id"])
    for k in ("minutes_a", "minutes_b", "den_a", "den_b"):
        d[k] = _f(d[k], 1)
    for k in ("value_a", "value_b", "diff", "se", "ci_low", "ci_high", "league_diff", "vs_league", "z"):
        d[k] = _f(d[k])
    d["ci_excludes_league"] = (None if d["ci_low"] is None or d["league_diff"] is None
                               else bool(d["ci_low"] > d["league_diff"] or d["ci_high"] < d["league_diff"]))
    return d


def _options_payload(seasons):
    return {
        "seasons": seasons,
        "splits": [{"key": k, "label": v[0], "a": v[1], "b": v[2]} for k, v in SPLITS.items()],
        "stats": [{"key": k, "label": v[0], "format": v[4], "side_floor": v[5], "floor_unit": v[2]}
                  for k, v in STATS.items()],
        "min_games": MIN_GAMES, "long_trip": LONG_TRIP, "short_trip": SHORT_TRIP,
        "sorts": list(SORTS), "method": METHOD,
    }


@router.get("/splits/situational/options")
def situational_options():
    with get_db() as conn:
        cur = conn.cursor()
        seasons = _seasons(cur)
        pooled = _league(cur, 0)
    return {
        **_options_payload(seasons),
        # Pooled over every season: the headline effect per split and stat.
        "pooled": [pooled[(sp, st)] for sp in SPLITS for st in STATS if (sp, st) in pooled],
        "_source": _source(),
    }


@router.get("/splits/situational/leaderboard")
def situational_leaderboard(
    season: int | None = None,
    split: str = Query("home"),
    stat: str = Query("pts"),
    sort: str = Query("z"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    qualified_only: bool = True,
    team: str | None = None,
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
):
    if split not in SPLITS:
        raise HTTPException(status_code=400, detail=f"Unknown split '{split}'. Use one of {', '.join(SPLITS)}.")
    if stat not in STATS:
        raise HTTPException(status_code=400, detail=f"Unknown stat '{stat}'. Use one of {', '.join(STATS)}.")
    if sort not in SORTS:
        raise HTTPException(status_code=400, detail=f"Can't sort by '{sort}'. Use one of {', '.join(SORTS)}.")
    names = _names()
    with get_db() as conn:
        cur = conn.cursor()
        seasons = _seasons(cur)
        season = season or seasons[-1]
        if season not in seasons:
            raise HTTPException(status_code=404, detail=(
                f"No situational splits for {label(season)}: they cover {label(seasons[0])} to {label(seasons[-1])}."))
        where = ["season = %s", "split = %s", "stat = %s"]
        params = [season, split, stat]
        if qualified_only:
            where.append("qualified")
        if team:
            # A traded player's teams read like 'BKN/PHI' (most games first).
            where.append("%s = ANY(string_to_array(teams, '/'))")
            params.append(team.upper())
        cur.execute(f"""SELECT {', '.join(PLAYER_COLS)}, count(*) OVER ()
                        FROM player_situational_splits WHERE {' AND '.join(where)}
                        ORDER BY {SORTS[sort]} {order.upper()} NULLS LAST, player_id
                        LIMIT %s OFFSET %s""", params + [limit, offset])
        rows = cur.fetchall()
        total = rows[0][-1] if rows else 0
        cur.execute("""SELECT count(*), count(*) FILTER (WHERE qualified) FROM player_situational_splits
                       WHERE season = %s AND split = %s AND stat = %s""", (season, split, stat))
        stored, qualified = cur.fetchone()
        cur.execute("""SELECT DISTINCT unnest(string_to_array(teams, '/')) FROM player_situational_splits
                       WHERE season = %s ORDER BY 1""", (season,))
        teams = [t for (t,) in cur.fetchall()]
        league = _league(cur, season, split, stat).get((split, stat))
        pooled = _league(cur, 0, split, stat).get((split, stat))
    sp, st = SPLITS[split], STATS[stat]
    return {
        "season": season, "split": split, "stat": stat, "split_label": sp[0], "a": sp[1], "b": sp[2],
        "stat_label": st[0], "format": st[4], "side_floor": st[5], "min_games": MIN_GAMES,
        "sort": sort, "order": order, "qualified_only": qualified_only, "team": team.upper() if team else None,
        "total": total, "offset": offset, "limit": limit, "stored": stored, "qualified": qualified, "teams": teams,
        "league": league, "pooled": pooled,
        "results": [_row(r[:-1], names) for r in rows],
        "method": METHOD, "_source": _source(),
    }


@router.get("/splits/situational/player/{player_id}")
def situational_player(player_id: int, season: int | None = None):
    names = _names()
    if player_id not in names:
        raise HTTPException(status_code=404, detail=f"No NBA seasons on file for player id {player_id}.")
    with get_db() as conn:
        cur = conn.cursor()
        all_seasons = _seasons(cur)
        cur.execute("""SELECT season, count(*) FILTER (WHERE qualified), max(teams)
                       FROM player_situational_splits WHERE player_id = %s GROUP BY 1 ORDER BY 1""", (player_id,))
        have = [{"season": s, "qualified_rows": q, "teams": t} for s, q, t in cur.fetchall()]
        if not have:
            raise HTTPException(status_code=404, detail=(
                f"No situational splits for {names[player_id]}: they need 3+ games on both sides of a split, in "
                f"regular seasons {label(all_seasons[0])} to {label(all_seasons[-1])}."))
        seasons = [h["season"] for h in have]
        season = season if season is not None else seasons[-1]
        if season not in seasons:
            raise HTTPException(status_code=404, detail=f"No situational splits for {names[player_id]} in {label(season)}.")
        cur.execute(f"SELECT {', '.join(PLAYER_COLS)} FROM player_situational_splits WHERE player_id = %s AND season = %s",
                    (player_id, season))
        rows = {(r[2], r[3]): _row(r, names) for r in cur.fetchall()}
        league = _league(cur, season)
        pooled = _league(cur, 0)
    splits = []
    for sp, (sp_label, a, b, *_rest) in SPLITS.items():
        stats = []
        for st, (st_label, *_x, fmt, floor) in STATS.items():
            r = rows.get((sp, st))
            lg, pl = league.get((sp, st)), pooled.get((sp, st))
            stats.append({
                "stat": st, "label": st_label, "format": fmt, "side_floor": floor, "row": r,
                "league_diff": lg["league_diff"] if lg else None,
                "league_ci": [lg["league_ci_low"], lg["league_ci_high"]] if lg else None,
                "chance": ({k: pl[k] for k in ("outside_95", "chance_outside_95", "players", "yoy_r", "yoy_n")}
                           if pl else None),
            })
        any_row = next((s["row"] for s in stats if s["row"]), None)
        splits.append({"split": sp, "label": sp_label, "a": a, "b": b,
                       "games_a": any_row["games_a"] if any_row else 0, "games_b": any_row["games_b"] if any_row else 0,
                       "stats": stats})
    return {
        "player_id": player_id, "player_name": names[player_id], "season": season, "seasons": have,
        "min_games": MIN_GAMES, "splits": splits, "method": METHOD, "_source": _source(),
    }
