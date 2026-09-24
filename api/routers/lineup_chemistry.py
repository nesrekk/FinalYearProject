from source_badge import make_source

from fastapi import APIRouter

from impact_core import (
    _fetch_lineup_stats_season,
    check_season_exists,
    get_db,
    get_latest_season,
)

router = APIRouter()


@router.get("/lineups/chemistry")
def get_lineup_chemistry(season: int = None, min_minutes: float = 40, top_n: int = 15, order: str = "best"):
    top_n = max(1, min(top_n, 50))
    order = order if order in ("best", "worst") else "best"

    with get_db() as conn:
        cursor = conn.cursor()
        resolved_season = season or get_latest_season(cursor)
        check_season_exists(cursor, resolved_season)

        lineups = _fetch_lineup_stats_season(resolved_season)
        qualified = [l for l in lineups if l["min"] >= min_minutes]

        all_ids = {pid for l in qualified for pid in l["player_ids"]}
        name_map = {}
        if all_ids:
            cursor.execute(
                """SELECT DISTINCT ON (player_id) player_id, player_name
                   FROM player_season_stats
                   WHERE player_id = ANY(%s)
                   ORDER BY player_id, season DESC;""",
                (list(all_ids),),
            )
            name_map = {r[0]: r[1] for r in cursor.fetchall()}

    qualified.sort(key=lambda l: l["net_rating"], reverse=(order == "best"))
    top = qualified[:top_n]

    results = []
    for i, l in enumerate(top):
        players = [
            {"player_id": pid, "player_name": name_map.get(pid, abbr)}
            for pid, abbr in zip(l["player_ids"], l["abbr_names"])
        ]
        results.append({
            "rank": i + 1,
            "players": players,
            "team_abbreviation": l["team_abbreviation"],
            "gp": l["gp"],
            "min": l["min"],
            "off_rating": l["off_rating"],
            "def_rating": l["def_rating"],
            "net_rating": l["net_rating"],
            "ast_pct": l["ast_pct"],
            "ts_pct": l["ts_pct"],
            "pace": l["pace"],
        })

    return {
        "season": resolved_season,
        "min_minutes": min_minutes,
        "order": order,
        "lineups_qualified": len(qualified),
        "lineups_total": len(lineups),
        "methodology": (
            f"Real 5-man lineup combinations that have actually shared the floor this season, fetched live from "
            f"the NBA's own real lineup data (not a simulation of hypothetical lineups). Only lineups with at "
            f"least {min_minutes:.0f} real shared minutes are shown ({len(qualified)} of {len(lineups)} total "
            "combinations qualify) — lineups with only a few shared minutes produce real but extremely noisy "
            "net ratings, so that noise is filtered out and disclosed here rather than hidden."
        ),
        "results": results,
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com, LeagueDashLineups, live)", live=True),
    }
