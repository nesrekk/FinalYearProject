from typing import Optional
from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    MATCHUP_MIN_POSS,
    MATCHUP_RELIABLE_POSS,
    find_player,
    get_db,
    get_latest_season,
)

router = APIRouter()


@router.get("/matchups/player/{player_name}")
def get_player_matchups(player_name: str, role: str = "scorer", season: Optional[int] = None, top_n: int = 15):
    role = role.lower()
    if role not in ("scorer", "defender"):
        raise HTTPException(status_code=400, detail="role must be 'scorer' or 'defender'.")
    top_n = max(1, min(top_n, 50))

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT to_regclass('public.player_matchups');")
        if cursor.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="No matchup data yet — run scripts/fetch_matchups.py first.")

        player_id, resolved_name = find_player(cursor, player_name)
        resolved_season = season or get_latest_season(cursor)

        if role == "scorer":
            # Real defenders who have guarded this player while this player was on offense.
            cursor.execute(
                """SELECT def_player_id, def_player_name, gp, matchup_min, partial_poss,
                          player_pts, matchup_fgm, matchup_fga, matchup_fg_pct
                   FROM player_matchups
                   WHERE off_player_id = %s AND season = %s AND partial_poss >= %s;""",
                (player_id, resolved_season, MATCHUP_MIN_POSS),
            )
        else:
            # Real offensive players this player has guarded.
            cursor.execute(
                """SELECT off_player_id, off_player_name, gp, matchup_min, partial_poss,
                          player_pts, matchup_fgm, matchup_fga, matchup_fg_pct
                   FROM player_matchups
                   WHERE def_player_id = %s AND season = %s AND partial_poss >= %s;""",
                (player_id, resolved_season, MATCHUP_MIN_POSS),
            )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No real matchup data for {resolved_name} in season {resolved_season} "
                   "(too few tracked possessions, or before real matchup tracking's full coverage began in 2017-18).",
        )

    results = [
        {
            "player_id": r[0], "player_name": r[1], "gp": r[2],
            "matchup_min": round(r[3], 1) if r[3] is not None else None,
            "partial_poss": round(r[4], 1), "player_pts": r[5], "matchup_fgm": r[6], "matchup_fga": r[7],
            "matchup_fg_pct": round(r[8], 3) if r[8] is not None else None,
            "reliable": r[4] >= MATCHUP_RELIABLE_POSS,
        }
        for r in rows
    ]

    toughest = sorted(results, key=lambda x: (x["matchup_fg_pct"] is None, x["matchup_fg_pct"]))[:top_n]
    easiest = sorted(results, key=lambda x: (x["matchup_fg_pct"] is None, -(x["matchup_fg_pct"] or 0)))[:top_n]

    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "season": resolved_season,
        "role": role,
        "toughest": toughest,
        "easiest": easiest,
        "reliable_poss_threshold": MATCHUP_RELIABLE_POSS,
        "methodology": (
            ("Real defenders who have actually guarded " if role == "scorer" else "Real offensive players actually guarded by ")
            + f"{resolved_name} this season (NBA's own real matchup tracking), ranked by real FG% allowed in "
            "those specific real matchups — 'toughest' is where the offensive player shot worst, 'easiest' is "
            "where they shot best. Pairs below "
            f"{int(MATCHUP_RELIABLE_POSS)} real partial possessions matched up are marked unreliable — small "
            "samples produce noisy FG% (a single defended shot is either 0% or 100%)."
        ),
        "_source": make_source(["player_matchups"], "nba_api (stats.nba.com, LeagueSeasonMatchups)"),
    }
