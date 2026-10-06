from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from fastapi import APIRouter

from local_only import LOCAL_ONLY, NOTE as LOCAL_ONLY_NOTE
from routers.leaders import live_leaders, qualifying
from source_badge import make_source

from impact_core import (
    TEAM_META,
    current_standings,
    fetch_nba_api_team_stats,
    get_current_nba_season,
    get_db,
    get_latest_season,
    stored_standings_rows,
)

router = APIRouter()


def db_standings(cursor, season):
    """[(abbr, wins, losses)] for a stored season: the real record (team_seasons, Basketball-Reference;
    equal to the final scores in game_scores for every team-season 2009-10 on, test_known_facts).
    The same rows impact_core.stored_standings() turns into the standings table."""
    return stored_standings_rows(cursor, season)


def db_team_stats(cursor, season):
    """{abbr: {ppg, rpg, apg, spg, bpg, fgPct, threePct, ftPct}} for a stored season, the shape of the
    live stats.nba.com block (percentages as 46.5). Points per game from the real final scores
    (game_scores, every game in the standings); rebounds, assists, steals, blocks and the shooting
    percentages from the play-by-play game lines summed per team (player_game_lines joined to the
    schedule, so the NBA Cup finals drop out; team rebounds belong to nobody, so rebounds run about
    one a game under NBA.com's team total). Seasons without game lines (before 2020-21) get points only."""
    cursor.execute(
        """
        WITH g AS (
            SELECT team_abbreviation, COUNT(*) AS games, SUM(pts_for)::float / COUNT(*) AS ppg
            FROM game_scores WHERE season = %s GROUP BY 1),
        l AS (
            SELECT l.team_abbreviation, COUNT(DISTINCT l.game_id) AS games,
                   SUM(l.oreb + l.dreb) AS reb, SUM(l.ast) AS ast, SUM(l.stl) AS stl, SUM(l.blk) AS blk,
                   SUM(l.fgm) AS fgm, SUM(l.fga) AS fga, SUM(l.fg3m) AS fg3m, SUM(l.fg3a) AS fg3a,
                   SUM(l.ftm) AS ftm, SUM(l.fta) AS fta
            FROM player_game_lines l
            JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
            WHERE l.season = %s GROUP BY 1)
        SELECT g.team_abbreviation, g.ppg, l.games, l.reb, l.ast, l.stl, l.blk, l.fgm, l.fga, l.fg3m, l.fg3a,
               l.ftm, l.fta
        FROM g LEFT JOIN l USING (team_abbreviation)
        """,
        (season, season),
    )
    out = {}
    for abbr, ppg, games, reb, ast, stl, blk, fgm, fga, fg3m, fg3a, ftm, fta in cursor.fetchall():
        def per_game(v):
            return round(float(v) / games, 1) if games and v is not None else None

        def pct(made, att):
            return round(100.0 * float(made) / float(att), 1) if att else None

        out[abbr] = {
            "ppg": round(float(ppg), 1),
            "rpg": per_game(reb), "apg": per_game(ast), "spg": per_game(stl), "bpg": per_game(blk),
            "fgPct": pct(fgm, fga), "threePct": pct(fg3m, fg3a), "ftPct": pct(ftm, fta),
        }
    return out


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
    The current season's standings, team per-game stats and scoring leader for the Dashboard,
    Standings and Team Comparison pages.

    Round 8 step 4: standings come from ESPN's public API (regular season only; every team 0-0
    before opening night), with the latest stored season's record (team_seasons) when ESPN doesn't
    answer within 3 s. The team block and the scoring leader still come from stats.nba.com when it
    answers within 3 s (the stored tables don't have the season in progress until the play-by-play
    is fetched) and otherwise from the stored tables. Every block says which season it is
    (`*_season`) and where it came from (`*_source`), so a page never shows last season's numbers
    under this season's label (R8-004). The two live calls run concurrently.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        db_latest_season = get_latest_season(cursor)
        current_live_season = get_current_nba_season()
        season = max(db_latest_season, current_live_season)

        with ThreadPoolExecutor(max_workers=3) as pool:
            standings_future = pool.submit(current_standings, season)
            team_stats_future = pool.submit(fetch_nba_api_team_stats, season)
            leaders_future = pool.submit(live_leaders, "pts", season, 1)
            standings_block = standings_future.result()
            live_team_stats = team_stats_future.result()
            live_pts_leaders = leaders_future.result()

        floor = qualifying("pts")  # Stat Leaders' floor (R8-068), so the tile names the same player
        cursor.execute(
            """
            SELECT player_name, pts
            FROM player_season_stats
            WHERE season = %s AND pts IS NOT NULL AND team_abbreviation <> 'TOT' AND gp >= %s AND min >= %s
            ORDER BY pts DESC, gp DESC, player_name
            LIMIT 1;
            """,
            (db_latest_season, floor["min_gp"], floor["min_mpg"]),
        )
        top_scorer_row = cursor.fetchone()

        # Stored fallbacks (round 8 R8-062). Before, the standings took each team's record as its
        # best player's w_pct x 82 (CLE 82-0 for a real 52-30) and the per-game stats summed every
        # player's season row under his last team and divided by the roster's most games played
        # (CLE 147.5 points a game for a real 119.5). Now: the record from team_seasons, points
        # from the real final scores, the rest from the play-by-play game lines (team rebounds
        # aren't anybody's, so rebounds run a little under NBA.com's team total).
        team_rows = db_team_stats(cursor, db_latest_season)

    stored_team_stats = {}
    for abbr, stats in team_rows.items():
        if abbr not in TEAM_META:
            continue
        stored_team_stats[abbr] = {"name": TEAM_META[abbr]["name"], "abbr": abbr, **stats}

    live_top_scorer = None
    if live_pts_leaders and live_pts_leaders.get("results"):
        p0 = live_pts_leaders["results"][0]
        live_top_scorer = {"player_name": p0["player_name"], "ppg": p0["value"]}

    return {
        "season": season,
        "stored_season": db_latest_season,
        "standings_source": standings_block["source"],
        "standings_season": standings_block["season"],
        "standings_played": standings_block["played"],
        "standings": standings_block["standings"],
        "team_stats_source": "nba_api" if live_team_stats else "local_db",
        # The stored fallback's season (the live block is the current season's).
        "team_stats_season": season if live_team_stats else db_latest_season,
        "team_stats": live_team_stats or stored_team_stats,
        "top_scorer_source": "nba_api" if live_top_scorer else "local_db",
        "top_scorer_season": season if live_top_scorer else db_latest_season,
        "top_scorer": live_top_scorer or (
            {"player_name": top_scorer_row[0], "ppg": round(float(top_scorer_row[1]), 1)}
            if top_scorer_row
            else None
        ),
        "_source": make_source(
            ["team_seasons", "game_scores", "player_game_lines", "player_season_stats"],
            "ESPN standings (live)" + (" + nba_api (stats.nba.com, live)" if live_team_stats or live_top_scorer else "")
            if standings_block["source"] == "espn" else
            ("stored tables" + (" + nba_api (stats.nba.com, live)" if live_team_stats or live_top_scorer else "")),
            live=standings_block["source"] == "espn" or bool(live_team_stats or live_top_scorer),
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
        "source": "NBA shot chart: bulk play-by-play files per season (load_pbp_shots.py); 2025-26 re-fetched from stats.nba.com's ShotChartDetail on 2026-10-06 (fetch_season_shots.py: the four games the file lacked, playoffs and play-in).",
        "gap": "shot_zone_basic is NULL on bulk-loaded rows (zones come from api/shots_lib.classify_zone()); mixes regular season/playoffs/play-in — regular season only is game_id LIKE '002%'. No defender distance or shot type (catch-and-shoot vs. pull-up) per shot.",
        "used_by": ["shotcharts"],
    },
    {
        "table": "player_game_lines", "label": "Per player-game lines", "group": "Games",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_game_lines", "range_fmt": "season_int",
        "source": "Rebuilt from ESPN play-by-play (scripts/build_player_game_lines.py); minutes rebuilt from substitutions; a missed shot is a two or a three as the NBA shot chart (player_shots) calls the same shot where it can be matched (~99%).",
        "gap": "Regular season only, 2020-21 on (no earlier seasons, no playoffs). game_id is ESPN's (espn_...), not the NBA 002... ids used elsewhere. Players ESPN gives no id are matched by exact name through player_bio (since round 8 step 6a); 9 name-team-seasons stay unmatched (typos and spellings no source pairs with an NBA id, about 260 events), so those players have no line in those games.",
        "used_by": ["stability", "analytics#withwithout"],
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
        "used_by": ["standings", "teams"],
    },
    {
        "table": "player_shot_hex", "label": "Shot quality map (per player-season hexagon cells)", "group": "Shooting",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_shot_hex", "range_fmt": "season_int",
        "source": "scripts/build_shot_making.py: every regular-season shot binned into 2-foot hexagons of the half court (api/shot_hex.py), per qualified player-season (200+ FGA) the attempts, makes and expected makes (the cross-fitted shot-making model) per cell as arrays; shot_hex_league holds the league's attempts and makes per cell and season, shot_hex_meta the grid.",
        "gap": "Shots beyond half court (0.14%) and, through 2009-10, the ~25% of shots the NBA gave no location (stored at (0, 0), nearly all at the rim) are in no cell; each player-season keeps their counts off the map so cells + off-map equal his attempts and makes (0 of 9,026 differ). No defender distance or shot type, as in player_shots.",
        "used_by": ["shotcharts"],
    },
    {
        "table": "shot_value_added", "label": "Shot Value Added (attempts priced before the game, with and without the shooter)", "group": "Shooting",
        "range_sql": "SELECT MIN(season), MAX(season) FROM shot_value_added", "range_fmt": "season_int",
        "source": "scripts/build_shot_value.py (model in scripts/shot_value_lib.py): every regular-season chart shot from 2020-21 priced by a gradient-boosting location model fitted only on earlier seasons and on none of the shooter's fold of players (30 fits), moved by the league's level so far that season, with and without the shooter's own skill as of the day before (a hidden log-odds number per player for rim, other twos, threes and free throws, carried across seasons and updated game by game; its four settings per kind estimated by empirical Bayes on 2010-11 to 2019-20). Free throws from player_game_lines. shot_value_shots holds the per-shot prices, shot_value_states the skills carried into and out of each season, shot_value_validation the out-of-sample log loss and year-to-year checks.",
        "gap": "Free-throw history before 2020-21 is rebuilt from per-game averages (player_season_stats: games x FTA, makes = attempts x FT%), off by a few attempts a season; players under the 2009-10 to 2024-25 legacy minutes floor have none. No defender distance or shot type, so skill carries the defence a shooter usually faces and the shots he creates. The league level is a running estimate, so a level that drifts during a season puts the whole league above or below its forecast (stored per season). The three NBA Cup finals are not regular-season games and are left out; the 4 games of 2025-26 the shot chart lacks have no priced field goals (the shooter-aware RAPM prices their attempts by the unmatched-attempt rule).",
        "used_by": ["shotcharts", "rapm"],
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
        "used_by": ["scores", "analytics#withwithout"],
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
        "gap": "Ratings use only games before the game, but the three logistic coefficients and the prior constants are fitted across seasons. No injuries, trades or line-ups: a team is one rating (pregame_availability_odds adds who played, 2020-21 on).",
        "used_by": ["simulator", "bestgames"],
    },
    {
        "table": "pregame_availability_odds", "label": "Pre-game odds with who played", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM pregame_availability_odds", "range_fmt": "season_int",
        "source": "scripts/build_pregame_availability.py (math in api/availability_lib.py): every game 2020-21 on, the pre-game odds plus a lineup term from the rotation players who played (player_game_lines), their expected minutes from earlier games and ratings fixed before the season (BPM projection, last season's RAPM); held out by season, and under the paper's protocol with paired tests in pregame_availability_tests.",
        "gap": "Who played is known at tip-off, not when a forecast is usually made, so the gain is an upper bound on what injury news is worth. One game (CHI-LAC 2026-01-20) has no lineups and keeps the base odds; RAPM ratings start in 2021-22; before 2025-26 players ESPN gives no id look absent.",
        "used_by": ["simulator"],
    },
    {
        "table": "pregame_availability_players", "label": "Rotation players of every game: who played, who sat", "group": "Models",
        "range_sql": None, "range_fmt": None,
        "source": "scripts/build_pregame_availability.py: per game and side, the rotation players (expected to play 10+ minutes) who played and those who sat while still on the team, with expected minutes and both ratings; read by the Season Simulator's lineup what-if.",
        "gap": "'Sat' means played for the team earlier that season and not for another team since, so a long injury is listed every game, and a player waived without signing elsewhere stays listed. Rookies before their first game and deep-bench players are not in it.",
        "used_by": ["simulator"],
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
        "used_by": ["simulator", "scores"],
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
        "gap": "Every stint of every regular-season game 2020-21 on, reconciled per game against the real final score, game length and team totals (lineup_stint_games says which games fail). A free throw is credited to the five on the floor at the foul (the box score's rule; since round 8 step 6a). Untracked stints (a player the parser can't identify, or a game that doesn't reconcile) are left out and counted: 0.02-0.4% of minutes a season since round 8 step 6a matched ESPN's no-id players (two-way and 10-day players) through player_bio (2-6% in 2020-21 to 2024-25 before).",
        "used_by": ["rotations"],
    },
    {
        "table": "player_game_onfloor", "label": "On-floor plus-minus per player-game", "group": "Games",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_game_onfloor", "range_fmt": "season_int",
        "source": "scripts/build_player_game_onfloor.py: the lineup_stints lineups and points per player-game, with free throws credited to the players on the floor at the foul (the box score's convention). Matches ESPN's box-score +/- for 98% of player-games and within 2 points for 99.7% (300 random games, checked 2026-10-03 and again 2026-10-05).",
        "gap": "Regular season 2020-21 on. The Workbench shows it only in the 7,220 of 7,232 games whose play-by-play reconciles with the final score. player_game_lines' on-court points (tm_pts/op_pts) follow the same rules since round 8 step 6a (before, they credited stale ESPN score fields and didn't add up in about 1 team-game in 4); On/Off's on-court points come from it since 2026-10-03.",
        "used_by": ["analytics#onoff", "player", "team"],
    },
    {
        "table": "possessions", "label": "Possessions from play-by-play", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM possessions", "range_fmt": "season_int",
        "source": "ESPN play-by-play cut into possessions by scripts/build_possessions.py (rules in scripts/pbp_possessions.py, on the same parser as lineup_stints); times on a clock rebuilt from the NBA shot chart, because ESPN logs made shots a median 14 s late; per-game checks in possession_games, team-season totals by start type in possession_seasons.",
        "gap": "Every regular-season game 2020-21 on; 7,220 of 7,232 games add up to the final score and the team totals, the same games as lineup_stints (possession_games says why the rest don't). Counted possessions run ~2 a team-game under the box-score estimate because team offensive rebounds continue a possession. ESPN stamps a turnover at about the time of the next play, so possessions after turnovers have no time-to-first-shot or transition flag, and about 27% of possessions have an approximate length.",
        "used_by": ["possessions", "player", "coaching", "rotations"],
    },
    {
        "table": "coaching_decisions", "label": "Coaching decisions: timeouts after runs, challenges, fouling up 3, the 2-for-1", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM coaching_decisions", "range_fmt": "season_int",
        "source": "scripts/build_coaching_decisions.py over the possessions table, ESPN's timeout and coach's-challenge events and the corrected clock (pbp_event_clock); win probability from Game Replay's model; tests (permutation within matched moments, Benjamini-Hochberg per family) in coaching_decision_tests and coaching_decision_summary.",
        "gap": "Only games whose possessions reconcile (7,220 of 7,232, 2020-21 on). ESPN's log has one kind of timeout, always charged to a team, so a mandatory television break charged to a team looks like a coach's choice; about one timeout in five is stamped late, so timeouts are placed by the log's order, not its clock. What a coach challenged (foul, out of bounds, goaltending) can't be read: ESPN rewrites the log after an overturn. 527 challenges ('replaycenter' and lone 'Challenge' records) have no outcome.",
        "used_by": ["coaching"],
    },
    {
        "table": "lineup_predictor_units", "label": "Lineup Predictor: every five-man lineup, predicted before its first game", "group": "Teams",
        "range_sql": "SELECT MIN(season), MAX(season) FROM lineup_predictor_units", "range_fmt": "season_int",
        "source": "scripts/build_lineup_predictor.py (math in api/lineup_predictor_lib.py): every five-man lineup of every team-season 2021-22 on, its net rating over the possessions table's possessions with those five on the floor, and its predicted net from what was known before its first game (ratings fixed before the season, Gravity and roles of the season before, projected usage, the team's and the five's earlier games); fits in lineup_predictor_fit, scores and paired tests under the paper's protocol in lineup_predictor_metrics and lineup_predictor_tests; per-player inputs in lineup_predictor_players.",
        "gap": "2020-21 is left out (no RAPM or Rating Tracker season before it). Only possessions in games that reconcile with five identified players a side (possessions.tracked_ok); 4,536 lineups with no possession on one end have no net rating. Opponents, home court and familiarity aren't modelled. A lineup's record is mostly noise (median a dozen possessions), so scores are given as the share of the real spread explained, from a noise model checked against a split-half estimate (11% apart).",
        "used_by": ["rotations"],
    },
    {
        "table": "report_card_tests", "label": "Model Report Card: every model's score, season by season", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM report_card_tests", "range_fmt": "season_int",
        "source": "scripts/build_report_card.py: every model re-run with a rolling origin (each season predicted by models rebuilt from the seasons before it, by their own selection rules): pre-game odds and the Season Simulator 2012-13 on, player impact (RAPM versions, Rating Tracker, BPM, on/off, expected-points RAPM) 2022-23 on, by game and by possession, and the shot prices 2021-22 on. Per season every model's score and every pair's difference with a 95% cluster-bootstrap interval; pooled across seasons with random effects in report_card_pooled; choices per season in report_card_choices.",
        "gap": "Few seasons for player impact (4) and top-6 odds (6), so the between-season spread is rough there. The held-out-games task, year-to-year reliabilities, availability-aware odds, Lineup Predictor and the Forecast Ledger aren't on it. Shot-model and Shot Value settings and BPM's formula are fixed, not re-chosen per season.",
        "used_by": ["reportcard"],
    },
    {
        "table": "report_card_units", "label": "Model Report Card: every prediction it scored", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM report_card_units", "range_fmt": "season_int",
        "source": "scripts/build_report_card.py: one row per scored game (pre-game odds, next-season game margins) or team-season (simulator, per checkpoint), with the prediction and the outcome; per-game sums for the per-possession and per-shot scores in report_card_game_sums.",
        "gap": "Possessions and shots are stored as per-game sums, not one row each.",
        "used_by": ["reportcard"],
    },
    {
        "table": "data_quality_game_flags", "label": "Data Quality: the error classes on every game", "group": "Games",
        "range_sql": "SELECT MIN(season), MAX(season) FROM data_quality_game_flags", "range_fmt": "season_int",
        "source": "scripts/build_data_quality.py: every ESPN regular-season game 2020-21 on (the three NBA Cup finals included) with the size of each per-game error class of the paper's data-quality audit (events tagged to the wrong player, tag/text disagreements, players with no id, substitutions with no team, stale or backward score fields, last score vs the final, missed threes worded as twos, chart matches, clock offsets, events outside their period, zero-distance threes, the plus-minus field), which classes touch the game by api/data_quality_lib.py's rules, and its level (excluded, flagged, worked around, clean). The build re-runs scripts/paper_data_audit.py's checks first and stops unless every stored audit number reproduces.",
        "gap": "No flag before 2020-21 (no play-by-play here). The season-table classes (a wrong team on a season row, two age conventions) aren't per game, and a shot at (0, 0) is a real shot at the rim in this era, so the unlocated class flags no game. Zero-distance threes touch nearly every game, so almost none is 'clean'.",
        "used_by": ["quality"],
    },
    {
        "table": "data_quality_sensitivity", "label": "Data Quality: results re-scored without the flagged games", "group": "Models",
        "range_sql": None, "range_fmt": None,
        "source": "scripts/build_data_quality.py: three headline results (RAPM with a BPM prior vs BPM on next-season margins, points per possession after a steal vs a made shot and transition vs settled, the availability-aware odds vs the pre-game model) re-scored with each drop set (the flagged games, then each per-game class), every score and difference with paper_tests' paired bootstrap by game, and 30 random drops of as many games from the same seasons as the control. The every-game rows equal paper_eval_tests and pregame_availability_tests bit for bit.",
        "gap": "Hyperparameters stay at the protocol's choices (not re-chosen without the games); the availability odds are only re-scored (not refitted), and their expected minutes still come from earlier games, flagged or not. A class touching more than half the games isn't dropped on its own.",
        "used_by": ["quality"],
    },
    {
        "table": "coaching_decision_tests", "label": "Coaching decisions: the tests", "group": "Teams",
        "range_sql": None, "range_fmt": None,
        "source": "scripts/build_coaching_decisions.py: per decision the matched effect on the treated, a game-clustered bootstrap interval, a permutation p (2,000 shuffles of the decision within matched moments, 20,000 when p <= 0.02) and a Benjamini-Hochberg q within its family (league level; per team); paper_beliefs.py's machinery.",
        "gap": "Matching removes differences in season, quarter, time left, score and run size or start type (per decision), not everything: the fouling teams up 3 were about 1.2 points stronger before the game, and an early shot at the end of a quarter is partly an open look, not only a choice.",
        "used_by": ["coaching"],
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
        "gap": "Only stints that reconciled with five identified players a side (99.6-99.98% of minutes a season since round 8 step 6b). Single-season RAPM is noisy by nature (about 0.4 year-to-year correlation against 0.75 for BPM), and the held-out tests show it predicts next season's games about as well as BPM, not better; rapm_validation stores every test.",
        "used_by": ["rapm", "player"],
    },
    {
        "table": "player_rating_tracker", "label": "Rating Tracker (RAPM that carries across seasons)", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_rating_tracker WHERE kind = 'filtered'", "range_fmt": "season_int",
        "source": "scripts/build_rating_tracker.py (model in scripts/rating_tracker_lib.py): the same stint rows as RAPM, but each player's offence and defence rating is a hidden state that drifts between seasons (Kalman filter and smoother), with that season's Basketball-Reference OBPM/DBPM read as a noisy measurement; the drift, the newcomer spread, the BPM weight and scale and the carry-over are chosen by next-season game-margin RMSE over the 2020-21 to 2023-24 season pairs and held fixed after (rating_tracker_fit, which also stores the marginal-likelihood estimate, not used; re-chosen 2026-10-05 after the play-by-play rebuild). Two kinds per player-season: filtered (nothing after that season) and smoothed (with hindsight).",
        "gap": "Only stints that reconciled with five identified players a side; a player with a BPM row but no tracked stint that season gets no BPM measurement that season. Posterior standard deviations assume Gaussian stint noise proportional to one over possessions (checked flat across stint lengths), not a bootstrap. The smoothed kind uses later seasons and must not be read as a forecast; rating_tracker_validation scores only the filtered kind.",
        "used_by": ["rapm", "player"],
    },
    {
        "table": "rim_deterrence", "label": "Rim deterrence (opponents' shots by distance, defender on vs. off)", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM rim_deterrence", "range_fmt": "season_int",
        "source": "scripts/build_rim_deterrence.py: every field-goal attempt from the play-by-play parser, placed in its lineup_stints stint by event number, distance from the NBA shot chart's coordinates (player_shots); per-season totals and checks in rim_deterrence_seasons.",
        "gap": "99.4% of attempts matched to the NBA shot chart (97.4% in 2025-26, four of whose games player_shots lacks); the rest use ESPN's text distance, or count a layup/dunk/tip with no distance as 0-3 ft (right 85.8% of the time where it can be checked). Only tracked stints (99.6-99.97% of attempts a season since round 8 step 6b). On/off, not adjusted for teammates or opponents.",
        "used_by": ["analytics#rim", "player"],
    },
    {
        "table": "assist_pairs", "label": "Assist network (passer to scorer, every assisted basket)", "group": "Players",
        "range_sql": "SELECT MIN(season), MAX(season) FROM assist_pairs", "range_fmt": "season_int",
        "source": "ESPN play-by-play: the passer named in each made shot's text ('(X assists)'), matched to an NBA id by the same parser as player_game_lines (scripts/build_assist_network.py); per player assisted shares in player_assisted_share, league totals and checks in assist_seasons.",
        "gap": "Every player's assists equal his player_game_lines assists except 4 player-team-seasons, each by one assist (4 plays dropped as data errors), and are within 0.3% of NBA.com's season totals. 18 assists name a passer the parser can't identify and aren't in any pair (the basket still counts as assisted; 0.3% of assists before round 8 step 6b matched the feed's no-id players by name); the three NBA Cup finals aren't counted. Assists are the scorekeeper's call, which varies by arena.",
        "used_by": ["assists", "player", "team"],
    },
    {
        "table": "play_finder_events", "label": "Play Finder (every play, one row per player per play)", "group": "Games",
        "range_sql": "SELECT MIN(season), MAX(season) FROM play_finder_games", "range_fmt": "season_int",
        "source": "scripts/build_play_finder.py: every regular-season game's ESPN play-by-play through the same parser as player_game_lines (shooter, passer, blocker, stealer, two or three), the score before each play reconciled to the real final (game_scores), the time from the corrected clock (pbp_event_clock), shot distance from the NBA shot chart's coordinates (player_shots) where matched; per-game sources in play_finder_games, per-season checks in play_finder_seasons.",
        "gap": "Every player-game's shots (twos and threes), free throws, rebounds, assists, steals, blocks and turnovers equal his player_game_lines line (0 of 154,299 differ). 0.23% of shots have no distance; 223 rows (0.01%) name a player the parser can't identify (0.32% before round 8 step 6b matched the feed's no-id players by name). Team rebounds aren't included; the three NBA Cup finals aren't either.",
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
        "gap": "About 94% of events land within 2 s of NBA.com's log (ESPN's own times: 32%). The moment a turnover happened can't be recovered from ESPN, which stamps it at about the time of the next play. Read by Game Replay, the Play Finder, Rotations' closing stretch, Best Games, the possessions, Coaching Decisions, Clutch WPA (since 2026-10-02) and the Garbage-Time Deflator (since 2026-10-06); player minutes, stints and Situational Splits still use ESPN's times (substitutions happen at dead balls, where ESPN is on time).",
        "used_by": ["analytics#replay", "plays", "rotations", "bestgames", "possessions", "coaching"],
    },
    {
        "table": "player_wpa_totals", "label": "Win-probability-added totals", "group": "Models",
        "range_sql": None, "range_fmt": None,
        "source": "compute_wpa.py over deduplicated play-by-play (scripts/wpa_lib.PBP_DEDUP_WHERE).",
        "gap": "Clutch plays carry 3.7x the leverage of non-clutch plays — never compare raw clutch and non-clutch WPA per play. Most players are statistically indistinguishable from zero on the clutch split. On the corrected clock (pbp_event_clock) since 2026-10-02: ESPN's own clock places a made shot a median 14 s late. The switch moved 186 players with 100+ clutch chances to 184 and no clutch conclusion.",
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
        "gap": None,
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
        "gap": "Distinct from player_clusters (6 archetypes) below — Trivia hard-codes the 6-cluster names and hasn't moved to this table.",
        "used_by": ["rolefinder"],
    },
    {
        "table": "player_clusters", "label": "Player Clusters (6 archetypes)", "group": "Models",
        "range_sql": "SELECT MIN(season), MAX(season) FROM player_clusters", "range_fmt": "season_int",
        "source": "Older clustering model, kept in place because Trivia hard-codes its archetype names (Player Comparison shows it too).",
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
            local_only = table in LOCAL_ONLY
            if cursor.fetchone()[0] is None:
                # A LOCAL_ONLY table is absent from the Layerbase mirror on purpose (api/local_only.py).
                rows.append({**entry, "exists": False, "n_rows": 0, "season_from": None, "season_to": None,
                             "local_only": local_only, "note": LOCAL_ONLY_NOTE if local_only else None})
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
                "local_only": local_only,
                "note": None,
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
