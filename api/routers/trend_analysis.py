from typing import Optional
from psycopg2 import pool
from source_badge import make_source

from fastapi import APIRouter, HTTPException

from impact_core import (
    TREND_PLAYER_STATS,
    resolve_player,
    get_db,
)

router = APIRouter()


@router.get("/players/history/{player_name}")
def get_player_history(player_name: str, player_id: Optional[int] = None):
    """Every season a player appears in player_season_stats, unfiltered (no
    qualified-pool minimum) — trend analysis should show the real trajectory,
    injury-shortened or rookie seasons included, not just the "clean" ones."""
    with get_db() as conn:
        cursor = conn.cursor()
        player_id, resolved_name = resolve_player(cursor, player_name, player_id)
        cursor.execute(
            f"""
            SELECT season, age, gp, {', '.join(TREND_PLAYER_STATS)}
            FROM player_season_stats
            WHERE player_id = %s
            ORDER BY season ASC;
            """,
            (player_id,),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No season data for {resolved_name}.")

    cols = ["season", "age", "gp"] + TREND_PLAYER_STATS
    return {
        "player_id": player_id,
        "player_name": resolved_name,
        "seasons": [dict(zip(cols, row)) for row in rows],
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com)"),
    }

@router.get("/teams/history/{team_abbr}")
def get_team_history(team_abbr: str):
    """
    Team-level trend: minutes-weighted roster aggregates per season — the
    same computation Trade Analyzer and the win% model use (SUM(min*stat) /
    SUM(min)), done directly in SQL here since it's one aggregate row per
    season rather than a roster to assemble in Python.
    Also includes the Four Factors (Dean Oliver's eFG%/TOV%/OREB%/FTr) —
    same minutes-weighted-roster-average methodology as net_rating/win_pct
    above (this DB has no team-level game log, so "team eFG%" here means
    "this roster's players' own eFG%, weighted by minutes played" — a
    roster-composition proxy, not an official team box score stat. FTr is
    computed as weighted-team-FTA / weighted-team-FGA rather than averaging
    individual FTr ratios directly, to avoid distorting the ratio.
    Note: franchise relocations/renames (e.g. NOH -> NOP, NJN -> BKN) show
    up as separate abbreviations, not stitched into one continuous history.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT season,
                   SUM(min * net_rating) / NULLIF(SUM(min), 0) AS net_rating,
                   SUM(min * off_rating) / NULLIF(SUM(min), 0) AS off_rating,
                   SUM(min * def_rating) / NULLIF(SUM(min), 0) AS def_rating,
                   SUM(min * ts_pct) / NULLIF(SUM(min), 0) AS ts_pct,
                   SUM(min * w_pct) / NULLIF(SUM(min), 0) AS win_pct,
                   SUM(min * efg_pct) / NULLIF(SUM(min), 0) AS efg_pct,
                   SUM(min * oreb_pct) / NULLIF(SUM(min), 0) AS oreb_pct,
                   SUM(min * tov_pct) / NULLIF(SUM(min), 0) AS tov_pct,
                   SUM(min * fta) / NULLIF(SUM(min), 0) AS weighted_fta,
                   SUM(min * fga) / NULLIF(SUM(min), 0) AS weighted_fga,
                   COUNT(*) AS n_players
            FROM player_season_stats
            WHERE team_abbreviation = %s
            GROUP BY season
            ORDER BY season ASC;
            """,
            (team_abbr.upper(),),
        )
        rows = cursor.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No data for team '{team_abbr.upper()}'.")

    raw_cols = ["season", "net_rating", "off_rating", "def_rating", "ts_pct", "win_pct",
                "efg_pct", "oreb_pct", "tov_pct", "weighted_fta", "weighted_fga", "n_players"]
    seasons = []
    for row in rows:
        entry = dict(zip(raw_cols, row))
        wfta, wfga = entry.pop("weighted_fta"), entry.pop("weighted_fga")
        entry["ftr"] = (wfta / wfga) if wfga else None
        seasons.append(entry)

    return {
        "team": team_abbr.upper(), "seasons": seasons,
        "_source": make_source(["player_season_stats"], "nba_api (stats.nba.com)"),
    }
