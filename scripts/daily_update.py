"""
daily_update.py
===============
The live 2026-27 season's one command (round 9 step 2, 2026-10-06): every game final since the last run is fetched
and stored, and the season's own source tables are refreshed, so the pages and the season rebuild (round 9 step 3)
have the night's games. Nothing of an earlier season is ever touched.

Run it once a day after the night's games are final, about 14:00 IST (04:30 US Eastern) on a game day, the Forecast
Ledger's hour (docs/LEDGER_RUNBOOK.md, docs/DAILY_RUNBOOK.md once written). Since round 9 step 6 this one command also runs the
Forecast Ledger's ledger_update.py first (step 0 below), so a game day needs nothing else:

    cd scripts && python3 daily_update.py                       # the day's run, ~2-4 min on a game day
    python3 daily_update.py --dry-run                           # fetch and compute everything in one transaction, roll it back
    python3 daily_update.py --date 2026-10-25                   # "today" (US Eastern) is this date: finals through it
    python3 daily_update.py --from-date 2026-10-20              # scoreboards from this date on (default: the last successful
                                                                #   run's through date, else the locked schedule's opening night)
    python3 daily_update.py --offline                           # no network: the last online run's caches (live_data/<season>/)
    python3 daily_update.py --season-types preseason --from-date 2026-10-02    # the preseason: the tests only (below)
    options: --season N (end year, default 2027)  --sleep S (between shot-chart calls, default 1.0)
             --refresh-shots (re-fetch every chart even if nothing is missing)  --force-shots (allow a large shot deletion)
             --no-rebuild (fetch only)  --rebuild (rebuild the season's tables even when nothing new was fetched)
             --rebuild-only (no fetch: just the season rebuild, e.g. after a build script changed)
             --no-models (skip the season-to-date models after the rebuild)  --models (run them even when the rebuild
             was skipped)  --models-only (no fetch, no rebuild: just the models)
             --no-ledger (don't run ledger_update.py)  --ledger-only (run nothing else)

What a run does, in order (each step in its own transaction; a step that fails is logged and the rest still run):
  0. The Forecast Ledger (round 9 step 6): scripts/ledger_update.py, unchanged, as its own process (it imports the
     model code from the lock's git tag and writes only the ledger_* tables; output in live_data/<season>/ledger/
     <date>.log). It runs first because its odds should be logged before the day's first tip (a row logged after tip
     is labelled recomputed). Its exit code and its "odds:" / "scored:" lines go into the run row (ledger_status,
     ledger_seconds, ledger_summary); a ledger failure fails the run (exit 1) but the fetch still runs. Passed through:
     --dry-run (with --date as its --today), --offline. Skipped with a reason, never silently: --no-ledger, the
     preseason runs of the tests, --rebuild-only / --models-only, a --date other than today outside a dry run (a logged
     row carries the real time), a --season other than the ledger's.
  1. ESPN scoreboard, one request per US date from --from-date to today: which games are final, by season type
     (1 preseason, 2 regular season, 3 playoffs, 5 play-in). The through date = the last date whose every game is
     final, postponed or cancelled; the next run starts there.
  2. stats.nba.com LeagueGameFinder, one call per season type: team_game_fatigue's rows of the season rebuilt (rest
     days, travel: build_schedule_fatigue.build_season_rows) and game_team_box's upserted (fetch_referee_officials.box_rows).
  3. game_scores: every regular-season final matched to its NBA game id by date and the pair of teams
     (fetch_game_scores._match, a day either side), upserted; the NBA Cup final has no LeagueGameFinder row and gets
     none, as in every stored season.
  4. postseason_games: play-in and playoff finals (fetch_postseason_games.parse), upserted.
  5. player_season_stats' rows of the season: LeagueDashPlayerStats Base + Advanced, per game
     (fetch_2025_26_season_data.fetch_with_retry, load_2025_26_into_db.merge_frames, plus age and pf), replaced; then
     impact_score / impact_score_raw / impact_score_star for that season (compute_impact_score.impact_z,
     upgrade_impact_scores.compute_raw_score / compute_star_score): the z is per season, so the values equal a
     whole-table run's. Rookies get their ids here (R9-003: player_bio waits for the season's end).
  6. pbp_games / pbp_events: for every regular-season final not stored yet, ESPN's game summary (espn_summary.py,
     cached once per game) mapped to the hosted release's rows; names matched by fetch_pbp_espn.PlayerMatcher on the
     season rows just refreshed (so this step waits for the next run when step 5 failed: a rookie's events would
     otherwise be stored without an id); then repair_espn_player_ids.find() on the new games only, and every name the new
     events carry is looked up in the lines parser's name index (pbp_lineups.load_season_names): the run log and
     the summary name the ones without an id. A final whose play-by-play ESPN hasn't published yet is left pending.
  7. game_officials: BoxScoreSummaryV3 for every game of the season not in game_officials_fetch_log (R9-005), cached.
  8. player_shots: when a final of a season type has no shots stored yet, that type's chart is fetched per team
     (fetch_season_shots.fetch_all, 30 calls of 1-2 s; the per-game form takes 23 s, R9-006) and merged by
     fetch_season_shots' own rule: every unchanged shot keeps its id, stored-only rows go, new ones are inserted with
     the stored conventions (shot_zone_basic NULL: zones come from api/shots_lib.classify_zone() on the coordinates,
     as for every bulk row). A merge that would delete more than max(50, 10%) of the type's stored shots, or a chart
     that answers nothing for a type with stored shots, stops instead (--force-shots overrides the first).
  9. The season rebuild (round 9 step 3), when the run stored new play-by-play or changed the shot chart (or
     --rebuild): the fourteen season-level builds of rebuild_all.sh's derived stage, in its order, each as
     `python3 <script> --season N` in its own process (scripts/season_mode.py: only that season's rows of each table
     are deleted and rebuilt, through the same code as the full build; the tables without a season dimension are
     left alone), stopping at the first failure since every build reads the one before it. Each build's output goes
     to live_data/<season>/rebuild/<date>_<script>.log; the run row keeps every step's status and seconds. Only the
     regular season is rebuilt (never the preseason runs of the tests). The update restarts nothing itself: it prints
     "restart impact_api" (the API caches these tables per process).
 10. The season-to-date models (round 9 step 4), after a rebuild that ran clean (or --models): the eight model builds of
     rebuild_all.sh's derived stage that take `--season N` (MODEL_STEPS, in its order: luck, the league zone mix, RAPM,
     Shot Value, the Rating Tracker, the team zone mix, the scouting splits, the simulator and pre-game odds), the same
     way as the rebuild (own process, own log, stop at the first failure; the run row's rebuild_steps lists them with
     phase 'models'). Every one of them reads its pooled fit from the stored fit row and applies it to the season
     (round 9 issue R9-009): nothing is tuned on the live season.
 11. One row in daily_update_runs (listed in paper_manifest.LIVE: outside the paper's digest) and one summary line.
     On Mondays (US Eastern) the summary reminds that the week's report is due (scripts/weekly_report.py).

Idempotent: a second run on the same day writes nothing new (stored games are skipped, upserts repeat their values,
the chart pairs every shot). Resumable: every answer is cached under live_data/<season>/ (gitignored; delete it to
re-fetch) and a game's play-by-play is fetched once; --offline rebuilds from the caches alone.

Never touched: rows of any season before the live one (every DELETE and UPDATE is bound to the season or to the new
games' ids), the Forecast Ledger's tables, the paper's tables (api/paper_freeze.py; the manifest hashes only rows up
to 2025-26). The preseason is never stored by a real run: --season-types preseason is for
api/tests/test_daily_update.py, which runs this script on copies of the tables in a zz_ schema (PGOPTIONS
search_path, the pattern of test_ledger_gameday.py) against the real preseason feeds.

Exit code 0 = every step done; 1 = a step failed (the summary names it; the run row's status is 'failed').
"""

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import time
import traceback
import uuid
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

from db_config import DB_CONFIG
import build_schedule_fatigue as SF
import compute_impact_score as CI
import espn_summary as ES
import fetch_2025_26_season_data as SD
import fetch_game_scores as GS
import fetch_pbp_espn as FP
import fetch_postseason_games as PG
import fetch_referee_officials as RO
import fetch_season_shots as SS
import ledger_espn as E
import load_2025_26_into_db as LS
import pbp_lineups as PL
import repair_espn_player_ids as RP
import upgrade_impact_scores as UI

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

ROOT = Path(__file__).resolve().parent.parent
LIVE_DIR = Path(os.environ.get("LIVE_DATA_DIR") or ROOT / "live_data")
SEASON = 2027
EASTERN = ZoneInfo("America/New_York")
# kind -> ESPN scoreboard season type, nba_api season type, NBA game-id prefix
TYPES = {"preseason": {"espn": 1, "nba": "Pre Season", "prefix": "001"},
         "regular": {"espn": 2, "nba": "Regular Season", "prefix": "002"},
         "playoffs": {"espn": 3, "nba": "Playoffs", "prefix": "004"},
         "playin": {"espn": 5, "nba": "PlayIn", "prefix": "005"}}
KIND_OF_ESPN = {v["espn"]: k for k, v in TYPES.items()}
DEFAULT_KINDS = "regular,playoffs,playin"
BOX_KINDS = ("regular", "preseason")     # the one kind whose games fill fatigue / box / scores / officials / stats / pbp
NOT_PLAYED = ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_SUSPENDED", "STATUS_FORFEIT")
STEPS = ("scoreboard", "nba_games", "game_scores", "postseason", "season_stats", "pbp", "officials", "shots")
# Round 9 step 3: the season-level builds the update runs for the live season after the fetch, in rebuild_all.sh
# order, each as `python3 <script> --season N` (scripts/season_mode.py; the rebuild phase itself is still to be wired
# in: the list is the order api/tests/test_season_rebuild.py proves against rebuild_all.sh).
# Round 9 step 4: the season-to-date models, run after a clean rebuild, in rebuild_all.sh order (each takes --season N).
MODEL_STEPS = ["build_luck_schedule.py", "build_league_zone_mix.py", "build_rapm.py", "build_shot_value.py",
               "build_rating_tracker.py", "build_team_zone_mix.py", "build_scouting_reports.py", "build_season_sim.py"]
REBUILD_STEPS = ["build_event_clock.py", "build_player_game_lines.py", "build_team_game_totals.py", "build_lineup_stints.py",
                 "build_player_game_onfloor.py", "build_player_on_off.py", "build_possessions.py", "build_situational_splits.py",
                 "build_rotations.py", "build_rim_deterrence.py", "build_assist_network.py", "build_play_finder.py",
                 "build_best_games.py", "build_leverage_splits.py"]
LEDGER_SEASON = 2027         # the season the Forecast Ledger locked (ledger_update.SEASON; not imported: that module
                             # loads the frozen code from the tag on import of main())
NEEDS = {"game_scores": ("scoreboard", "nba_games"), "postseason": ("scoreboard",), "season_stats": (),
         "pbp": ("scoreboard", "season_stats"), "officials": ("nba_games",), "shots": ("scoreboard", "nba_games"), "nba_games": (),
         "rebuild": ()}

RUN_LOG_DDL = """CREATE TABLE IF NOT EXISTS daily_update_runs (
    season INTEGER NOT NULL, run_id TEXT PRIMARY KEY, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, today_et DATE,
    mode TEXT, season_types TEXT, from_date DATE, through_date DATE, dates_checked INTEGER, events INTEGER, finals INTEGER,
    pbp_games_new INTEGER, pbp_events_new INTEGER, pbp_pending INTEGER, pbp_unmatched_events INTEGER, pbp_unmatched_names TEXT,
    game_scores_rows INTEGER, fatigue_rows INTEGER, box_rows INTEGER, officials_games INTEGER,
    shots_inserted INTEGER, shots_deleted INTEGER, shot_kinds TEXT, season_stats_players INTEGER,
    failed_steps TEXT, status TEXT, summary TEXT, code_commit TEXT, rebuild_seconds REAL, rebuild_steps TEXT)"""
# the two rebuild columns were added in round 9 step 3: a run log created by step 2 gains them here
RUN_LOG_ALTER = """ALTER TABLE daily_update_runs ADD COLUMN IF NOT EXISTS rebuild_seconds REAL,
    ADD COLUMN IF NOT EXISTS rebuild_steps TEXT, ADD COLUMN IF NOT EXISTS ledger_status TEXT,
    ADD COLUMN IF NOT EXISTS ledger_seconds REAL, ADD COLUMN IF NOT EXISTS ledger_summary TEXT"""
# (the three ledger columns since round 9 step 6)

FATIGUE_INSERT = """INSERT INTO team_game_fatigue (game_id, team_abbreviation, season, game_date, is_home, opponent, win, plus_minus,
    rest_days, is_b2b, games_last_7_days, travel_miles_since_last, timezones_crossed_since_last) VALUES %s"""
BOX_UPSERT = """INSERT INTO game_team_box (game_id, season, game_date, team_id, team_abbreviation, fta, pf, fga, oreb, tov, poss_est)
    VALUES %s ON CONFLICT (game_id, team_abbreviation) DO UPDATE SET season = EXCLUDED.season, game_date = EXCLUDED.game_date,
    team_id = EXCLUDED.team_id, fta = EXCLUDED.fta, pf = EXCLUDED.pf, fga = EXCLUDED.fga, oreb = EXCLUDED.oreb,
    tov = EXCLUDED.tov, poss_est = EXCLUDED.poss_est"""
SCORES_UPSERT = """INSERT INTO game_scores (game_id, team_abbreviation, opponent, season, game_date, is_home, neutral_site, pts_for,
    pts_against, periods, espn_id) VALUES %s ON CONFLICT (game_id, team_abbreviation) DO UPDATE SET opponent = EXCLUDED.opponent,
    season = EXCLUDED.season, game_date = EXCLUDED.game_date, is_home = EXCLUDED.is_home, neutral_site = EXCLUDED.neutral_site,
    pts_for = EXCLUDED.pts_for, pts_against = EXCLUDED.pts_against, periods = EXCLUDED.periods, espn_id = EXCLUDED.espn_id"""
POSTSEASON_UPSERT = """INSERT INTO postseason_games (espn_id, season, game_date, stage, round, conference, home, away, pts_home, pts_away,
    winner, note) VALUES %s ON CONFLICT (espn_id) DO UPDATE SET season = EXCLUDED.season, game_date = EXCLUDED.game_date,
    stage = EXCLUDED.stage, round = EXCLUDED.round, conference = EXCLUDED.conference, home = EXCLUDED.home, away = EXCLUDED.away,
    pts_home = EXCLUDED.pts_home, pts_away = EXCLUDED.pts_away, winner = EXCLUDED.winner, note = EXCLUDED.note"""
PBP_GAME_INSERT = "INSERT INTO pbp_games (game_id, season, game_date, home_team, away_team, home_win, source) VALUES (%s, %s, %s, %s, %s, %s, %s)"
PBP_EVENT_INSERT = """INSERT INTO pbp_events (game_id, action_number, period, seconds_remaining, score_home, score_away, team_id,
    team_tricode, person_id, player_name, action_type, sub_type, description) VALUES %s"""


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def eastern_today():
    return datetime.now(EASTERN).date()


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class _NoCommit:
    """A connection whose commit() does nothing: hands a shared function that commits (fetch_season_shots.apply_merge)
    to the run's own transaction, which the step wrapper commits or (--dry-run) rolls back."""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *a, **k):
        return self._conn.cursor(*a, **k)

    def commit(self):
        pass


def fill_official_names(officials, espn_names):
    """BoxScoreSummaryV3 can list an official with an id and no name (8834 in 0012600009, 2026-10-03, a first-year
    official): when exactly one name is missing and ESPN's summary lists exactly one official whose name is not among
    V3's, that is the name. Otherwise the row keeps the empty name (the id is the key)."""
    missing = [i for i, (_, n) in enumerate(officials) if not n]
    if len(missing) == 1 and espn_names:
        known = {n.lower() for _, n in officials if n}
        cand = [n for n in espn_names if n and n.lower() not in known]
        if len(cand) == 1:
            officials[missing[0]] = (officials[missing[0]][0], cand[0])
    return officials


def parse_scoreboard(day, events, season):
    """The scoreboard's events of one US date as plain dicts (only the season's; every season type)."""
    games = []
    for e in events:
        s = e.get("season") or {}
        if int(s.get("year") or 0) != season or not e.get("competitions"):
            continue
        comp = e["competitions"][0]
        st = (comp.get("status") or {}).get("type") or {}
        sides = {c["homeAway"]: c for c in comp.get("competitors") or [] if c.get("homeAway")}

        def code(side):
            abbr = (sides.get(side, {}).get("team") or {}).get("abbreviation")
            return None if abbr in (None, "TBD") else E.nba_code(abbr)

        def pts(side):
            v = sides.get(side, {}).get("score")
            return int(float(v)) if v not in (None, "") else None

        name = str(st.get("name") or "")
        final = bool(st.get("completed")) or name == "STATUS_FINAL"
        kind = KIND_OF_ESPN.get(int(s.get("type") or 0))
        games.append({"espn_id": str(e["id"]), "date": day, "tip_utc": e.get("date"), "type": int(s.get("type") or 0),
                      "kind": kind, "status": name, "final": final, "not_played": name in NOT_PLAYED,
                      "home": code("home"), "away": code("away"), "home_pts": pts("home") if final else None,
                      "away_pts": pts("away") if final else None, "periods": int((comp.get("status") or {}).get("period") or 0),
                      "neutral": bool(comp.get("neutralSite")), "pbp": bool(comp.get("playByPlayAvailable")),
                      "event": e})
    return games


def through_date(games, dates):
    """The last date (in order) whose every game is final, postponed or cancelled: the dates before the first
    incomplete one; the day before the first date when that one is incomplete."""
    by_day = {}
    for g in games:
        by_day.setdefault(g["date"], []).append(g)
    through = dates[0] - timedelta(days=1)
    for day in dates:
        if all(g["final"] or g["not_played"] for g in by_day.get(day, [])):
            through = day
        else:
            break
    return through


class Run:
    def __init__(self, args):
        self.args = args
        self.season = args.season
        self.label = SS.season_label(self.season)
        self.kinds = [k.strip() for k in args.season_types.split(",") if k.strip()]
        bad = [k for k in self.kinds if k not in TYPES]
        if bad:
            raise SystemExit(f"--season-types: unknown {bad}; choose from {', '.join(TYPES)}")
        box = [k for k in self.kinds if k in BOX_KINDS]
        if len(box) > 1:
            raise SystemExit("--season-types: the preseason can't be run together with the regular season")
        self.box_kind = box[0] if box else None
        self.mode = "dry-run" if args.dry_run else ("offline" if args.offline else "live")
        self.today = args.date or eastern_today()
        self.cache = LIVE_DIR / str(self.season)
        self.run_id = uuid.uuid4().hex[:12]
        self.started = datetime.now(timezone.utc)
        self.t0 = time.time()
        self.counts, self.errors, self.timing = {}, {}, {}
        self.games, self.finder, self.dates = [], {}, []
        self.through = None
        self.conn = psycopg2.connect(**DB_CONFIG)
        self.cur = self.conn.cursor()

    # ── plumbing ──────────────────────────────────────────────────────────────

    def step(self, name, fn):
        missing = [n for n in NEEDS.get(name, ()) if n in self.errors or n not in self.counts]
        if missing:
            self.errors[name] = f"not run: needs {', '.join(missing)}"
            log(f"-- {name}: skipped ({self.errors[name]})")
            return
        t0 = time.time()
        self.cur.execute(f"SAVEPOINT s_{name}")
        try:
            out = fn() or {}
            self.cur.execute(f"RELEASE SAVEPOINT s_{name}")
            if not self.args.dry_run:
                self.conn.commit()
            self.counts[name] = out
            self.timing[name] = time.time() - t0
            log(f"-- {name}: {json.dumps(out, default=str)} ({self.timing[name]:.1f}s)")
        except (Exception, SystemExit) as exc:
            self.cur.execute(f"ROLLBACK TO SAVEPOINT s_{name}")
            self.errors[name] = f"{type(exc).__name__}: " + " ".join(str(exc).split())[:300]
            self.timing[name] = time.time() - t0
            log(f"-- {name}: FAILED {self.errors[name]}")
            traceback.print_exc()

    def cached_json(self, rel, fetch):
        """A JSON answer cached under the season's cache dir: online fetched and saved, offline read (None if missing)."""
        path = self.cache / rel
        if self.args.offline:
            return json.load(open(path)) if path.exists() else None
        data = fetch()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        return data

    def cached_frame(self, name, fetch, dtype=None):
        """A nba_api frame cached as CSV (read back after saving, so both paths give the same types)."""
        path = self.cache / "nba_api" / f"{name}.csv"
        if not self.args.offline:
            df = fetch()
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(path, index=False)
        elif not path.exists():
            raise FileNotFoundError(f"--offline: {path} is not cached")
        return pd.read_csv(path, dtype=dtype)

    # ── 1. ESPN scoreboard ────────────────────────────────────────────────────

    def step_scoreboard(self):
        a = self.args
        from_date = a.from_date
        if from_date is None:
            self.cur.execute("""SELECT max(through_date) FROM daily_update_runs WHERE season = %s AND status = 'ok'
                                AND mode <> 'dry-run' AND season_types = %s""", (self.season, a.season_types))
            from_date = self.cur.fetchone()[0]
        if from_date is None:
            if self.box_kind != "regular":
                raise SystemExit("--from-date is needed for the first run of this season type")
            self.cur.execute("SELECT min(game_date) FROM ledger_schedule WHERE season = %s", (self.season,))
            from_date = self.cur.fetchone()[0]
            if from_date is None:
                raise SystemExit("--from-date is needed: no earlier run and no locked schedule for the season")
        self.from_date = from_date
        # before the season's first date there is nothing to check yet (the other steps still refresh their tables)
        self.dates = [from_date + timedelta(days=i) for i in range((self.today - from_date).days + 1)]
        games = []
        for day in self.dates:
            key = day.strftime("%Y%m%d")
            events = self.cached_json(f"espn_scoreboard/{key}.json",
                                      lambda: E._get(E.SCOREBOARD, {"dates": key, "limit": 100}).get("events", []))
            if events is None:
                raise FileNotFoundError(f"--offline: the scoreboard of {day} is not cached")
            games += [g for g in parse_scoreboard(day, events, self.season) if g["kind"] in self.kinds]
        self.games = games
        self.through = through_date(games, self.dates) if self.dates else from_date - timedelta(days=1)
        finals = [g for g in games if g["final"]]
        by_kind = {k: sum(1 for g in finals if g["kind"] == k) for k in self.kinds}
        out = {"dates": len(self.dates), "from": str(from_date), "through": str(self.through), "events": len(games),
               "finals": len(finals), "finals_by_kind": by_kind, "not_played": sum(1 for g in games if g["not_played"])}
        if not self.dates:
            out["note"] = f"nothing to check yet: the first date is {from_date}"
        return out

    # ── 2. LeagueGameFinder: fatigue + box ────────────────────────────────────

    def step_nba_games(self):
        for kind in self.kinds:
            self.finder[kind] = self.cached_frame(f"leaguegamefinder_{kind}",
                                                  lambda k=kind: SF.fetch_season_games(self.season, TYPES[k]["nba"]),
                                                  dtype={"GAME_ID": str, "GAME_DATE": str})
        out = {"games_by_kind": {k: int(df.GAME_ID.nunique()) if len(df) else 0 for k, df in self.finder.items()}}
        if not self.box_kind:
            return out
        df = self.finder[self.box_kind]
        rows = SF.build_season_rows(self.season, df) if len(df) else []
        self.cur.execute("DELETE FROM team_game_fatigue WHERE season = %s", (self.season,))
        if rows:
            execute_values(self.cur, FATIGUE_INSERT, rows)
        box = RO.box_rows(df, self.season) if len(df) else []
        if box:
            execute_values(self.cur, BOX_UPSERT, box)
        out.update(fatigue_rows=len(rows), box_rows=len(box))
        return out

    # ── 3. game_scores ────────────────────────────────────────────────────────

    def step_game_scores(self):
        kind = self.box_kind
        if not kind:
            return {}
        df = self.finder[kind]
        finals = [g for g in self.games if g["kind"] == kind and g["final"] and g["home"] and g["away"]]
        self.nba_id_of, self.espn_of = {}, {}
        if df.empty or not finals:
            return {"rows": 0, "finals": len(finals), "finals_without_nba_id": len(finals)}
        tg = pd.DataFrame({"game_id": df.GAME_ID, "team": df.TEAM_ABBREVIATION, "matchup": df.MATCHUP,
                           "win": df.WL == "W", "game_date": [date.fromisoformat(d) for d in df.GAME_DATE]})
        tg["is_home"] = [" vs. " in m for m in tg.matchup]
        both = tg.groupby("game_id").team.agg(["min", "max"])
        tg["opponent"] = [both.at[g, "max"] if t == both.at[g, "min"] else both.at[g, "min"]
                          for g, t in zip(tg.game_id, tg.team)]
        games = tg.drop_duplicates("game_id")[["game_id", "game_date", "team", "opponent"]]
        games = games.assign(pair=[GS._pair(a, b) for a, b in zip(games.team, games.opponent)])
        espn = pd.DataFrame([{"espn_id": g["espn_id"], "game_date": g["date"], "home_key": GS._key(g["home"]),
                              "away_key": GS._key(g["away"]), "home_pts": g["home_pts"], "away_pts": g["away_pts"],
                              "periods": g["periods"]} for g in finals])
        matched = GS._match(games, espn)
        self.nba_id_of = {e.espn_id: gid for gid, e in matched.items()}
        self.espn_of = {gid: e.espn_id for gid, e in matched.items()}
        neutral = set(tg.groupby("game_id").is_home.sum().loc[lambda s: s == 0].index)
        rows, wrong = [], 0
        for r in tg.itertuples():
            e = matched.get(r.game_id)
            if e is None:
                continue
            mine_home = GS._key(r.team) == e.home_key
            pf, pa = (e.home_pts, e.away_pts) if mine_home else (e.away_pts, e.home_pts)
            wrong += int((pf > pa) != bool(r.win))
            rows.append((r.game_id, r.team, r.opponent, self.season, r.game_date, bool(r.is_home), r.game_id in neutral,
                         int(pf), int(pa), int(e.periods), e.espn_id))
        if rows:
            execute_values(self.cur, SCORES_UPSERT, rows)
        return {"rows": len(rows), "games": len(matched), "finals": len(finals),
                "finals_without_nba_id": len(finals) - len(matched), "winner_disagrees_with_wl": wrong}

    # ── 4. postseason_games ───────────────────────────────────────────────────

    def step_postseason(self):
        events = [g["event"] for g in self.games if g["kind"] in ("playoffs", "playin")]
        rows = PG.parse(events, self.season) if events else []
        if rows:
            execute_values(self.cur, POSTSEASON_UPSERT, rows)
        return {"rows": len(rows)}

    # ── 5. player_season_stats ────────────────────────────────────────────────

    def step_season_stats(self):
        kind = self.box_kind
        if not kind:
            return {}
        frames = {m: self.cached_frame(f"leaguedashplayerstats_{kind}_{m.lower()}",
                                       lambda m=m: SD.fetch_with_retry(m, self.label, TYPES[kind]["nba"], allow_empty=True))
                  for m in ("Base", "Advanced")}
        base, adv = frames["Base"], frames["Advanced"]
        self.cur.execute("DELETE FROM player_season_stats WHERE season = %s", (self.season,))
        if base.empty or adv.empty:
            return {"players": 0, "note": "LeagueDashPlayerStats has no rows yet for this season type"}
        df = LS.merge_frames(base, adv, self.season)
        df["pf"] = df.player_id.map(base.drop_duplicates("PLAYER_ID").set_index("PLAYER_ID")["PF"])
        cols = LS.INSERT_COLUMNS + ["pf"]
        values = [tuple(None if pd.isna(row[c]) else (row[c].item() if hasattr(row[c], "item") else row[c]) for c in cols)
                  for _, row in df.iterrows()]
        execute_values(self.cur, f"INSERT INTO player_season_stats ({', '.join(cols)}) VALUES %s", values)
        # impact scores for this season only: the per-season z of the whole-table scripts, on the season's rows
        comp = pd.read_sql_query(f"SELECT player_id, season, {', '.join(CI.COMPONENTS)} FROM player_season_stats WHERE season = %s",
                                 self.conn, params=(self.season,))
        z = CI.impact_z(comp)
        self.cur.execute("UPDATE player_season_stats SET impact_score = NULL WHERE season = %s", (self.season,))
        if len(z):
            execute_values(self.cur, "UPDATE player_season_stats AS p SET impact_score = v.z FROM (VALUES %s) AS v(pid, s, z) "
                                     "WHERE p.player_id = v.pid AND p.season = v.s",
                           [(int(r.player_id), int(r.season), float(r.impact_score)) for r in z.itertuples()])
        d = pd.read_sql_query(f"SELECT {', '.join(UI.REQUIRED_COLS)} FROM player_season_stats WHERE season = %s",
                              self.conn, params=(self.season,))
        d = d.dropna(subset=[c for c in UI.REQUIRED_COLS if c not in ("player_id", "player_name", "season")]).reset_index(drop=True)
        self.cur.execute("UPDATE player_season_stats SET impact_score_raw = NULL, impact_score_star = NULL WHERE season = %s",
                         (self.season,))
        if len(d):
            with contextlib.redirect_stdout(io.StringIO()):
                d = UI.compute_star_score(UI.compute_raw_score(d))
            execute_values(self.cur, "UPDATE player_season_stats AS p SET impact_score_raw = v.r, impact_score_star = v.s "
                                     "FROM (VALUES %s) AS v(pid, season, r, s) WHERE p.player_id = v.pid AND p.season = v.season",
                           [(int(r.player_id), int(r.season), float(r.impact_score_raw),
                             None if pd.isna(r.impact_score_star) else float(r.impact_score_star)) for r in d.itertuples()],
                           template="(%s, %s, %s::float8, %s::float8)")
        return {"players": len(values), "impact_rows": int(len(z)), "star_rows": int(d.impact_score_star.notna().sum()) if len(d) else 0}

    # ── 6. play-by-play ───────────────────────────────────────────────────────

    def summary(self, espn_id):
        path = self.cache / "espn_summary" / f"{espn_id}.json.gz"
        if path.exists():
            s = ES.load_summary(path)
            if ES.summary_complete(s) or self.args.offline:
                return s
        if self.args.offline:
            return None
        s = ES.fetch_summary(espn_id)
        if ES.summary_complete(s):      # an incomplete answer is not cached, so the next run asks again
            path.parent.mkdir(parents=True, exist_ok=True)
            ES.save_summary(s, path)
        return s

    def step_pbp(self):
        kind = self.box_kind
        if not kind:
            return {}
        finals = [g for g in self.games if g["kind"] == kind and g["final"]]
        ids = [f"espn_{g['espn_id']}" for g in finals]
        have = set()
        if ids:
            self.cur.execute("SELECT game_id FROM pbp_games WHERE game_id = ANY(%s)", (ids,))
            have = {r[0] for r in self.cur.fetchall()}
        todo = [g for g in finals if f"espn_{g['espn_id']}" not in have]
        skipped = [g["espn_id"] for g in todo if g["home"] not in FP.REAL_NBA_TRICODES or g["away"] not in FP.REAL_NBA_TRICODES]
        todo = [g for g in todo if g["espn_id"] not in skipped]
        matcher = FP.PlayerMatcher(self.cur)
        new_ids, n_events, pending = [], 0, []
        for g in todo:
            s = self.summary(g["espn_id"])
            if s is None or not ES.summary_complete(s):
                pending.append(g["espn_id"])
                continue
            game_row, rows, _ = ES.event_rows(s, self.season, matcher)
            self.cur.execute(PBP_GAME_INSERT, game_row)
            execute_values(self.cur, PBP_EVENT_INSERT, rows, page_size=2000)
            new_ids.append(game_row[0])
            n_events += len(rows)
        fixes = RP.find(self.conn, game_ids=new_ids) if new_ids else pd.DataFrame()
        if len(fixes):
            RP.apply_fixes(self.cur, fixes)
        unmatched_events, no_id, unresolved = 0, [], []
        if new_ids:
            names_by_season, index = PL.load_season_names(self.cur)
            season_names = names_by_season.get(self.season, {})
            self.cur.execute("""SELECT player_name, count(*), bool_and(person_id IS NULL) FROM pbp_events
                                WHERE game_id = ANY(%s) AND player_name IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""", (new_ids,))
            for name, n, without_id in self.cur.fetchall():
                key = PL.norm(name)
                if without_id:
                    no_id.append(name)
                    unmatched_events += n
                if not (key in season_names or key in index or (self.season, key) in index.bio):
                    unresolved.append(name)
        self.new_pbp = new_ids
        hits, misses = matcher.stats()
        return {"new_games": len(new_ids), "events": n_events, "already_stored": len(have), "pending": pending,
                "skipped_non_nba": skipped, "name_matches": hits, "name_misses": misses, "repaired_events": int(len(fixes)),
                "events_without_id": unmatched_events, "names_without_id": no_id, "names_not_in_parser_index": unresolved}

    # ── 7. officials ──────────────────────────────────────────────────────────

    def step_officials(self):
        kind = self.box_kind
        if not kind:
            return {}
        df = self.finder[kind]
        ids = sorted(df.GAME_ID.unique()) if len(df) else []
        done = set()
        if ids:
            self.cur.execute("SELECT game_id FROM game_officials_fetch_log WHERE game_id = ANY(%s)", (ids,))
            done = {r[0] for r in self.cur.fetchall()}
        fetched, empty, missing, unnamed = 0, 0, 0, 0
        for gid in [g for g in ids if g not in done]:
            offs = self.cached_json(f"nba_api/officials/{gid}.json", lambda g=gid: RO.fetch_game_officials(g))
            if offs is None:
                missing += 1
                continue
            offs = [(int(o), str(n or "").strip()) for o, n in offs]
            if any(not n for _, n in offs):
                espn_id = getattr(self, "espn_of", {}).get(gid)
                s = self.summary(espn_id) if espn_id else None
                offs = fill_official_names(offs, ES.officials(s) if s else [])
                unnamed += sum(1 for _, n in offs if not n)
            if offs:
                execute_values(self.cur, "INSERT INTO game_officials (game_id, official_id, official_name) VALUES %s ON CONFLICT DO NOTHING",
                               [(gid, int(o), str(n)) for o, n in offs])
            else:
                empty += 1
            self.cur.execute("INSERT INTO game_officials_fetch_log (game_id, n_officials) VALUES (%s, %s) ON CONFLICT (game_id) DO NOTHING",
                             (gid, len(offs)))
            fetched += 1
            if not self.args.offline:
                time.sleep(0.5)
        return {"games": fetched, "without_officials": empty, "officials_without_a_name": unnamed, "already_logged": len(done),
                "not_cached_offline": missing}

    # ── 8. shots ──────────────────────────────────────────────────────────────

    def step_shots(self):
        self.cur.execute("SELECT game_id, count(*) FROM player_shots WHERE season = %s GROUP BY 1", (self.label,))
        chart_fga = dict(self.cur.fetchall())
        box_fga = {}
        if self.box_kind and len(self.finder.get(self.box_kind, [])):
            box_fga = self.finder[self.box_kind].groupby("GAME_ID").FGA.sum().to_dict()
        lo, hi = str(self.from_date), str(self.today)
        to_fetch, coverage = [], {}
        for kind in self.kinds:
            df = self.finder.get(kind)
            if df is None or df.empty:
                continue
            prefix = TYPES[kind]["prefix"]
            all_ids = set(df.GAME_ID)
            in_range = set(df.loc[(df.GAME_DATE >= lo) & (df.GAME_DATE <= hi), "GAME_ID"])
            stored_k = {g for g in chart_fga if g.startswith(prefix)}
            # a game whose chart has fewer attempts than the box score (the chart lags, or is short for good: one 2025-26
            # game, one 2026-27 preseason game) is re-fetched while it is among the dates checked
            short = sorted(g for g in in_range & stored_k if kind == self.box_kind and chart_fga[g] < box_fga.get(g, 0))
            coverage[kind] = {"games": len(all_ids), "with_shots": len(all_ids & stored_k), "short_of_box": short}
            if (in_range - stored_k) or short or self.args.refresh_shots:
                to_fetch.append(kind)
        out = {"fetched_kinds": to_fetch, "inserted": 0, "deleted": 0, "coverage": coverage}
        if not to_fetch:
            return out
        cache = str(self.cache / "shotchart_detail")
        try:
            raw = SS.fetch_all(self.season, to_fetch, cache, self.args.offline, refresh=not self.args.offline, pause=self.args.sleep)
        except ValueError as exc:      # pd.concat of nothing: the chart has no rows yet for these season types
            if "No objects to concatenate" not in str(exc):
                raise
            raw = pd.DataFrame()
        if raw.empty:
            out["note"] = "the chart has no shots yet for these season types"
            return out
        st = SS.to_stage(raw, self.season)
        if st.duplicated(["game_id", "game_event_id"]).any() or (st.attempted != 1).any():
            raise RuntimeError("the chart has duplicate (game, event) rows or non-attempts: not merged")
        sd = SS.stored(self.conn, self.season)
        proxy = _NoCommit(self.conn)
        for kind in to_fetch:
            prefix = TYPES[kind]["prefix"]
            st_k = st[st.kind == kind]
            sd_k = sd[sd.game_id.str.startswith(prefix)]
            if st_k.empty:
                if len(sd_k):
                    raise RuntimeError(f"{kind}: the chart answered no shots but {len(sd_k):,} are stored: not merged")
                continue
            if not st_k.game_id.str.startswith(prefix).all():
                raise RuntimeError(f"{kind}: chart rows with a game id outside {prefix}")
            both, old_only, new_only = SS.pair(st_k, sd_k)
            if len(old_only) > max(50, 0.1 * len(sd_k)) and not self.args.force_shots:
                raise RuntimeError(f"{kind}: the merge would delete {len(old_only):,} of {len(sd_k):,} stored shots "
                                   f"(paired {len(both):,}, new {len(new_only):,}); rerun with --force-shots if that is right")
            with contextlib.redirect_stdout(io.StringIO()):
                SS.apply_merge(proxy, st_k, self.season, old_only, new_only)
            out["inserted"] += len(new_only)
            out["deleted"] += len(old_only)
            got = st_k.groupby("game_id").size().to_dict()
            coverage[kind]["with_shots"] = len(got)
            if kind == self.box_kind:
                coverage[kind]["short_of_box"] = sorted(g for g, n in got.items() if n < box_fga.get(g, 0))
        return out

    # ── 0. the Forecast Ledger ────────────────────────────────────────────────

    def ledger_wanted(self):
        """(run it?, why) for ledger_update.py; every skip has a reason the summary prints."""
        a = self.args
        if a.no_ledger:
            return False, "--no-ledger"
        if a.rebuild_only or a.models_only:
            return False, "--rebuild-only / --models-only"
        if self.season != LEDGER_SEASON:
            return False, f"the ledger scores {SS.season_label(LEDGER_SEASON)} only"
        if self.box_kind != "regular":
            return False, "only with the regular season (the ledger scores regular-season games)"
        if a.date and a.date != eastern_today() and not a.dry_run:
            return False, "--date outside a dry run (a logged row carries the real time; ledger_update.py --today is dry-run only)"
        return True, ""

    def ledger_command(self):
        cmd = [sys.executable, "ledger_update.py"]
        if self.args.dry_run:
            cmd.append("--dry-run")
            if self.args.date:
                cmd += ["--today", self.today.isoformat()]
        if self.args.offline:
            cmd.append("--offline")
        return cmd

    def step_ledger(self):
        wanted, why = self.ledger_wanted()
        if not wanted:
            log(f"   ledger skipped: {why}")
            return {"ran": False, "why": why}
        path = self.cache / "ledger" / f"{self.today}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self.ledger_command()
        t0 = time.time()
        r = subprocess.run(cmd, cwd=ROOT / "scripts", env=dict(os.environ), capture_output=True, text=True)
        secs = round(time.time() - t0, 1)
        with open(path, "a") as f:
            f.write(f"$ {' '.join(cmd[1:])}  ({datetime.now(timezone.utc).isoformat(timespec='seconds')})\n{r.stdout}{r.stderr}\n")
        lines = [ln.strip() for ln in r.stdout.splitlines()]
        keep = [ln for ln in lines if ln.startswith(("odds:", "scored:", "dry run:", "offline:"))
                or "WARNING" in ln or "note:" in ln]
        out = {"ran": True, "exit": r.returncode, "seconds": secs, "log": str(path), "lines": keep}
        for ln in r.stdout.splitlines():
            log(f"   ledger | {ln.rstrip()}")
        if r.returncode:
            tail = " ".join((r.stderr or r.stdout).strip().splitlines()[-3:])[-400:]
            raise RuntimeError(f"ledger_update.py exited {r.returncode} after {secs:.0f}s ({path}): {tail}")
        return out

    # ── 9. the season rebuild ─────────────────────────────────────────────────

    def rebuild_wanted(self):
        """(run it?, why): --rebuild always, --no-rebuild / --dry-run never, else when this run stored new play-by-play
        or changed the shot chart; only for the regular season (the preseason runs of the tests never rebuild)."""
        a = self.args
        if a.no_rebuild:
            return False, "--no-rebuild"
        if a.dry_run:
            return False, "dry run (the builds commit in their own processes)"
        if self.box_kind != "regular":
            return False, "only the regular season is rebuilt"
        if a.rebuild or a.rebuild_only:
            return True, "--rebuild"
        pb, sh = self.counts.get("pbp", {}), self.counts.get("shots", {})
        new = pb.get("new_games", 0) + sh.get("inserted", 0) + sh.get("deleted", 0)
        if "pbp" in self.errors or "shots" in self.errors:
            return False, "the play-by-play or shot step failed: nothing rebuilt until it succeeds"
        return (new > 0), ("new play-by-play or shots" if new else "nothing new was fetched")

    def step_rebuild(self):
        wanted, why = self.rebuild_wanted()
        if not wanted:
            log(f"   rebuild skipped: {why}")
            return {"ran": False, "why": why}
        log(f"   rebuilding season {self.season}'s tables ({why}): {len(REBUILD_STEPS)} builds, logs in {self.cache / 'rebuild'}")
        return self.run_builds(REBUILD_STEPS, "rebuild", why)

    def run_builds(self, scripts, phase, why):
        """`python3 <script> --season N` for each script in order, own process and log, stopping at the first failure."""
        env = {**os.environ, "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS", "4")}   # PGOPTIONS inherited, like the fetch
        steps = []
        for script in scripts:
            path = self.cache / "rebuild" / f"{self.today}_{script[:-3]}.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            t0 = time.time()
            with open(path, "w") as f:
                r = subprocess.run([sys.executable, script, "--season", str(self.season)], cwd=ROOT / "scripts", env=env,
                                   stdout=f, stderr=subprocess.STDOUT)
            secs = round(time.time() - t0, 1)
            ok = r.returncode == 0
            steps.append({"script": script, "status": "ok" if ok else "failed", "seconds": secs, "phase": phase})
            log(f"   {script}: {'ok' if ok else f'FAILED (exit {r.returncode})'} ({secs:.0f}s)")
            if not ok:
                tail = " ".join(open(path).read().strip().splitlines()[-3:])[-400:]
                self.errors[phase] = f"{script} exited {r.returncode} after {secs:.0f}s ({path}): {tail}"
                steps += [{"script": s, "status": "not run", "seconds": 0.0, "phase": phase} for s in scripts[len(steps):]]
                break
        return {"ran": True, "why": why, "steps": steps, "seconds": round(sum(s["seconds"] for s in steps), 1),
                "ok": sum(s["status"] == "ok" for s in steps)}

    # ── 10. the season-to-date models ────────────────────────────────────────

    def models_wanted(self):
        """(run them?, why): --models / --models-only always, --no-models / --dry-run never, else after a rebuild that ran
        clean (the models read the rebuilt tables)."""
        a = self.args
        if a.no_models:
            return False, "--no-models"
        if a.dry_run:
            return False, "dry run"
        if self.box_kind != "regular":
            return False, "only the regular season"
        if a.models or a.models_only:
            return True, "--models"
        if a.rebuild_only:
            return False, "--rebuild-only (add --models to run them)"
        rb = self.counts.get("rebuild", {})
        if "rebuild" in self.errors:
            return False, "the rebuild failed"
        if not rb.get("ran"):
            return False, f"no rebuild ({rb.get('why', 'skipped')})"
        return True, "after the rebuild"

    def step_models(self):
        wanted, why = self.models_wanted()
        if not wanted:
            log(f"   models skipped: {why}")
            return {"ran": False, "why": why}
        log(f"   season-to-date models for {self.season} ({why}): {len(MODEL_STEPS)} builds, nothing tuned on the season")
        return self.run_builds(MODEL_STEPS, "models", why)

    # ── 10. run log + summary ─────────────────────────────────────────────────

    def summary_line(self):
        c, e = self.counts, self.errors
        sb, pb, gs, ng, of, sh, ss = (c.get(k, {}) for k in ("scoreboard", "pbp", "game_scores", "nba_games", "officials", "shots", "season_stats"))
        rb, md = c.get("rebuild", {}), c.get("models", {})
        parts = [f"daily_update {self.today} ({self.mode}, {','.join(self.kinds)}):"]
        if sb:
            parts.append(f"{sb['dates']} dates {sb['from']}..{self.today} (complete through {sb['through']}), {sb['finals']} finals;")
        if pb:
            parts.append(f"pbp +{pb['new_games']} games / {pb['events']:,} events ({len(pb['pending'])} pending, "
                         f"{pb['events_without_id']} events without an id: {', '.join(pb['names_without_id'][:5]) or 'none'});")
        if gs:
            parts.append(f"game_scores {gs['rows']} rows ({gs.get('finals_without_nba_id', 0)} finals not in LeagueGameFinder yet);")
        if ng and "fatigue_rows" in ng:
            parts.append(f"fatigue {ng['fatigue_rows']}, box {ng['box_rows']};")
        if of:
            parts.append(f"officials +{of['games']} games;")
        if sh:
            parts.append(f"shots +{sh['inserted']:,}/-{sh['deleted']:,} ({','.join(sh['fetched_kinds']) or 'nothing to fetch'});")
        if ss:
            parts.append(f"season stats {ss['players']} players;")
        if rb:
            parts.append(f"rebuild {rb['ok']} of {len(REBUILD_STEPS)} builds in {rb['seconds']:.0f}s;" if rb.get("ran")
                         else f"rebuild skipped ({rb['why']});")
        if md:
            parts.append(f"models {md['ok']} of {len(MODEL_STEPS)} in {md['seconds']:.0f}s;" if md.get("ran")
                         else f"models skipped ({md['why']});")
        lg = c.get("ledger", {})
        if lg:
            parts.append(f"ledger ok ({'; '.join(x.rstrip('.') for x in lg['lines'] if x.startswith(('odds:', 'scored:')))});"
                         if lg.get("ran") else f"ledger skipped ({lg['why']});")
        parts.append(f"{time.time() - self.t0:.0f}s")
        if e:
            parts.append("FAILED: " + "; ".join(f"{k} ({v})" for k, v in e.items()))
        if self.today.weekday() == 0 and self.box_kind == "regular":
            parts.append(f"| Monday: the week's report is due (python3 weekly_report.py --end {self.today - timedelta(days=1)})")
        return " ".join(parts)

    def write_run_log(self, status, summary):
        c = self.counts
        sb, pb, gs, ng, of, sh, ss = (c.get(k, {}) for k in ("scoreboard", "pbp", "game_scores", "nba_games", "officials", "shots", "season_stats"))
        rb, md = c.get("rebuild", {}), c.get("models", {})
        steps = (rb.get("steps") or []) + (md.get("steps") or [])
        secs = (rb.get("seconds") or 0) + (md.get("seconds") or 0)
        self.cur.execute("""INSERT INTO daily_update_runs (season, run_id, started_at, finished_at, today_et, mode, season_types,
            from_date, through_date, dates_checked, events, finals, pbp_games_new, pbp_events_new, pbp_pending, pbp_unmatched_events,
            pbp_unmatched_names, game_scores_rows, fatigue_rows, box_rows, officials_games, shots_inserted, shots_deleted, shot_kinds,
            season_stats_players, failed_steps, status, summary, code_commit, rebuild_seconds, rebuild_steps,
            ledger_status, ledger_seconds, ledger_summary)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s)""",
                         (self.season, self.run_id, self.started, datetime.now(timezone.utc), self.today, self.mode,
                          self.args.season_types, getattr(self, "from_date", None), self.through, len(self.dates),
                          sb.get("events"), sb.get("finals"), pb.get("new_games"), pb.get("events"),
                          len(pb.get("pending", [])) if pb else None, pb.get("events_without_id"),
                          ", ".join(pb.get("names_without_id", [])) if pb else None, gs.get("rows"), ng.get("fatigue_rows"),
                          ng.get("box_rows"), of.get("games"), sh.get("inserted"), sh.get("deleted"),
                          ",".join(sh.get("fetched_kinds", [])) if sh else None, ss.get("players"),
                          ", ".join(self.errors) or None, status, summary, git_commit(),
                          secs if steps else None, json.dumps(steps) if steps else None,
                          *self.ledger_columns()))
        self.conn.commit()

    def ledger_columns(self):
        """(ledger_status, ledger_seconds, ledger_summary) for the run row: ok / failed / skipped: <why>."""
        lg = self.counts.get("ledger")
        if "ledger" in self.errors:
            return "failed", self.timing.get("ledger"), self.errors["ledger"]
        if not lg:
            return None, None, None
        if not lg.get("ran"):
            return f"skipped: {lg['why']}", None, None
        return "ok", lg["seconds"], " | ".join(lg["lines"]) or None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch and compute everything, write nothing (one rolled-back transaction)")
    ap.add_argument("--offline", action="store_true", help="no network: the last online run's caches")
    ap.add_argument("--date", type=date.fromisoformat, help="today (US Eastern), YYYY-MM-DD; default the real date")
    ap.add_argument("--from-date", type=date.fromisoformat, help="first scoreboard date; default the last run's through date")
    ap.add_argument("--season", type=int, default=SEASON, help="end year (2027 = 2026-27)")
    ap.add_argument("--season-types", default=DEFAULT_KINDS, help=f"comma list of {', '.join(TYPES)}; default {DEFAULT_KINDS}")
    ap.add_argument("--sleep", type=float, default=1.0, help="seconds between shot-chart calls")
    ap.add_argument("--refresh-shots", action="store_true", help="fetch every season type's chart even if nothing is missing")
    ap.add_argument("--force-shots", action="store_true", help="allow a merge that deletes many stored shots")
    ap.add_argument("--no-rebuild", action="store_true", help="fetch only: don't rebuild the season's derived tables")
    ap.add_argument("--rebuild", action="store_true", help="rebuild the season's derived tables even if nothing new was fetched")
    ap.add_argument("--rebuild-only", action="store_true", help="no fetch: only the season rebuild")
    ap.add_argument("--no-models", action="store_true", help="skip the season-to-date models after the rebuild")
    ap.add_argument("--models", action="store_true", help="run the season-to-date models even when the rebuild was skipped")
    ap.add_argument("--models-only", action="store_true", help="no fetch, no rebuild: only the season-to-date models")
    ap.add_argument("--no-ledger", action="store_true", help="don't run the Forecast Ledger's ledger_update.py")
    ap.add_argument("--ledger-only", action="store_true", help="run the Forecast Ledger's update and nothing else")
    args = ap.parse_args()
    if args.ledger_only and (args.no_ledger or args.rebuild_only or args.models_only):
        raise SystemExit("--ledger-only can't be combined with --no-ledger, --rebuild-only or --models-only")
    run = Run(args)
    if args.rebuild_only or args.models_only:
        run.mode = "rebuild" if args.rebuild_only else "models"
    elif args.ledger_only and run.mode == "live":
        run.mode = "ledger"
    log(f"run {run.run_id}: season {run.season} {run.kinds}, today {run.today} ({run.mode}), cache {run.cache}")
    run.cur.execute(RUN_LOG_DDL)
    run.cur.execute(RUN_LOG_ALTER)
    if not args.dry_run:
        run.conn.commit()
    run.step("ledger", run.step_ledger)
    if args.ledger_only:
        return finish(run, args)
    for name in STEPS if not (args.rebuild_only or args.models_only) else ():
        run.step(name, getattr(run, f"step_{name}"))
        if name == "scoreboard" and name in run.errors:
            break
    if args.rebuild_only or (not args.models_only and "scoreboard" not in run.errors):
        run.step("rebuild", run.step_rebuild)
    if args.models_only or "scoreboard" not in run.errors:
        run.step("models", run.step_models)
    return finish(run, args)


def finish(run, args):
    status = "ok" if not run.errors else "failed"
    summary = run.summary_line()
    if args.dry_run:
        run.conn.rollback()
        summary += " [dry run: nothing written]"
    else:
        run.write_run_log(status, summary)
    run.conn.close()
    print(summary, flush=True)
    if run.counts.get("rebuild", {}).get("ran") or run.counts.get("models", {}).get("ran"):
        print("restart impact_api (and mvp_api after the models): they cache these tables per process", flush=True)
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
