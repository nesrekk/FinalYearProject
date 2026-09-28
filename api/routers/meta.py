from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

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
    Current-season standings + team comparison stats from local DB, layered
    with live data from stats.nba.com when it's available and fast.

    The four live lookups below (standings x2, external standings, team
    stats) are each independently cached with a short TTL and a short
    request timeout (see the comment above `_CACHE` in impact_core.py) and
    are fired concurrently rather than one after another — previously they
    ran in series and a cold cache could take 100+ real seconds (each of the
    four live calls paying its own worst-case timeout back to back). Every
    one of them still falls back to real local-DB data below if it fails or
    times out; nothing here is ever invented.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        db_latest_season = get_latest_season(cursor)
        current_live_season = get_current_nba_season()
        season = max(db_latest_season, current_live_season)
        badges = get_team_badges()

        with ThreadPoolExecutor(max_workers=5) as pool:
            standings_future = pool.submit(fetch_nba_api_standings, season)
            cdn_future = pool.submit(fetch_nba_cdn_standings)
            external_future = pool.submit(fetch_balldontlie_standings, season)
            team_stats_future = pool.submit(fetch_nba_api_team_stats, season)
            leaders_future = pool.submit(fetch_nba_api_player_leaders, "pts", season, 1)

            nba_api_standings = standings_future.result()
            cdn_standings = cdn_future.result()
            external_standings = external_future.result()
            live_team_stats = team_stats_future.result()
            live_pts_leaders = leaders_future.result()

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


def _season_label(season):
    """End-year int -> 'YYYY-YY' label, e.g. 2026 -> '2025-26'."""
    if season is None:
        return None
    return f"{season - 1}-{str(season)[-2:]}"


def _fmt_season_int(v):
    return _season_label(v) if v is not None else None


def _fmt_year_int(v):
    return str(v) if v is not None else None


_RANGE_FORMATTERS = {
    "season_int": _fmt_season_int,
    "season_text": lambda v: v,
    "year_int": _fmt_year_int,
    None: lambda v: v,
}

# Hand-maintained coverage map: one entry per table that backs a real
# feature. Row counts and first/last-season are computed live below (never
# hand-typed, so they can't drift); `source` and `gap` are hand-written and
# reuse the same wording as README "Known real gaps" and the Methodology
# page rather than inventing new phrasing. `used_by` are page ids from
# navConfig.js / App.jsx PAGES so the frontend can link each row to the
# feature(s) that read it.
COVERAGE_MAP = [
    {
        "table": "player_season_stats", "label": "Player season stats", "group": "Core",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_season_stats", "range_fmt": "season_int",
        "source": "Basketball-Reference (pre-2010) / NBA.com via a Kaggle historical export, extended each season with nba_api loads.",
        "gap": "Two age conventions: 2009-10+ uses NBA.com's age, pre-2010 uses Basketball-Reference's Feb-1 age (about 45% of players a year apart). Models trained on it as-is.",
        "used_by": ["players", "compare", "leaders", "builder", "regression", "breakouts", "stability", "statline", "era"],
    },
    {
        "table": "player_shots", "label": "Shot chart locations", "group": "Shooting",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_shots", "range_fmt": "season_text",
        "source": "nba_api shot-chart endpoint, bulk-loaded per season.",
        "gap": "shot_zone_basic is NULL on bulk-loaded rows (zones come from api/shots_lib.classify_zone()); mixes regular season/playoffs/play-in — regular season only is game_id LIKE '002%'. No defender distance or shot type (catch-and-shoot vs. pull-up) per shot.",
        "used_by": ["shotcharts"],
    },
    {
        "table": "player_game_lines", "label": "Per player-game lines", "group": "Games",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_game_lines", "range_fmt": "season_int",
        "source": "Rebuilt from ESPN play-by-play (scripts/build_player_game_lines.py); minutes rebuilt from substitutions.",
        "gap": "Regular season only, 2020-21 on (no earlier seasons, no playoffs). game_id is ESPN's (espn_...), not the NBA 002... ids used elsewhere.",
        "used_by": ["stability"],
    },
    {
        "table": "team_game_fatigue", "label": "Team game log (rest/travel)", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM team_game_fatigue", "range_fmt": "season_int",
        "source": "Built from the NBA schedule; one row per team-game with rest days, back-to-backs and travel.",
        "gap": "Margin only — no points for/against stored per team-game — and plus_minus is stats.nba.com's summed player +/- ÷ 5, not the final margin: it differs from the real final score in 160 of 20,348 games (use game_scores for margins). Records are right.",
        "used_by": [],
    },
    {
        "table": "team_seasons", "label": "Team seasons (records, ratings)", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM team_seasons", "range_fmt": "season_int",
        "source": "Basketball-Reference team summaries via the local Kaggle export (scripts/build_team_seasons.py), with franchise and per-season codes from api/teams_lib.py.",
        "gap": "Ratings, pace and four factors are Basketball-Reference's; the earliest BAA seasons lack some of them. Two 1946-48 team-seasons have no player rows.",
        "used_by": [],
    },
    {
        "table": "team_zone_mix", "label": "Team shot mix", "group": "Shooting",
        "range_sql": "SELECT MIN(LEFT(season, 4)::int + 1), MAX(LEFT(season, 4)::int + 1) FROM team_zone_mix", "range_fmt": "season_int",
        "source": "Every located regular-season shot in player_shots, placed on a team game by game (scripts/build_team_zone_mix.py).",
        "gap": "99.98% of shots placed; every team-season holds 97-100% of Basketball-Reference's FGA. No shot type or defender distance, as in player_shots.",
        "used_by": [],
    },
    {
        "table": "game_scores", "label": "Final scores", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM game_scores", "range_fmt": "season_int",
        "source": "ESPN's scoreboard, matched to NBA game ids by date and teams (scripts/fetch_game_scores.py).",
        "gap": "Regular season only (the NBA Cup final, which doesn't count in the standings, is left out). Season points for/against equal Basketball-Reference's for all 510 team-seasons.",
        "used_by": [],
    },
    {
        "table": "team_luck_schedule", "label": "Luck & schedule strength", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM team_luck_schedule", "range_fmt": "season_int",
        "source": "Computed from game_scores (scripts/build_luck_schedule.py): expected wins, luck, close-game records, SRS/SOS.",
        "gap": "SRS weighs every game the same (blowouts, games stars sat) and gives a team one number per season.",
        "used_by": [],
    },
    {
        "table": "game_team_box", "label": "Team box scores", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM game_team_box", "range_fmt": "season_int",
        "source": "nba_api team box score, bulk-loaded per season.",
        "gap": "2020-21 on only. No points column (FGA/FTA/OREB/TOV/possession estimate).",
        "used_by": [],
    },
    {
        "table": "lineup_stats", "label": "Lineup on-court stats", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM lineup_stats", "range_fmt": "season_int",
        "source": "nba_api lineup endpoint, top lineups by minutes per team-season.",
        "gap": "Only the top 2,000 lineups a season are stored — 31-89% of a team's minutes depending on rotation depth, not full coverage.",
        "used_by": [],
    },
    {
        "table": "player_wpa_totals", "label": "Win-probability-added totals", "group": "Models",
        "range_sql": None, "range_fmt": None,
        "source": "compute_wpa.py over deduplicated play-by-play (scripts/wpa_lib.PBP_DEDUP_WHERE).",
        "gap": "Clutch plays carry 3.7x the leverage of non-clutch plays — never compare raw clutch and non-clutch WPA per play. Most players are statistically indistinguishable from zero on the clutch split.",
        "used_by": [],
    },
    {
        "table": "player_gravity", "label": "Gravity Index / Spacing", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_gravity", "range_fmt": "season_int",
        "source": "scripts/build_gravity_index.py, from shot-location and lineup data.",
        "gap": None,
        "used_by": ["rolefinder"],
    },
    {
        "table": "defender_dad", "label": "Defensive Adjusted Deflections (DAD)", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM defender_dad", "range_fmt": "season_int",
        "source": "scripts/build_dad_index.py.",
        "gap": "Pair Synergy still reads dbpm_repro (not the published BPM) until it's retrained.",
        "used_by": ["rolefinder"],
    },
    {
        "table": "scouting_splits", "label": "Scouting report shot-diet splits", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM scouting_splits", "range_fmt": "season_int",
        "source": "scripts/build_scouting_report.py.",
        "gap": "Only ~180 players a season have enough tracked possessions — treated as fit flags, not scored, in Role Player Finder.",
        "used_by": ["rolefinder"],
    },
    {
        "table": "contract_value", "label": "Contract Value (salary vs. production)", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM contract_value", "range_fmt": "season_int",
        "source": "scripts/build_contract_value.py over third-party salary CSVs (see README for where to get them) joined to impact score.",
        "gap": "Non-contiguous seasons only (salary data isn't loaded for every year) — no 2021-24, no 2026. The salary filter on Role Player Finder only works for seasons this table actually has.",
        "used_by": ["tradeimpact", "rolefinder"],
    },
    {
        "table": "player_roles", "label": "Player Roles (10 archetypes)", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_roles", "range_fmt": "season_int",
        "source": "scripts/build_player_roles.py.",
        "gap": "Distinct from player_clusters (6 archetypes) below — Pair Synergy and Trivia hard-code the 6-cluster names and haven't moved to this table.",
        "used_by": ["rolefinder"],
    },
    {
        "table": "player_clusters", "label": "Player Clusters (6 archetypes)", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_clusters", "range_fmt": "season_int",
        "source": "Older clustering model, kept in place because Pair Synergy and Trivia hard-code its archetype names.",
        "gap": None,
        "used_by": [],
    },
    {
        "table": "draft_history", "label": "Draft history", "group": "Draft",
        "range_sql": "SELECT MIN(draft_year), MAX(draft_year) FROM draft_history", "range_fmt": "year_int",
        "source": "Basketball-Reference (scripts/load_draft_history_bref.py) — stats.nba.com has been unreachable from this machine since 2026-09-26.",
        "gap": "105 of 3,747 drafted players who played aren't matched to an NBA id: they keep a placeholder id, have no photo, but still have Win Shares.",
        "used_by": ["draft"],
    },
    {
        "table": "draft_pick_outcomes", "label": "Draft pick outcomes (Win Shares)", "group": "Draft",
        "range_sql": "SELECT MIN(draft_year), MAX(draft_year) FROM draft_pick_outcomes", "range_fmt": "year_int",
        "source": "Basketball-Reference Win Shares, first five seasons, classes 1980-2021 (scripts/load_draft_history_bref.py).",
        "gap": None,
        "used_by": ["draft"],
    },
    {
        "table": "college_team_seasons", "label": "College team ratings (Torvik)", "group": "College",
        "range_sql": "SELECT MIN(season), MAX(season) FROM college_team_seasons", "range_fmt": "season_int",
        "source": "Owner's Kaggle Torvik team-ratings export (nba_data/college_teams/, gitignored).",
        "gap": "These ratings include the tournament — never train the March Madness model on this table.",
        "used_by": [],
    },
    {
        "table": "cbb_games", "label": "College basketball game results", "group": "College",
        "range_sql": "SELECT MIN(season), MAX(season) FROM cbb_games", "range_fmt": "season_int",
        "source": "Every D1 game from CollegeBasketballData.com.",
        "gap": "A few known data quirks handled in code and disclosed on the March Madness page (2017 First Four, 2021 Oregon-VCU no-contest, an LIU name split) — see README Known real gaps.",
        "used_by": [],
    },
    {
        "table": "league_season_averages", "label": "League season averages (pace)", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM league_season_averages", "range_fmt": "season_int",
        "source": "scripts/build_league_averages.py, from the Kaggle historical export.",
        "gap": "Pace before 1973-74 is an estimate (1949-50 estimated as the anchor).",
        "used_by": ["era"],
    },
    {
        "table": "player_awards", "label": "Player awards history", "group": "Core",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_awards", "range_fmt": "season_int",
        "source": "scripts/build_player_profile_data.py, from Basketball-Reference.",
        "gap": None,
        "used_by": ["player"],
    },
    {
        "table": "player_team_stints", "label": "Traded-season team stints", "group": "Core",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_team_stints", "range_fmt": "season_int",
        "source": "scripts/build_player_profile_data.py.",
        "gap": None,
        "used_by": ["player"],
    },
    {
        "table": "greats", "label": "Greats of the Game", "group": "Players",
        "range_sql": "SELECT MIN(first_season), MAX(last_season) FROM greats", "range_fmt": "season_int",
        "source": "scripts/build_greats.py: 75th Anniversary Team + today's stars by a stated rule, facts from Basketball-Reference data.",
        "gap": "Trivia only ships when proven by league-wide records in the data or checked against a linked Wikipedia article.",
        "used_by": ["greats"],
    },
    {
        "table": "stat_stability", "label": "Stat reliability (stabilization points)", "group": "Models",
        "range_sql": "SELECT MIN(season_from), MAX(season_to) FROM stat_stability", "range_fmt": "season_int",
        "source": "scripts/build_stat_stability.py, split-half reliability over player_game_lines and player_shots.",
        "gap": "Per-game and play-by-play estimates only cover the player_game_lines era (2020-21 on); applying them to other eras assumes a similar spread of players.",
        "used_by": ["stability", "builder", "breakouts"],
    },
    {
        "table": "referee_crew_tendencies", "label": "Referee crew tendencies", "group": "Games",
        "range_sql": None, "range_fmt": None,
        "source": "scripts/build_referee_tendencies.py, grouped by the exact real 3-official crew per game.",
        "gap": "Only 514 of 5,373 tracked crews ever worked together more than once — most rows sit at the 10-game small-sample floor. This is a real, disclosed null result, not a bug.",
        "used_by": [],
    },
    {
        "table": "player_first_season", "label": "Player first NBA/BAA season", "group": "Models",
        "range_sql": "SELECT MIN(first_season), MAX(first_season) FROM player_first_season", "range_fmt": "season_int",
        "source": "scripts/build_first_nba_season.py — earlier of Basketball-Reference's first season and the first season in player_season_stats. Feeds ROY rookie eligibility.",
        "gap": None,
        "used_by": [],
    },
    {
        "table": "league_zone_mix", "label": "League shot-zone mix by season", "group": "Shooting",
        "range_sql": "SELECT MIN(season), MAX(season) FROM league_zone_mix", "range_fmt": "season_text",
        "source": "scripts/build_league_zone_mix.py, from player_shots.",
        "gap": "Regular season only, same coverage as player_shots.",
        "used_by": ["shotcharts", "player"],
    },
]


@lru_cache(maxsize=1)
def _coverage():
    """
    Live row counts and first/last-season per table in COVERAGE_MAP — never
    hand-typed, so a stale gap description can't also carry a stale count.
    Cached per process (same pattern as the rest of the app's lru_cache
    endpoints): restart impact_api after a rebuild changes these tables.
    """
    rows = []
    with get_db() as conn:
        cursor = conn.cursor()
        for entry in COVERAGE_MAP:
            table = entry["table"]
            cursor.execute("SELECT to_regclass(%s);", (f"public.{table}",))
            if cursor.fetchone()[0] is None:
                rows.append({**entry, "exists": False, "n_rows": 0, "season_from": None, "season_to": None})
                continue

            cursor.execute(f"SELECT COUNT(*) FROM {table};")  # nosec: table from our own hand-written map, never user input
            n_rows = cursor.fetchone()[0]

            season_from = season_to = None
            if entry["range_sql"]:
                cursor.execute(entry["range_sql"])  # nosec: hand-written SQL in COVERAGE_MAP, never user input
                raw_from, raw_to = cursor.fetchone()
                fmt = _RANGE_FORMATTERS[entry["range_fmt"]]
                season_from, season_to = fmt(raw_from), fmt(raw_to)

            rows.append({
                "table": table,
                "label": entry["label"],
                "group": entry["group"],
                "source": entry["source"],
                "gap": entry["gap"],
                "used_by": entry["used_by"],
                "exists": True,
                "n_rows": n_rows,
                "season_from": season_from,
                "season_to": season_to,
            })
    return tuple(rows)


@router.get("/meta/coverage")
def get_data_coverage():
    """
    Data Coverage page: for every table in the hand-maintained COVERAGE_MAP,
    a live row count and season span, plus its source and known gaps in the
    same wording as README "Known real gaps" and the Methodology page.
    """
    rows = list(_coverage())
    groups = []
    for row in rows:
        if row["group"] not in groups:
            groups.append(row["group"])
    return {
        "tables": rows,
        "groups": groups,
        "_source": make_source(
            [r["table"] for r in rows],
            "Postgres COUNT/MIN/MAX over this project's own real tables, cached per process",
        ),
    }
