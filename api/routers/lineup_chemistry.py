"""Lineup Chemistry: the best and worst real five-man lineups of a season.

    GET /lineups/chemistry?season=&min_minutes=&top_n=&order=best|worst

Stored data only (stats.nba.com has been unreachable from this machine since
2026-09-26, and stored data is reproducible). Two sources, chosen by season
(api/lineups_lib.py): from 2020-21 on, every five-on-five stint rebuilt from
ESPN play-by-play (`lineup_seasons`, scripts/build_lineup_stints.py, every
minute of every reconciled game); before that, `lineup_stats` (stats.nba.com's
2,000 most-used lineups a season). The response names the source.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from lineups_lib import (STINT_UPSTREAM, STORED_UPSTREAM, SOURCE_LABEL, lineup_sources, season_label,
                         stint_seasons)
from source_badge import make_source

router = APIRouter()

COLS = ["team_abbreviation", "player_ids", "gp", "min", "poss", "off_rating", "def_rating", "net_rating"]


@router.get("/lineups/chemistry")
def get_lineup_chemistry(season: Optional[int] = None, min_minutes: float = 40, top_n: int = 15, order: str = "best"):
    top_n = max(1, min(top_n, 50))
    order = order if order in ("best", "worst") else "best"
    min_minutes = max(0.0, min(float(min_minutes), 3000.0))

    sources = lineup_sources()
    seasons = list(sources)
    if not seasons:
        raise HTTPException(status_code=503, detail="No stored lineups — run scripts/build_lineup_stints.py "
                                                    "or scripts/fetch_spacing_data.py.")
    season = season or seasons[-1]
    if season not in sources:
        raise HTTPException(status_code=404, detail=f"No stored lineups for {season}; "
                                                    f"stored seasons are {seasons[0]}-{seasons[-1]}.")
    source = sources[season]

    with get_db() as conn:
        cur = conn.cursor()
        if source == "stints":
            cur.execute("""SELECT team_abbreviation, player_ids, games, minutes, poss, off_rating, def_rating, net_rating
                           FROM lineup_seasons WHERE season = %s""", (season,))
        else:
            cur.execute("""SELECT team_abbreviation, player_ids, gp, minutes, poss, off_rating, def_rating, net_rating
                           FROM lineup_stats WHERE season = %s""", (season,))
        lineups = [dict(zip(COLS, r)) for r in cur.fetchall()]
        qualified = [l for l in lineups if float(l["min"] or 0) >= min_minutes and l["net_rating"] is not None]
        qualified.sort(key=lambda l: float(l["net_rating"]), reverse=(order == "best"))
        top = qualified[:top_n]
        all_ids = {int(pid) for l in top for pid in l["player_ids"]}
        name_map = {}
        if all_ids:
            cur.execute(
                """SELECT DISTINCT ON (player_id) player_id, player_name
                   FROM player_season_stats WHERE player_id = ANY(%s)
                   ORDER BY player_id, season DESC;""",
                (list(all_ids),),
            )
            name_map = {r[0]: r[1] for r in cur.fetchall()}

    results = []
    for i, l in enumerate(top):
        results.append({
            "rank": i + 1,
            "players": [{"player_id": int(pid), "player_name": name_map.get(int(pid), f"#{pid}")}
                        for pid in sorted(l["player_ids"])],
            "team_abbreviation": l["team_abbreviation"],
            "gp": int(l["gp"]) if l["gp"] is not None else None,
            "min": round(float(l["min"]), 1),
            "poss": int(round(float(l["poss"]))) if l["poss"] is not None else None,
            "off_rating": round(float(l["off_rating"]), 1),
            "def_rating": round(float(l["def_rating"]), 1),
            "net_rating": round(float(l["net_rating"]), 1),
        })

    check = stint_seasons().get(season) if source == "stints" else None
    if source == "stints":
        methodology = (
            f"Every five-man lineup that shared the floor in {season_label(season)}, from every stint of every "
            f"regular-season game rebuilt from ESPN play-by-play ({check['tracked_games']} of {check['games']} games "
            f"reconcile with the real final score, game length and team totals and are used; the rest are excluded, "
            f"not hidden). Ratings are points per 100 possessions (FGA + 0.44 FTA - OREB + TOV, averaged over both "
            f"sides, so about 3 points under NBA.com's scale). Only lineups with at least {min_minutes:.0f} shared "
            f"minutes are ranked ({len(qualified)} of {len(lineups)} lineups qualify): a few shared minutes give a "
            f"real but extremely noisy net rating, so that noise is filtered out and disclosed here rather than hidden."
        )
    else:
        methodology = (
            f"Real five-man lineups that shared the floor in {season_label(season)}, from the {len(lineups):,} "
            f"most-used lineups stats.nba.com's lineup endpoint returns for a season (its cap; shorter-used units "
            f"aren't stored). Only lineups with at least {min_minutes:.0f} shared minutes are ranked "
            f"({len(qualified)} of {len(lineups)} qualify): a few shared minutes give a real but extremely noisy net "
            f"rating, so that noise is filtered out and disclosed here rather than hidden."
        )
    return {
        "season": season,
        "seasons_available": seasons,
        "sources": {str(s): src for s, src in sources.items()},
        "source": source,
        "source_label": SOURCE_LABEL[source],
        "season_check": check,
        "min_minutes": min_minutes,
        "order": order,
        "lineups_qualified": len(qualified),
        "lineups_total": len(lineups),
        "methodology": methodology,
        "results": results,
        "_source": make_source(
            (["lineup_seasons", "lineup_stint_seasons"] if source == "stints" else ["lineup_stats"]) + ["player_season_stats"],
            STINT_UPSTREAM if source == "stints" else STORED_UPSTREAM),
    }
