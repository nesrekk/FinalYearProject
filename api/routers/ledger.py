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

Written once by scripts/ledger_lock.py; the rules are in api/ledger_lib.py. Every read is cached per
process (the lock never changes; restart impact_api after a --relock).
"""

import json
from datetime import date, datetime
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

import ledger_lib as LL
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
