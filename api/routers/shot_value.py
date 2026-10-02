"""
Shot Value Added (scripts/build_shot_value.py, model in scripts/shot_value_lib.py; round 6 step 8).

    GET /shots/shot-value/options                  seasons, the skill model's hyperparameters, the shot-level
                                                   validation, year-to-year correlations, method
    GET /shots/shot-value?season=&min_fga=         one season's players: points split into what the shots were
                                                   worth, the shooter's skill as known before each game, and what he
                                                   made beyond it; skills carried into the season
    GET /shots/shot-value/player/{player_id}       one player's seasons and his skill track (carried in / after)

Every number is read from shot_value_added, shot_value_states, shot_value_fit and
shot_value_validation; skills are stored on the log-odds scale and shown here as
percentage points at the season's league make rate for the class. Cached per
process: restart impact_api after rerunning build_shot_value.py.
"""

import math
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Query

from impact_core import get_db
from source_badge import make_source

router = APIRouter()

CLASSES = ("rim", "mid", "three", "ft")
CLASS_LABELS = {"rim": "At the rim", "mid": "Other twos", "three": "Threes", "ft": "Free throws"}
TABLES = ["shot_value_added", "shot_value_states", "shot_value_fit", "shot_value_validation", "shot_value_shots"]
UPSTREAM = ("stats.nba.com shot locations (player_shots) and ESPN play-by-play free throws (player_game_lines), "
            "priced by scripts/build_shot_value.py")
NOT_ON_FILE = ("No shot has a closest-defender distance or a shot type (catch-and-shoot vs. pull-up), so a shooter's "
               "skill also carries the defence he usually faces and the shots he creates; and the league's level so "
               "far lags a level that drifts during a season, so the whole league can beat or miss its forecast.")
METHOD = (
    "Every regular-season attempt from 2020-21 on is priced twice before its game. Blind: what an average shooter "
    "makes from that spot - a gradient-boosting location model fitted only on earlier seasons (and on none of the "
    "shooter's fold of players), moved by the league's level so far this season; a free throw at last season's league "
    "rate, moved the same way. Aware: the same plus the shooter's own skill, a hidden number per player for four "
    "kinds of attempt (at the rim, other twos, threes, free throws) that starts at his debut near a rookie's average, "
    "carries a share of itself from season to season, and is updated by every attempt game by game - so an "
    "attempt's price uses only games before its own. How fast skill decays, how far a rookie may start from average "
    "and how much a season may move it were estimated once, by empirical Bayes on 2010-11 to 2019-20, before any "
    "priced season. Skill points = aware minus blind (what his record said his shooting adds); beyond = points "
    "scored minus aware (what he made beyond that this season, luck included); total = points minus blind (points "
    "above an average shooter taking the same shots). Shot Value Added = skill points on field goals and free throws."
)
SORTS = ("sva", "skill_pts", "above_pts", "total_pts", "ft_skill_pts", "beyond", "fga", "pts", "pre_rim", "pre_mid",
         "pre_three", "pre_ft")


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def _expit(x):
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p):
    return math.log(p / (1.0 - p))


def pp(theta, base):
    """A log-odds skill as percentage points at a make rate."""
    if theta is None or base is None:
        return None
    return round(100.0 * (_expit(_logit(base) + theta) - base), 2)


def _r(v, d=2):
    return None if v is None else round(float(v), d)


@lru_cache(maxsize=1)
def _built():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('shot_value_added'), to_regclass('shot_value_validation')")
        return all(cur.fetchone())


def _need():
    if not _built():
        raise HTTPException(status_code=503, detail="Shot Value Added isn't built yet (run scripts/build_shot_value.py).")


@lru_cache(maxsize=1)
def _validation():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT scope, seasons, cls, price, n, log_loss, brier, mean_price, make_rate, d_log_loss_vs_lf,
                              ci_lo, ci_hi, p_boot, games, corr FROM shot_value_validation""")
        cols = ["scope", "seasons", "cls", "price", "n", "log_loss", "brier", "mean_price", "make_rate", "d_log_loss_vs_lf",
                "ci_lo", "ci_hi", "p_boot", "games", "corr"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@lru_cache(maxsize=1)
def _rates():
    """{(season, class): league make rate} from the validation rows (the lf price's make rate)."""
    out = {}
    for r in _validation():
        if r["price"] == "lf" and r["cls"] in CLASSES and len(r["seasons"]) == 7:      # one season, e.g. '2024-25'
            out[(int(r["seasons"][:4]) + 1, r["cls"])] = float(r["make_rate"])
    return out


@lru_cache(maxsize=1)
def _fit():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT cls, mu0, v0, phi, q, n2ll, delta_var, estimated_on, attempts, players, detail FROM shot_value_fit")
        out = {}
        for c, mu0, v0, phi, q, n2ll, dv, on, att, pl, detail in cur.fetchall():
            out[c] = {"mu0": mu0, "v0": v0, "phi": phi, "q": q, "n2ll": n2ll, "delta_var": dv, "estimated_on": on,
                      "attempts": att, "players": pl, "detail": detail}
        return out


@lru_cache(maxsize=1)
def _seasons():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT season FROM shot_value_added ORDER BY 1")
        return [r[0] for r in cur.fetchall()]


def _params_readable(fit, rates):
    """Each class's hyperparameters in plain units: percentage points at the latest season's league rate."""
    last = max(s for s, _ in rates) if rates else None
    out = []
    for c in CLASSES:
        f = fit.get(c)
        if not f:
            continue
        base = rates.get((last, c))
        stat_sd = math.sqrt(f["q"] / (1 - f["phi"] ** 2)) if f["phi"] < 1 else None
        out.append({
            "cls": c, "label": CLASS_LABELS[c], "base_rate": base,
            "rookie_mean_pp": pp(f["mu0"], base), "rookie_sd_pp": pp(math.sqrt(f["v0"]), base),
            "carry": _r(f["phi"], 3), "drift_sd_pp": pp(math.sqrt(f["q"]), base),
            "veteran_sd_pp": pp(stat_sd, base) if stat_sd is not None else None,
            "raw": {k: f[k] for k in ("mu0", "v0", "phi", "q", "delta_var")},
            "estimated_on": f["estimated_on"], "attempts": f["attempts"], "players": f["players"],
            "starts": (f["detail"] or {}).get("starts"),
        })
    return out


@router.get("/shots/shot-value/options")
def shot_value_options():
    _need()
    val = _validation()
    fit = _fit()
    shot_rows = [r for r in val if r["cls"] != "yty"]
    yty = [r for r in val if r["cls"] == "yty"]
    return {
        "seasons": _seasons(),
        "min_fga": 200,
        "classes": [{"id": c, "label": CLASS_LABELS[c]} for c in CLASSES],
        "params": _params_readable(fit, _rates()),
        "models": (fit.get("models") or {}).get("detail"),
        "validation": [{k: (_r(v, 6) if isinstance(v, float) else v) for k, v in r.items()} for r in shot_rows],
        "year_to_year": [{"pair": r["scope"], "season": r["seasons"], "part": r["price"], "n": r["n"], "r": _r(r["corr"], 3)} for r in yty],
        "method": METHOD,
        "not_on_file": NOT_ON_FILE,
        "_source": make_source(TABLES, UPSTREAM),
    }


ROW_COLS = ["player_id", "season", "player_name", "team_abbreviation", "fga", "fgm", "fg3a", "fg3m", "pts", "x_blind", "x_aware",
            "skill_pts", "above_pts", "total_pts", "fta", "ftm", "ft_x_blind", "ft_x_aware", "ft_skill_pts", "ft_above_pts",
            "ft_total_pts", "sva", "beyond", "att_rim", "made_rim", "att_mid", "made_mid", "att_three", "made_three",
            "pre_rim", "pre_rim_sd", "post_rim", "post_rim_sd", "pre_mid", "pre_mid_sd", "post_mid", "post_mid_sd",
            "pre_three", "pre_three_sd", "post_three", "post_three_sd", "pre_ft", "pre_ft_sd", "post_ft", "post_ft_sd",
            "qualified", "rank_sva", "rank_total", "rank_beyond", "pool"]


def _shape(d, rates):
    s = d["season"]
    shots = d["fga"] + 0.44 * d["fta"]
    for k in ("x_blind", "x_aware", "skill_pts", "above_pts", "total_pts", "ft_x_blind", "ft_x_aware", "ft_skill_pts", "ft_above_pts",
              "ft_total_pts", "sva", "beyond"):
        d[k] = _r(d[k], 1)
    d["sva_per100"] = _r(100 * d["sva"] / shots, 2) if shots else None
    d["beyond_per100"] = _r(100 * d["beyond"] / shots, 2) if shots else None
    for c in CLASSES:
        base = rates.get((s, c))
        for when in ("pre", "post"):
            th, sd = d.pop(f"{when}_{c}"), d.pop(f"{when}_{c}_sd")
            d[f"{when}_{c}"] = pp(th, base)
            d[f"{when}_{c}_lo"] = pp(None if th is None else th - 1.96 * sd, base)
            d[f"{when}_{c}_hi"] = pp(None if th is None else th + 1.96 * sd, base)
    for c in ("rim", "mid", "three"):
        a = d[f"att_{c}"]
        d[f"pct_{c}"] = _r(d[f"made_{c}"] / a, 4) if a else None
    d["ft_pct"] = _r(d["ftm"] / d["fta"], 4) if d["fta"] else None
    return d


@lru_cache(maxsize=16)
def _season_rows(season):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(ROW_COLS)} FROM shot_value_added WHERE season = %s ORDER BY sva DESC", (season,))
        return [dict(zip(ROW_COLS, r)) for r in cur.fetchall()]


def _league(rows, season, rates):
    """The season's league totals: the gap between what the league made and its own forecast, per 100 attempts."""
    fga = sum(r["fga"] for r in rows)
    fta = sum(r["fta"] for r in rows)
    return {
        "fga": fga, "fta": fta, "pts": sum(r["pts"] for r in rows), "ftm": sum(r["ftm"] for r in rows),
        "above_per100_fga": _r(100 * sum(r["above_pts"] for r in rows) / fga, 2) if fga else None,
        "skill_per100_fga": _r(100 * sum(r["skill_pts"] for r in rows) / fga, 2) if fga else None,
        "ft_above_per100_fta": _r(100 * sum(r["ft_above_pts"] for r in rows) / fta, 2) if fta else None,
        "rates": {c: rates.get((season, c)) for c in CLASSES},
    }


@router.get("/shots/shot-value")
def shot_value(season: int | None = None, min_fga: int = Query(200, ge=0, le=2000), team: str | None = None):
    _need()
    seasons = _seasons()
    season = season or seasons[-1]
    if season not in seasons:
        raise HTTPException(status_code=404, detail=f"No Shot Value Added for {label(season)}; on file: "
                            + ", ".join(label(s) for s in seasons) + ".")
    rates = _rates()
    raw = _season_rows(season)
    league = _league(raw, season, rates)
    rows = [_shape(dict(r), rates) for r in raw if r["fga"] >= min_fga]
    teams = sorted({r["team_abbreviation"] for r in rows if r["team_abbreviation"]})
    if team:
        team = team.upper()
        rows = [r for r in rows if r["team_abbreviation"] == team]
    yty = [r for r in _validation() if r["cls"] == "yty"]
    return {
        "season": season, "seasons": seasons, "min_fga": min_fga, "team": team, "teams": teams,
        "league": league, "players": rows, "n_qualified": sum(1 for r in raw if r["qualified"]),
        "year_to_year": [{"pair": r["scope"], "part": r["price"], "n": r["n"], "r": _r(r["corr"], 3)} for r in yty
                         if r["seasons"] == label(season)],
        "not_on_file": NOT_ON_FILE,
        "_source": make_source(TABLES, UPSTREAM),
    }


@router.get("/shots/shot-value/player/{player_id}")
def shot_value_player(player_id: int):
    _need()
    rates = _rates()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(ROW_COLS)} FROM shot_value_added WHERE player_id = %s ORDER BY season", (player_id,))
        rows = [_shape(dict(zip(ROW_COLS, r)), rates) for r in cur.fetchall()]
    if not rows:
        raise HTTPException(status_code=404, detail="No attempts on file for this player from 2020-21 on.")
    track = []
    for r in rows:
        for c in CLASSES:
            track.append({"season": r["season"], "cls": c, "pre": r[f"pre_{c}"], "pre_lo": r[f"pre_{c}_lo"], "pre_hi": r[f"pre_{c}_hi"],
                          "post": r[f"post_{c}"], "post_lo": r[f"post_{c}_lo"], "post_hi": r[f"post_{c}_hi"],
                          "att": r[f"att_{c}"] if c != "ft" else r["fta"]})
    return {"player_id": player_id, "player_name": rows[-1]["player_name"], "seasons": rows, "track": track,
            "classes": [{"id": c, "label": CLASS_LABELS[c]} for c in CLASSES], "not_on_file": NOT_ON_FILE,
            "_source": make_source(TABLES, UPSTREAM)}
