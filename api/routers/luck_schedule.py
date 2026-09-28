"""Luck & schedule strength for every team-season 2009-10 to 2025-26.

    GET /teams/luck-schedule?season=&as_of=      every team: record, expected wins from
                                                 points, luck, close games, SRS and SOS;
                                                 with as_of, the same as of that morning plus
                                                 the schedule left and a projection
    GET /teams/luck-schedule/model               the expected-win curves, the checks
                                                 (does luck carry over? mid-season
                                                 prediction, Basketball-Reference SRS)
    GET /teams/luck-schedule/team/{abbr}         one franchise across every season

Reads `team_luck_schedule`, `luck_schedule_seasons`, `luck_model_fit` and
`luck_schedule_validation` (scripts/build_luck_schedule.py), and `game_scores`
(real final scores, scripts/fetch_game_scores.py) for the as-of view, which
recomputes ratings live with the same functions as the script (api/luck_lib.py).
Cached per process: restart impact_api after rerunning either script.
"""

from datetime import date
from functools import lru_cache
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException

from impact_core import get_db
from luck_lib import FRANCHISE, GAMES_SQL, as_of as as_of_table, prepare
from source_badge import make_source

router = APIRouter()

TABLES = ["team_luck_schedule", "luck_schedule_seasons", "luck_model_fit", "luck_schedule_validation", "game_scores"]
UPSTREAM = "ESPN scoreboard final scores (scripts/fetch_game_scores.py)"

METHOD = (
    "Every regular-season game 2009-10 to 2025-26 with its real final score (the NBA Cup final, which doesn't count "
    "in the standings, is left out). Expected wins: the win% a team's points scored and allowed usually produce, "
    "Pythagorean with the exponent fitted on all 510 team-seasons (it beat a straight line and a normal curve in "
    "margin per game on leave-one-season-out error). Luck = actual wins minus expected wins. Close games: final "
    "margin of 3 or fewer (or 5 or fewer) points. SRS: least-squares ratings where each game's margin = home court + "
    "team rating - opponent rating, ratings summing to zero, so a rating is points per game better than an average "
    "team on a neutral floor; SOS = the average rating of the opponents a team actually played. Neutral-site games "
    "(international games, NBA Cup semifinals, the 2020 Orlando restart) get no home court. The as-of view refits "
    "the ratings on games played before that date, pulls them toward average by the share of their spread that's "
    "still noise, and turns each remaining game into a win chance with the season's home court and game-to-game "
    "spread."
)

ROW_COLS = ["season", "team_abbreviation", "franchise", "games", "wins", "losses", "win_pct", "pts_for",
            "pts_against", "mov", "exp_win_pct", "exp_wins", "luck", "luck_per82", "luck_rank", "close3_w",
            "close3_l", "close5_w", "close5_l", "ot_w", "ot_l", "srs", "sos", "srs_rank"]
SEASON_COLS = ["season", "games", "scheduled", "complete", "hca", "sigma", "close3_share", "close5_share",
               "ot_share", "first_date", "last_date", "halfway_date"]


def _round(d):
    out = {}
    for k, v in d.items():
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float):
            v = round(v, 4 if k.endswith("_pct") or k.endswith("_share") or k == "shrink" else 3)
        elif isinstance(v, date):
            v = v.isoformat()
        out[k] = v
    return out


@lru_cache(maxsize=1)
def _stored():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(ROW_COLS)} FROM team_luck_schedule ORDER BY season, srs_rank")
        rows = [dict(zip(ROW_COLS, r)) for r in cur.fetchall()]
        cur.execute(f"SELECT {', '.join(SEASON_COLS)} FROM luck_schedule_seasons ORDER BY season")
        seasons = [dict(zip(SEASON_COLS, r)) for r in cur.fetchall()]
        cur.execute("SELECT method, param, loso_rmse_wins, loso_mae_wins, r2, n, chosen FROM luck_model_fit")
        fits = [dict(zip(["method", "param", "loso_rmse_wins", "loso_mae_wins", "r2", "n", "chosen"], r))
                for r in cur.fetchall()]
        cur.execute("SELECT metric, value, n, lo, hi, note FROM luck_schedule_validation")
        checks = {r[0]: dict(zip(["value", "n", "lo", "hi", "note"], r[1:])) for r in cur.fetchall()}
    if not rows:
        raise HTTPException(status_code=503, detail="No luck & schedule data — run scripts/build_luck_schedule.py.")
    return rows, seasons, fits, checks


def _chosen_fit():
    _, _, fits, _ = _stored()
    return next(f for f in fits if f["chosen"])


@lru_cache(maxsize=4)
def _season_games(season):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(GAMES_SQL.format(where="WHERE season = %s"), (season,))
        df = pd.DataFrame(cur.fetchall(), columns=[c[0] for c in cur.description])
    return prepare(df)


def _pick_season(season):
    _, seasons, _, _ = _stored()
    available = [s["season"] for s in seasons]
    season = season or available[-1]
    if season not in available:
        raise HTTPException(status_code=404, detail=(
            f"No games for {season}; scores cover {available[0]}-{available[-1]}."))
    return season, next(s for s in seasons if s["season"] == season), available


def _as_of_rows(season, info, cutoff, final_by_team):
    first, last = info["first_date"], info["last_date"]
    if cutoff <= first or cutoff > last:
        raise HTTPException(status_code=400, detail=(
            f"Pick a date after {first.isoformat()} and on or before {last.isoformat()} for {season}."))
    fit = _chosen_fit()
    tbl, meta = as_of_table(_season_games(season), cutoff, fit["method"], fit["param"])
    if tbl is None:
        raise HTTPException(status_code=400, detail="Too few games played by that date to rate the teams (need 30).")
    tbl = tbl.reset_index().rename(columns={"index": "team_abbreviation"})
    tbl["luck"] = tbl.wins - tbl.exp_win_pct * tbl.games
    tbl["srs_rank"] = tbl.srs.rank(ascending=False, method="min").astype(int)
    tbl["rem_sos_rank"] = tbl.rem_sos.rank(ascending=False, method="min")
    rows = []
    for r in tbl.to_dict("records"):
        final = final_by_team.get(r["team_abbreviation"], {})
        r["final_wins"] = final.get("wins")
        r["final_losses"] = final.get("losses")
        r["win_pct"] = r["wins"] / r["games"] if r["games"] else None
        r["exp_wins"] = r["exp_win_pct"] * r["games"]
        if pd.isna(r.get("rem_sos")):
            r["rem_sos"] = None
            r["rem_sos_rank"] = None
        rows.append(_round(r))
    rows.sort(key=lambda r: r["srs_rank"])
    return rows, _round(meta)


@router.get("/teams/luck-schedule")
def luck_schedule(season: Optional[int] = None, as_of: Optional[date] = None):
    stored, _, _, checks = _stored()
    season, info, available = _pick_season(season)
    final = [r for r in stored if r["season"] == season]
    final_by_team = {r["team_abbreviation"]: r for r in final}
    fit = _chosen_fit()
    meta = None
    if as_of:
        rows, meta = _as_of_rows(season, info, as_of, final_by_team)
    else:
        rows = [_round(r) for r in final]
    luck_abs = [abs(r["luck"]) for r in rows if r["luck"] is not None]
    return {
        "season": season,
        "seasons_available": available,
        "as_of": as_of.isoformat() if as_of else None,
        "season_info": _round(info),
        "as_of_info": meta,
        "fit": _round(fit),
        "luck_carryover": _round(checks.get("luck_next_luck_r", {})),
        "summary": {
            "teams": len(rows),
            "luck_within_3": sum(1 for x in luck_abs if x <= 3),
            "luck_over_5": sum(1 for x in luck_abs if x > 5),
        },
        "teams": rows,
        "method": METHOD,
        "_source": make_source(TABLES, UPSTREAM),
    }


@lru_cache(maxsize=1)
def _model():
    rows, seasons, fits, checks = _stored()
    points = [{"season": r["season"], "team": r["team_abbreviation"], "mov": round(r["mov"], 2),
               "win_pct": round(r["win_pct"], 4), "exp_win_pct": round(r["exp_win_pct"], 4),
               "games": r["games"]} for r in rows]
    return {
        "fits": [_round(f) for f in sorted(fits, key=lambda f: f["loso_rmse_wins"])],
        "checks": {k: _round(v) for k, v in checks.items()},
        "seasons": [_round(s) for s in seasons],
        "points": points,
    }


@router.get("/teams/luck-schedule/model")
def luck_schedule_model():
    return {**_model(), "method": METHOD, "_source": make_source(TABLES, UPSTREAM)}


@router.get("/teams/luck-schedule/team/{abbr}")
def luck_schedule_team(abbr: str):
    stored, _, _, _ = _stored()
    abbr = abbr.upper()
    franchise = FRANCHISE.get(abbr, abbr)
    rows = [_round(r) for r in stored if r["franchise"] == franchise]
    if not rows:
        raise HTTPException(status_code=404, detail=f"No seasons for {abbr}.")
    luck = [r["luck"] for r in rows]
    return {
        "franchise": franchise,
        "abbreviations": sorted({r["team_abbreviation"] for r in rows}),
        "seasons": rows,
        "total_luck": round(sum(luck), 2),
        "method": METHOD,
        "_source": make_source(TABLES, UPSTREAM),
    }
