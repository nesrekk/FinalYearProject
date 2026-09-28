"""RAPM (regularized adjusted plus-minus) from the play-by-play stints.

    GET /rapm/options                         versions, seasons per version, floors, method
    GET /rapm?version=&season=&min_poss=&team= the leaderboard for one version and season, with the
                                              fit summary, the lambda curve and that season's validation
    GET /rapm/validation                      every validation row and fit summary (Methodology)

Reads `player_rapm`, `rapm_fits`, `rapm_lambda_cv` and `rapm_validation`
(scripts/build_rapm.py): ridge regression on every tracked five-man stint
2020-21 to 2025-26, offence and defence separately, lambda by game-grouped
cross-validation, standard errors from a game bootstrap. Everything is
cached per process: restart impact_api after rerunning the script.
"""

import json
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from source_badge import make_source

router = APIRouter()

TABLES = ["player_rapm", "rapm_fits", "rapm_lambda_cv", "rapm_validation", "lineup_stints", "player_season_stats"]
UPSTREAM = "ESPN play-by-play (pbp_events) rebuilt into five-man stints by scripts/build_lineup_stints.py, fitted by scripts/build_rapm.py"
MAX_MIN_POSS = 20000

VERSIONS = {
    "single": {"label": "One season", "short": "Single season",
               "blurb": "Each season on its own, shrunk toward the league average (zero)."},
    "multi": {"label": "Three seasons", "short": "3-season window",
              "blurb": "A three-season window ending in the season: one rating per player over the window, one intercept per season. Steadier, slower to notice change."},
    "prior": {"label": "One season, BPM prior", "short": "BPM prior",
              "blurb": "Each season on its own, shrunk toward a scaled Basketball-Reference BPM (offence toward OBPM, defence toward DBPM) instead of zero; the scale and the shrinkage are both chosen by cross-validation."},
}

MODELS = {
    "rapm_single": "RAPM, one season",
    "rapm_multi": "RAPM, three seasons",
    "rapm_prior": "RAPM, BPM prior",
    "bpm": "BPM (Basketball-Reference)",
    "onoff": "On/off net (from the same stints)",
    "zero": "Everyone average (intercept and home only)",
    "orapm": "Offensive RAPM",
    "drapm": "Defensive RAPM",
}

METHOD = (
    "One row per stint per side from every tracked five-man stint (lineup_stints): the offence's points per 100 "
    "possessions in the stint, weighted by those possessions, regressed on +1 for each of the five offensive players "
    "(their offence columns), -1 for each defender (their defence columns), a season intercept and a home term. "
    "Ridge regression shrinks every player toward zero (the league average) with a strength chosen by 5-fold "
    "cross-validation grouped by game; the curve is flat, so the exact choice matters little. ORAPM is the offence "
    "coefficient (points per 100 possessions his team scores more with him, the other nine held constant), DRAPM the "
    "defence coefficient (points per 100 the opponent scores less), RAPM their sum. Standard errors come from "
    "refitting on games resampled with replacement. Stints from games whose play-by-play didn't reconcile, and "
    "stints with a player ESPN gives no id to, are left out (the stints page lists them). Possessions are the side's "
    "own FGA + 0.44 FTA - OREB + TOV, so the scale sits about 3 points under NBA.com's; differences don't depend on it."
)


@lru_cache(maxsize=1)
def _fits():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('rapm_fits')")
        if cur.fetchone()[0] is None:
            return {}
        cur.execute("""SELECT version, season, seasons_from, seasons_to, games, stints, rows, players, poss, lambda,
                              prior_scale, lambda_rule, cv_folds, cv_rmse, cv_rmse_zero, cv_best_lambda, cv_best_scale,
                              cv_best_rmse, intercepts, home_coef, home_edge_per_100, bootstraps, qualified_poss,
                              qualified, players_with_prior
                       FROM rapm_fits ORDER BY version, season""")
        keys = ["version", "season", "seasons_from", "seasons_to", "games", "stints", "rows", "players", "poss", "lambda",
                "prior_scale", "lambda_rule", "cv_folds", "cv_rmse", "cv_rmse_zero", "cv_best_lambda", "cv_best_scale",
                "cv_best_rmse", "intercepts", "home_coef", "home_edge_per_100", "bootstraps", "qualified_poss",
                "qualified", "players_with_prior"]
        out = {}
        for r in cur.fetchall():
            d = dict(zip(keys, r))
            if isinstance(d["intercepts"], str):
                d["intercepts"] = json.loads(d["intercepts"])
            for k in ("lambda", "cv_best_lambda"):
                if d[k] is not None and d[k] == int(d[k]):
                    d[k] = int(d[k])
            out[(d["version"], d["season"])] = d
        return out


@lru_cache(maxsize=1)
def _validation():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('rapm_validation')")
        if cur.fetchone()[0] is None:
            return []
        cur.execute("""SELECT test, season, model, fit_seasons, rows, games, players, poss, coverage, coverage_all10,
                              scale_fit, stint_rmse, game_rmse, game_corr, corr
                       FROM rapm_validation ORDER BY test, season, game_rmse NULLS LAST, model""")
        keys = ["test", "season", "model", "fit_seasons", "rows", "games", "players", "poss", "coverage", "coverage_all10",
                "scale_fit", "stint_rmse", "game_rmse", "game_corr", "corr"]
        return [dict(zip(keys, r), model_label=MODELS.get(r[2], r[2])) for r in cur.fetchall()]


@lru_cache(maxsize=1)
def _curves():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('rapm_lambda_cv')")
        if cur.fetchone()[0] is None:
            return {}
        cur.execute("SELECT version, season, lambda, prior_scale, cv_rmse FROM rapm_lambda_cv ORDER BY version, season, prior_scale, lambda")
        out = {}
        for version, season, lam, scale, rmse in cur.fetchall():
            out.setdefault((version, season), []).append({"lambda": int(lam), "prior_scale": scale, "cv_rmse": rmse})
        return out


def _seasons_by_version(fits):
    out = {v: [] for v in VERSIONS}
    for (version, season) in sorted(fits):
        out[version].append(season)
    return out


def _names(cur, player_ids):
    if not player_ids:
        return {}
    cur.execute("""SELECT DISTINCT ON (player_id) player_id, player_name FROM player_season_stats
                   WHERE player_id = ANY(%s) ORDER BY player_id, season DESC""", (list(player_ids),))
    return dict(cur.fetchall())


ROW_COLS = ["player_id", "teams", "games", "stints", "minutes", "poss_off", "poss_def", "poss", "orapm", "drapm", "rapm",
            "orapm_se", "drapm_se", "rapm_se", "rapm_ci_low", "rapm_ci_high", "prior_o", "prior_d", "obpm", "dbpm", "bpm"]


def _round(d):
    for k, v in d.items():
        if isinstance(v, float):
            d[k] = round(v, 3)
    return d


def _pick(version, season):
    fits = _fits()
    if not fits:
        raise HTTPException(status_code=503, detail="No RAPM data: run scripts/build_rapm.py.")
    if version not in VERSIONS:
        raise HTTPException(status_code=400, detail=f"version must be one of {', '.join(VERSIONS)}.")
    seasons = _seasons_by_version(fits)[version]
    if not seasons:
        raise HTTPException(status_code=404, detail=f"No {VERSIONS[version]['label']} fits on file.")
    season = season or seasons[-1]
    if season not in seasons:
        raise HTTPException(status_code=404, detail=(
            f"No {VERSIONS[version]['label']} RAPM for {season - 1}-{str(season)[-2:]}; "
            f"on file: {', '.join(f'{s - 1}-{str(s)[-2:]}' for s in seasons)}."))
    return season, fits[(version, season)], seasons


def _validation_for(season):
    rows = _validation()
    return {
        "held_out_games": [r for r in rows if r["test"] == "held_out_games" and r["season"] == season],
        "next_season": [r for r in rows if r["test"] == "next_season" and r["season"] == season],
        "next_season_from_this": [r for r in rows if r["test"] == "next_season" and r["season"] == season + 1],
        "year_to_year": [r for r in rows if r["test"] == "year_to_year" and r["season"] == season],
    }


@router.get("/rapm/options")
def rapm_options():
    fits = _fits()
    if not fits:
        raise HTTPException(status_code=503, detail="No RAPM data: run scripts/build_rapm.py.")
    any_fit = next(iter(fits.values()))
    return {
        "versions": [{"id": k, **v, "seasons": _seasons_by_version(fits)[k]} for k, v in VERSIONS.items()],
        "qualified_poss": any_fit["qualified_poss"],
        "bootstraps": any_fit["bootstraps"],
        "cv_folds": any_fit["cv_folds"],
        "models": MODELS,
        "method": METHOD,
        "_source": make_source(TABLES, UPSTREAM),
    }


@router.get("/rapm")
def rapm(version: str = "single", season: Optional[int] = None, min_poss: float = -1, team: Optional[str] = None):
    season, fit, seasons = _pick(version, season)
    floor = fit["qualified_poss"] if min_poss < 0 else max(0.0, min(float(min_poss), MAX_MIN_POSS))
    team = team.upper() if team else None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"""SELECT {', '.join(ROW_COLS)} FROM player_rapm WHERE version = %s AND season = %s
                        ORDER BY rapm DESC, poss DESC""", (version, season))
        raw = cur.fetchall()
        names = _names(cur, {r[0] for r in raw})
        teams = sorted({t for r in raw for t in (r[1] or "").split("/") if t})
    if team and team not in teams:
        raise HTTPException(status_code=404, detail=f"No {team} player in the {season - 1}-{str(season)[-2:]} stints.")
    rows = []
    for r in raw:
        d = _round(dict(zip(ROW_COLS, r)))
        d["player_name"] = names.get(d["player_id"])
        d["qualified"] = (d["poss"] or 0) >= floor
        d["ci_excludes_zero"] = None if d["rapm_ci_low"] is None else bool(d["rapm_ci_low"] > 0 or d["rapm_ci_high"] < 0)
        d["team_list"] = [t for t in (d["teams"] or "").split("/") if t]
        rows.append(d)
    if team:
        rows = [r for r in rows if team in r["team_list"]]
    # Ranks among the qualified, league-wide (a team filter keeps the league rank).
    qualified = [r for r in rows if r["qualified"]] if not team else None
    league_q = [r for r in _all_qualified(version, season, floor)] if team else qualified
    for key in ("rapm", "orapm", "drapm"):
        order = sorted(league_q, key=lambda r: -r[key])
        rank = {r["player_id"]: i + 1 for i, r in enumerate(order)}
        for r in rows:
            r[f"{key}_rank"] = rank.get(r["player_id"])
    n_q = len(league_q)
    excl = sum(1 for r in league_q if r["ci_excludes_zero"])
    bpm_pairs = [(r["rapm"], r["bpm"]) for r in league_q if r["bpm"] is not None]
    corr_bpm = None
    if len(bpm_pairs) > 10:
        import numpy as np
        a = np.array(bpm_pairs, float)
        corr_bpm = round(float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]), 3)
    return {
        "version": version, "version_label": VERSIONS[version]["label"], "season": season,
        "seasons_available": seasons, "versions": {k: v["label"] for k, v in VERSIONS.items()},
        "teams": teams, "team": team, "min_poss": floor, "default_min_poss": fit["qualified_poss"],
        "fit": fit,
        "noise": {"qualified": n_q, "ci_excludes_zero": excl, "expected_by_chance": round(0.05 * n_q, 1),
                  "corr_with_bpm": corr_bpm, "bpm_pairs": len(bpm_pairs)},
        "lambda_curve": _curves().get((version, season), []),
        "validation": _validation_for(season),
        "players": rows,
        "method": METHOD,
        "_source": make_source(TABLES, UPSTREAM),
    }


@lru_cache(maxsize=64)
def _all_qualified(version, season, floor):
    """League-wide qualified rows (id, rapm, orapm, drapm, bpm, interval) for ranks under a team filter."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT player_id, rapm, orapm, drapm, bpm, rapm_ci_low, rapm_ci_high FROM player_rapm
                       WHERE version = %s AND season = %s AND poss >= %s""", (version, season, floor))
        return tuple({"player_id": a, "rapm": b, "orapm": c, "drapm": d, "bpm": e,
                      "ci_excludes_zero": None if f is None else bool(f > 0 or g < 0)} for a, b, c, d, e, f, g in cur.fetchall())


@router.get("/rapm/validation")
def rapm_validation():
    fits = _fits()
    if not fits:
        raise HTTPException(status_code=503, detail="No RAPM data: run scripts/build_rapm.py.")
    return {
        "fits": sorted(fits.values(), key=lambda f: (f["version"], f["season"])),
        "validation": _validation(),
        "models": MODELS,
        "_source": make_source(TABLES, UPSTREAM),
    }
