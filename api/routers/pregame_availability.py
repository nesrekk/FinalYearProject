"""Availability-aware pre-game odds and the lineup what-if tool (Season Simulator page).

    GET /pregame/availability/model            what knowing who played is worth: the chosen rating source, the
                                               constants, coefficients, the protocol's scores with paired
                                               intervals (tune / validate / test), breakdowns, the biggest upsets
    GET /pregame/availability/games?date=      every game that day: the pre-game odds before and with who played,
                                               and the rotation players who sat
    GET /pregame/availability/game/{game_id}?out=&add=
                                               one game's rotation players (who played, who sat) and the odds with
                                               any of them taken out (out = ids who played) or put back (add = ids
                                               who sat), with an 80% range

All from scripts/build_pregame_availability.py (math in api/availability_lib.py, shared with it). The odds use
the season's own held-out lineup coefficient, so the what-if with nothing changed gives back the stored number.
Who played is known at tip-off, not at forecast time: every number here is an upper bound on what injury news
is worth. The model summary is cached per process: restart impact_api after rerunning the script.
"""

import hashlib
from datetime import date
from functools import lru_cache

import numpy as np
from fastapi import APIRouter, HTTPException, Query

import availability_lib as A
from impact_core import get_db
from source_badge import make_source

router = APIRouter()

TABLES = ["pregame_availability_odds", "pregame_availability_players", "pregame_availability_fit",
          "pregame_availability_tests", "game_pregame_odds", "player_game_lines", "projection_backtest_rows", "player_rapm"]
UPSTREAM = ("ESPN play-by-play (who played, minutes) and the stored pre-game odds; scripts/build_pregame_availability.py")
DRAWS = 4000
CAVEAT = ("Who played is known at tip-off (injury reports, the inactive list), not when a forecast is usually made; "
          "late scratches and a player hurt in the first minute count as playing. So the gain is an upper bound on "
          "what injury news is worth to this model.")
METHOD = (
    "Each team's lineup strength in a game is five times the minutes-weighted mean rating of its rotation players "
    "who played (players expected to play 10+ minutes), minutes being each one's average in his earlier games, "
    "never the minutes he actually played, and minutes they don't cover going to a replacement-level player. "
    "Ratings are fixed before the season: the Projections page's box-score projection (BPM), or last season's "
    "RAPM with prior. The change against what the team rating already knows (last season's lineup and this "
    "season's earlier lineups, blended the way the rating blends them), home minus away, is added to the pre-game "
    "model's log-odds with one fitted coefficient; the pre-game model itself is unchanged. " + CAVEAT)


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _clean(v, digits=4):
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        return None if np.isnan(v) else round(v, digits)
    if isinstance(v, date):
        return v.isoformat()
    return v


def _r(d, digits=4):
    return {k: _clean(v, digits) for k, v in d.items()}


@lru_cache(maxsize=1)
def _stored():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('public.pregame_availability_fit')")
        if cur.fetchone()[0] is None:
            raise HTTPException(status_code=503, detail="No availability data: run scripts/build_pregame_availability.py.")
        fit = _rows(cur, "SELECT kind, source, phase, season, name, value, se, n, detail FROM pregame_availability_fit")
        tests = _rows(cur, """SELECT phase, metric, model_a, model_b, seasons, n, value_a, value_b, diff, ci_lo, ci_hi,
                                     p_boot, p_perm, dm_p, resamples FROM pregame_availability_tests ORDER BY phase, metric, model_a, model_b""")
        seasons = _rows(cur, """SELECT season, COUNT(*) AS games, MIN(game_date) AS first_date, MAX(game_date) AS last_date
                                FROM pregame_availability_odds GROUP BY 1 ORDER BY 1""")
    chosen = next(f["source"] for f in fit if f["kind"] == "choice" and f["name"] == "source")
    coef = {(f["source"], f["season"]): (f["value"], f["se"]) for f in fit
            if f["kind"] == "coef" and f["phase"] == "platform" and f["name"] == "b"}
    consts = {(f["name"], f["source"]): f["value"] for f in fit if f["kind"] == "constant"}
    return {"fit": fit, "tests": tests, "chosen": chosen, "coef": coef, "consts": consts, "seasons": seasons}


def _fill(source):
    return _stored()["consts"][("replacement", source)]


@router.get("/pregame/availability/model")
def availability_model():
    st = _stored()
    fit = st["fit"]
    chosen = st["chosen"]
    pick = lambda **kw: [f for f in fit if all(f[k] == v for k, v in kw.items())]  # noqa: E731

    def phase_rows():
        out = []
        for ph in ("tune", "validate", "test"):
            base = pick(kind="metric", phase=ph, source="prior_rest", name="log_loss")[0]
            row = {"phase": ph, "n": base["n"], "base_log_loss": base["value"],
                   "base_brier": pick(kind="metric", phase=ph, source="prior_rest", name="brier")[0]["value"]}
            for src in A.RATING_SOURCES:
                ll = pick(kind="metric", phase=ph, source=src, name="log_loss")[0]
                row[f"{src}_log_loss"] = ll["value"]
                row[f"{src}_brier"] = pick(kind="metric", phase=ph, source=src, name="brier")[0]["value"]
                row[f"{src}_n"] = ll["n"]
                row[f"{src}_b"] = pick(kind="coef", phase=ph, source=src, name="b")[0]["value"] if ph != "tune" else None
                for metric in ("log_loss", "brier"):
                    t = next((t for t in st["tests"] if t["phase"] == ph and t["metric"] == metric
                              and t["model_a"] == f"avail_{src}" and t["model_b"] == "prior_rest"), None)
                    if t:
                        row[f"{src}_{metric}_test"] = _r({k: t[k] for k in ("diff", "ci_lo", "ci_hi", "p_boot", "p_perm", "dm_p", "n", "seasons")}, 5)
            sens = {}
            for src in A.RATING_SOURCES:
                t = next((t for t in st["tests"] if t["phase"] == ph and t["metric"] == "log_loss"
                          and t["model_a"] == f"avail_{src}_all" and t["model_b"] == f"avail_{src}"), None)
                if t:
                    sens[src] = _r({k: t[k] for k in ("diff", "ci_lo", "ci_hi", "p_boot")}, 5)
            t = next((t for t in st["tests"] if t["phase"] == ph and t["metric"] == "log_loss"
                      and t["model_a"] == "avail_bpm" and t["model_b"] == "avail_rapm"), None)
            row["bpm_vs_rapm"] = _r({k: t[k] for k in ("diff", "ci_lo", "ci_hi", "p_boot", "n")}, 5) if t else None
            row["all_appearances_vs_rotation"] = sens
            out.append(_r(row, 5))
        return out

    platform = {}
    for src in list(A.RATING_SOURCES) + [f"{s}_all" for s in A.RATING_SOURCES]:
        ll = pick(kind="metric", phase="platform", source=src, name="log_loss")[0]
        br = pick(kind="metric", phase="platform", source=src, name="brier")[0]
        b_all = pick(kind="coef", phase="platform", source=src, name="b_all")[0]
        platform[src] = _r({"log_loss": ll["value"], "base_log_loss": ll["detail"]["base"], "brier": br["value"],
                            "base_brier": br["detail"]["base"], "n": ll["n"], "seasons": ll["detail"].get("seasons"),
                            "b_all": b_all["value"], "b_all_se": b_all["se"],
                            "avail_sd": pick(kind="feature", phase="platform", source=src, name="avail_sd")[0]["value"],
                            "by_season": [_r({"season": f["season"], "b": f["value"], "se": f["se"]}) for f in
                                          sorted(pick(kind="coef", phase="platform", source=src, name="b"), key=lambda f: f["season"])]}, 5)
    buckets = {name: [_r({"label": f["detail"]["label"], "n": f["n"], "log_loss": f["value"], "base": f["detail"]["base"],
                          "mean_move": f["detail"].get("mean_move")}, 5) for f in pick(kind="bucket", name=name)]
               for name in ("games_played", "abs_avail")}
    checks = {f"{f['name']}|{f['source']}": _r({"value": f["value"], "n": f["n"], **(f["detail"] or {})}, 5)
              for f in pick(kind="check")}
    upsets = sorted([{**f["detail"], "season": f["season"], "with_lineups": round(f["value"], 4), "base": round(f["detail"]["base"], 4)}
                     for f in pick(kind="upset")], key=lambda d: d["rank"])
    choice = pick(kind="choice", name="source")[0]
    consts = {f"{k[0]}{'_' + k[1] if k[1] else ''}": round(v, 4) for k, v in st["consts"].items()}
    tune_label = next((f["detail"]["seasons"] for f in fit if f["kind"] == "constant" and f["detail"]), "")
    return {
        "chosen": chosen, "choice": choice["detail"], "sources": A.RATING_SOURCES, "min_season": A.MIN_SEASON,
        "constants": consts, "constants_from": tune_label, "phases": phase_rows(), "platform": platform,
        "buckets": buckets, "checks": checks, "upsets": upsets, "seasons": [_r(s) for s in st["seasons"]],
        "method": METHOD, "caveat": CAVEAT, "_source": make_source(TABLES, UPSTREAM),
    }


@router.get("/pregame/availability/games")
def availability_games(date_: date = Query(..., alias="date")):
    st = _stored()
    src = st["chosen"]
    with get_db() as conn:
        cur = conn.cursor()
        games = _rows(cur, f"""SELECT game_id, season, home, away, home_won, lineups_ok, p_base, p_{src} AS p_avail,
                                      avail_{src} AS avail FROM pregame_availability_odds WHERE game_date = %s ORDER BY game_id""",
                      (date_,))
        sat = _rows(cur, f"""SELECT game_id, team, player_name, exp_min, r_{src} AS r FROM pregame_availability_players
                             WHERE NOT played AND game_id IN (SELECT game_id FROM pregame_availability_odds WHERE game_date = %s)
                             ORDER BY game_id, team, exp_min DESC""", (date_,))
    by = {}
    for s in sat:
        by.setdefault((s["game_id"], s["team"]), []).append(_r({"name": s["player_name"], "exp_min": s["exp_min"], "r": s["r"]}, 1))
    out = [{**_r(g), "sat_home": by.get((g["game_id"], g["home"]), []), "sat_away": by.get((g["game_id"], g["away"]), [])}
           for g in games]
    return {"date": date_.isoformat(), "source": src, "games": out, "caveat": CAVEAT, "_source": make_source(TABLES, UPSTREAM)}


def _ids(text):
    if not text:
        return []
    try:
        return sorted({int(x) for x in text.split(",") if x.strip()})
    except ValueError:
        raise HTTPException(status_code=400, detail="out and add are comma-separated player ids")


@router.get("/pregame/availability/game/{game_id}")
def availability_game(game_id: str, out: str = "", add: str = "", source: str = ""):
    st = _stored()
    src = source or st["chosen"]
    if src not in A.RATING_SOURCES:
        raise HTTPException(status_code=400, detail=f"source is one of {', '.join(A.RATING_SOURCES)}")
    with get_db() as conn:
        cur = conn.cursor()
        g = _rows(cur, f"""SELECT o.game_id, o.season, o.game_date, o.home, o.away, o.home_won, o.lineups_ok, o.p_base,
                                  o.p_{src} AS p_avail, o.avail_{src} AS avail, o.s_home_{src} AS s_home,
                                  o.ref_home_{src} AS ref_home, o.s_away_{src} AS s_away, o.ref_away_{src} AS ref_away,
                                  o.unidentified_min_home, o.unidentified_min_away, p.pts_home, p.pts_away, p.exp_margin,
                                  p.home_b2b, p.away_b2b
                           FROM pregame_availability_odds o JOIN game_pregame_odds p USING (game_id)
                           WHERE o.game_id = %s""", (game_id,))
        if not g:
            raise HTTPException(status_code=404, detail="No such regular-season game from 2020-21 on (game ids look like 0022500641).")
        g = g[0]
        players = _rows(cur, f"""SELECT team, player_id, player_name, played, exp_min, exp_from, minutes,
                                        r_{src} AS r, sd_{src} AS sd, rated_{src} AS rated
                                 FROM pregame_availability_players WHERE game_id = %s ORDER BY team, played DESC, exp_min DESC""",
                      (game_id,))
    if g["p_avail"] is None or not g["lineups_ok"]:
        return {"game": _r(g), "source": src, "available": False,
                "reason": ("No lineups for this game (it isn't in the ESPN play-by-play)" if not g["lineups_ok"] else
                           f"The {src.upper()} source starts in {A.MIN_SEASON[src] - 1}-{str(A.MIN_SEASON[src])[-2:]}."),
                "_source": make_source(TABLES, UPSTREAM)}
    b, se = st["coef"][(src, g["season"])]
    fill = _fill(src)
    out_ids, add_ids = _ids(out), _ids(add)
    played_ids = {p["player_id"] for p in players if p["played"]}
    sat_ids = {p["player_id"] for p in players if not p["played"]}
    bad = [i for i in out_ids if i not in played_ids] + [i for i in add_ids if i not in sat_ids]
    if bad:
        raise HTTPException(status_code=400, detail=f"Not in this game's rotation lists: {bad} (out = players who played, add = players who sat).")
    side = np.array([p["team"] == g["home"] for p in players])
    r = np.array([p["r"] for p in players], float)
    sd = np.array([p["sd"] for p in players], float)
    m = np.array([p["exp_min"] for p in players], float)
    actual = np.array([p["played"] for p in players])
    pid = np.array([p["player_id"] for p in players])
    mine = (actual & ~np.isin(pid, out_ids)) | np.isin(pid, add_ids)

    def s_of(sel):
        return [float(A.strength_from_sums((r * m)[sel & h].sum(), m[sel & h].sum(), fill)) for h in (side, ~side)]

    s_act = s_of(actual)
    if abs(s_act[0] - g["s_home"]) > 1e-6 or abs(s_act[1] - g["s_away"]) > 1e-6:
        raise HTTPException(status_code=500, detail="Stored lineup strength doesn't reproduce: rerun scripts/build_pregame_availability.py.")
    s_new = s_of(mine)
    avail_new = g["avail"] + (s_new[0] - s_act[0]) - (s_new[1] - s_act[1])
    p_new = float(A.predict(g["p_base"], avail_new, b))
    # 80% ranges: the lineup coefficient and the players' ratings, drawn together
    seed = int(hashlib.md5(f"{game_id}|{src}|{out_ids}|{add_ids}".encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    bd = rng.normal(b, se, DRAWS)
    rd = r[None, :] + sd[None, :] * rng.standard_normal((DRAWS, len(r)))
    d_act = np.stack([A.strength_from_sums((rd * m)[:, actual & h].sum(axis=1), m[actual & h].sum(), fill) for h in (side, ~side)])
    d_new = np.stack([A.strength_from_sums((rd * m)[:, mine & h].sum(axis=1), m[mine & h].sum(), fill) for h in (side, ~side)])
    av_draw = g["avail"] + (d_new[0] - d_act[0]) - (d_new[1] - d_act[1])
    p_draw = A.predict(g["p_base"], av_draw, bd)
    p_act_draw = A.predict(g["p_base"], g["avail"], bd)

    # each player's own effect: the odds with just him toggled
    def toggled(i):
        sel = actual.copy()
        sel[i] = not sel[i]
        s2 = s_of(sel)
        return float(A.predict(g["p_base"], g["avail"] + (s2[0] - s_act[0]) - (s2[1] - s_act[1]), b))

    roster = []
    for i, p in enumerate(players):
        roster.append(_r({**p, "home": bool(side[i]), "in_lineup": bool(mine[i]), "p_if_toggled": toggled(i)}, 4))
    home_share = float(m[mine & side].sum()), float(m[mine & ~side].sum())
    return {
        "game": _r(g), "source": src, "source_label": A.RATING_SOURCES[src], "available": True,
        "b": round(b, 5), "b_se": round(se, 5), "fill": round(fill, 3),
        "actual": {"p": round(g["p_avail"], 4), "p10": round(float(np.percentile(p_act_draw, 10)), 4),
                   "p90": round(float(np.percentile(p_act_draw, 90)), 4), "s_home": round(s_act[0], 3), "s_away": round(s_act[1], 3)},
        "whatif": {"p": round(p_new, 4), "p10": round(float(np.percentile(p_draw, 10)), 4), "p90": round(float(np.percentile(p_draw, 90)), 4),
                   "s_home": round(s_new[0], 3), "s_away": round(s_new[1], 3), "avail": round(float(avail_new), 3),
                   "minutes_home": round(home_share[0], 1), "minutes_away": round(home_share[1], 1),
                   "out": out_ids, "add": add_ids, "changed": bool(out_ids or add_ids), "draws": DRAWS, "seed": seed},
        "roster": roster, "caveat": CAVEAT,
        "range_note": ("80% range from the uncertainty in the lineup coefficient and in the players' ratings (drawn "
                       f"{DRAWS:,} times); the team ratings' own uncertainty is the same for any lineup and is not in it."),
        "_source": make_source(TABLES, UPSTREAM),
    }
