"""Lineup Predictor: the "try a lineup" panel on Teams > Rotations.

    GET /lineup-predictor/options                     seasons, teams, the model's held-out scores and tests
    GET /lineup-predictor/team?team=&season=          the team-season's players and its most-used lineups,
                                                      predicted (at their first game) vs what they did
    GET /lineup-predictor/predict?team=&season=&ids=  any five of the team's players: the prediction before
                                                      the season and with the whole season so far, its
                                                      parts, an 80% range, and the real record if it played

Reads the tables of scripts/build_lineup_predictor.py; the model math is
api/lineup_predictor_lib.py (shared with the script). Everything is cached
per process: restart impact_api after rerunning the script.
"""

import json
from functools import lru_cache
from typing import Optional

import numpy as np
from fastapi import APIRouter, HTTPException

import lineup_predictor_lib as LP
from impact_core import get_db
from lineups_lib import season_label
from source_badge import make_source

router = APIRouter()

TABLES = ["lineup_predictor_units", "lineup_predictor_players", "lineup_predictor_teams", "lineup_predictor_fit",
          "lineup_predictor_metrics", "lineup_predictor_tests"]
UPSTREAM = "ESPN play-by-play: possessions and five-man stints (build_possessions.py, build_lineup_stints.py)"
Z80 = 1.2815515655446004
SHOW_LINEUPS = 15
PANEL_POSS = 100              # "what you'd see over 100 possessions a side"

METHOD = (
    "A lineup is one five-man combination on one team in one season. Its net rating is points per 100 possessions "
    "scored minus allowed, over the counted possessions (round 6's possessions table) that began with those five on "
    "the floor. The prediction uses only what was known before the lineup's first game: each player's rating fixed "
    "before the season (the rating source chosen on the tune seasons), their Gravity and roles the season before, "
    "their projected usage, and, for the 'season so far' version, the team's and the five players' on-court net "
    "rating in earlier games, shrunk toward zero. The weights are a possession-weighted ridge regression chosen and "
    "fitted on 2021-22 to 2023-24, checked on 2024-25 and 2025-26 (round 5's protocol). Most lineups play a handful "
    "of possessions, so their record is mostly noise; scores are given as the share of the real (noise-free) spread "
    "between lineups the model explains."
)


def _q(sql, params=()):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.lineup_predictor_fit')")
        if cur.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="The Lineup Predictor hasn't been built yet: run scripts/build_lineup_predictor.py.")
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@lru_cache(maxsize=1)
def _fit():
    rows = _q("SELECT fit_on, model, name, value, detail FROM lineup_predictor_fit")
    fits, consts, hyper, cv, meta = {}, {}, {}, {}, {}
    for r in rows:
        name = r["name"]
        if name == "meta":
            meta = r["detail"]
        elif name.startswith("const:"):
            consts[name[6:]] = r["value"] if r["value"] is not None else (r["detail"] or {}).get("value")
        elif name == "cv":
            cv[r["model"]] = {"cv_wmse": r["value"], **(r["detail"] or {})}
        elif not r["fit_on"]:
            hyper.setdefault(r["model"], {})[name] = r["value"] if name != "source" else r["detail"]["source"]
        else:
            f = fits.setdefault((r["fit_on"], r["model"]), {"intercept": 0.0, "coef": {}})
            if name == "intercept":
                f["intercept"] = r["value"]
            else:
                f["coef"][name.split(":", 1)[1]] = r["value"]
    return fits, consts, hyper, cv, meta


@lru_cache(maxsize=1)
def _summary():
    metrics = _q("""SELECT model, phase, subset, seasons, n, poss, true_rmse, r2_true, wmse, noise, true_var
                    FROM lineup_predictor_metrics ORDER BY phase, subset, model""")
    tests = _q("""SELECT phase, metric, model_a, model_b, variant, seasons, n, n_clusters, value_a, value_b, diff, ci_lo, ci_hi,
                         p_boot, p_perm FROM lineup_predictor_tests ORDER BY phase, variant, metric, model_a, model_b""")
    teams = _q("SELECT season, team, games, last_date FROM lineup_predictor_teams ORDER BY season, team")
    return metrics, tests, teams


@lru_cache(maxsize=256)
def _players(season, team):
    return _q("""SELECT player_id, player_name, bpm, bpm_known, rapm, rapm_known, tracker, tracker_known, gravity, gravity_known,
                        usage, usage_known, family, po, pf, pd, pa
                 FROM lineup_predictor_players WHERE season = %s AND team = %s ORDER BY (po + pd) DESC, player_id""", (season, team))


@lru_cache(maxsize=256)
def _team_row(season, team):
    r = _q("SELECT games, last_date, po, pf, pd, pa FROM lineup_predictor_teams WHERE season = %s AND team = %s", (season, team))
    return r[0] if r else None


def _check(team, season):
    team = (team or "").upper()
    row = _team_row(season, team)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No lineups on file for {team} in {season_label(season)} "
                                                    "(the Lineup Predictor covers 2021-22 to 2025-26).")
    return team, row


def _f(v, nd=2):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else round(float(v), nd)


@router.get("/lineup-predictor/options")
def options():
    fits, consts, hyper, cv, meta = _fit()
    metrics, tests, teams = _summary()
    by_season = {}
    for t in teams:
        by_season.setdefault(str(t["season"]), []).append(t["team"])
    seasons = sorted(int(s) for s in by_season)
    return {
        "seasons": seasons,
        "teams": by_season,
        "default_season": seasons[-1],
        "source": consts.get("source"),
        "source_label": LP.RATING_SOURCES.get(consts.get("source")),
        "source_short": LP.RATING_SHORT.get(consts.get("source")),
        "later_after": int(consts.get("later_after", 20)),
        "hyper": hyper,
        "noise_check": {"units": consts.get("noise_check_units"), "split_half": consts.get("noise_check_split_cov"),
                        "model": consts.get("noise_check_model_var")},
        "metrics": [{**m, "true_rmse": _f(m["true_rmse"]), "r2_true": _f(m["r2_true"], 4)} for m in metrics],
        "tests": tests,
        "models": LP.MODEL_LABELS,
        "features": LP.FEATURE_LABELS,
        "protocol": {k: meta.get(k) for k in ("tune", "validate", "test", "units", "resamples", "built")},
        "method": METHOD,
        "_source": make_source(TABLES, UPSTREAM),
    }


def _player_out(p, src):
    return {"player_id": p["player_id"], "player_name": p["player_name"], "poss": _f((p["po"] + p["pd"]) / 2, 0),
            "rating": _f(p[src]), "rating_known": p[f"{src}_known"], "gravity": _f(p["gravity"]), "gravity_known": p["gravity_known"],
            "usage": _f(p["usage"], 3), "usage_known": p["usage_known"], "role": p["family"],
            "on_net": _f(LP.net_rating(p["pf"], p["po"], p["pa"], p["pd"]) if p["po"] and p["pd"] else None, 1)}


@router.get("/lineup-predictor/team")
def team_view(team: str, season: int):
    team, trow = _check(team, season)
    _, consts, _, _, _ = _fit()
    src = consts["source"]
    players = _players(season, team)
    names = {p["player_id"]: p["player_name"] for p in players}
    units = _q("""SELECT player_ids, first_date, first_game_no, later, games, po, pd, net, noise, pred_fit, pred_full, pred_sum
                  FROM lineup_predictor_units WHERE season = %s AND team = %s ORDER BY (po + pd) DESC, player_ids LIMIT %s""",
               (season, team, SHOW_LINEUPS))
    n_units = _q("SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE later) AS later FROM lineup_predictor_units WHERE season = %s AND team = %s",
                 (season, team))[0]
    return {
        "team": team, "season": season, "season_label": season_label(season), "games": trow["games"],
        "last_date": str(trow["last_date"]),
        "team_net": _f(LP.net_rating(trow["pf"], trow["po"], trow["pa"], trow["pd"]), 1),
        "players": [_player_out(p, src) for p in players],
        "lineups": [{"player_ids": u["player_ids"], "names": [names.get(i) for i in u["player_ids"]],
                     "first_date": str(u["first_date"]), "first_game_no": u["first_game_no"], "later": u["later"],
                     "games": u["games"], "poss": _f((u["po"] + u["pd"]) / 2, 0), "net": _f(u["net"], 1),
                     "noise_sd": _f(np.sqrt(u["noise"]), 1), "pred_fit": _f(u["pred_fit"], 1), "pred_full": _f(u["pred_full"], 1),
                     "pred_sum": _f(u["pred_sum"], 1)} for u in units],
        "n_lineups": n_units["n"], "n_later": n_units["later"],
        "_source": make_source(TABLES, UPSTREAM),
    }


def _apply(fit, feats, x):
    return fit["intercept"] + sum(fit["coef"][f] * x[f] for f in feats)


@router.get("/lineup-predictor/predict")
def predict(team: str, season: int, ids: str):
    team, trow = _check(team, season)
    try:
        pid = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="ids must be five comma-separated player ids.")
    if len(pid) != 5 or len(set(pid)) != 5:
        raise HTTPException(status_code=400, detail="Pick five different players.")
    fits, consts, hyper, _, _ = _fit()
    src = consts["source"]
    players = {p["player_id"]: p for p in _players(season, team)}
    missing = [i for i in pid if i not in players]
    if missing:
        raise HTTPException(status_code=404, detail=f"Player id(s) {missing} weren't on the floor for {team} in {season_label(season)}.")
    five = [players[i] for i in pid]
    x = LP.fit_features([p[src] for p in five], [p["gravity"] for p in five], [p["family"] for p in five], [p["usage"] for p in five])
    k = hyper["full"]["k"]
    tn = (trow["po"] + trow["pd"]) / 2
    x["td_team"] = float(LP.shrink(LP.net_rating(trow["pf"], trow["po"], trow["pa"], trow["pd"]), tn, k))
    x["td_on"] = float(np.mean([LP.shrink(LP.net_rating(p["pf"], p["po"], p["pa"], p["pd"]), (p["po"] + p["pd"]) / 2, k) for p in five]))
    fit_fit, fit_full, fit_scaled = fits[("app", "fit")], fits[("app", "full")], fits[("app", f"scaled_{src}")]
    pre = _apply(fit_fit, LP.MODELS["fit"], x)
    now = _apply(fit_full, LP.MODELS["full"], x)
    s2 = consts.get(f"s2_{season}") or consts.get("s2_2026")
    noise100 = 1e4 * s2 * (2.0 / PANEL_POSS)

    def band(pred, model):
        sd = consts[f"true_resid_sd_{model}"]
        return {"pred": _f(pred, 1), "lo": _f(pred - Z80 * sd, 1), "hi": _f(pred + Z80 * sd, 1),
                "obs_lo": _f(pred - Z80 * np.sqrt(sd ** 2 + noise100), 1), "obs_hi": _f(pred + Z80 * np.sqrt(sd ** 2 + noise100), 1)}

    parts = [{"feature": f, "label": LP.FEATURE_LABELS[f], "value": _f(x[f], 3), "coef": _f(fit_fit["coef"][f], 3),
              "contribution": _f(fit_fit["coef"][f] * x[f], 2)} for f in LP.MODELS["fit"]]
    parts_now = [{"feature": f, "label": LP.FEATURE_LABELS[f], "value": _f(x[f], 3), "coef": _f(fit_full["coef"][f], 3),
                  "contribution": _f(fit_full["coef"][f] * x[f], 2)} for f in ("td_team", "td_on")]
    key = sorted(pid)
    real = _q("""SELECT first_date, first_game_no, later, games, po, pd, net, noise, pred_fit, pred_full, phase
                 FROM lineup_predictor_units WHERE season = %s AND team = %s AND player_ids = %s::int[]""", (season, team, key))
    actual = None
    if real:
        r = real[0]
        actual = {"first_date": str(r["first_date"]), "first_game_no": r["first_game_no"], "later": r["later"], "games": r["games"],
                  "poss": _f((r["po"] + r["pd"]) / 2, 0), "net": _f(r["net"], 1), "noise_sd": _f(np.sqrt(r["noise"]), 1),
                  "pred_fit_then": _f(r["pred_fit"], 1), "pred_full_then": _f(r["pred_full"], 1), "phase": r["phase"]}
    return {
        "team": team, "season": season, "season_label": season_label(season), "last_date": str(trow["last_date"]),
        "games": trow["games"], "players": [_player_out(p, src) for p in five],
        "sum": _f(x["r_sum"], 1), "scaled": _f(_apply(fit_scaled, ["r_sum"], x), 1),
        "intercept": _f(fit_fit["intercept"], 2), "parts": parts, "parts_now": parts_now, "k": k,
        "preseason": band(pre, "fit"), "season_so_far": band(now, "full"), "panel_poss": PANEL_POSS,
        "actual": actual,
        "source": src, "source_label": LP.RATING_SOURCES[src],
        "_source": make_source(TABLES, UPSTREAM),
    }
