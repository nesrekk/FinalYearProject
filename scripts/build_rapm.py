"""
build_rapm.py
==============
Regularized adjusted plus-minus (RAPM) for every player, from the five-man
stints in `lineup_stints` (scripts/build_lineup_stints.py). Where the On/Off
page credits a player with everything his lineups did, RAPM asks what each
player adds once the other nine players on the floor are held constant.

The regression
  One row per stint per side, offence first: the target is the offence's
  points per 100 possessions in that stint (possessions = FGA + 0.44 FTA -
  OREB + TOV, the side's own count), weighted by those possessions. The
  five offensive players get +1 in their offence columns, the five
  defenders -1 in their defence columns, plus a season intercept and a
  home term (+1 when the offence is the home team, -1 when it is away,
  so twice the coefficient is the home-court edge per 100 possessions).
  Only stints with `tracked_ok` (the game reconciled and five identified
  players a side) are used; a side with no possession in a stint carries
  no information and is dropped (about 29% of sides: substitutions during
  free throws and the like).
  Ridge regression: the player coefficients are shrunk toward zero (the
  league average) with strength lambda, the intercepts and home term are
  not. lambda is chosen by 5-fold cross-validation grouped by game (a
  game's stints are all in or all out of a fold), minimising the weighted
  held-out error of stint points per 100; the whole curve is stored
  (`rapm_lambda_cv`) because it is flat and the choice matters little.
  Normal equations are solved directly (X'WX is about 1,100 x 1,100 a
  season), which makes cross-validation and the bootstrap cheap.

Three versions (`player_rapm.version`)
  single  one season on its own;
  multi   a three-season window ending in the season (one coefficient per
          player over the window, one intercept per season); only for end
          seasons with all three seasons on file (2022-23 on);
  prior   one season, shrunk toward a BPM-based prior instead of zero:
          offence toward k x OBPM and defence toward k x DBPM
          (Basketball-Reference's published values, player_season_stats).
          The scale k (0.5 to 1.5) is chosen by the same cross-validation,
          but lambda is the single-season version's: with lambda free, the
          cross-validation minimum slides to the top of any grid (128,000
          here), i.e. to BPM itself, because the curve is nearly flat and
          BPM alone predicts held-out stints about as well as anything.
          Keeping lambda lets the stints move a player off his BPM by the
          same amount the plain version moves him off zero. Both the choice
          and the cross-validation's own minimum are stored (rapm_fits:
          lambda_rule, cv_best_lambda) and shown on the page. Players with
          no BPM that season get a zero prior.

Uncertainty  a game-clustered bootstrap (games resampled with replacement,
  BOOTSTRAPS times, the fit repeated each time): standard errors for the
  offence, defence and total coefficients and a percentile 95% interval
  for the total. Rows under QUALIFIED_POSS possessions (offence and
  defence averaged) are flagged, not hidden.

Validation (`rapm_validation`), all with the same stint rows and the same
  additive form (a stint's predicted points per 100 = intercept + home term
  + the five offensive players' offence ratings - the five defenders'
  defence ratings):
  held_out_games  within a season, the 5 cross-validation folds: each fold's
                  stints predicted by a fit on the other four; scored on the
                  season's stints. Baselines fitted on the same training
                  games: zero (intercept and home term only), BPM (published
                  OBPM/DBPM with one scale factor fitted on the training
                  rows; BPM itself comes from the full season, which favours
                  it slightly), on/off (recomputed from the training stints
                  only, each player's on - off net split evenly between
                  offence and defence, one fitted scale). The RAPM rows use
                  the lambda chosen on the same folds, so they are very
                  slightly optimistic; the next-season test has no such leak.
  next_season     every rating from season S used as is (scale 1) to predict
                  season S+1's stints; only the intercept and home term are
                  refitted on S+1 (the same two numbers for every model,
                  because league scoring drifts between seasons). Players
                  without a rating in S (rookies, newcomers) count as league
                  average for every model; `coverage` is the share of
                  player-slots (ten a stint, possession-weighted) with a
                  rating and `coverage_all10` the share of possessions
                  where all ten had one. `scale_fit` is
                  the multiplier that would have fitted best, a calibration
                  check (1 = the rating's size was right).
  year_to_year    correlation of a rating between consecutive seasons among
                  players qualified in both, for RAPM, BPM and on/off.
  Each row carries the weighted RMSE of stint points per 100 and, summing
  each game's stints into a predicted home margin, the RMSE and correlation
  of game margins.

Tables written (all dropped and rebuilt):
  player_rapm        one row per version, season and player (see columns);
  rapm_fits          one row per version and season: lambda, prior scale,
                     cross-validated error against the zero model, home
                     edge, intercepts, sizes, bootstraps;
  rapm_lambda_cv     the cross-validation curve per version and season;
  rapm_validation    the tests above.

Checks printed at the end (the README quotes them): the top of each season,
Jokic's rank, the correlation with BPM, the home edge, and the validation
summary.

Usage:
    cd scripts && python3 build_rapm.py
Rerun after build_lineup_stints.py or load_bref_bpm_vorp.py.
"""

import hashlib
import json
import time

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
import scipy.sparse as sp

from db_config import DB_CONFIG

LAMBDAS = [250, 500, 1000, 1500, 2000, 3000, 4000, 6000, 8000, 12000, 16000, 24000, 32000, 48000, 64000, 96000, 128000]
PRIOR_SCALES = [0.5, 0.75, 1.0, 1.25, 1.5]
FOLDS = 5
BOOTSTRAPS = 300
SEED = 20260929
QUALIFIED_POSS = 1000    # offence/defence-averaged possessions; the API's default floor
WINDOW = 3               # seasons in the multi-season version
VERSIONS = ("single", "multi", "prior")


# ── Data ─────────────────────────────────────────────────────────────────────

def load_rows(conn, through=None):
    """Two rows per tracked stint (each side's offence), sides with no possession dropped. `through` (an end year)
    keeps the seasons up to it: the paper's scripts pass paper_freeze.MAX_PAPER_SEASON (round 9 step 1); the
    app's build reads every season."""
    cap = f" AND season <= {int(through)}" if through else ""
    st = pd.read_sql_query(
        f"""SELECT stint_id, game_id, season, home_team, away_team, home_ids, away_ids, home_pts, away_pts,
                  home_poss, away_poss, seconds
           FROM lineup_stints WHERE tracked_ok{cap} ORDER BY stint_id""", conn)
    home = pd.DataFrame({
        "stint_id": st.stint_id, "game_id": st.game_id, "season": st.season, "team": st.home_team, "opp": st.away_team,
        "off": st.home_ids, "de": st.away_ids, "pts": st.home_pts, "poss": st.home_poss, "home": 1, "seconds": st.seconds})
    away = pd.DataFrame({
        "stint_id": st.stint_id, "game_id": st.game_id, "season": st.season, "team": st.away_team, "opp": st.home_team,
        "off": st.away_ids, "de": st.home_ids, "pts": st.away_pts, "poss": st.away_poss, "home": -1, "seconds": st.seconds})
    rows = pd.concat([home, away], ignore_index=True)
    dropped = int((rows.poss <= 0).sum())
    rows = rows[rows.poss > 0].reset_index(drop=True)
    rows["off"] = rows["off"].apply(lambda ids: [int(x) for x in ids])
    rows["de"] = rows["de"].apply(lambda ids: [int(x) for x in ids])
    rows["fold"] = rows.game_id.map(game_fold)
    return rows, len(st), dropped


def game_fold(game_id):
    """Deterministic fold per game, the same for every version and season."""
    return int(hashlib.md5(game_id.encode()).hexdigest(), 16) % FOLDS


def load_bpm(conn, through=None):
    """(player, season) -> (obpm, dbpm, bpm, minutes) from Basketball-Reference's published values; `through` as
    in load_rows."""
    cap = f" AND season <= {int(through)}" if through else ""
    df = pd.read_sql_query(
        f"""SELECT player_id, season, obpm, dbpm, bpm, gp * min AS minutes FROM player_season_stats
           WHERE bpm IS NOT NULL AND obpm IS NOT NULL AND dbpm IS NOT NULL{cap}""", conn)
    return {(int(r.player_id), int(r.season)): (float(r.obpm), float(r.dbpm), float(r.bpm), float(r.minutes or 0))
            for r in df.itertuples()}


def load_names(conn):
    df = pd.read_sql_query(
        "SELECT DISTINCT ON (player_id) player_id, player_name FROM player_season_stats ORDER BY player_id, season DESC", conn)
    return dict(zip(df.player_id.astype(int), df.player_name))


# ── Design matrix ────────────────────────────────────────────────────────────

class Design:
    """Sparse design for a set of rows: offence columns, defence columns, one
    intercept per season, a home column. Columns 0..P-1 are offence, P..2P-1
    defence, then the season intercepts, then home."""

    def __init__(self, rows):
        self.rows = rows.reset_index(drop=True)
        self.players = sorted(set(p for ids in self.rows.off for p in ids) | set(p for ids in self.rows.de for p in ids))
        self.pidx = {p: i for i, p in enumerate(self.players)}
        self.seasons = sorted(self.rows.season.unique())
        self.sidx = {s: i for i, s in enumerate(self.seasons)}
        P, S, n = len(self.players), len(self.seasons), len(self.rows)
        self.P, self.S, self.n = P, S, n
        self.ncol = 2 * P + S + 1
        off = np.array([[self.pidx[p] for p in ids] for ids in self.rows.off])
        de = np.array([[P + self.pidx[p] for p in ids] for ids in self.rows.de])
        sea = 2 * P + self.rows.season.map(self.sidx).to_numpy()[:, None]
        hm = np.full((n, 1), 2 * P + S)
        cols = np.hstack([off, de, sea, hm])
        vals = np.hstack([np.ones((n, 5)), -np.ones((n, 5)), np.ones((n, 1)), self.rows.home.to_numpy(float)[:, None]])
        ri = np.repeat(np.arange(n), cols.shape[1])
        self.X = sp.csr_matrix((vals.ravel(), (ri, cols.ravel())), shape=(n, self.ncol))
        self.y = (100.0 * self.rows.pts / self.rows.poss).to_numpy(float)
        self.w = self.rows.poss.to_numpy(float)
        self.fold = self.rows.fold.to_numpy()
        self.game_idx, self.games = pd.factorize(self.rows.game_id)
        self.sign = self.rows.home.to_numpy(float)   # +1 home offence, -1 away offence

    def gram(self, mask=None, weights=None):
        X = self.X if mask is None else self.X[mask]
        w = self.w if weights is None else weights
        y = self.y
        if mask is not None:
            w = w[mask]
            y = y[mask]
        Xw = X.multiply(w[:, None]).tocsr()
        return (X.T @ Xw).toarray(), X.T @ (w * y), float(w.sum())

    def penalty(self, lam):
        pen = np.zeros(self.ncol)
        pen[:2 * self.P] = lam
        return np.diag(pen)

    def prior_vector(self, prior_od):
        """Column vector of the prior: (obpm, dbpm) per player, zeros elsewhere."""
        b0 = np.zeros(self.ncol)
        for p, i in self.pidx.items():
            o, d = prior_od.get(p, (0.0, 0.0))
            b0[i], b0[self.P + i] = o, d
        return b0

    def solve(self, G, b, lam, prior=None):
        """Ridge toward `prior` (a column vector; None = zero)."""
        if prior is None:
            return np.linalg.solve(G + self.penalty(lam), b)
        return prior + np.linalg.solve(G + self.penalty(lam), b - G @ prior)

    def rating_vector(self, o, d):
        """Column vector from per-player (offence, defence) dicts; missing players 0."""
        v = np.zeros(self.ncol)
        for p, i in self.pidx.items():
            v[i] = o.get(p, 0.0)
            v[self.P + i] = d.get(p, 0.0)
        return v

    def player_cols(self, v):
        """The player part of a column vector (intercepts and home zeroed)."""
        out = np.zeros(self.ncol)
        out[:2 * self.P] = v[:2 * self.P]
        return out

    def nuisance_cols(self):
        return list(range(2 * self.P, self.ncol))


def fit_nuisance(design, mask, base, fit_scale):
    """Given player ratings `base` (a column vector), fit the intercept(s) and
    home term (and, if asked, one scale on the ratings) on the masked rows.
    Returns the full coefficient vector and the scale."""
    X, w, y = design.X[mask], design.w[mask], design.y[mask]
    Xn = X[:, design.nuisance_cols()]
    played = X @ design.player_cols(base)
    if fit_scale:
        A = sp.hstack([Xn, sp.csr_matrix(played[:, None])]).tocsr()
    else:
        A = Xn
        y = y - played
    Aw = A.multiply(w[:, None]).tocsr()
    G = (A.T @ Aw).toarray()
    b = A.T @ (w * y)
    coef = np.linalg.solve(G, b)
    k = float(coef[-1]) if fit_scale else 1.0
    beta = design.player_cols(base) * k
    beta[design.nuisance_cols()] = coef[:len(design.nuisance_cols())]
    return beta, k


def weighted_rmse(design, mask, pred):
    w, y = design.w[mask], design.y[mask]
    return float(np.sqrt(np.sum(w * (y - pred) ** 2) / w.sum()))


def game_margins(design, mask, pred):
    """Predicted and actual home margins per game over the masked rows."""
    w, y, sign, gi = design.w[mask], design.y[mask], design.sign[mask], design.game_idx[mask]
    p = np.bincount(gi, weights=sign * pred * w / 100, minlength=len(design.games))
    a = np.bincount(gi, weights=sign * y * w / 100, minlength=len(design.games))
    present = np.bincount(gi, minlength=len(design.games)) > 0
    return p[present], a[present]


def score(design, mask, pred):
    p, a = game_margins(design, mask, pred)
    return {
        "rows": int(mask.sum()), "games": int(len(a)), "poss": round(float(design.w[mask].sum()), 1),
        "stint_rmse": round(weighted_rmse(design, mask, pred), 4),
        "game_rmse": round(float(np.sqrt(np.mean((p - a) ** 2))), 3),
        "game_corr": round(float(np.corrcoef(p, a)[0, 1]), 4) if len(a) > 2 else None,
    }


# ── On/off from stint rows (for the baselines) ───────────────────────────────

def on_off_from_rows(design, mask):
    """Each player's on - off net rating from the masked rows: on-court from
    the stints he is in; off-court = his team's totals in the games he
    played minus his on-court totals. Returns {player: net} (None dropped)."""
    rows = design.rows[mask]
    X = design.X[mask]
    P = design.P
    w, y = design.w[mask], design.y[mask]
    pts = w * y / 100
    Xo = X[:, :P].tocsc()
    Xd = -X[:, P:2 * P].tocsc()
    pf_on = Xo.T @ pts
    poss_for_on = Xo.T @ w
    pa_on = Xd.T @ pts
    poss_against_on = Xd.T @ w
    # Team totals per (game, team): points and possessions on offence and on defence.
    gt = pd.DataFrame({"game_id": rows.game_id.to_numpy(), "team": rows.team.to_numpy(), "opp": rows.opp.to_numpy(),
                       "pts": pts, "poss": w})
    off_tot = gt.groupby(["game_id", "team"])[["pts", "poss"]].sum()
    def_tot = gt.groupby(["game_id", "opp"])[["pts", "poss"]].sum()
    def_tot.index.names = ["game_id", "team"]
    tot = off_tot.join(def_tot, lsuffix="_for", rsuffix="_against", how="outer").fillna(0.0)
    tot_idx = {k: i for i, k in enumerate(tot.index)}
    tot_arr = tot.to_numpy()
    # Player -> the (game, team) pairs he appears in (on offence rows; every stint has both sides).
    gt_of_row = np.array([tot_idx[(g, t)] for g, t in zip(rows.game_id, rows.team)])
    presence = sp.csr_matrix((np.ones(len(rows) * 5), (np.repeat(np.arange(len(rows)), 5),
                                                       np.array([[design.pidx[p] for p in ids] for ids in rows.off]).ravel())),
                             shape=(len(rows), P))
    pg = presence.T @ sp.csr_matrix((np.ones(len(rows)), (np.arange(len(rows)), gt_of_row)), shape=(len(rows), len(tot)))
    pg = (pg > 0).astype(float)
    team_in_games = pg @ tot_arr   # P x 4: pts_for, poss_for, pts_against, poss_against
    out = {}
    for i, p in enumerate(design.players):
        pf_off = team_in_games[i, 0] - pf_on[i]
        poss_f_off = team_in_games[i, 1] - poss_for_on[i]
        pa_off = team_in_games[i, 2] - pa_on[i]
        poss_a_off = team_in_games[i, 3] - poss_against_on[i]
        if min(poss_for_on[i], poss_against_on[i], poss_f_off, poss_a_off) <= 0:
            continue
        net_on = 100 * pf_on[i] / poss_for_on[i] - 100 * pa_on[i] / poss_against_on[i]
        net_off = 100 * pf_off / poss_f_off - 100 * pa_off / poss_a_off
        out[p] = float(net_on - net_off)
    return out


# ── One fit ──────────────────────────────────────────────────────────────────

def cross_validate(design, prior=None, fixed_lambda=None):
    """Held-out weighted RMSE per (lambda, scale). The choice is the cross-validation
    minimum, or the best scale at `fixed_lambda`; out-of-fold predictions at the choice."""
    folds = sorted(set(design.fold))
    grams = {}
    for k in folds:
        m = design.fold == k
        grams[k] = design.gram(mask=m)
    G_all = sum(g[0] for g in grams.values())
    b_all = sum(g[1] for g in grams.values())
    curve = []
    oofs = {}
    scales = PRIOR_SCALES if prior is not None else [None]
    for s in scales:
        pv = None if prior is None else prior * s
        for lam in LAMBDAS:
            sse = wsum = 0.0
            preds = np.zeros(design.n)
            for k in folds:
                m = design.fold == k
                beta = design.solve(G_all - grams[k][0], b_all - grams[k][1], lam, pv)
                pred = design.X[m] @ beta
                preds[m] = pred
                sse += float(np.sum(design.w[m] * (design.y[m] - pred) ** 2))
                wsum += float(design.w[m].sum())
            rmse = float(np.sqrt(sse / wsum))
            curve.append({"lambda": lam, "prior_scale": s, "cv_rmse": rmse})
            oofs[(lam, s)] = preds
    cv_min = min(curve, key=lambda c: c["cv_rmse"])
    if fixed_lambda is None:
        chosen = cv_min
    else:
        chosen = min((c for c in curve if c["lambda"] == fixed_lambda), key=lambda c: c["cv_rmse"])
    best = {"lambda": chosen["lambda"], "prior_scale": chosen["prior_scale"], "cv_rmse": chosen["cv_rmse"],
            "oof": oofs[(chosen["lambda"], chosen["prior_scale"])],
            "cv_best_lambda": cv_min["lambda"], "cv_best_scale": cv_min["prior_scale"], "cv_best_rmse": cv_min["cv_rmse"],
            "lambda_rule": "cv_min" if fixed_lambda is None else "single_lambda"}
    # Zero model on the same folds.
    zero = np.zeros(design.n)
    for k in folds:
        m = design.fold == k
        beta, _ = fit_nuisance(design, ~m, np.zeros(design.ncol), False)
        zero[m] = design.X[m] @ beta
    best["cv_rmse_zero"] = weighted_rmse(design, np.ones(design.n, bool), zero)
    best["oof_zero"] = zero
    return curve, best, (G_all, b_all)


def bootstrap(design, lam, prior, rng):
    n_games = len(design.games)
    O = np.zeros((BOOTSTRAPS, design.P))
    D = np.zeros((BOOTSTRAPS, design.P))
    for b in range(BOOTSTRAPS):
        cnt = np.bincount(rng.integers(0, n_games, n_games), minlength=n_games)
        wb = design.w * cnt[design.game_idx]
        G, bb, _ = design.gram(weights=wb)
        beta = design.solve(G, bb, lam, prior)
        O[b], D[b] = beta[:design.P], beta[design.P:2 * design.P]
    T = O + D
    lo, hi = np.percentile(T, [2.5, 97.5], axis=0)
    return O.std(axis=0, ddof=1), D.std(axis=0, ddof=1), T.std(axis=0, ddof=1), lo, hi


def player_sizes(design):
    """Per player: games, stints (sides), seconds, possessions on offence and defence, teams by possessions."""
    P = design.P
    Xo = design.X[:, :P].tocsc()
    Xd = -design.X[:, P:2 * P].tocsc()
    poss_off = np.asarray(Xo.T @ design.w).ravel()
    poss_def = np.asarray(Xd.T @ design.w).ravel()
    secs = np.asarray(Xo.T @ design.rows.seconds.to_numpy(float)).ravel()
    stints = np.asarray(Xo.T @ np.ones(design.n)).ravel()
    games = {}
    teams = {}
    for ids, g, t, w in zip(design.rows.off, design.rows.game_id, design.rows.team, design.w):
        for p in ids:
            games.setdefault(p, set()).add(g)
            teams.setdefault(p, {})
            teams[p][t] = teams[p].get(t, 0.0) + w
    out = {}
    for p, i in design.pidx.items():
        tm = sorted(teams.get(p, {}).items(), key=lambda kv: -kv[1])
        out[p] = {"games": len(games.get(p, ())), "stints": int(stints[i]), "minutes": round(secs[i] / 60, 1),
                  "poss_off": round(float(poss_off[i]), 1), "poss_def": round(float(poss_def[i]), 1),
                  "teams": "/".join(t for t, _ in tm)}
    return out


def run_fit(version, season, rows, bpm, rng, log, fixed_lambda=None):
    """Fit one version for one (end) season. Returns (design, beta, fit_row, curve, player_rows, oof, oof_zero).
    `fixed_lambda` (the prior version) keeps that lambda; cross-validation then picks only the prior scale."""
    if version == "multi":
        seasons = list(range(season - WINDOW + 1, season + 1))
    else:
        seasons = [season]
    design = Design(rows[rows.season.isin(seasons)])
    prior = None
    prior_od = {}
    if version == "prior":
        prior_od = {p: (bpm[(p, season)][0], bpm[(p, season)][1]) for p in design.players if (p, season) in bpm}
        prior = design.prior_vector(prior_od)
    t0 = time.time()
    curve, best, (G, b) = cross_validate(design, prior, fixed_lambda)
    lam, scale = best["lambda"], best["prior_scale"]
    pv = None if prior is None else prior * scale
    beta = design.solve(G, b, lam, pv)
    o_se, d_se, t_se, lo, hi = bootstrap(design, lam, pv, rng)
    sizes = player_sizes(design)
    P = design.P
    player_rows = []
    for p, i in design.pidx.items():
        sz = sizes[p]
        po = prior_od.get(p)
        bp = None
        if version == "multi":
            # Minutes-weighted BPM over the window, for the comparison column.
            parts = [bpm[(p, s)] for s in seasons if (p, s) in bpm and bpm[(p, s)][3] > 0]
            if parts:
                mins = sum(x[3] for x in parts)
                bp = (sum(x[0] * x[3] for x in parts) / mins, sum(x[1] * x[3] for x in parts) / mins,
                      sum(x[2] * x[3] for x in parts) / mins)
        elif (p, season) in bpm:
            bp = bpm[(p, season)][:3]
        poss = (sz["poss_off"] + sz["poss_def"]) / 2
        player_rows.append({
            "version": version, "season": season, "player_id": p, "seasons_from": seasons[0], "seasons_to": seasons[-1],
            "teams": sz["teams"], "games": sz["games"], "stints": sz["stints"], "minutes": sz["minutes"],
            "poss_off": sz["poss_off"], "poss_def": sz["poss_def"], "poss": round(poss, 1),
            "orapm": round(float(beta[i]), 3), "drapm": round(float(beta[P + i]), 3),
            "rapm": round(float(beta[i] + beta[P + i]), 3),
            "orapm_se": round(float(o_se[i]), 3), "drapm_se": round(float(d_se[i]), 3), "rapm_se": round(float(t_se[i]), 3),
            "rapm_ci_low": round(float(lo[i]), 3), "rapm_ci_high": round(float(hi[i]), 3),
            "prior_o": None if po is None else round(po[0] * scale, 3),
            "prior_d": None if po is None else round(po[1] * scale, 3),
            "obpm": None if bp is None else round(bp[0], 2), "dbpm": None if bp is None else round(bp[1], 2),
            "bpm": None if bp is None else round(bp[2], 2),
            "qualified": poss >= QUALIFIED_POSS,
        })
    intercepts = {int(s): round(float(beta[2 * P + design.sidx[s]]), 3) for s in seasons}
    home = float(beta[2 * P + design.S])
    fit_row = {
        "version": version, "season": season, "seasons_from": seasons[0], "seasons_to": seasons[-1],
        "games": int(len(design.games)), "stints": int(design.rows.stint_id.nunique()), "rows": design.n,
        "players": design.P, "poss": round(float(design.w.sum()), 1),
        "lambda": lam, "prior_scale": scale, "cv_folds": FOLDS, "lambda_rule": best["lambda_rule"],
        "cv_rmse": round(best["cv_rmse"], 4), "cv_rmse_zero": round(best["cv_rmse_zero"], 4),
        "cv_best_lambda": best["cv_best_lambda"], "cv_best_scale": best["cv_best_scale"],
        "cv_best_rmse": round(best["cv_best_rmse"], 4),
        "intercepts": json.dumps(intercepts), "home_coef": round(home, 3), "home_edge_per_100": round(2 * home, 3),
        "bootstraps": BOOTSTRAPS, "seed": SEED, "qualified_poss": QUALIFIED_POSS,
        "qualified": sum(r["qualified"] for r in player_rows),
        "players_with_prior": len(prior_od) if version == "prior" else None,
    }
    for c in curve:
        c.update({"version": version, "season": season})
    cv_note = "" if best["lambda_rule"] == "cv_min" else \
        f" (cv minimum {best['cv_best_rmse']:.4f} at lambda {best['cv_best_lambda']}, scale {best['cv_best_scale']})"
    log(f"  {version} {season}: {design.n} rows, {design.P} players, lambda {lam}"
        f"{'' if scale is None else f', prior scale {scale}'}, cv {best['cv_rmse']:.4f} vs zero {best['cv_rmse_zero']:.4f}"
        f"{cv_note}, home edge {2 * home:+.2f}/100 ({time.time() - t0:.0f}s)")
    return design, beta, fit_row, curve, player_rows, best["oof"], best["oof_zero"]


# ── Validation ───────────────────────────────────────────────────────────────

def ratings_from_rows(player_rows):
    o = {r["player_id"]: r["orapm"] for r in player_rows}
    d = {r["player_id"]: r["drapm"] for r in player_rows}
    return o, d


def held_out_validation(season, fits, bpm, log):
    """Within-season held-out games for one season: RAPM versions' out-of-fold
    predictions, and the baselines refitted per fold, scored on the season's rows."""
    out = []
    single = fits[("single", season)]
    design = single["design"]
    n = design.n
    all_rows = np.ones(n, bool)
    # RAPM versions: out-of-fold predictions at the chosen lambda.
    out.append({"test": "held_out_games", "season": season, "model": "rapm_single", **score(design, all_rows, single["oof"])})
    if ("prior", season) in fits:
        out.append({"test": "held_out_games", "season": season, "model": "rapm_prior", **score(design, all_rows, fits[("prior", season)]["oof"])})
    if ("multi", season) in fits:
        m = fits[("multi", season)]
        md = m["design"]
        mask = md.rows.season.to_numpy() == season
        # The multi window's rows for this season are the same stints in the same order.
        assert (md.rows.stint_id.to_numpy()[mask] == design.rows.stint_id.to_numpy()).all()
        out.append({"test": "held_out_games", "season": season, "model": "rapm_multi", **score(md, mask, m["oof"][mask])})
    out.append({"test": "held_out_games", "season": season, "model": "zero", **score(design, all_rows, single["oof_zero"])})
    # BPM (full-season published values, one scale fitted per training fold) and on/off recomputed per fold.
    bpm_o = {p: bpm[(p, season)][0] for p in design.players if (p, season) in bpm}
    bpm_d = {p: bpm[(p, season)][1] for p in design.players if (p, season) in bpm}
    bpm_vec = design.rating_vector(bpm_o, bpm_d)
    pred_bpm = np.zeros(n)
    pred_oo = np.zeros(n)
    k_b, k_o = [], []
    for k in sorted(set(design.fold)):
        test = design.fold == k
        train = ~test
        beta, kb = fit_nuisance(design, train, bpm_vec, True)
        pred_bpm[test] = design.X[test] @ beta
        k_b.append(kb)
        oo = on_off_from_rows(design, train)
        oo_vec = design.rating_vector({p: v / 2 for p, v in oo.items()}, {p: v / 2 for p, v in oo.items()})
        beta, ko = fit_nuisance(design, train, oo_vec, True)
        pred_oo[test] = design.X[test] @ beta
        k_o.append(ko)
    out.append({"test": "held_out_games", "season": season, "model": "bpm", "scale_fit": round(float(np.mean(k_b)), 3),
                **score(design, all_rows, pred_bpm)})
    out.append({"test": "held_out_games", "season": season, "model": "onoff", "scale_fit": round(float(np.mean(k_o)), 3),
                **score(design, all_rows, pred_oo)})
    for r in out:
        r["fit_seasons"] = f"{season - 1}-{str(season)[-2:]} (other folds)"
    log(f"  held-out {season}: " + ", ".join(f"{r['model']} {r['game_rmse']:.2f}" for r in out))
    return out


def next_season_validation(season, fits, bpm, onoff_full, log):
    """Ratings from `season - 1` predict `season`'s stints, scale 1, intercept and home refitted."""
    prev = season - 1
    if ("single", prev) not in fits:
        return []
    design = fits[("single", season)]["design"]
    all_rows = np.ones(design.n, bool)
    label = f"{prev - 1}-{str(prev)[-2:]}"
    models = {"zero": ({}, {}), "bpm": ({p: v[0] for (p, s), v in bpm.items() if s == prev},
                                        {p: v[1] for (p, s), v in bpm.items() if s == prev})}
    oo = onoff_full.get(prev, {})
    models["onoff"] = ({p: v / 2 for p, v in oo.items()}, {p: v / 2 for p, v in oo.items()})
    models["rapm_single"] = ratings_from_rows(fits[("single", prev)]["player_rows"])
    if ("prior", prev) in fits:
        models["rapm_prior"] = ratings_from_rows(fits[("prior", prev)]["player_rows"])
    if ("multi", prev) in fits:
        models["rapm_multi"] = ratings_from_rows(fits[("multi", prev)]["player_rows"])
    out = []
    for name, (o, d) in models.items():
        vec = design.rating_vector(o, d)
        beta, _ = fit_nuisance(design, all_rows, vec, False)
        pred = design.X @ beta
        _, k = fit_nuisance(design, all_rows, vec, True) if name != "zero" else (None, None)
        rated = set(o) | set(d)
        # Coverage two ways: the share of player-slots (ten a row) with a rating, and the share of
        # possessions where all ten had one (rookies and newcomers make the second much smaller).
        n_rated = np.array([sum(p in rated for p in ids) + sum(p in rated for p in ids2)
                            for ids, ids2 in zip(design.rows.off, design.rows.de)]) if rated else np.zeros(design.n)
        cov_slots = float((design.w * n_rated).sum() / (10 * design.w.sum()))
        cov_all = float(design.w[n_rated == 10].sum() / design.w.sum())
        fs = label if name != "rapm_multi" else f"{prev - WINDOW}-{str(prev - WINDOW + 1)[-2:]} to {label}"
        out.append({"test": "next_season", "season": season, "model": name, "fit_seasons": fs,
                    "coverage": None if name == "zero" else round(cov_slots, 4),
                    "coverage_all10": None if name == "zero" else round(cov_all, 4),
                    "scale_fit": None if k is None else round(float(k), 3), **score(design, all_rows, pred)})
    log(f"  next-season {season}: " + ", ".join(f"{r['model']} {r['game_rmse']:.2f} (r {r['game_corr']:.3f})" for r in out))
    return out


def year_to_year(fits, bpm, onoff_full, seasons, log):
    out = []
    for s in seasons:
        prev = s - 1
        if ("single", prev) not in fits:
            continue
        a = {r["player_id"]: r for r in fits[("single", prev)]["player_rows"] if r["qualified"]}
        b = {r["player_id"]: r for r in fits[("single", s)]["player_rows"] if r["qualified"]}
        both = sorted(set(a) & set(b))
        if len(both) < 20:
            continue
        pairs = {
            "rapm_single": [(a[p]["rapm"], b[p]["rapm"]) for p in both],
            "orapm": [(a[p]["orapm"], b[p]["orapm"]) for p in both],
            "drapm": [(a[p]["drapm"], b[p]["drapm"]) for p in both],
            "bpm": [(bpm[(p, prev)][2], bpm[(p, s)][2]) for p in both if (p, prev) in bpm and (p, s) in bpm],
            "onoff": [(onoff_full[prev][p], onoff_full[s][p]) for p in both if p in onoff_full.get(prev, {}) and p in onoff_full.get(s, {})],
        }
        if ("prior", prev) in fits and ("prior", s) in fits:
            pa = {r["player_id"]: r["rapm"] for r in fits[("prior", prev)]["player_rows"]}
            pb = {r["player_id"]: r["rapm"] for r in fits[("prior", s)]["player_rows"]}
            pairs["rapm_prior"] = [(pa[p], pb[p]) for p in both if p in pa and p in pb]
        for model, xy in pairs.items():
            arr = np.array(xy, float)
            if len(arr) < 20:
                continue
            out.append({"test": "year_to_year", "season": s, "model": model,
                        "fit_seasons": f"{prev - 1}-{str(prev)[-2:]} vs {s - 1}-{str(s)[-2:]}",
                        "players": int(len(arr)), "corr": round(float(np.corrcoef(arr[:, 0], arr[:, 1])[0, 1]), 4)})
        log(f"  year-to-year {s}: " + ", ".join(f"{r['model']} r {r['corr']:.3f}" for r in out if r["season"] == s))
    return out


# ── Write ────────────────────────────────────────────────────────────────────

PLAYER_COLS = ["version", "season", "player_id", "seasons_from", "seasons_to", "teams", "games", "stints", "minutes",
               "poss_off", "poss_def", "poss", "orapm", "drapm", "rapm", "orapm_se", "drapm_se", "rapm_se",
               "rapm_ci_low", "rapm_ci_high", "prior_o", "prior_d", "obpm", "dbpm", "bpm", "qualified"]
FIT_COLS = ["version", "season", "seasons_from", "seasons_to", "games", "stints", "rows", "players", "poss", "lambda",
            "prior_scale", "lambda_rule", "cv_folds", "cv_rmse", "cv_rmse_zero", "cv_best_lambda", "cv_best_scale",
            "cv_best_rmse", "intercepts", "home_coef", "home_edge_per_100",
            "bootstraps", "seed", "qualified_poss", "qualified", "players_with_prior"]
CURVE_COLS = ["version", "season", "lambda", "prior_scale", "cv_rmse"]
VAL_COLS = ["test", "season", "model", "fit_seasons", "rows", "games", "players", "poss", "coverage", "coverage_all10",
            "scale_fit", "stint_rmse", "game_rmse", "game_corr", "corr"]


def clean(v):
    if v is None:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    return v.item() if hasattr(v, "item") else v


def write(conn, player_rows, fit_rows, curve_rows, val_rows):
    cur = conn.cursor()
    for t in ("rapm_validation", "rapm_lambda_cv", "rapm_fits", "player_rapm"):
        cur.execute(f"DROP TABLE IF EXISTS {t};")
    cur.execute("""CREATE TABLE player_rapm (
        version TEXT NOT NULL, season INTEGER NOT NULL, player_id BIGINT NOT NULL,
        seasons_from INTEGER NOT NULL, seasons_to INTEGER NOT NULL, teams TEXT,
        games INTEGER, stints INTEGER, minutes REAL, poss_off REAL, poss_def REAL, poss REAL,
        orapm REAL NOT NULL, drapm REAL NOT NULL, rapm REAL NOT NULL,
        orapm_se REAL, drapm_se REAL, rapm_se REAL, rapm_ci_low REAL, rapm_ci_high REAL,
        prior_o REAL, prior_d REAL, obpm REAL, dbpm REAL, bpm REAL, qualified BOOLEAN NOT NULL,
        PRIMARY KEY (version, season, player_id));""")
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO player_rapm ({', '.join(PLAYER_COLS)}) VALUES %s",
        [tuple(clean(r.get(c)) for c in PLAYER_COLS) for r in player_rows], page_size=2000)
    cur.execute("CREATE INDEX ON player_rapm (player_id);")
    cur.execute("CREATE INDEX ON player_rapm (version, season, qualified, rapm DESC);")
    cur.execute("""CREATE TABLE rapm_fits (
        version TEXT NOT NULL, season INTEGER NOT NULL, seasons_from INTEGER, seasons_to INTEGER,
        games INTEGER, stints INTEGER, rows INTEGER, players INTEGER, poss REAL, lambda REAL NOT NULL, prior_scale REAL,
        lambda_rule TEXT, cv_folds INTEGER, cv_rmse REAL, cv_rmse_zero REAL, cv_best_lambda REAL, cv_best_scale REAL,
        cv_best_rmse REAL, intercepts JSONB, home_coef REAL, home_edge_per_100 REAL,
        bootstraps INTEGER, seed BIGINT, qualified_poss INTEGER, qualified INTEGER, players_with_prior INTEGER,
        PRIMARY KEY (version, season));""")
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO rapm_fits ({', '.join(FIT_COLS)}) VALUES %s",
        [tuple(clean(r.get(c)) for c in FIT_COLS) for r in fit_rows])
    cur.execute("""CREATE TABLE rapm_lambda_cv (
        version TEXT NOT NULL, season INTEGER NOT NULL, lambda REAL NOT NULL, prior_scale REAL, cv_rmse REAL NOT NULL);""")
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO rapm_lambda_cv ({', '.join(CURVE_COLS)}) VALUES %s",
        [tuple(clean(r.get(c)) for c in CURVE_COLS) for r in curve_rows])
    cur.execute("CREATE INDEX ON rapm_lambda_cv (version, season);")
    cur.execute("""CREATE TABLE rapm_validation (
        test TEXT NOT NULL, season INTEGER NOT NULL, model TEXT NOT NULL, fit_seasons TEXT,
        rows INTEGER, games INTEGER, players INTEGER, poss REAL, coverage REAL, coverage_all10 REAL, scale_fit REAL,
        stint_rmse REAL, game_rmse REAL, game_corr REAL, corr REAL,
        PRIMARY KEY (test, season, model));""")
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO rapm_validation ({', '.join(VAL_COLS)}) VALUES %s",
        [tuple(clean(r.get(c)) for c in VAL_COLS) for r in val_rows])
    conn.commit()


# ── Checks ───────────────────────────────────────────────────────────────────

def print_checks(conn, names):
    cur = conn.cursor()
    print("\nFits (rapm_fits):")
    print(pd.read_sql_query("""SELECT version, season, games, rows, players, lambda, prior_scale, lambda_rule, cv_rmse,
                                      cv_rmse_zero, cv_best_lambda, cv_best_scale, cv_best_rmse, home_edge_per_100, qualified
                               FROM rapm_fits ORDER BY version, season""", conn).to_string(index=False))
    for version in VERSIONS:
        df = pd.read_sql_query(
            """SELECT season, player_id, teams, poss, orapm, drapm, rapm, rapm_se, bpm FROM player_rapm
               WHERE version = %s AND qualified ORDER BY season, rapm DESC""", conn, params=(version,))
        df["name"] = df.player_id.map(names)
        print(f"\n{version}: top 8 per season (qualified, {QUALIFIED_POSS}+ possessions)")
        for season, g in df.groupby("season"):
            top = g.head(8)
            jok = g.reset_index(drop=True)
            jrank = jok.index[jok.player_id == 203999]
            jr = f"Jokić #{jrank[0] + 1} of {len(jok)}" if len(jrank) else "Jokić not qualified"
            r = g[["rapm", "bpm"]].dropna().corr().iloc[0, 1]
            print(f"  {season - 1}-{str(season)[-2:]} ({jr}; r with BPM {r:.2f}, sd RAPM {g.rapm.std():.2f} vs BPM {g.bpm.std():.2f}): "
                  + "; ".join(f"{x.name} {x.rapm:+.1f}±{x.rapm_se:.1f}" for x in top.itertuples()))
    print("\nValidation (rapm_validation):")
    v = pd.read_sql_query("""SELECT test, season, model, fit_seasons, games, coverage, coverage_all10, scale_fit, stint_rmse,
                                    game_rmse, game_corr, corr, players
                             FROM rapm_validation ORDER BY test, season, game_rmse NULLS LAST, model""", conn)
    print(v.to_string(index=False))
    for t in ("player_rapm", "rapm_fits", "rapm_lambda_cv", "rapm_validation"):
        cur.execute(f"SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('{t}')) FROM {t}")
        n, size = cur.fetchone()
        print(f"  {t}: {n:,} rows, {size}")


def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    log = lambda s: print(f"{s}  [{time.time() - t0:.0f}s]")
    rows, n_stints, dropped = load_rows(conn)
    bpm = load_bpm(conn)
    names = load_names(conn)
    seasons = sorted(rows.season.unique())
    log(f"{n_stints} tracked stints -> {len(rows)} side-rows ({dropped} sides with no possession dropped); "
        f"seasons {seasons[0]}-{seasons[-1]}; {len(bpm)} BPM rows")
    rng = np.random.default_rng(SEED)

    fits = {}
    fit_rows, curve_rows, player_rows = [], [], []
    single_lambda = {}
    for season in seasons:
        for version in VERSIONS:
            if version == "multi" and season - WINDOW + 1 < seasons[0]:
                continue
            fixed = single_lambda[season] if version == "prior" else None
            design, beta, fit_row, curve, prow, oof, oof_zero = run_fit(version, season, rows, bpm, rng, log, fixed)
            if version == "single":
                single_lambda[season] = fit_row["lambda"]
            fits[(version, season)] = {"design": design, "beta": beta, "player_rows": prow, "oof": oof, "oof_zero": oof_zero}
            fit_rows.append(fit_row)
            curve_rows.extend(curve)
            player_rows.extend(prow)

    # Full-season on/off from the same stints, for the next-season and year-to-year tests.
    onoff_full = {s: on_off_from_rows(fits[("single", s)]["design"], np.ones(fits[("single", s)]["design"].n, bool))
                  for s in seasons}
    val_rows = []
    for season in seasons:
        val_rows.extend(held_out_validation(season, fits, bpm, log))
        val_rows.extend(next_season_validation(season, fits, bpm, onoff_full, log))
    val_rows.extend(year_to_year(fits, bpm, onoff_full, seasons, log))

    write(conn, player_rows, fit_rows, curve_rows, val_rows)
    log(f"wrote {len(player_rows)} player rows, {len(fit_rows)} fits, {len(curve_rows)} curve points, {len(val_rows)} validation rows")
    print_checks(conn, names)
    conn.close()


if __name__ == "__main__":
    main()
