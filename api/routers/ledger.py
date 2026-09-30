"""Forecast Ledger: the preseason forecasts locked before a season's first tip, and the proof that
they haven't changed since.

    GET /ledger/preseason?season=         the lock (hash, lock time, first tip, code commit and tag, and
                                          whether the stored rows still reproduce the hash), both
                                          forecasts per team, the hindcast summary and every rule
    GET /ledger/games?season=&team=       every scheduled game's locked P(home wins) under both forecasts
    GET /ledger/roster/{team}?season=     the ESPN roster as it was at lock time: matched ids,
                                          projections, the minutes each player was given
    GET /ledger/hindcast?season=          the 2010-11 to 2025-26 hindcast per team-season
    GET /ledger/lock.csv?season=          the canonical CSV the hash is taken of, rebuilt from the
                                          stored rows (sha256 of the download = the stored hash)
    GET /ledger/live?season=              the season scored so far (round 6 step 2): running Brier / log loss of
                                          every version on the games all of them scored, calibration with Wilson
                                          intervals, the latest paired tests, standings against the locked ranges,
                                          tonight's logged odds, the latest results
    GET /ledger/live/games?season=&team=  every scored game with each version's odds

Written once by scripts/ledger_lock.py; the rules are in api/ledger_lib.py. Every read is cached per
process (the lock never changes; restart impact_api after a --relock), except the two live reads, which
read the tables scripts/ledger_update.py appends to each night.
"""

import json
from datetime import date, datetime
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

import numpy as np
import pandas as pd

import ledger_lib as LL
import ledger_live as LV
from impact_core import get_db
from source_badge import make_source

router = APIRouter()

TABLES = ["ledger_lock", "ledger_meta", "ledger_forecasts", "ledger_schedule", "ledger_rosters", "ledger_hindcast"]
UPSTREAM = ("ESPN schedule and rosters read at lock time (scripts/ledger_lock.py); Marcel projections "
            "(build_projections.py); Season Simulator model (season_sim_params, pregame_model_fit)")
TEAM_COLS = ["forecast", "key", "conference", "team_bpm", "srs_prev", "prior_mean", "prior_sd", "games_scheduled",
             "games_placeholder", "mean_wins", "sd_wins", "wins_p10", "wins_p50", "wins_p90", "p_playoffs", "p_top6",
             "p_playin", "p_first", "p_round2", "p_conf_finals", "p_finals", "p_title"]
JSON_META = {"frozen_files", "roster_matched", "roster_injury_status", "pregame_beta", "seeds", "hindcast",
             "hindcast_by_season", "hindcast_loso_coefficients", "projection_check", "simulation_checks", "sources"}


def _clean(v):
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [{c: _clean(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


@lru_cache(maxsize=1)
def _seasons():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.ledger_lock')")
        if cur.fetchone()[0] is None:
            return ()
        cur.execute("SELECT season FROM ledger_lock ORDER BY season")
        return tuple(r[0] for r in cur.fetchall())


def _season(season):
    seasons = _seasons()
    if not seasons:
        raise HTTPException(status_code=404, detail=(
            f"No forecasts locked yet: scripts/ledger_lock.py --lock runs once before "
            f"{LL.SEASON - 1}-{str(LL.SEASON)[-2:]}'s first tip."))
    season = season or seasons[-1]
    if season not in seasons:
        raise HTTPException(status_code=404, detail=f"No lock for {season}; locked seasons: {list(seasons)}.")
    return season


@lru_cache(maxsize=4)
def _csv(season):
    with get_db() as conn:
        return LL.canonical_csv(conn.cursor(), season)


@lru_cache(maxsize=4)
def _preseason(season):
    with get_db() as conn:
        cur = conn.cursor()
        lock = _rows(cur, "SELECT * FROM ledger_lock WHERE season = %s", (season,))[0]
        meta = {}
        for r in _rows(cur, "SELECT key, value, note FROM ledger_meta WHERE season = %s ORDER BY key", (season,)):
            v = json.loads(r["value"]) if r["key"] in JSON_META else r["value"]
            meta[r["key"]] = {"value": v, "note": r["note"]}
        teams = _rows(cur, f"""SELECT {', '.join(TEAM_COLS)} FROM ledger_forecasts
                               WHERE season = %s AND kind = 'team' ORDER BY forecast, key""", (season,))
        gaps = _rows(cur, """SELECT team, SUM(CASE WHEN counted THEN 1 ELSE 0 END) AS counted,
                                    COUNT(*) AS rostered, SUM(CASE WHEN proj_min IS NOT NULL THEN 1 ELSE 0 END) AS projected
                             FROM ledger_rosters WHERE season = %s GROUP BY team ORDER BY team""", (season,))
    reproduced = LL.sha256(_csv(season)) == lock["lock_sha256"]
    by = {f: [] for f in LL.FORECASTS}
    for r in teams:
        by[r.pop("forecast")].append({"team": r.pop("key"), **r})
    return {
        "season": season, "season_label": f"{season - 1}-{str(season)[-2:]}",
        "lock": {**lock, "hash_reproduced": reproduced, "csv_name": f"ledger_{season - 1}-{str(season)[-2:]}_lock.csv"},
        "forecasts": by, "forecast_labels": LL.FORECAST_LABELS, "rosters": {g["team"]: g for g in gaps},
        "meta": meta,
    }


@router.get("/ledger/preseason")
def ledger_preseason(season: Optional[int] = None):
    season = _season(season)
    return {**_preseason(season), "seasons": list(_seasons()), "_source": make_source(TABLES, UPSTREAM)}


@lru_cache(maxsize=4)
def _games(season):
    with get_db() as conn:
        cur = conn.cursor()
        rows = _rows(cur, """SELECT a.key AS espn_id, a.game_date, a.home, a.away, a.venue, a.home_b2b, a.away_b2b,
                                    s.tip_utc, s.note, s.city, a.exp_margin AS exp_margin_as_is, a.p_home AS p_home_as_is,
                                    r.exp_margin AS exp_margin_roster, r.p_home AS p_home_roster
                             FROM ledger_forecasts a
                             JOIN ledger_forecasts r ON r.season = a.season AND r.kind = 'game' AND r.key = a.key
                                                    AND r.forecast = 'roster'
                             JOIN ledger_schedule s ON s.season = a.season AND s.espn_id = a.key
                             WHERE a.season = %s AND a.kind = 'game' AND a.forecast = 'as_is'
                             ORDER BY s.game_date, s.tip_utc, s.espn_id""", (season,))
        tbd = _rows(cur, """SELECT espn_id, game_date, tip_utc, note, venue, city FROM ledger_schedule
                            WHERE season = %s AND NOT counted ORDER BY game_date, espn_id""", (season,))
    return rows, tbd


@router.get("/ledger/games")
def ledger_games(season: Optional[int] = None, team: Optional[str] = None):
    season = _season(season)
    rows, tbd = _games(season)
    if team:
        t = team.upper()
        rows = [r for r in rows if t in (r["home"], r["away"])]
    return {"season": season, "team": team.upper() if team else None, "games": rows, "not_forecast": tbd,
            "_source": make_source(["ledger_forecasts", "ledger_schedule"], UPSTREAM)}


@router.get("/ledger/roster/{team}")
def ledger_roster(team: str, season: Optional[int] = None):
    season = _season(season)
    with get_db() as conn:
        rows = _rows(conn.cursor(), """SELECT espn_athlete_id, player_name, birth_date, position, experience, injury_status,
                                              has_contract, player_id, match_method, proj_min, proj_bpm, counted, minutes,
                                              contribution
                                       FROM ledger_rosters WHERE season = %s AND team = %s
                                       ORDER BY minutes DESC, proj_min DESC NULLS LAST, player_name""", (season, team.upper()))
    if not rows:
        raise HTTPException(status_code=404, detail=f"No {team.upper()} roster in the {season} lock.")
    return {"season": season, "team": team.upper(), "players": rows,
            "_source": make_source(["ledger_rosters"], UPSTREAM)}


@router.get("/ledger/hindcast")
def ledger_hindcast(season: Optional[int] = None):
    season = _season(season)
    with get_db() as conn:
        rows = _rows(conn.cursor(), """SELECT target_season, team, players, team_bpm_centred, srs_prev, srs_final, pred_as_is,
                                              pred_roster_loso, wins, games, exp_wins_as_is, exp_wins_roster
                                       FROM ledger_hindcast WHERE season = %s ORDER BY target_season, team""", (season,))
    return {"season": season, "rows": rows, "_source": make_source(["ledger_hindcast"], UPSTREAM)}


@router.get("/ledger/lock.csv")
def ledger_lock_csv(season: Optional[int] = None):
    season = _season(season)
    data = _csv(season)
    name = f"ledger_{season - 1}-{str(season)[-2:]}_lock.csv"
    return Response(content=data, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Ledger-SHA256": LL.sha256(data)})


# ── live scoring (round 6 step 2: scripts/ledger_update.py, api/ledger_live.py) ─────────────────────

LIVE_UPSTREAM = ("ESPN scoreboard read nightly by scripts/ledger_update.py; odds computed with the code at the lock's "
                 "git tag from the final scores before each date")
PIVOT_COLS = ["espn_id", "game_date", "home", "away", "home_pts", "away_pts", "in_lock"]


def _pivot(df):
    """One row per scored game: result, every version's P(home wins), and when the in-season odds were logged."""
    if not len(df):
        return []
    wide = df.pivot_table(index="espn_id", columns="version", values="p", aggfunc="first")
    ins = df[df.version.isin(LV.IN_SEASON)].groupby("espn_id").agg(before_tip=("before_tip", "all"),
                                                                    logged_at=("computed_at", "min"))
    base = df.drop_duplicates("espn_id").set_index("espn_id")[PIVOT_COLS[1:]]
    out = base.join(wide).join(ins).reset_index().sort_values(["game_date", "espn_id"], ascending=[False, True])
    rows = []
    for r in out.to_dict("records"):
        rows.append({k: (None if isinstance(v, float) and np.isnan(v) else _clean(v)) for k, v in r.items()})
    return rows


def _live_payload(conn, season):
    cur = conn.cursor()
    cur.execute("SELECT first_tip_utc FROM ledger_lock WHERE season = %s", (season,))
    first_tip = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM ledger_schedule WHERE season = %s AND counted", (season,))
    in_lock = cur.fetchone()[0]
    status = {"first_tip_utc": _clean(first_tip), "games_in_lock": in_lock, "games_scheduled": in_lock, "games_final": 0,
              "games_scored": 0, "games_scored_all_versions": 0, "last_run": None, "next_date": None, "live_tables": False}
    empty = {"status": status, "metrics": [], "running": [], "calibration": {}, "tests": [], "tests_as_of": None,
             "teams": [], "teams_as_of": None, "upcoming": [], "recent": [], "min_test_games": LV.MIN_TEST_GAMES,
             "version_labels": LV.VERSION_LABELS, "pairs": [list(p) for p in LV.PAIRS]}
    if not LV.live_tables_exist(cur):
        return empty
    status["live_tables"] = True
    cur.execute("""SELECT COUNT(*) FILTER (WHERE counts), COUNT(*) FILTER (WHERE counts AND completed),
                          MIN(game_date) FILTER (WHERE counts AND NOT completed AND status NOT LIKE 'STATUS_POSTPONED%%')
                   FROM ledger_results WHERE season = %s""", (season,))
    status["games_scheduled"], status["games_final"], nd = cur.fetchone()
    status["next_date"] = _clean(nd)
    runs = _rows(cur, """SELECT run_id, started_at, finished_at, today_et, mode, espn_events, events_final, new_rows, late_rows,
                                scored_common, waiting, code_tag FROM ledger_runs WHERE season = %s
                         ORDER BY started_at DESC LIMIT 1""", (season,))
    status["last_run"] = runs[0] if runs else None

    df = LV.scored(conn, season)
    both = LV.common(df)
    status["games_scored"] = int(df[df.version.isin(LV.IN_SEASON)].espn_id.nunique())
    status["games_scored_all_versions"] = int(both.espn_id.nunique())
    status["games_before_tip"] = int(both[both.version.isin(LV.IN_SEASON)].groupby("espn_id").before_tip.all().sum()) if len(both) else 0

    cur.execute("SELECT MAX(as_of) FROM ledger_tests WHERE season = %s", (season,))
    tests_as_of = cur.fetchone()[0]
    tests = _rows(cur, """SELECT metric, model_a, model_b, variant, n, value_a, value_b, diff, ci_lo, ci_hi, p_boot, p_perm, dm_p,
                                 resamples FROM ledger_tests WHERE season = %s AND as_of = %s ORDER BY variant, model_a, model_b, metric""",
                  (season, tests_as_of)) if tests_as_of else []
    for t in tests:   # keep small differences visible
        for k in ("value_a", "value_b", "diff", "ci_lo", "ci_hi"):
            t[k] = None if t[k] is None else float(f"{t[k]:.6g}")

    cur.execute("SELECT MAX(as_of) FROM ledger_team_log WHERE season = %s", (season,))
    teams_as_of = cur.fetchone()[0]
    teams = []
    if teams_as_of:
        tl = pd.read_sql("SELECT * FROM ledger_team_log WHERE season = %s AND as_of = %s", conn, params=(season, teams_as_of))
        lk = pd.read_sql("""SELECT forecast, key AS team, conference, mean_wins, wins_p10, wins_p90 FROM ledger_forecasts
                            WHERE season = %s AND kind = 'team'""", conn, params=(season,))
        for t, g in tl.groupby("team"):
            row = {"team": t}
            for r in g.itertuples():
                row.update(games=int(r.games), wins=int(r.wins), losses=int(r.losses))
                l_ = lk[(lk.team == t) & (lk.forecast == r.forecast)].iloc[0]
                row["conference"] = l_.conference
                row[r.forecast] = {"rating": round(float(r.rating), 3), "rating_sd": round(float(r.rating_sd), 3),
                                   "exp_final_wins": round(float(r.exp_final_wins), 2), "locked_mean": round(float(l_.mean_wins), 2),
                                   "locked_p10": float(l_.wins_p10), "locked_p90": float(l_.wins_p90)}
            teams.append(row)

    up = pd.read_sql("""SELECT DISTINCT ON (g.espn_id, g.forecast) g.espn_id, g.forecast, g.game_date, g.tip_utc, g.home, g.away,
                               g.p_home, g.exp_margin, g.before_tip, g.computed_at, g.home_b2b, g.away_b2b
                        FROM ledger_game_log g JOIN ledger_results r ON r.season = g.season AND r.espn_id = g.espn_id
                                                                    AND r.game_date = g.game_date
                        WHERE g.season = %s AND NOT r.completed
                        ORDER BY g.espn_id, g.forecast, g.computed_at""", conn, params=(season,))
    upcoming = []
    for gid, g in up.groupby("espn_id"):
        f = g.iloc[0]
        row = {"espn_id": gid, "game_date": _clean(f.game_date), "tip_utc": f.tip_utc, "home": f.home, "away": f.away,
               "home_b2b": bool(f.home_b2b), "away_b2b": bool(f.away_b2b), "logged_at": _clean(g.computed_at.min()),
               "before_tip": bool(g.before_tip.all())}
        for r in g.itertuples():
            row[r.forecast] = round(float(r.p_home), 4)
        upcoming.append(row)
    upcoming.sort(key=lambda r: (r["game_date"], r["tip_utc"], r["espn_id"]))

    def rnd(rows, keys):
        return [{k: (round(v, 5) if k in keys and v is not None else v) for k, v in r.items()} for r in rows]
    return {**empty, "status": status,
            "metrics": rnd(LV.metrics(both), {"brier", "log_loss", "favourite_won"}),
            "running": rnd(LV.running(both), {"brier", "log_loss"}),
            "calibration": {v: rnd(LV.calibration(both, v), {"predicted", "actual", "ci_lo", "ci_hi"})
                            for v in LV.VERSIONS if (both.version == v).any()},
            "tests": tests, "tests_as_of": _clean(tests_as_of), "teams": teams, "teams_as_of": _clean(teams_as_of),
            "upcoming": upcoming, "recent": _pivot(df)[:15]}


@router.get("/ledger/live")
def ledger_live(season: Optional[int] = None):
    """The season scored so far (not cached: the nightly update appends to these tables)."""
    season = _season(season)
    with get_db() as conn:
        body = _live_payload(conn, season)
    return {"season": season, "season_label": f"{season - 1}-{str(season)[-2:]}", **body,
            "_source": make_source(LV.LIVE_TABLES + ["ledger_forecasts"], LIVE_UPSTREAM)}


@router.get("/ledger/live/games")
def ledger_live_games(season: Optional[int] = None, team: Optional[str] = None):
    """Every scored game with each version's P(home wins), newest first (not cached)."""
    season = _season(season)
    with get_db() as conn:
        rows = _pivot(LV.scored(conn, season)) if LV.live_tables_exist(conn.cursor()) else []
    if team:
        t = team.upper()
        rows = [r for r in rows if t in (r["home"], r["away"])]
    return {"season": season, "team": team.upper() if team else None, "games": rows,
            "_source": make_source(["ledger_results", "ledger_game_log", "ledger_forecasts"], LIVE_UPSTREAM)}
