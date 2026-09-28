"""
Expected FG% and shot-making (scripts/build_shot_making.py).

    GET /shots/shot-making/leaderboard?season=2016&sort=shot_making&order=desc&limit=50
    GET /shots/shot-making/model                       how the per-shot model was checked
    GET /shots/player/{player_name}/shot-making        one player's seasons

Every number is read from the stored tables player_shot_making,
shot_making_league and shot_making_validation; nothing is computed live.
A season under MIN_FGA attempts is returned with qualified=false (no rank)
so the UI can grey it out.
"""

from fastapi import APIRouter, HTTPException, Query

from impact_core import find_player, get_db
from source_badge import make_source

router = APIRouter()

PLAYER_COLS = ["season", "team_abbreviation", "fga", "fgm", "fg3a", "fg3m", "efg_pct", "x_efg_pct", "shot_making",
               "se", "pts_above", "fg_pct", "x_fg_pct", "fg3_pct", "x_fg3_pct", "fg2_pct", "x_fg2_pct",
               "qualified", "rank", "quality_rank", "pool"]
SORTS = {"shot_making": "shot_making", "quality": "x_efg_pct", "pts_above": "pts_above", "efg": "efg_pct"}
# What the model does and doesn't know, repeated on every response so no
# page can show the numbers without the caveat.
NOT_ON_FILE = ("No shot has a closest-defender distance or a shot type (catch-and-shoot vs. pull-up), "
               "so shot-making also carries the defence a player faced and the shots he created himself.")


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _r(v, d=4):
    return None if v is None else round(float(v), d)


def _row(cols, values):
    out = dict(zip(cols, values))
    for k, v in out.items():
        if isinstance(v, float):
            out[k] = _r(v, 1 if k == "pts_above" else 4)
    out["margin95"] = _r(1.96 * out["se"]) if out.get("se") is not None else None
    return out


def _source():
    return make_source(["player_shot_making", "shot_making_league", "shot_making_validation"],
                       "stats.nba.com shot locations (player_shots), regular season, cross-fitted boosting model")


def _league(cur, season=None):
    cur.execute("SELECT season, fga, fgm, efg_pct, x_efg_pct, log_loss, n_qualified, sd_shot_making, sd_quality "
                "FROM shot_making_league" + (" WHERE season = %s" if season else "") + " ORDER BY season",
                (season,) if season else ())
    cols = ["season", "fga", "fgm", "efg_pct", "x_efg_pct", "log_loss", "n_qualified", "sd_shot_making", "sd_quality"]
    return [{k: (_r(v) if isinstance(v, float) else v) for k, v in zip(cols, r)} for r in cur.fetchall()]


def _min_fga(cur):
    cur.execute("SELECT notes->>'min_fga' FROM shot_making_validation WHERE scope = 'crossfit' LIMIT 1")
    r = cur.fetchone()
    if r is None:
        raise HTTPException(status_code=503, detail="Shot-making isn't built yet (run scripts/build_shot_making.py).")
    return int(r[0])


@router.get("/shots/shot-making/leaderboard")
def shot_making_leaderboard(
    season: int | None = None,
    sort: str = Query("shot_making", pattern="^(shot_making|quality|pts_above|efg)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=5, le=500),
):
    """Qualified player-seasons of one season (default: the latest), ranked
    by shot-making, shot quality, points above expected or eFG%."""
    with get_db() as conn:
        cur = conn.cursor()
        min_fga = _min_fga(cur)
        league = _league(cur)
        seasons = [r["season"] for r in league]
        if season is None:
            season = seasons[-1]
        if season not in seasons:
            raise HTTPException(status_code=404, detail=f"Shot-making covers {label(seasons[0])} to {label(seasons[-1])}.")
        cur.execute(
            f"""SELECT player_id, player_name, {', '.join(PLAYER_COLS)} FROM player_shot_making
                WHERE season = %s AND qualified ORDER BY {SORTS[sort]} {'DESC' if order == 'desc' else 'ASC'}, fga DESC
                LIMIT %s""",
            (season, limit),
        )
        rows = []
        for r in cur.fetchall():
            row = _row(PLAYER_COLS, r[2:])
            row.update({"player_id": r[0], "player_name": r[1]})
            rows.append(row)
    return {
        "season": season, "seasons": seasons, "sort": sort, "order": order, "min_fga": min_fga,
        "league": next(r for r in league if r["season"] == season),
        "rows": rows,
        "not_on_file": NOT_ON_FILE,
        "_source": _source(),
    }


@router.get("/shots/player/{player_name}/shot-making")
def player_shot_making(player_name: str):
    """One player's regular seasons: actual vs expected eFG%, shot-making
    with its 95% margin, points above expected, and rank among that
    season's qualified players."""
    with get_db() as conn:
        cur = conn.cursor()
        player_id, resolved = find_player(cur, player_name)
        min_fga = _min_fga(cur)
        league = _league(cur)
        cur.execute(f"SELECT {', '.join(PLAYER_COLS)} FROM player_shot_making WHERE player_id = %s ORDER BY season",
                    (int(player_id),))
        rows = [_row(PLAYER_COLS, r) for r in cur.fetchall()]
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No regular-season shots on file for {resolved}. Shot-making covers "
                   f"{label(league[0]['season'])} to {label(league[-1]['season'])}.",
        )
    by_season = {r["season"]: r for r in league}
    for r in rows:
        lg = by_season.get(r["season"])
        r["league_efg_pct"] = lg["efg_pct"] if lg else None
        r["sd_shot_making"] = lg["sd_shot_making"] if lg else None
    return {
        "player_id": int(player_id), "player_name": resolved, "min_fga": min_fga,
        "coverage": {"first": label(league[0]["season"]), "last": label(league[-1]["season"])},
        "rows": rows,
        "not_on_file": NOT_ON_FILE,
        "_source": _source(),
    }


@router.get("/shots/shot-making/model")
def shot_making_model():
    """How the per-shot model was chosen and checked: the held-out season's
    log loss for each candidate next to two baselines, reliability bins,
    the cross-fit score over every season, and year-to-year stability."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT computed_at, model_type, scope, deployed, n_train, n_test, log_loss, brier, roc_auc,
                              reliability_bins, notes
                       FROM shot_making_validation ORDER BY scope DESC, log_loss DESC""")
        cols = ["computed_at", "model_type", "scope", "deployed", "n_train", "n_test", "log_loss", "brier", "roc_auc",
                "reliability_bins", "notes"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        league = _league(cur)
    if not rows:
        raise HTTPException(status_code=503, detail="Shot-making isn't built yet (run scripts/build_shot_making.py).")
    for r in rows:
        r["computed_at"] = r["computed_at"].isoformat()
        for k in ("log_loss", "brier", "roc_auc"):
            r[k] = _r(r[k])
    crossfit = next((r for r in rows if r["scope"] == "crossfit"), None)
    holdout = [r for r in rows if r["scope"] == "holdout"]
    return {
        "holdout_season": holdout[0]["notes"].get("holdout_season") if holdout else None,
        "holdout": holdout,
        "crossfit": crossfit,
        "league": league,
        "not_on_file": NOT_ON_FILE,
        "_source": _source(),
    }
