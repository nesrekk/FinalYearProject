"""
build_lineup_predictor.py
=========================
Lineup Predictor (round 6, step 9): predict the net rating of a five-man
unit from its players, using only what was known before the unit's first
game, and score it on the possessions the unit then played. The model math
lives in api/lineup_predictor_lib.py (shared with the "try a lineup" panel
on Teams > Rotations).

Units and the outcome
---------------------
A unit is one five-man combination on one team in one season, 2021-22 to
2025-26 (2020-21 has no RAPM or Rating Tracker season before it). Its
possessions are the counted possessions of the `possessions` table (round 6
step 3) that began while those five were on the floor (the stint at the
possession's start, `stint_no`), in games whose possessions reconcile and
stints are tracked (`possessions.tracked_ok`). Outcome: net rating =
100 x (points per offensive possession - points allowed per defensive
possession), points without technical free throws (`possessions.pts`).
Units with no offensive or no defensive possession are dropped (no net
rating to score). Weight: the harmonic mean of the two sides' possessions.

Noise. A unit's net rating over n possessions is mostly noise (the median
unit plays ~12). The noise variance is estimated per unit as
1e4 x s2 x (1/n_off + 1/n_def), s2 = the season's variance of points per
possession (possessions independent, equal variance: a judgment call), so
every error is reported twice: possession-weighted mean squared error
(wMSE, what the tests compare; noise cancels in a difference between two
models on the same units) and the share of the REAL spread explained,
R2_true = 1 - (wMSE - noise) / (weighted variance - noise).

Before the first game, only
---------------------------
Ratings fixed before the season (RATING_SOURCES in the lib: the BPM
projection, last season's RAPM with prior, last season's Rating Tracker),
Gravity and roles of the season before, projected usage for the season,
and for the season-to-date features the team's and the five players'
possessions in games on dates before the unit's first game (on-court for
this team only). Population constants (the projection's regression and age
steps) were fitted on all seasons: the same caveat as the availability
model's ratings.

Replacement values: a player without a rating (Gravity, projected usage)
gets the possession-weighted mean SAME-season value of such players on the
tune seasons (the availability model's rule).

Protocol (round 5's split, paper_eval.TUNE / VALIDATE / TEST)
-------------------------------------------------------------
tune 2021-22 to 2023-24 (2020-21 dropped: no season before for RAPM/tracker),
validate 2024-25, test 2025-26. Every choice is made on tune by 5-fold
cross-validation grouped by team-season (fold = md5 of season|team): the
rating source (the scaled sum's CV wMSE), each model's ridge penalty
(ALPHAS) and the season-to-date shrinkage K (K_GRID). Tune is scored
out-of-fold; validate with coefficients fitted on tune; test with
coefficients refitted on tune + validate (hyperparameters kept), like
paper_eval's pre-game constants. The panel uses a fit on all five seasons
(fit_on = 'app').

Headline subset: units first used after the team's 20th game ("later":
LATER_AFTER), the lineups a coach would be trying out mid-season; every
number is also stored for all units.

Tests: paired cluster bootstrap over team-seasons (units of one team share
players), with paper_tests' functions and seeds: diff in wMSE (model A -
model B, negative = A better) with a 95% interval, bootstrap p and a
sign-flip p on the clusters' summed weighted loss differentials; and the
same resamples for the difference in R2_true.

Not modelled (judgment calls): who the unit faced (the opponents' five is
known only during the game), home court, and how familiar the five are with
each other.

Noise check (stored as const:noise_check_*): each unit's possessions split
in two at random (hash of game and possession number), the covariance of the
two halves' net ratings across units estimates the real variance assuming
nothing about noise: 105.2 vs the noise model's 93.5 on the same 58,872
units, so R2_true may read ~11% high; differences in wMSE between models
don't involve the noise model at all.

Result (2026-10-02; 2025-26 test season, the 12,490 lineups first used
after game 20; R2_true with 95% intervals over team-seasons)
  sum of the five BPM projections   15.9% [6.0, 27.2]  (rescaled: -0.7 pts)
  + spacing, roles, usage           -0.2 pts [-1.9, +1.2]  (tune 0.0, validate -0.8): nothing
  + the season so far               +4.3 pts [0.7, 9.8], p 0.017 -> 19.3% [10.3, 30.9]
                                    (tune +5.4, validate +5.9; carried by the five's own
                                    on-court net, weight 1.02-1.11, not the team's)
  team's net so far alone           11.9%
  RAPM / Rating Tracker vs BPM sums within +-2 pts in every phase, none outside its interval.
Two full runs are content-identical.

Writes lineup_predictor_units, lineup_predictor_players,
lineup_predictor_teams, lineup_predictor_fit, lineup_predictor_metrics,
lineup_predictor_tests.

    cd scripts && OMP_NUM_THREADS=4 python3 build_lineup_predictor.py                  # ~40 s
    cd scripts && python3 build_lineup_predictor.py --resamples 500                    # while developing
"""

import argparse
import hashlib
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

import paper_eval as PE
import paper_tests as PT
from db_config import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
import lineup_predictor_lib as LP  # noqa: E402

warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

TUNE = tuple(s for s in PE.TUNE if s >= 2022)        # 2021-22 to 2023-24
VALIDATE, TEST = PE.VALIDATE, PE.TEST
SEASONS = TUNE + (VALIDATE, TEST)
PHASE_OF = {**{s: "tune" for s in TUNE}, VALIDATE: "validate", TEST: "test"}
SOURCES = tuple(LP.RATING_SOURCES)
ALPHAS = (0.0, 1e-4, 1e-3, 1e-2, 1e-1)
K_GRID = (250, 500, 1000, 2000, 4000, 8000)          # possessions
FOLDS = 5
LATER_AFTER = 20                                      # "later": first used after the team's 20th game
Z80 = 1.2815515655446004
SUBSETS = ("later", "all")
PAIRS = [("scaled", "sum"), ("scaled", "zero"), ("fit", "scaled"), ("full", "fit"), ("full", "scaled"),
         ("full", "team"), ("scaled", "team"), ("team", "zero"),
         ("scaled_rapm", "scaled_bpm"), ("scaled_tracker", "scaled_bpm"), ("scaled_tracker", "scaled_rapm")]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ── loading ──────────────────────────────────────────────────────────────────

LINEUP_GAMES_SQL = """
WITH p AS (
  SELECT p.season, p.game_id, g.game_date, p.offense, p.defense, p.pts,
         CASE WHEN p.off_home THEN s.home_ids ELSE s.away_ids END AS off_ids,
         CASE WHEN p.off_home THEN s.away_ids ELSE s.home_ids END AS def_ids
  FROM possessions p
  JOIN lineup_stints s ON s.game_id = p.game_id AND s.stint_no = p.stint_no
  JOIN possession_games g ON g.game_id = p.game_id
  WHERE p.tracked_ok AND p.season >= %(first)s),
sides AS (
  SELECT season, game_id, game_date, offense AS team, off_ids AS ids, pts AS pf, 0 AS pa, 1 AS po, 0 AS pd FROM p
  UNION ALL
  SELECT season, game_id, game_date, defense, def_ids, 0, pts, 0, 1 FROM p)
SELECT season, team, game_id, game_date, (SELECT array_agg(x ORDER BY x) FROM unnest(ids) x) AS ids,
       SUM(po)::int AS po, SUM(pf)::int AS pf, SUM(pd)::int AS pd, SUM(pa)::int AS pa
FROM sides GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 2, 4, 3, 5"""

GAME_NO_SQL = """
SELECT season, team, game_date, ROW_NUMBER() OVER (PARTITION BY season, team ORDER BY game_date, game_id) AS game_no
FROM (SELECT season, home_team AS team, game_date, game_id FROM lineup_stint_games
      UNION ALL SELECT season, away_team, game_date, game_id FROM lineup_stint_games) x
WHERE season >= %(first)s ORDER BY 1, 2, 3"""


def load(conn):
    first = SEASONS[0]
    lg = pd.read_sql(LINEUP_GAMES_SQL, conn, params={"first": first})
    lg["ids"] = lg.ids.map(tuple)
    assert (lg.ids.map(len) == 5).all(), "a tracked possession has five a side"
    game_no = pd.read_sql(GAME_NO_SQL, conn, params={"first": first})
    q = lambda sql: pd.read_sql(sql, conn)  # noqa: E731
    pre = {   # (season, player_id) -> value, fixed before the season
        "bpm": q("SELECT season, player_id, projection AS v FROM projection_backtest_rows WHERE stat = 'bpm'"),
        "rapm": q("SELECT season + 1 AS season, player_id, rapm::text::float8 AS v FROM player_rapm WHERE version = 'prior'"),
        "tracker": q("SELECT season + 1 AS season, player_id, rapm::text::float8 AS v FROM player_rating_tracker WHERE kind = 'filtered'"),
        "gravity": q("SELECT season + 1 AS season, player_id, gravity AS v FROM player_gravity WHERE gravity IS NOT NULL"),
        "usage": q("SELECT season, player_id, projection AS v FROM projection_backtest_rows WHERE stat = 'usg_pct'"),
    }
    same = {  # the same season's own value, for the replacement level only
        "bpm": q("SELECT season, player_id, bpm AS v FROM player_season_stats WHERE bpm IS NOT NULL"),
        "rapm": q("SELECT season, player_id, rapm::text::float8 AS v FROM player_rapm WHERE version = 'prior'"),
        "tracker": q("SELECT season, player_id, rapm::text::float8 AS v FROM player_rating_tracker WHERE kind = 'filtered'"),
        "gravity": q("SELECT season, player_id, gravity AS v FROM player_gravity WHERE gravity IS NOT NULL"),
        "usage": q("SELECT season, player_id, usg_pct AS v FROM player_season_stats WHERE usg_pct IS NOT NULL"),
    }
    roles = q("SELECT season + 1 AS season, player_id, family FROM player_roles")
    s2 = q("""SELECT season, var_pop(pts)::float8 AS s2 FROM possessions WHERE tracked_ok GROUP BY season ORDER BY season""")
    names = q("""SELECT DISTINCT ON (player_id) player_id, player_name FROM (
                     SELECT player_id, player_name, 1 AS pri, 0 AS season FROM player_bio
                     UNION ALL SELECT player_id, player_name, 2, season FROM player_season_stats) x
                 ORDER BY player_id, pri, season DESC""")
    for d in list(pre.values()) + list(same.values()):
        assert not d.duplicated(["season", "player_id"]).any()
    return lg, game_no, pre, same, roles, s2.set_index("season").s2.to_dict(), names.set_index("player_id").player_name.to_dict()


# ── per-player values and the replacement level ──────────────────────────────

def player_values(lg, pre, same, roles):
    """One row per (season, team, player) who was in a unit: pre-season values (filled), end-of-season on-court sums."""
    ex = lg.assign(player_id=lg.ids).explode("player_id")
    ex["player_id"] = ex.player_id.astype(int)
    pv = ex.groupby(["season", "team", "player_id"], as_index=False)[["po", "pf", "pd", "pa"]].sum()
    pv["poss"] = (pv.po + pv.pd) / 2.0
    consts = {}
    for key in ("bpm", "rapm", "tracker", "gravity", "usage"):
        m = pv[["season", "player_id"]].merge(pre[key], on=["season", "player_id"], how="left").v.to_numpy(float)
        known = ~np.isnan(m)
        # replacement: possession-weighted mean same-season value of the unrated, on the tune seasons
        un = pv[~known & pv.season.isin(TUNE)][["season", "player_id", "poss"]].merge(same[key], on=["season", "player_id"])
        rep = float(np.average(un.v, weights=un.poss))
        consts[f"replacement_{key}"] = rep
        consts[f"replacement_n_{key}"] = int(len(un))
        consts[f"unrated_share_{key}"] = float(pv.poss[~known & pv.season.isin(TUNE)].sum() / pv.poss[pv.season.isin(TUNE)].sum())
        pv[f"{key}_known"] = known
        pv[key] = np.where(known, m, rep)
    pv = pv.merge(roles, on=["season", "player_id"], how="left")
    return pv, consts


# ── units and their features ─────────────────────────────────────────────────

def build_units(lg, game_no, pv, s2):
    u = lg.groupby(["season", "team", "ids"], as_index=False).agg(
        first_date=("game_date", "min"), games=("game_id", "nunique"), po=("po", "sum"), pf=("pf", "sum"),
        pd=("pd", "sum"), pa=("pa", "sum"))
    n_all = len(u)
    u = u[(u.po > 0) & (u.pd > 0)].reset_index(drop=True)
    log(f"units: {n_all:,}, {n_all - len(u):,} dropped with no offensive or no defensive possession")
    u = u.merge(game_no.rename(columns={"game_date": "first_date", "game_no": "first_game_no"}),
                on=["season", "team", "first_date"], how="left")
    assert u.first_game_no.notna().all()
    u["later"] = u.first_game_no > LATER_AFTER
    u["net"] = LP.net_rating(u.pf, u.po, u.pa, u.pd)
    u["w"] = LP.harmonic_weight(u.po, u.pd)
    u["noise"] = 1e4 * u.season.map(s2) * (1.0 / u.po + 1.0 / u.pd)
    u["phase"] = u.season.map(PHASE_OF)

    # per-player pre-season values for the five
    P = np.array(u.ids.tolist())                                    # (n, 5)
    key = pv.set_index(["season", "team", "player_id"])
    idx = pd.MultiIndex.from_arrays([np.repeat(u.season.to_numpy(), 5), np.repeat(u.team.to_numpy(), 5), P.ravel()])
    vals = key.reindex(idx)
    assert vals.po.notna().all()
    get = lambda c: vals[c].to_numpy().reshape(-1, 5)  # noqa: E731
    for src in SOURCES:
        u[f"r_sum_{src}"] = get(src).astype(float).sum(1)
        u[f"unrated_{src}"] = (~get(f"{src}_known").astype(bool)).sum(1)
    fam = get("family")
    fx = [LP.fit_features(np.zeros(5), g, f, us) for g, f, us in zip(get("gravity").astype(float), fam, get("usage").astype(float))]
    fx = pd.DataFrame(fx)
    for c in ("spacing", "n_big", "n_play", "n_scorer", "n_wing", "two_bigs", "no_handler", "usg_sum"):
        u[c] = fx[c].to_numpy()
    u["unrated_gravity"] = (~get("gravity_known").astype(bool)).sum(1)
    u["no_role"] = pd.isna(fam).sum(1)

    # season to date: the team's and each player's possessions on dates before the unit's first game
    tg = lg.groupby(["season", "team", "game_date"], as_index=False)[["po", "pf", "pd", "pa"]].sum()
    tg = tg.sort_values(["season", "team", "game_date"])
    for c in ("po", "pf", "pd", "pa"):
        tg[f"b_{c}"] = tg.groupby(["season", "team"])[c].cumsum() - tg[c]
    tb = u[["season", "team", "first_date"]].merge(
        tg.rename(columns={"game_date": "first_date"}), on=["season", "team", "first_date"], how="left")
    for c in ("po", "pf", "pd", "pa"):
        u[f"team_b_{c}"] = tb[f"b_{c}"].to_numpy()
    ex = lg.assign(player_id=lg.ids).explode("player_id")
    ex["player_id"] = ex.player_id.astype(int)
    pg = ex.groupby(["season", "team", "player_id", "game_date"], as_index=False)[["po", "pf", "pd", "pa"]].sum()
    pg = pg.sort_values(["season", "team", "player_id", "game_date"])
    for c in ("po", "pf", "pd", "pa"):
        pg[f"b_{c}"] = pg.groupby(["season", "team", "player_id"])[c].cumsum() - pg[c]
    pk = pg.set_index(["season", "team", "player_id", "game_date"])
    idx = pd.MultiIndex.from_arrays([np.repeat(u.season.to_numpy(), 5), np.repeat(u.team.to_numpy(), 5), P.ravel(),
                                     np.repeat(u.first_date.to_numpy(), 5)])
    pb = pk.reindex(idx)
    assert pb.b_po.notna().all(), "every player of a unit played on its first date"
    before = {c: pb[f"b_{c}"].to_numpy(float).reshape(-1, 5) for c in ("po", "pf", "pd", "pa")}
    return u, before


def season_to_date(u, before, k):
    """(td_team, td_on) at shrinkage K."""
    tn = (u.team_b_po + u.team_b_pd).to_numpy(float) / 2.0
    tnet = LP.net_rating(u.team_b_pf, u.team_b_po, u.team_b_pa, u.team_b_pd)
    td_team = LP.shrink(tnet, tn, k)
    pn = (before["po"] + before["pd"]) / 2.0
    pnet = LP.net_rating(before["pf"], before["po"], before["pa"], before["pd"])
    td_on = LP.shrink(pnet, pn, k).mean(1)
    return td_team, td_on


SPLIT_SQL = """
WITH p AS (
  SELECT p.season, p.offense, p.defense, p.pts, abs(hashtext(p.game_id || ':' || p.poss_no)) %% 2 AS h,
         CASE WHEN p.off_home THEN s.home_ids ELSE s.away_ids END AS off_ids,
         CASE WHEN p.off_home THEN s.away_ids ELSE s.home_ids END AS def_ids
  FROM possessions p JOIN lineup_stints s ON s.game_id = p.game_id AND s.stint_no = p.stint_no
  WHERE p.tracked_ok AND p.season >= %(first)s),
sides AS (
  SELECT season, offense AS team, off_ids AS ids, h, pts AS pf, 0 AS pa, 1 AS po, 0 AS pd FROM p
  UNION ALL SELECT season, defense, def_ids, h, 0, pts, 0, 1 FROM p)
SELECT season, team, (SELECT array_agg(x ORDER BY x) FROM unnest(ids) x)::text AS ids, h,
       SUM(po)::int AS po, SUM(pf)::int AS pf, SUM(pd)::int AS pd, SUM(pa)::int AS pa
FROM sides GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4"""


def noise_check(conn, s2):
    """The noise model against a check that assumes nothing about noise: each unit's possessions split in two
    at random (hash of game and possession number); the covariance of the two halves' net ratings across units
    estimates the real variance. Units with both sides in both halves."""
    d = pd.read_sql(SPLIT_SQL, conn, params={"first": SEASONS[0]})
    w = d.pivot_table(index=["season", "team", "ids"], columns="h", values=["po", "pf", "pd", "pa"], fill_value=0)
    w = w[(w["po"][0] > 0) & (w["pd"][0] > 0) & (w["po"][1] > 0) & (w["pd"][1] > 0)]
    a = LP.net_rating(w["pf"][0], w["po"][0], w["pa"][0], w["pd"][0])
    b = LP.net_rating(w["pf"][1], w["po"][1], w["pa"][1], w["pd"][1])
    po, pd_ = (w["po"][0] + w["po"][1]).to_numpy(float), (w["pd"][0] + w["pd"][1]).to_numpy(float)
    y = LP.net_rating(w["pf"][0] + w["pf"][1], po, w["pa"][0] + w["pa"][1], pd_)
    wt = LP.harmonic_weight(po, pd_)
    s2v = np.array([s2[s] for s in w.index.get_level_values("season")])
    noise = 1e4 * s2v * (1 / po + 1 / pd_)
    ma, mb, my = (np.average(x, weights=wt) for x in (a, b, y))
    cov = float(np.average((a - ma) * (b - mb), weights=wt))
    model = float(np.average((y - my) ** 2, weights=wt) - np.average(noise, weights=wt))
    return {"noise_check_units": int(len(w)), "noise_check_split_cov": cov, "noise_check_model_var": model}


# ── fitting ──────────────────────────────────────────────────────────────────

def fold_of(u):
    return np.array([int(hashlib.md5(f"{s}|{t}".encode()).hexdigest(), 16) % FOLDS for s, t in zip(u.season, u.team)])


def design(u, feats, src, td=None):
    cols = []
    for f in feats:
        if f == "r_sum":
            cols.append(u[f"r_sum_{src}"].to_numpy(float))
        elif f == "td_team":
            cols.append(td[0])
        elif f == "td_on":
            cols.append(td[1])
        else:
            cols.append(u[f].to_numpy(float))
    return np.column_stack(cols) if cols else np.zeros((len(u), 0))


def wmse(y, p, w):
    return float(np.sum(w * (y - p) ** 2) / np.sum(w))


def oof(X, y, w, folds, alpha):
    pred = np.empty(len(y))
    for f in range(FOLDS):
        tr = folds != f
        pred[~tr] = LP.predict(LP.fit_ridge(X[tr], y[tr], w[tr], alpha), X[~tr])
    return pred


def choose(u, feats, src, before, folds, ks=(None,)):
    """(alpha, K, cv wMSE, candidates) by grouped CV on tune."""
    t = (u.phase == "tune").to_numpy()
    y, w = u.net.to_numpy(float)[t], u.w.to_numpy(float)[t]
    cands = []
    for k in ks:
        td = season_to_date(u, before, k) if k is not None else None
        X = design(u, feats, src, td)[t]
        for a in ALPHAS:
            cands.append((wmse(y, oof(X, y, w, folds[t], a), w), a, k))
    best = min(cands, key=lambda c: (round(c[0], 9), -c[1], c[2] or 0))   # ties: more penalty, smaller K
    return best[1], best[2], best[0], [{"alpha": a, "k": k, "cv_wmse": round(m, 4)} for m, a, k in cands]


def score_model(u, feats, src, alpha, k, before, folds):
    """Protocol predictions + the validate / test / app fits."""
    td = season_to_date(u, before, k) if k is not None else None
    X = design(u, feats, src, td)
    y, w = u.net.to_numpy(float), u.w.to_numpy(float)
    ph = u.phase.to_numpy()
    pred = np.empty(len(u))
    t = ph == "tune"
    pred[t] = oof(X[t], y[t], w[t], folds[t], alpha)
    fits = {"tune": LP.fit_ridge(X[t], y[t], w[t], alpha)}
    v = ph == "validate"
    pred[v] = LP.predict(fits["tune"], X[v])
    tv = t | v
    fits["tune_validate"] = LP.fit_ridge(X[tv], y[tv], w[tv], alpha)
    s = ph == "test"
    pred[s] = LP.predict(fits["tune_validate"], X[s])
    fits["app"] = LP.fit_ridge(X, y, w, alpha)
    return pred, fits


# ── metrics and tests ────────────────────────────────────────────────────────

def metric_row(model, phase, subset, d, p):
    y, w, nz = d.net.to_numpy(float), d.w.to_numpy(float), d.noise.to_numpy(float)
    W = w.sum()
    m = wmse(y, p, w)
    noise = float(np.sum(w * nz) / W)
    ybar = float(np.sum(w * y) / W)
    var = float(np.sum(w * (y - ybar) ** 2) / W)
    true_mse = m - noise
    return {"model": model, "phase": phase, "subset": subset, "seasons": PE.span(d.season.unique()), "n": len(d),
            "poss": float(((d.po + d.pd) / 2).sum()), "weight": W, "wmse": m, "noise": noise, "var": var,
            "true_var": var - noise, "true_mse": true_mse, "true_rmse": float(np.sqrt(max(true_mse, 0.0))),
            "r2_true": 1.0 - true_mse / (var - noise), "mean_pred": float(np.sum(w * p) / W), "mean_net": ybar}


def boot_cols(d, preds):
    """Columns whose resampled sums give wMSE and R2_true per model: w*l_m..., w*noise, w*y, w*y^2, w."""
    y, w, nz = d.net.to_numpy(float), d.w.to_numpy(float), d.noise.to_numpy(float)
    return [w * (y - p) ** 2 for p in preds] + [w * nz, w * y, w * y * y, w]


def r2_from_sums(S, j, m):
    """R2_true of model j from resampled column sums (m models first)."""
    wl, wn, wy, wyy, W = S[:, j], S[:, m], S[:, m + 1], S[:, m + 2], S[:, m + 3]
    noise = wn / W
    var = wyy / W - (wy / W) ** 2
    return 1.0 - (wl / W - noise) / (var - noise)


def test_rows(out, u, preds, resamples):
    for phase in ("tune", "validate", "test"):
        for subset in SUBSETS:
            sel = (u.phase == phase) & (u.later if subset == "later" else True)
            d = u[sel]
            idx, C = PT.cluster_codes((d.season.astype(str) + "|" + d.team).to_numpy())
            seasons = PE.span(d.season.unique())
            for model in preds:
                seed = PT.row_seed("lineup", phase, "r2_true", model, "", subset, seasons)
                rng = np.random.default_rng(seed)
                S = PT.boot_sums(rng, idx, C, boot_cols(d, [preds[model][sel.to_numpy()]]), resamples)
                boot = r2_from_sums(S, 0, 1)
                full = np.array([c.sum() for c in boot_cols(d, [preds[model][sel.to_numpy()]])])[None, :]
                val = float(r2_from_sums(full, 0, 1)[0])
                lo, hi = PT.percentile_ci(boot)
                out.add(task="lineup", phase=phase, metric="r2_true", model_a=model, variant=subset, seasons=seasons,
                        unit_type="lineup", cluster_by="team_season", n=len(d), n_clusters=C, value_a=val, diff=val,
                        ci_lo=lo, ci_hi=hi, seed=seed)
            for a, b in PAIRS:
                pa_, pb_ = preds[a][sel.to_numpy()], preds[b][sel.to_numpy()]
                cols = boot_cols(d, [pa_, pb_])
                full = np.array([c.sum() for c in cols])[None, :]
                for metric in ("wmse", "r2_true"):
                    seed = PT.row_seed("lineup", phase, metric, a, b, subset, seasons)
                    rng = np.random.default_rng(seed)
                    S = PT.boot_sums(rng, idx, C, cols, resamples)
                    if metric == "wmse":
                        va, vb = float(full[0, 0] / full[0, 5]), float(full[0, 1] / full[0, 5])
                        dd = (S[:, 0] - S[:, 1]) / S[:, 5]
                        p_perm = PT.sign_flip_p(rng, np.bincount(idx, weights=cols[0] - cols[1], minlength=C), resamples)
                    else:
                        va, vb = float(r2_from_sums(full, 0, 2)[0]), float(r2_from_sums(full, 1, 2)[0])
                        dd = r2_from_sums(S, 0, 2) - r2_from_sums(S, 1, 2)
                        p_perm = None
                    lo, hi = PT.percentile_ci(dd)
                    out.add(task="lineup", phase=phase, metric=metric, model_a=a, model_b=b, variant=subset, seasons=seasons,
                            unit_type="lineup", cluster_by="team_season", n=len(d), n_clusters=C, value_a=va, value_b=vb,
                            diff=va - vb, ci_lo=lo, ci_hi=hi, p_boot=PT.boot_p(dd), p_perm=p_perm, seed=seed)


# ── writing ──────────────────────────────────────────────────────────────────

def py(v):
    if v is None:
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and np.isnan(v):
        return None
    return v


def write(cur, table, ddl, df):
    cur.execute(f"DROP TABLE IF EXISTS {table}")
    cur.execute(f"CREATE TABLE {table} ({ddl})")
    cols = list(df.columns)
    rows = [tuple(py(v) if not isinstance(v, (list, tuple)) else list(v) for v in r) for r in df.itertuples(index=False)]
    execute_values(cur, f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s", rows, page_size=5000)


MODEL_COLS = ["zero", "sum", "team", "scaled", "fit", "full"] + [f"{m}_{s}" for m in ("sum", "scaled") for s in SOURCES]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resamples", type=int, default=PT.RESAMPLES)
    args = ap.parse_args()
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    lg, game_no, pre, same, roles, s2, names = load(conn)
    log(f"{len(lg):,} lineup-games, {PE.span(SEASONS)}")
    pv, consts = player_values(lg, pre, same, roles)
    u, before = build_units(lg, game_no, pv, s2)
    folds = fold_of(u)
    log(f"{len(u):,} units; later (first used after game {LATER_AFTER}): {int(u.later.sum()):,}")

    choices, fit_rows, preds = [], [], {}

    def add_fits(model, feats, src, alpha, k, fits):
        for fit_on, f in fits.items():
            fit_rows.append((fit_on, model, "intercept", f["intercept"], None))
            for name, c, mu, sd in zip(feats, f["coef"], f["mean"], f["sd"]):
                fit_rows.append((fit_on, model, f"coef:{name}", float(c), json.dumps({"mean": float(mu), "sd": float(sd)})))
        fit_rows.append(("", model, "alpha", alpha, None))
        if k is not None:
            fit_rows.append(("", model, "k", float(k), None))
        if src is not None:
            fit_rows.append(("", model, "source", None, json.dumps({"source": src})))

    # the rating source: scaled sum's CV wMSE on tune
    cv_src = {}
    for src in SOURCES:
        a, _, m, c = choose(u, ["r_sum"], src, before, folds)
        cv_src[src] = m
        p, fits = score_model(u, ["r_sum"], src, a, None, before, folds)
        preds[f"scaled_{src}"] = p
        preds[f"sum_{src}"] = u[f"r_sum_{src}"].to_numpy(float)
        add_fits(f"scaled_{src}", ["r_sum"], src, a, None, fits)
        choices.append({"model": f"scaled_{src}", "alpha": a, "k": None, "cv_wmse": m, "candidates": c})
    src = min(cv_src, key=cv_src.get)
    log("rating source by tune CV wMSE: " + ", ".join(f"{s} {m:.2f}" for s, m in cv_src.items()) + f" -> {src}")
    preds["sum"], preds["scaled"] = preds[f"sum_{src}"], preds[f"scaled_{src}"]
    preds["zero"] = np.zeros(len(u))

    for model, ks in (("team", K_GRID), ("fit", (None,)), ("full", K_GRID)):
        feats = LP.MODELS[model]
        a, k, m, c = choose(u, feats, src, before, folds, ks)
        p, fits = score_model(u, feats, src, a, k, before, folds)
        preds[model] = p
        add_fits(model, feats, src if "r_sum" in feats else None, a, k, fits)
        choices.append({"model": model, "alpha": a, "k": k, "cv_wmse": m, "candidates": c})
        log(f"{model}: alpha {a}, K {k}, tune CV wMSE {m:.3f}")
        if model == "full":
            td_team, td_on = season_to_date(u, before, k)
            u["td_team"], u["td_on"] = td_team, td_on
            u["td_k"] = k
    u["td_poss"] = (u.team_b_po + u.team_b_pd) / 2.0
    for m in MODEL_COLS:
        u[f"pred_{m}"] = preds[m]

    # metrics
    mrows = []
    for phase in ("tune", "validate", "test"):
        for subset in SUBSETS:
            sel = ((u.phase == phase) & (u.later if subset == "later" else True)).to_numpy()
            for m in MODEL_COLS:
                mrows.append(metric_row(m, phase, subset, u[sel], preds[m][sel]))
    metrics = pd.DataFrame(mrows)
    for (ph, sub), g in metrics.groupby(["phase", "subset"], sort=False):
        log(f"{ph:8s} {sub:5s} n {int(g.n.iloc[0]):6,} " + "  ".join(
            f"{r.model} {r.r2_true:+.3f}" for r in g.itertuples() if r.model in ("zero", "sum", "team", "scaled", "fit", "full")))

    # the residual spread of the true net around the fit model (the panel's 80% range), tune out-of-fold
    tune = (u.phase == "tune").to_numpy()
    for model in ("fit", "full"):
        r = metric_row(model, "tune", "all", u[tune], preds[model][tune])
        consts[f"true_resid_sd_{model}"] = float(np.sqrt(max(r["true_mse"], 0.0)))
    consts.update(noise_check(conn, s2))
    log(f"noise check: split-half covariance {consts['noise_check_split_cov']:.1f} vs the noise model's real variance "
        f"{consts['noise_check_model_var']:.1f} ({consts['noise_check_units']:,} units)")
    consts["source"] = src
    consts["later_after"] = LATER_AFTER
    for s, v in s2.items():
        if s in SEASONS:
            consts[f"s2_{s}"] = v

    out = PT.Rows(args.resamples)
    test_rows(out, u, preds, args.resamples)
    tests = pd.DataFrame(out.rows, columns=PT.Rows.COLS)
    for r in tests[(tests.metric == "wmse") & (tests.variant == "later")].itertuples():
        log(f"{r.phase:8s} {r.model_a:>14s} - {r.model_b:<12s} {r.diff:+8.2f} [{r.ci_lo:+.2f}, {r.ci_hi:+.2f}] p {r.p_boot:.3f}")

    # tables
    pv["player_name"] = pv.player_id.map(names)
    players = pv[["season", "team", "player_id", "player_name", "bpm", "bpm_known", "rapm", "rapm_known", "tracker",
                  "tracker_known", "gravity", "gravity_known", "usage", "usage_known", "family", "po", "pf", "pd", "pa"]]
    teams = lg.groupby(["season", "team"], as_index=False).agg(games=("game_id", "nunique"), last_date=("game_date", "max"),
                                                               po=("po", "sum"), pf=("pf", "sum"), pd=("pd", "sum"), pa=("pa", "sum"))
    unit_cols = ["season", "phase", "team", "ids", "first_date", "first_game_no", "later", "games", "po", "pf", "pd", "pa",
                 "net", "w", "noise", *[f"r_sum_{s}" for s in SOURCES], *[f"unrated_{s}" for s in SOURCES], "spacing",
                 "unrated_gravity", "n_big", "n_play", "n_scorer", "n_wing", "two_bigs", "no_handler", "no_role", "usg_sum",
                 "td_poss", "td_k", "td_team", "td_on", *[f"pred_{m}" for m in MODEL_COLS]]
    units = u[unit_cols].rename(columns={"ids": "player_ids"})
    units["player_ids"] = units.player_ids.map(list)
    fit = pd.DataFrame(fit_rows, columns=["fit_on", "model", "name", "value", "detail"])
    extra = [("", "", f"const:{k}", float(v) if not isinstance(v, str) else None,
              json.dumps({"value": v}) if isinstance(v, str) else None) for k, v in consts.items()]
    extra += [("", c["model"], "cv", c["cv_wmse"], json.dumps({"chosen": {"alpha": c["alpha"], "k": c["k"]}, "candidates": c["candidates"],
                                                               "criterion": f"5-fold CV wMSE grouped by team-season, {PE.span(TUNE)}"}))
              for c in choices]
    extra += [("", "", "meta", None, json.dumps({
        "tune": PE.span(TUNE), "validate": PE.label(VALIDATE), "test": PE.label(TEST), "folds": FOLDS, "alphas": ALPHAS,
        "k_grid": K_GRID, "later_after": LATER_AFTER, "resamples": args.resamples, "units": len(u),
        "models": LP.MODEL_LABELS, "features": LP.FEATURE_LABELS, "pairs": PAIRS, "built": time.strftime("%Y-%m-%d")}))]
    fit = pd.concat([fit, pd.DataFrame(extra, columns=fit.columns)], ignore_index=True)

    cur = conn.cursor()
    write(cur, "lineup_predictor_units", """season INTEGER NOT NULL, phase TEXT NOT NULL, team TEXT NOT NULL, player_ids INTEGER[] NOT NULL,
          first_date DATE NOT NULL, first_game_no INTEGER NOT NULL, later BOOLEAN NOT NULL, games INTEGER NOT NULL,
          po INTEGER NOT NULL, pf INTEGER NOT NULL, pd INTEGER NOT NULL, pa INTEGER NOT NULL, net REAL NOT NULL, w REAL NOT NULL,
          noise REAL NOT NULL, """ + "".join(f"r_sum_{s} REAL, " for s in SOURCES) +
          ", ".join(f"unrated_{s} SMALLINT" for s in SOURCES) + """, spacing REAL, unrated_gravity SMALLINT,
          n_big SMALLINT, n_play SMALLINT, n_scorer SMALLINT, n_wing SMALLINT, two_bigs REAL, no_handler REAL, no_role SMALLINT,
          usg_sum REAL, td_poss REAL, td_k REAL, td_team REAL, td_on REAL, """ + ", ".join(f"pred_{m} REAL" for m in MODEL_COLS) +
          ", PRIMARY KEY (season, team, player_ids)", units)
    cur.execute("CREATE INDEX ON lineup_predictor_units USING gin (player_ids)")
    cur.execute("CREATE INDEX ON lineup_predictor_units (season, team)")
    write(cur, "lineup_predictor_players", """season INTEGER NOT NULL, team TEXT NOT NULL, player_id INTEGER NOT NULL, player_name TEXT,
          bpm REAL, bpm_known BOOLEAN, rapm REAL, rapm_known BOOLEAN, tracker REAL, tracker_known BOOLEAN, gravity REAL,
          gravity_known BOOLEAN, usage REAL, usage_known BOOLEAN, family TEXT, po INTEGER, pf INTEGER, pd INTEGER, pa INTEGER,
          PRIMARY KEY (season, team, player_id)""", players)
    write(cur, "lineup_predictor_teams", """season INTEGER NOT NULL, team TEXT NOT NULL, games INTEGER, last_date DATE,
          po INTEGER, pf INTEGER, pd INTEGER, pa INTEGER, PRIMARY KEY (season, team)""", teams)
    write(cur, "lineup_predictor_fit", "fit_on TEXT NOT NULL, model TEXT NOT NULL, name TEXT NOT NULL, value DOUBLE PRECISION, detail JSONB, "
          "PRIMARY KEY (fit_on, model, name)", fit)
    write(cur, "lineup_predictor_metrics", """model TEXT NOT NULL, phase TEXT NOT NULL, subset TEXT NOT NULL, seasons TEXT NOT NULL,
          n INTEGER NOT NULL, poss DOUBLE PRECISION, weight DOUBLE PRECISION, wmse DOUBLE PRECISION, noise DOUBLE PRECISION,
          var DOUBLE PRECISION, true_var DOUBLE PRECISION, true_mse DOUBLE PRECISION, true_rmse DOUBLE PRECISION,
          r2_true DOUBLE PRECISION, mean_pred DOUBLE PRECISION, mean_net DOUBLE PRECISION, PRIMARY KEY (model, phase, subset)""", metrics)
    cur.execute("DROP TABLE IF EXISTS lineup_predictor_tests")
    cur.execute(f"CREATE TABLE lineup_predictor_tests ({PT.DDL})")
    execute_values(cur, f"INSERT INTO lineup_predictor_tests ({', '.join(PT.Rows.COLS)}) VALUES %s", out.rows)
    conn.commit()
    conn.close()
    log(f"done in {time.time() - t0:.0f}s: {len(units):,} units, {len(players):,} player rows, {len(tests)} tests")


if __name__ == "__main__":
    main()
