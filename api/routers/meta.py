
from fastapi import APIRouter

from source_badge import make_source

from impact_core import (
    TEAM_META,
    fetch_balldontlie_standings,
    fetch_nba_api_player_leaders,
    fetch_nba_api_standings,
    fetch_nba_api_team_stats,
    fetch_nba_cdn_standings,
    get_current_nba_season,
    get_db,
    get_latest_season,
    get_team_badges,
)

router = APIRouter()


@router.get("/meta/site-stats")
def get_site_stats():
    """
    Real, live-counted headline numbers for the landing page (real season
    span, real player-season count, real college player-season count) —
    computed directly from the same tables every other endpoint reads, not
    hardcoded, so they never drift from what's actually in the database.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT MIN(season), MAX(season), COUNT(*) FROM player_season_stats;")
        season_min, season_max, n_player_seasons = cursor.fetchone()

        cursor.execute("SELECT to_regclass('public.college_player_season_stats');")
        n_college_seasons = 0
        if cursor.fetchone()[0] is not None:
            cursor.execute("SELECT COUNT(*) FROM college_player_season_stats;")
            n_college_seasons = cursor.fetchone()[0]

    return {
        "season_min": season_min,
        "season_max": season_max,
        "n_seasons": (season_max - season_min + 1) if season_min and season_max else 0,
        "n_player_seasons": n_player_seasons,
        "n_college_seasons": n_college_seasons,
        "_source": make_source(
            ["player_season_stats", "college_player_season_stats"],
            "Postgres COUNT/MIN/MAX over this project's own real tables",
        ),
    }


@router.get("/meta/current")
def get_current_meta():
    """
    Current-season standings + team comparison stats from local DB.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        db_latest_season = get_latest_season(cursor)
        current_live_season = get_current_nba_season()
        season = max(db_latest_season, current_live_season)
        badges = get_team_badges()
        nba_api_standings = fetch_nba_api_standings(season)
        cdn_standings = fetch_nba_cdn_standings()
        external_standings = fetch_balldontlie_standings(season)
        live_team_stats = fetch_nba_api_team_stats(season)

        cursor.execute(
            """
            SELECT player_name, pts
            FROM player_season_stats
            WHERE season = %s AND pts IS NOT NULL
            ORDER BY pts DESC
            LIMIT 1;
            """,
            (db_latest_season,),
        )
        top_scorer_row = cursor.fetchone()

        cursor.execute(
            """
            SELECT team_abbreviation, MAX(w_pct) AS w_pct
            FROM player_season_stats
            WHERE season = %s AND team_abbreviation IS NOT NULL AND w_pct IS NOT NULL
            GROUP BY team_abbreviation;
            """,
            (db_latest_season,),
        )
        standing_rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT
                team_abbreviation,
                SUM(pts * gp) / NULLIF(MAX(gp), 0) AS ppg,
                SUM(reb * gp) / NULLIF(MAX(gp), 0) AS rpg,
                SUM(ast * gp) / NULLIF(MAX(gp), 0) AS apg,
                SUM(stl * gp) / NULLIF(MAX(gp), 0) AS spg,
                SUM(blk * gp) / NULLIF(MAX(gp), 0) AS bpg,
                SUM(fg_pct * gp) / NULLIF(SUM(gp), 0) AS fg_pct,
                SUM(fg3_pct * gp) / NULLIF(SUM(gp), 0) AS fg3_pct,
                SUM(ft_pct * gp) / NULLIF(SUM(gp), 0) AS ft_pct
            FROM player_season_stats
            WHERE season = %s
              AND team_abbreviation IS NOT NULL
              AND team_abbreviation <> 'TOT'
              AND gp IS NOT NULL
              AND gp > 0
            GROUP BY team_abbreviation;
            """,
            (db_latest_season,),
        )
        team_rows = cursor.fetchall()

    standings = []
    for abbr, w_pct in standing_rows:
        if abbr not in TEAM_META or w_pct is None:
            continue
        wins = int(round(float(w_pct) * 82))
        losses = max(0, 82 - wins)
        standings.append(
            {
                "abbr": abbr,
                "team": TEAM_META[abbr]["name"],
                "conference": TEAM_META[abbr]["conference"],
                "w": wins,
                "l": losses,
                "w_pct": float(w_pct),
            }
        )

    east = sorted([s for s in standings if s["conference"] == "eastern"], key=lambda x: x["w_pct"], reverse=True)
    west = sorted([s for s in standings if s["conference"] == "western"], key=lambda x: x["w_pct"], reverse=True)

    def decorate_with_rank_and_gb(rows):
        if not rows:
            return []
        leader_w, leader_l = rows[0]["w"], rows[0]["l"]
        out = []
        for i, row in enumerate(rows, start=1):
            gb = ((leader_w - row["w"]) + (row["l"] - leader_l)) / 2
            out.append(
                {
                    "rank": i,
                    "abbr": row["abbr"],
                    "team": row["team"],
                    "w": row["w"],
                    "l": row["l"],
                    "pct": f".{int(round(row['w_pct'] * 1000)):03d}",
                    "gb": "-" if i == 1 else f"{gb:.1f}".rstrip("0").rstrip("."),
                    "last10": "-",
                    "streak": "-",
                    "logo": badges.get(row["abbr"]),
                }
            )
        return out

    db_team_stats = {}
    for row in team_rows:
        abbr = row[0]
        if abbr not in TEAM_META:
            continue

        fg_pct = float(row[6]) if row[6] is not None else 0.0
        fg3_pct = float(row[7]) if row[7] is not None else 0.0
        ft_pct = float(row[8]) if row[8] is not None else 0.0

        # Normalize to percent style expected by frontend (e.g. 48.1).
        if fg_pct <= 1:
            fg_pct *= 100
        if fg3_pct <= 1:
            fg3_pct *= 100
        if ft_pct <= 1:
            ft_pct *= 100

        db_team_stats[abbr] = {
            "name": TEAM_META[abbr]["name"],
            "abbr": abbr,
            "ppg": round(float(row[1] or 0), 1),
            "rpg": round(float(row[2] or 0), 1),
            "apg": round(float(row[3] or 0), 1),
            "spg": round(float(row[4] or 0), 1),
            "bpg": round(float(row[5] or 0), 1),
            "fgPct": round(fg_pct, 1),
            "threePct": round(fg3_pct, 1),
            "ftPct": round(ft_pct, 1),
            "logo": badges.get(abbr),
        }

    if live_team_stats:
        for abbr, item in live_team_stats.items():
            item["logo"] = badges.get(abbr)

    if nba_api_standings:
        for conf in ("eastern", "western"):
            for item in nba_api_standings.get(conf, []):
                item["logo"] = badges.get(item["abbr"])
    elif cdn_standings:
        for conf in ("eastern", "western"):
            for item in cdn_standings.get(conf, []):
                item["logo"] = badges.get(item["abbr"])
    elif external_standings:
        for conf in ("eastern", "western"):
            for item in external_standings.get(conf, []):
                item["logo"] = badges.get(item["abbr"])

    live_pts_leaders = fetch_nba_api_player_leaders("pts", season, 1)
    live_top_scorer = None
    if live_pts_leaders and live_pts_leaders.get("results"):
        p0 = live_pts_leaders["results"][0]
        live_top_scorer = {
            "player_name": p0["player_name"],
            "ppg": p0["value"],
        }

    return {
        "season": season,
        "standings_source": (
            "nba_api" if nba_api_standings
            else ("nba_cdn" if cdn_standings else ("balldontlie" if external_standings else "local_db"))
        ),
        "standings": nba_api_standings or cdn_standings or external_standings or {
            "eastern": decorate_with_rank_and_gb(east),
            "western": decorate_with_rank_and_gb(west),
        },
        "team_stats": live_team_stats or db_team_stats,
        "top_scorer": live_top_scorer or (
            {
                "player_name": top_scorer_row[0],
                "ppg": round(float(top_scorer_row[1]), 1),
            }
            if top_scorer_row
            else None
        ),
    }
