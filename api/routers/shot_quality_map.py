"""Shot quality map: where a player shot, how well, and how well an average
shooter would have, on a hexagon grid of the half court.

    GET /shots/quality-map?player=Stephen Curry&season=2016   one player-season's cells next to the league's
    GET /shots/quality-map/options?player=Stephen Curry        the seasons that player has a map for

Data: player_shot_hex / shot_hex_league / shot_hex_meta, written by
scripts/build_shot_making.py while its cross-fitted predictions are in memory
(grid in api/shot_hex.py, shared). Per cell: attempts, makes and expected makes
(the shot-making model's make chance summed over the cell's shots; every shot
was scored by a model that never saw that player), and the league's attempts
and makes in the same cell that season. Nothing is computed live except
the cell centres. A player-season needs MIN_FGA (200) attempts to have a map;
shots beyond half court and, before 2010-11, the ~25% of shots the NBA gave no
location (stored at (0, 0)) are in no cell and are returned as `off_map`.
"""

from fastapi import APIRouter, HTTPException, Query

import shot_hex as H
from impact_core import get_db, resolve_player
from source_badge import make_source

router = APIRouter()

LEAGUE_MIN_FGA = 30    # a league cell with fewer shots than this has no FG% to compare with
NOT_ON_FILE = ("No shot has a closest-defender distance or a shot type (catch-and-shoot vs. pull-up), so 'expected' "
               "is what an average shooter makes from that spot and how the shot was set up isn't known.")


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _source():
    return make_source(["player_shot_hex", "shot_hex_league", "shot_hex_meta", "player_shot_making"],
                       "stats.nba.com shot locations (player_shots), regular season, cross-fitted boosting model")


def _require(cur):
    cur.execute("SELECT to_regclass('public.player_shot_hex')")
    if cur.fetchone()[0] is None:
        raise HTTPException(status_code=503, detail="No shot quality map yet: run scripts/build_shot_making.py.")


def _min_fga(cur):
    cur.execute("SELECT value FROM shot_hex_meta WHERE name = 'min_fga'")
    return int(cur.fetchone()[0])


def _seasons(cur, player_id):
    cur.execute("SELECT season FROM player_shot_hex WHERE player_id = %s ORDER BY season", (player_id,))
    return [r[0] for r in cur.fetchall()]


@router.get("/shots/quality-map/options")
def quality_map_options(player: str = Query(..., min_length=2, max_length=80), player_id: int | None = None):
    with get_db() as conn:
        cur = conn.cursor()
        _require(cur)
        player_id, resolved = resolve_player(cur, player, player_id)
        seasons = _seasons(cur, int(player_id))
        min_fga = _min_fga(cur)
    if not seasons:
        raise HTTPException(status_code=404, detail=f"{resolved} has no season with {min_fga}+ regular-season shots on file, "
                                                    "so there is no map for them.")
    return {"player_id": int(player_id), "player_name": resolved, "min_fga": min_fga,
            "seasons": [{"season": s, "label": label(s)} for s in seasons], "_source": _source()}


@router.get("/shots/quality-map")
def quality_map(player: str = Query(..., min_length=2, max_length=80), season: int | None = None,
                player_id: int | None = None):
    # player_id (the Workbench) picks the player by id instead of by name.
    with get_db() as conn:
        cur = conn.cursor()
        _require(cur)
        player_id, resolved = resolve_player(cur, player, player_id)
        player_id = int(player_id)
        seasons = _seasons(cur, player_id)
        min_fga = _min_fga(cur)
        if not seasons:
            raise HTTPException(status_code=404, detail=f"{resolved} has no season with {min_fga}+ regular-season shots on "
                                                        "file, so there is no map for them.")
        if season is None:
            season = seasons[-1]
        if season not in seasons:
            raise HTTPException(status_code=404, detail=f"{resolved} has no map for {label(season)}: his seasons with "
                                                        f"{min_fga}+ shots are {', '.join(label(s) for s in seasons)}.")
        cur.execute("""SELECT cells, fga, fgm, xm, off_fga, off_fgm, off_xm FROM player_shot_hex
                       WHERE player_id = %s AND season = %s""", (player_id, season))
        cells, fga, fgm, xm, off_fga, off_fgm, off_xm = cur.fetchone()
        cur.execute("SELECT cell, fga, fgm, xm FROM shot_hex_league WHERE season = %s", (season,))
        league = {c: (a, m, x) for c, a, m, x in cur.fetchall()}
        cur.execute("SELECT team_abbreviation FROM player_shot_making WHERE player_id = %s AND season = %s",
                    (player_id, season))
        team = (cur.fetchone() or [None])[0]
    cx, cy = H.center(cells)
    out = []
    for i, c in enumerate(cells):
        la, lm, lx = league.get(c, (0, 0, 0.0))
        out.append({
            "cell": c, "x": round(float(cx[i]) / 10, 2), "y": round(float(cy[i]) / 10, 2),
            "fga": fga[i], "fgm": fgm[i], "xm": round(float(xm[i]), 3),
            "league_fga": la, "league_fg_pct": round(lm / la, 4) if la >= LEAGUE_MIN_FGA else None,
        })
    n, made, exp = sum(fga), sum(fgm), float(sum(xm))
    l_fga = sum(a for a, _, _ in league.values())
    l_fgm = sum(m for _, m, _ in league.values())
    return {
        "player_id": player_id, "player_name": resolved, "team": team, "season": season, "season_label": label(season),
        "seasons": [{"season": s, "label": label(s)} for s in seasons], "min_fga": min_fga,
        "cell_radius_ft": round(H.SIZE / 10, 2), "x_range_ft": [-H.X_MAX / 10, H.X_MAX / 10],
        "y_range_ft": [H.Y_MIN / 10, H.Y_MAX / 10],
        "cells": out,
        "totals": {"fga": n, "fgm": made, "fg_pct": round(made / n, 4) if n else None,
                   "expected_fg_pct": round(exp / n, 4) if n else None, "cells": len(out)},
        "league": {"fga": l_fga, "fg_pct": round(l_fgm / l_fga, 4) if l_fga else None, "min_cell_fga": LEAGUE_MIN_FGA},
        "off_map": {"fga": off_fga, "fgm": off_fgm,
                    "reason": ("Shots beyond half court and, before 2010-11, shots the NBA gave no location (about a "
                               "quarter of that era's shots, nearly all at the rim) are in no cell."
                               if season <= H.LAST_UNLOCATED_SEASON else
                               "Shots beyond half court are in no cell.")},
        "not_on_file": NOT_ON_FILE,
        "_source": _source(),
    }
