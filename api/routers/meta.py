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
# navConfig.js / App.jsx PAGES (an Analytics tab as 'analytics#<tab>') so
# the frontend can link each row to the feature(s) that read it.
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
        "source": "Rebuilt from ESPN play-by-play (scripts/build_player_game_lines.py); minutes rebuilt from substitutions; a missed shot is a two or a three as the NBA shot chart (player_shots) calls the same shot where it can be matched (~99%).",
        "gap": "Regular season only, 2020-21 on (no earlier seasons, no playoffs). game_id is ESPN's (espn_...), not the NBA 002... ids used elsewhere.",
        "used_by": ["stability"],
    },
    {
        "table": "team_game_fatigue", "label": "Team game log (rest/travel)", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM team_game_fatigue", "range_fmt": "season_int",
        "source": "Built from the NBA schedule; one row per team-game with rest days, back-to-backs and travel.",
        "gap": "No points for/against (those are in game_scores). Its plus_minus is stats.nba.com's summed player +/- ÷ 5, not the final margin: it differs from the real final score in 160 of 20,348 games, so no tool reads it; margins come from game_scores. Records are right.",
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
        "table": "player_shot_hex", "label": "Shot quality map (per player-season hexagon cells)", "group": "Shooting",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_shot_hex", "range_fmt": "season_int",
        "source": "scripts/build_shot_making.py: every regular-season shot binned into 2-foot hexagons of the half court (api/shot_hex.py), per qualified player-season (200+ FGA) the attempts, makes and expected makes (the cross-fitted shot-making model) per cell as arrays; shot_hex_league holds the league's attempts and makes per cell and season, shot_hex_meta the grid.",
        "gap": "Shots beyond half court (0.14%) and, through 2009-10, the ~25% of shots the NBA gave no location (stored at (0, 0), nearly all at the rim) are in no cell; each player-season keeps their counts off the map so cells + off-map equal his attempts and makes (0 of 9,026 differ). No defender distance or shot type, as in player_shots.",
        "used_by": ["shotcharts"],
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
        "table": "game_pregame_odds", "label": "Pre-game win probability of every game", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM game_pregame_odds", "range_fmt": "season_int",
        "source": "scripts/build_season_sim.py: for every game 2010-11 on, P(home wins) as of that morning from ratings (this season's SRS blended with last season's), home court and back-to-backs; held out (coefficients fitted without that season); every form's probability stored.",
        "gap": "Ratings use only games before the game, but the three logistic coefficients and the prior constants are fitted across seasons. No injuries, trades or line-ups: a team is one rating.",
        "used_by": ["simulator", "bestgames"],
    },
    {
        "table": "ledger_forecasts", "label": "Forecast Ledger: locked 2026-27 forecasts", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM ledger_forecasts", "range_fmt": "season_int",
        "source": "scripts/ledger_lock.py, run once on 2026-09-30 (before the first tip): two forecasts (as is: the Season Simulator's opening day; roster-aware: projected BPM of each ESPN roster blended with last season's SRS), per team (win totals, playoff, play-in, round and title odds from 10,000 runs) and per scheduled game (P(home wins) from opening-day ratings). The SHA-256 of the locked rows is in ledger_lock.",
        "gap": "Never rebuilt: a lock is fixed before the season. No injury model; six NBA Cup knockout games had no teams at lock time and aren't forecast; the two games a team is still owed are simulated against a league-average opponent.",
        "used_by": ["ledger"],
    },
    {
        "table": "ledger_rosters", "label": "Forecast Ledger: rosters at lock time", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM ledger_rosters", "range_fmt": "season_int",
        "source": "ESPN's team rosters read by scripts/ledger_lock.py at lock time (training camp: 18-21 players a team), matched to NBA ids by name and birth date, with each player's Marcel projection and the minutes the depth-chart rule gave him.",
        "gap": "103 of 607 players have no NBA record on file (rookies, two-way and camp players); injury status is ESPN's at lock time and not used.",
        "used_by": ["ledger"],
    },
    {
        "table": "ledger_schedule", "label": "Forecast Ledger: 2026-27 schedule at lock time", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM ledger_schedule", "range_fmt": "season_int",
        "source": "ESPN's scoreboard, one request per date of its 2026-27 calendar, read at lock time: 1,206 regular-season events, 1,200 with both teams (80 a team), with back-to-back flags from the dates.",
        "gap": "The NBA adds each team's last two games after the NBA Cup group stage; they aren't in it. Later schedule changes aren't either (the lock keeps the schedule as it was).",
        "used_by": ["ledger"],
    },
    {
        "table": "ledger_results", "label": "Forecast Ledger: 2026-27 results, read nightly", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM ledger_results", "range_fmt": "season_int",
        "source": "scripts/ledger_update.py, each night of the season: ESPN's scoreboard for every date of its 2026-27 calendar (status and final score of every regular-season event). The odds each version gave every game, logged with the time they were computed, are in ledger_game_log (rows start with opening night, 2026-10-20); scored against these results on the Live scoring tab.",
        "gap": "Only as current as the last run (the laptop must be awake; a missed day's odds are computed on the next run and labelled as recomputed after tip-off). The NBA Cup Championship doesn't count as a regular-season game and is left out; fetch_game_scores.py (game_scores) can't read 2026-27 because it matches ESPN to stats.nba.com's game list.",
        "used_by": ["ledger"],
    },
    {
        "table": "ledger_team_log", "label": "Forecast Ledger: standings and expected wins by date", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM ledger_team_log", "range_fmt": "season_int",
        "source": "scripts/ledger_update.py: per night, forecast and team, the record, the in-season rating (locked prior updated by the results before that morning) and the expected final win total from it, next to the locked 80% range.",
        "gap": "Expected wins carry no rating uncertainty (a sum of win chances), so they sit a little further from 41 than the locked simulation's means.",
        "used_by": ["ledger"],
    },
    {
        "table": "ledger_hindcast", "label": "Forecast Ledger: roster hindcast 2010-11 to 2025-26", "group": "Models",
        "range_sql": "SELECT MIN(target_season), MAX(target_season) FROM ledger_hindcast", "range_fmt": "season_int",
        "source": "scripts/ledger_lock.py: every team-season 2010-11 to 2025-26 rated from the projections made before it and the players who played for it, next to its final SRS and wins; fits the roster-aware weights (leave-one-season-out predictions stored).",
        "gap": "The hindcast rosters know who played (players who missed the season aren't on them), which flatters the roster-aware forecast.",
        "used_by": ["ledger"],
    },
    {
        "table": "season_sim_backtest", "label": "Season simulator backtest", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM season_sim_backtest", "range_fmt": "season_int",
        "source": "scripts/build_season_sim.py: every season 2010-11 on simulated 10,000 times at opening day, the halfway date and 60 games in, next to what happened (season_postseason), with the standings and the record carried forward as baselines.",
        "gap": "Honest result: by midseason the record carried forward predicts the playoff field about as well as the model; the model's edge is win totals, ranges and opening day. Tiebreaks stop at head-to-head and conference record.",
        "used_by": ["simulator"],
    },
    {
        "table": "postseason_games", "label": "Play-in and playoff games", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM postseason_games", "range_fmt": "season_int",
        "source": "ESPN's scoreboard (scripts/fetch_postseason_games.py): every play-in and playoff game 2009-10 on with scores, round and conference; playoff fields cross-checked with player_shots' postseason games and Basketball-Reference.",
        "gap": "Postseason only (regular-season scores are in game_scores); no box scores or play-by-play, just results.",
        "used_by": ["simulator"],
    },
    {
        "table": "game_team_box", "label": "Team box scores", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM game_team_box", "range_fmt": "season_int",
        "source": "nba_api team box score, bulk-loaded per season.",
        "gap": "2020-21 on only. No points column (FGA/FTA/OREB/TOV/possession estimate).",
        "used_by": [],
    },
    {
        "table": "lineup_stats", "label": "Lineup on-court stats (stored top 2,000)", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM lineup_stats", "range_fmt": "season_int",
        "source": "nba_api lineup endpoint, top lineups by minutes per team-season.",
        "gap": "Only the top 2,000 lineups a season are stored — 31-89% of a team's minutes depending on rotation depth, not full coverage. Since 2026-09-28 Pair Chemistry, Lineup Chemistry and the team page use it only before 2020-21; later seasons come from lineup_stints.",
        "used_by": [],
    },
    {
        "table": "lineup_stints", "label": "Five-man stints from play-by-play", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM lineup_stints", "range_fmt": "season_int",
        "source": "ESPN play-by-play (pbp_events) rebuilt by scripts/build_lineup_stints.py with the same lineup parser as player_game_lines; aggregated into lineup_seasons and pair_seasons.",
        "gap": "Every stint of every regular-season game 2020-21 on, reconciled per game against the real final score, game length and team totals (lineup_stint_games says which games fail). Stints where a player had no id in the play-by-play (two-way and 10-day players missing from player_season_stats) are left out and counted: 2-6% of minutes in 2020-21 to 2024-25, almost none in 2025-26.",
        "used_by": ["rotations"],
    },
    {
        "table": "possessions", "label": "Possessions from play-by-play", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM possessions", "range_fmt": "season_int",
        "source": "ESPN play-by-play cut into possessions by scripts/build_possessions.py (rules in scripts/pbp_possessions.py, on the same parser as lineup_stints); times on a clock rebuilt from the NBA shot chart, because ESPN logs made shots a median 14 s late; per-game checks in possession_games, team-season totals by start type in possession_seasons.",
        "gap": "Every regular-season game 2020-21 on; 7,220 of 7,232 games add up to the final score and the team totals, the same games as lineup_stints (possession_games says why the rest don't). Counted possessions run ~2 a team-game under the box-score estimate because team offensive rebounds continue a possession. ESPN stamps a turnover at about the time of the next play, so possessions after turnovers have no time-to-first-shot or transition flag, and about 27% of possessions have an approximate length.",
        "used_by": ["possessions", "player"],
    },
    {
        "table": "rotation_closing_games", "label": "Closing stretch of every game (score at 5:00 left in the fourth)", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM rotation_closing_games", "range_fmt": "season_int",
        "source": "scripts/build_rotations.py: the lineup_stints parser rerun on the corrected clock (pbp_event_clock) with the stint in progress at 5:00 left in the fourth cut in two; closing pieces in rotation_closing_stints.",
        "gap": "Close game = within 5 points at 5:00 left in the fourth, checked once (the NBA's own clutch stats re-check the margin at every moment). Games whose play-by-play didn't reconcile (12 of 7,232) are left out of the closing lineups; one game has only three periods in the play-by-play and no 5:00 mark.",
        "used_by": ["rotations"],
    },
    {
        "table": "player_rapm", "label": "RAPM (regularized adjusted plus-minus)", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_rapm", "range_fmt": "season_int",
        "source": "Ridge regression on the tracked five-man stints (lineup_stints) by scripts/build_rapm.py: one season, a three-season window, and a BPM-prior version; shrinkage by game-grouped cross-validation, errors from a game bootstrap.",
        "gap": "Only stints that reconciled with five identified players a side (93.7-99.8% of minutes a season). Single-season RAPM is noisy by nature (about 0.4 year-to-year correlation against 0.75 for BPM), and the held-out tests show it predicts next season's games about as well as BPM, not better; rapm_validation stores every test.",
        "used_by": ["rapm", "player"],
    },
    {
        "table": "rim_deterrence", "label": "Rim deterrence (opponents' shots by distance, defender on vs. off)", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM rim_deterrence", "range_fmt": "season_int",
        "source": "scripts/build_rim_deterrence.py: every field-goal attempt from the play-by-play parser, placed in its lineup_stints stint by event number, distance from the NBA shot chart's coordinates (player_shots); per-season totals and checks in rim_deterrence_seasons.",
        "gap": "99.0% of attempts matched to the NBA shot chart (97.4% in 2025-26, four of whose games player_shots lacks); the rest use ESPN's text distance, or count a layup/dunk/tip with no distance as 0-3 ft (right 85.8% of the time where it can be checked). Only tracked stints (93.7-99.8% of attempts a season). On/off, not adjusted for teammates or opponents.",
        "used_by": ["analytics#rim", "player"],
    },
    {
        "table": "assist_pairs", "label": "Assist network (passer to scorer, every assisted basket)", "group": "Players",
        "range_sql": "SELECT MIN(season), MAX(season) FROM assist_pairs", "range_fmt": "season_int",
        "source": "ESPN play-by-play: the passer named in each made shot's text ('(X assists)'), matched to an NBA id by the same parser as player_game_lines (scripts/build_assist_network.py); per player assisted shares in player_assisted_share, league totals and checks in assist_seasons.",
        "gap": "Every player's assists equal his player_game_lines assists (0 player-seasons differ) and are within 0.3% of NBA.com's season totals. About 0.3% of assists name a passer ESPN gives no id to and aren't in any pair (the basket still counts as assisted); the three NBA Cup finals aren't counted. Assists are the scorekeeper's call, which varies by arena.",
        "used_by": ["assists", "player", "team"],
    },
    {
        "table": "play_finder_events", "label": "Play Finder (every play, one row per player per play)", "group": "Games",
        "range_sql": "SELECT MIN(season), MAX(season) FROM play_finder_games", "range_fmt": "season_int",
        "source": "scripts/build_play_finder.py: every regular-season game's ESPN play-by-play through the same parser as player_game_lines (shooter, passer, blocker, stealer, two or three), the score before each play reconciled to the real final (game_scores), the time from the corrected clock (pbp_event_clock), shot distance from the NBA shot chart's coordinates (player_shots) where matched; per-game sources in play_finder_games, per-season checks in play_finder_seasons.",
        "gap": "Every player-game's shots (twos and threes), free throws, rebounds, assists, steals, blocks and turnovers equal his player_game_lines line (0 of 152,428 differ). 0.35% of shots have no distance; 0.32% of rows name a player with no id. Team rebounds aren't included; the three NBA Cup finals aren't either.",
        "used_by": ["plays"],
    },
    {
        "table": "best_games", "label": "Best games (win-probability swings, one row per game)", "group": "Games",
        "range_sql": "SELECT MIN(season), MAX(season) FROM best_games", "range_fmt": "season_int",
        "source": "scripts/build_best_games.py: every regular-season game's reconciled score after each play (play_finder_events) run through Game Replay's win-probability model; per game the swing, lead changes, comeback, overtimes, the play that moved win probability most and an excitement score (formula in api/best_games.py; weights stored in best_games_meta).",
        "gap": "2020-21 on only (no earlier play-by-play), regular season only (no playoff games); the three NBA Cup finals are left out. 7 of 7,229 games have play-by-play whose score doesn't reconcile to the real final: stored, but left out of every ranking. The excitement weights are a stated judgment, not fitted.",
        "used_by": ["bestgames"],
    },
    {
        "table": "pbp_event_clock", "label": "Corrected game clock (every ESPN play-by-play event)", "group": "Games",
        "range_sql": "SELECT MIN(g.season), MAX(g.season) FROM pbp_games g WHERE g.source = 'espn'", "range_fmt": "season_int",
        "source": "scripts/build_event_clock.py: ESPN's clock is late by event type (made shots a median 14 s, rebounds 6 s, turnovers 5-10 s, misses 2 s, against NBA.com's own play-by-play of 418 games), so every field goal matched to the NBA shot chart takes the chart's time, free throws their trip's, a rebound 2 s after its miss, turnovers and unmatched shots ESPN's time less the median lag, everything else ESPN's own; pbp_event_clock_meta stores the checks.",
        "gap": "About 94% of events land within 2 s of NBA.com's log (ESPN's own times: 32%). The moment a turnover happened can't be recovered from ESPN, which stamps it at about the time of the next play. Read by Game Replay, the Play Finder, Rotations' closing stretch, Best Games and the possessions; player minutes, stints, Clutch WPA, Situational Splits and the Garbage-Time Deflator still use ESPN's times.",
        "used_by": ["analytics#replay", "plays", "rotations", "bestgames", "possessions"],
    },
    {
        "table": "player_wpa_totals", "label": "Win-probability-added totals", "group": "Models",
        "range_sql": None, "range_fmt": None,
        "source": "compute_wpa.py over deduplicated play-by-play (scripts/wpa_lib.PBP_DEDUP_WHERE).",
        "gap": "Clutch plays carry 3.7x the leverage of non-clutch plays — never compare raw clutch and non-clutch WPA per play. Most players are statistically indistinguishable from zero on the clutch split. Still on ESPN's clock, which places a made shot up to ~15 s late (it moves to the corrected clock together with the paper).",
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
