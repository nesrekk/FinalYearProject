"""RAPM (regularized adjusted plus-minus) from the play-by-play stints.

    GET /rapm/options                         versions, seasons per version, floors, method
    GET /rapm?version=&season=&min_poss=&team= the leaderboard for one version and season, with the
                                              fit summary, the lambda curve and that season's validation
    GET /rapm/validation                      every validation row and fit summary (Methodology)
    GET /rapm?version=tracker&kind=           the Rating Tracker (round 6 step 7): the same shape, rows from
                                              player_rating_tracker (kind filtered | smoothed), its fit, drift
                                              profile and validation in `tracker`

Reads `player_rapm`, `rapm_fits`, `rapm_lambda_cv` and `rapm_validation`
(scripts/build_rapm.py): ridge regression on every tracked five-man stint
2020-21 to 2025-26, offence and defence separately, lambda by game-grouped
cross-validation, standard errors from a game bootstrap; and, for the
tracker version, `player_rating_tracker`, `rating_tracker_fit`,
`rating_tracker_curve`, `rating_tracker_validation`
(scripts/build_rating_tracker.py): a state-space RAPM whose ratings carry
across seasons. Everything is cached per process: restart impact_api after
rerunning either script.
"""

import json
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, HTTPException

from impact_core import get_db
from source_badge import make_source

router = APIRouter()

TABLES = ["player_rapm", "rapm_fits", "rapm_lambda_cv", "rapm_validation", "lineup_stints", "player_season_stats"]
TRACKER_TABLES = ["player_rating_tracker", "rating_tracker_fit", "rating_tracker_curve", "rating_tracker_validation",
                  "rapm_validation", "lineup_stints", "player_season_stats"]
UPSTREAM = "ESPN play-by-play (pbp_events) rebuilt into five-man stints by scripts/build_lineup_stints.py, fitted by scripts/build_rapm.py"
TRACKER_UPSTREAM = ("ESPN play-by-play (pbp_events) rebuilt into five-man stints by scripts/build_lineup_stints.py, "
                    "filtered and smoothed across seasons by scripts/build_rating_tracker.py (scripts/rating_tracker_lib.py)")
TRACKER_KINDS = ("filtered", "smoothed")
MAX_MIN_POSS = 20000

VERSIONS = {
    "single": {"label": "One season", "short": "Single season",
               "blurb": "Each season on its own, shrunk toward the league average (zero)."},
    "multi": {"label": "Three seasons", "short": "3-season window",
              "blurb": "A three-season window ending in the season: one rating per player over the window, one intercept per season. Steadier, slower to notice change."},
    "prior": {"label": "One season, BPM prior", "short": "BPM prior",
              "blurb": "Each season on its own, shrunk toward a scaled Basketball-Reference BPM (offence toward OBPM, defence toward DBPM) instead of zero; the scale and the shrinkage are both chosen by cross-validation."},
    "tracker": {"label": "Rating Tracker", "short": "Tracker",
                "blurb": "Ratings that carry across seasons: each player's offence and defence rating is a hidden state that drifts between seasons, updated by each season's stints with that season's BPM read as a noisy measurement. How much of last season to keep, how much BPM is worth and how far a newcomer may start from average are chosen on 2020-21 to 2023-24 by next-season prediction and held fixed. \"As of then\" uses nothing after the season; \"with hindsight\" smooths every season's games back through the career."},
}

MODELS = {
    "rapm_single": "RAPM, one season",
    "rapm_multi": "RAPM, three seasons",
    "rapm_prior": "RAPM, BPM prior",
    "rapm_tracker": "Rating Tracker (as of then)",
    "rapm_tracker_smoothed": "Rating Tracker (with hindsight)",
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


@lru_cache(maxsize=1)
def _tracker_fit():
    """The one rating_tracker_fit row ({} when the tracker isn't built), with the per-season summary parsed."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('rating_tracker_fit')")
        if cur.fetchone()[0] is None:
            return {}
        cols = ["version", "seasons_from", "seasons_to", "estimated_on", "criterion", "lambda0", "lambda_q", "lambda_b",
                "prior_scale", "phi", "tune_rmse", "tune_games", "sigma2", "neg2ll", "nuisance_var", "newcomer_sd", "drift_sd",
                "bpm_sd", "evaluations", "converged", "quick", "starts", "ml_estimate", "loo_pairs", "ridge_check", "seasons",
                "players", "qualified_poss", "runtime_s"]
        cur.execute(f"SELECT {', '.join(cols)} FROM rating_tracker_fit WHERE version = 'tracker'")
        row = cur.fetchone()
        if row is None:
            return {}
        d = dict(zip(cols, row))
        for k in ("starts", "ml_estimate", "loo_pairs", "ridge_check", "seasons"):
            if isinstance(d[k], str):
                d[k] = json.loads(d[k])
        d["seasons"] = {int(k): v for k, v in (d["seasons"] or {}).items()}
        d["season_list"] = sorted(d["seasons"])
        return d


@lru_cache(maxsize=1)
def _tracker_curve():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('rating_tracker_curve')")
        if cur.fetchone()[0] is None:
            return []
        cur.execute("SELECT lambda_q, next_rmse, next_games, neg2ll, sigma2, chosen FROM rating_tracker_curve ORDER BY lambda_q")
        return [{"lambda_q": a, "next_rmse": b, "next_games": c, "neg2ll": d, "sigma2": e, "chosen": f} for a, b, c, d, e, f in cur.fetchall()]


@lru_cache(maxsize=1)
def _tracker_validation():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('rating_tracker_validation')")
        if cur.fetchone()[0] is None:
            return []
        cur.execute("""SELECT test, season, model, fit_seasons, rows, games, players, poss, coverage, coverage_all10,
                              scale_fit, stint_rmse, game_rmse, game_corr, corr
                       FROM rating_tracker_validation ORDER BY test, season, game_rmse NULLS LAST, corr DESC NULLS LAST, model""")
        keys = ["test", "season", "model", "fit_seasons", "rows", "games", "players", "poss", "coverage", "coverage_all10",
                "scale_fit", "stint_rmse", "game_rmse", "game_corr", "corr"]
        return [dict(zip(keys, r), model_label=MODELS.get(r[2], r[2])) for r in cur.fetchall()]


def _seasons_by_version(fits):
    out = {v: [] for v in VERSIONS}
    for (version, season) in sorted(fits):
        out[version].append(season)
    tf = _tracker_fit()
    out["tracker"] = tf["season_list"] if tf else []
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
        raise HTTPException(status_code=404, detail=f"No {VERSIONS[version]['label']} fits on file"
                            + (" (run scripts/build_rating_tracker.py)." if version == "tracker" else "."))
    season = season or seasons[-1]
    if season not in seasons:
        raise HTTPException(status_code=404, detail=(
            f"No {VERSIONS[version]['label']} RAPM for {season - 1}-{str(season)[-2:]}; "
            f"on file: {', '.join(f'{s - 1}-{str(s)[-2:]}' for s in seasons)}."))
    if version == "tracker":
        return season, _tracker_fit_summary(season), seasons
    return season, fits[(version, season)], seasons


def _tracker_fit_summary(season):
    """A fit dict for one season in rapm_fits' shape (what the page reads for every version), plus the tracker's own fields."""
    tf = _tracker_fit()
    per = tf["seasons"][season]
    return {
        "version": "tracker", "season": season, "seasons_from": tf["seasons_from"], "seasons_to": season,
        "games": per["games"], "stints": per["stints"], "rows": per["rows"], "players": per["players"], "poss": per["poss"],
        "lambda": tf["lambda0"], "prior_scale": tf["prior_scale"], "lambda_rule": "tune_next_rmse", "cv_folds": None,
        "cv_rmse": None, "cv_rmse_zero": None, "cv_best_lambda": None, "cv_best_scale": None, "cv_best_rmse": None,
        "intercepts": {season: per["intercept"]}, "home_coef": per["home_coef"], "home_edge_per_100": per["home_edge_per_100"],
        "bootstraps": None, "qualified_poss": tf["qualified_poss"], "qualified": per["qualified"], "players_with_prior": per["n_bpm"],
        "newcomers": per["newcomers"],
        "tracker": {k: tf[k] for k in ("estimated_on", "criterion", "lambda0", "lambda_q", "lambda_b", "prior_scale", "phi", "tune_rmse",
                                       "tune_games", "sigma2", "newcomer_sd", "drift_sd", "bpm_sd", "evaluations", "converged", "quick",
                                       "ml_estimate", "loo_pairs", "ridge_check", "players", "seasons_from", "seasons_to")},
    }


def _validation_for(season, tracker=False):
    """rapm_validation's rows for a season; for the tracker version its held-out row joins rapm_validation's (same
    folds, same rows) and the next-season and year-to-year panels are rating_tracker_validation's (every model
    rescored on the same rows and convention)."""
    rows = _validation()
    out = {
        "held_out_games": [r for r in rows if r["test"] == "held_out_games" and r["season"] == season],
        "next_season": [r for r in rows if r["test"] == "next_season" and r["season"] == season],
        "next_season_from_this": [r for r in rows if r["test"] == "next_season" and r["season"] == season + 1],
        "year_to_year": [r for r in rows if r["test"] == "year_to_year" and r["season"] == season],
    }
    if tracker:
        tv = _tracker_validation()
        held = [r for r in tv if r["test"] == "held_out_games" and r["season"] == season]
        out["held_out_games"] = sorted(held + out["held_out_games"], key=lambda r: (r["game_rmse"] is None, r["game_rmse"] or 0))
        out["next_season"] = [r for r in tv if r["test"] == "next_season" and r["season"] == season]
        out["next_season_from_this"] = [r for r in tv if r["test"] == "next_season" and r["season"] == season + 1]
        out["year_to_year"] = [r for r in tv if r["test"] == "year_to_year" and r["season"] == season]
    return out


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
        "tracker": {"kinds": list(TRACKER_KINDS), "method": TRACKER_METHOD, "built": bool(_tracker_fit())},
        "_source": make_source(TABLES, UPSTREAM),
    }


TRACKER_ROW_COLS = ["player_id", "teams", "games", "stints", "minutes", "poss_off", "poss_def", "poss", "orapm", "drapm", "rapm",
                    "orapm_sd", "drapm_sd", "rapm_sd", "rapm_ci_low", "rapm_ci_high", "prior_o", "prior_d", "obpm", "dbpm", "bpm",
                    "carried_o", "carried_d", "carried", "first_season", "seasons_seen"]

TRACKER_METHOD = (
    "The same stint rows as RAPM (every tracked five-man stint, each side's points per 100 possessions, weighted by "
    "possessions, +1 for the five on offence, -1 for the five on defence, an intercept and a home term a season), but each "
    "player's offence and defence rating is a hidden state that carries across seasons: between seasons it is multiplied by "
    "phi and gains drift variance, a newcomer starts at average with the ordinary ridge spread, and each season that "
    "player's Basketball-Reference OBPM and DBPM (times a scale) are read as noisy measurements of the state before the "
    "season's stints are seen. A Kalman filter in information form (the full covariance of ~900 players' two ratings) "
    "gives the posterior after each season ('as of then'); the Rauch-Tung-Striebel smoother runs back through the career "
    "('with hindsight'). The five hyperparameters (newcomer spread, drift, BPM weight, BPM scale, carry-over phi) were "
    "chosen on 2020-21 to 2023-24 by the pooled next-season game-margin RMSE over the three tune pairs, the criterion "
    "every other RAPM version's lambda was chosen by under the paper's protocol, and held fixed after; the state-space "
    "model's own maximum-likelihood estimate is stored beside them but not used, because a season's BPM already carries "
    "that season's point differential and the likelihood trusts it more than any out-of-sample test does. Standard "
    "deviations come from the posterior covariance (stint noise variance over possessions, estimated from the stints); "
    "the 95% interval is +-1.96 sd. 'Carried in' is what the tracker expected before the season started."
)


@router.get("/rapm")
def rapm(version: str = "single", season: Optional[int] = None, min_poss: float = -1, team: Optional[str] = None,
         kind: str = "filtered"):
    if version == "tracker" and kind not in TRACKER_KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of {', '.join(TRACKER_KINDS)}.")
    season, fit, seasons = _pick(version, season)
    floor = fit["qualified_poss"] if min_poss < 0 else max(0.0, min(float(min_poss), MAX_MIN_POSS))
    team = team.upper() if team else None
    tracker = version == "tracker"
    with get_db() as conn:
        cur = conn.cursor()
        if tracker:
            cur.execute(f"""SELECT {', '.join(TRACKER_ROW_COLS)} FROM player_rating_tracker WHERE kind = %s AND season = %s
                            ORDER BY rapm DESC, poss DESC""", (kind, season))
        else:
            cur.execute(f"""SELECT {', '.join(ROW_COLS)} FROM player_rapm WHERE version = %s AND season = %s
                            ORDER BY rapm DESC, poss DESC""", (version, season))
        raw = cur.fetchall()
        names = _names(cur, {r[0] for r in raw})
        teams = sorted({t for r in raw for t in (r[1] or "").split("/") if t})
    if team and team not in teams:
        raise HTTPException(status_code=404, detail=f"No {team} player in the {season - 1}-{str(season)[-2:]} stints.")
    rows = []
    for r in raw:
        d = _round(dict(zip(TRACKER_ROW_COLS if tracker else ROW_COLS, r)))
        if tracker:
            # the page reads the one-season names for the error columns: sd -> se
            d["orapm_se"], d["drapm_se"], d["rapm_se"] = d["orapm_sd"], d["drapm_sd"], d["rapm_sd"]
        d["player_name"] = names.get(d["player_id"])
        d["qualified"] = (d["poss"] or 0) >= floor
        d["ci_excludes_zero"] = None if d["rapm_ci_low"] is None else bool(d["rapm_ci_low"] > 0 or d["rapm_ci_high"] < 0)
        d["team_list"] = [t for t in (d["teams"] or "").split("/") if t]
        rows.append(d)
    if team:
        rows = [r for r in rows if team in r["team_list"]]
    # Ranks among the qualified, league-wide (a team filter keeps the league rank).
    qualified = [r for r in rows if r["qualified"]] if not team else None
    league_q = [r for r in _all_qualified(version, season, floor, kind if tracker else "")] if team else qualified
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
        "lambda_curve": [] if tracker else _curves().get((version, season), []),
        "validation": _validation_for(season, tracker=tracker),
        "players": rows,
        "method": TRACKER_METHOD if tracker else METHOD,
        "tracker": {"kind": kind, "kinds": list(TRACKER_KINDS), "fit": fit["tracker"], "curve": _tracker_curve(),
                    "n_carried": sum(1 for r in rows if r["seasons_seen"] > 1),
                    "n_newcomers": sum(1 for r in rows if r["seasons_seen"] == 1)} if tracker else None,
        "_source": make_source(TRACKER_TABLES if tracker else TABLES, TRACKER_UPSTREAM if tracker else UPSTREAM),
    }


@lru_cache(maxsize=64)
def _all_qualified(version, season, floor, kind=""):
    """League-wide qualified rows (id, rapm, orapm, drapm, bpm, interval) for ranks under a team filter."""
    with get_db() as conn:
        cur = conn.cursor()
        if version == "tracker":
            cur.execute("""SELECT player_id, rapm, orapm, drapm, bpm, rapm_ci_low, rapm_ci_high FROM player_rating_tracker
                           WHERE kind = %s AND season = %s AND poss >= %s""", (kind, season, floor))
        else:
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
        "tracker": {"fit": _tracker_fit() or None, "validation": _tracker_validation(), "curve": _tracker_curve()},
        "models": MODELS,
        "_source": make_source(TABLES + ["player_rating_tracker", "rating_tracker_fit", "rating_tracker_validation"], UPSTREAM),
    }
