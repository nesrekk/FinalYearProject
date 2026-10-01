"""
rating_tracker_lib.py
======================
The Rating Tracker (round 6, step 7): regularised adjusted plus-minus whose
ratings carry across seasons. Where build_rapm.py fits every season on its
own (or a fixed three-season window, or one season shrunk toward BPM with a
fixed weight), the tracker treats each player's offence and defence rating as
a hidden state that drifts between seasons, and lets the data say how much
of last season's rating to keep, how much the box score (BPM) is worth, and
how far from average a newcomer may be. Shared by scripts/build_rating_tracker.py
(the app's tables) and scripts/paper_eval.py (the paper's protocol), so the
two never diverge. Import-safe: no database work at import.

The model, in build_rapm's units (points per 100 possessions, rows weighted
by possessions, offence +1 / defence -1 columns, one intercept and one home
term a season):

  state       beta_t = (offence rating, defence rating) of every player who
              has appeared so far, plus the season's intercept and home term;
  transition  between seasons, for a player seen before:
                  beta_{t+1} = phi * beta_t + eta,   eta ~ N(0, sigma^2 / lambda_q)
              phi = 1 is a pure random walk (last season's rating, with more
              uncertainty); phi < 1 pulls old ratings back toward average.
              A player's first season starts at N(0, sigma^2 / lambda_0): the
              ordinary ridge prior. The intercept and home term are new every
              season (variance NUISANCE_VAR: effectively unpenalised);
  stints      y = X beta_t + e, Var(e) = sigma^2 / possessions: the same rows
              build_rapm.py regresses;
  box score   k * OBPM and k * DBPM (Basketball-Reference, that season) are read
              as noisy measurements of the offence and defence state, each
              with variance sigma^2 / lambda_b, and folded into the season's
              prior before its stints are seen. So the BPM prior is still
              there, but its weight (lambda_b) and scale (k) are estimated
              instead of fixed, and it combines with what last season's
              stints said instead of replacing it. BPM is conditioned on,
              never scored: the likelihood below is of the stint rows alone
              (scoring the BPM rows too would let the model "explain" them
              perfectly with k = 0 and an infinite lambda_b, which a first
              version did).

Why lambdas: every variance is sigma^2 / lambda, so the posterior means are
ridge solutions (build_rapm.Design.solve with a full prior precision matrix),
sigma^2 drops out of the means and is profiled out of the likelihood, and
lambda_0 is build_rapm's lambda when phi = 0 and lambda_q = lambda_0 (that is
the equivalence check build_rating_tracker.py stores: the tracker with those
settings reproduces build_rapm's one-season ridge toward a scaled BPM).

Estimation (Kalman filter in information form; the player count is ~900 so
the full covariance is a 1,850 x 1,850 dense matrix): seasons are processed
in order; the posterior after season t is prior precision + the BPM
measurement precisions + the season's X'WX, solved by Cholesky. The five
hyperparameters (lambda_0, lambda_q, lambda_b, k, phi) are chosen on the
protocol's tune seasons only (TUNE_SEASONS = paper_eval.TUNE) and then held
fixed, so the validate and test seasons are out of sample. The criterion is
the protocol's own: the pooled next-season game-margin RMSE over the tune
pairs (2020-21 -> 2021-22, 2021-22 -> 2022-23, 2022-23 -> 2023-24) from the
filtered ratings after each season, exactly the number every other impact
model's lambda and prior scale were chosen by in paper_eval.py. The marginal
likelihood of the stint rows (prediction-error decomposition with the matrix
determinant lemma, sigma^2 profiled out; nothing of size rows x rows is ever
formed) is also maximised and stored as a comparison, not used: on one
season it lands where build_rapm's cross-validation does (lambda about
3,000), but across the hyperparameters it overweights BPM, because a
season's BPM carries that season's point differential (its team adjustment)
and so predicts the same season's stints better than it predicts anything
out of sample. It still supplies sigma^2, the stint noise variance behind
every standard deviation. The smoother (Rauch-Tung-Striebel) gives the
with-hindsight estimate of every season from all seasons: the app shows it
as the career line; the paper scores only the filtered estimate, which
uses nothing after its season.

Nothing here is random: no bootstrap, the posterior covariance gives the
standard deviations (sqrt(sigma^2 * P_ii)).
"""

import math

import numpy as np
import scipy.linalg as sla
from scipy.optimize import minimize

import build_rapm as R

TUNE_SEASONS = (2021, 2022, 2023, 2024)          # == paper_eval.TUNE (asserted there)
NUISANCE_VAR = 1e6                               # ridge units: the intercept and home term are effectively unpenalised
PARAMS = ("lambda0", "lambda_q", "lambda_b", "prior_scale", "phi")
BOUNDS = {"lambda0": (10.0, 1e6), "lambda_q": (10.0, 1e6), "lambda_b": (10.0, 1e6), "prior_scale": (0.0, 2.0), "phi": (0.3, 1.0)}
STARTS = (
    {"lambda0": 1500.0, "lambda_q": 3000.0, "lambda_b": 1500.0, "prior_scale": 1.0, "phi": 0.9},   # build_rapm's prior version, carried
    {"lambda0": 3000.0, "lambda_q": 1000.0, "lambda_b": 3000.0, "prior_scale": 0.7, "phi": 0.7},   # a different corner
)
DRIFT_GRID = (250, 500, 1000, 2000, 3000, 4000, 6000, 8000, 12000, 16000, 24000, 32000, 64000, 128000)


# ── Data in one global index ─────────────────────────────────────────────────

class TrackerData:
    """Every season's build_rapm.Design in one global player index, with each season's gram (X'WX), X'Wy,
    y'Wy, sum of log weights, and the BPM measurements. The state is [offence x P, defence x P, intercept, home]."""

    def __init__(self, designs, bpm, folds=False):
        self.seasons = sorted(designs)
        self.designs = designs
        self.players = sorted(set().union(*(set(d.players) for d in designs.values())))
        self.gidx = {p: i for i, p in enumerate(self.players)}
        self.P = len(self.players)
        self.D = 2 * self.P + 2
        self.first = {p: min(s for s in self.seasons if p in designs[s].pidx) for p in self.players}
        first_arr = np.array([self.first[p] for p in self.players])
        # state entries (offence and defence) of the players active by season s (first season <= s)
        self.active = {s: np.concatenate([first_arr <= s, first_arr <= s]) for s in self.seasons}
        self.cols, self.gram, self.rhs, self.yWy, self.sum_log_w, self.n_rows = {}, {}, {}, {}, {}, {}
        self.meas_idx, self.meas_val = {}, {}
        self.fold_gram = {}
        for s in self.seasons:
            d = designs[s]
            assert d.S == 1, "one season per design"
            self.cols[s] = self.local_to_global(d)
            G, b, _ = d.gram()
            self.gram[s], self.rhs[s] = G, b
            self.yWy[s] = float(np.sum(d.w * d.y * d.y))
            self.sum_log_w[s] = float(np.sum(np.log(d.w)))
            self.n_rows[s] = int(d.n)
            idx, val = [], []
            for p in d.players:
                if (p, s) in bpm:
                    o, de = bpm[(p, s)][0], bpm[(p, s)][1]
                    idx += [self.gidx[p], self.P + self.gidx[p]]
                    val += [o, de]
            self.meas_idx[s] = np.array(idx, dtype=int)
            self.meas_val[s] = np.array(val, dtype=float)
            if folds:
                self.fold_gram[s] = {k: d.gram(mask=d.fold == k)[:2] for k in sorted(set(d.fold))}

    def local_to_global(self, d):
        P = self.P
        off = [self.gidx[p] for p in d.players]
        de = [P + self.gidx[p] for p in d.players]
        return np.array(off + de + [2 * P, 2 * P + 1], dtype=int)


# ── The filter ───────────────────────────────────────────────────────────────

def _chol_inv(M):
    """(inverse, log determinant) of a symmetric positive-definite matrix by Cholesky."""
    c, low = sla.cho_factor(M, lower=True, check_finite=False)
    logdet = 2.0 * float(np.sum(np.log(np.diag(c))))
    inv, info = sla.lapack.dpotri(c, lower=1)
    if info:
        raise np.linalg.LinAlgError(f"dpotri failed ({info})")
    inv = np.tril(inv) + np.tril(inv, -1).T
    return inv, logdet


class Filter:
    """One forward pass at fixed hyperparameters over `seasons` (a prefix of data.seasons, in order).
    Stores per season the posterior mean (always), the likelihood pieces (always) and, with keep=True, the
    prior (m_prior, P_prior), the prior after BPM (m_prior_bpm) and the posterior covariance (P)."""

    def __init__(self, data, par, seasons=None, keep=True):
        self.data, self.par = data, dict(par)
        self.seasons = list(seasons or data.seasons)
        assert self.seasons == data.seasons[:len(self.seasons)], "seasons must be a prefix of the data's, in order"
        self.keep = keep
        self.m_prior, self.P_prior, self.m, self.P, self.m_prior_bpm = {}, {}, {}, {}, {}
        self.pieces = {}
        self._run()

    def _initial_prior(self):
        D, P = self.data.D, self.data.P
        Pm = np.zeros((D, D))
        np.fill_diagonal(Pm, 1.0 / self.par["lambda0"])
        Pm[2 * P, 2 * P] = Pm[2 * P + 1, 2 * P + 1] = NUISANCE_VAR
        return np.zeros(D), Pm

    def _transition(self, s_prev, m, P):
        """Prior for the season after s_prev from the posterior (m, P) of s_prev."""
        D, Pn = self.data.D, self.data.P
        phi, lam_q, lam0 = self.par["phi"], self.par["lambda_q"], self.par["lambda0"]
        act = np.where(self.data.active[s_prev])[0]
        mm = np.zeros(D)
        Pm = np.zeros((D, D))
        np.fill_diagonal(Pm, 1.0 / lam0)                       # inert and newly arriving players
        mm[act] = phi * m[act]
        Pm[np.ix_(act, act)] = phi * phi * P[np.ix_(act, act)]
        Pm[act, act] += 1.0 / lam_q
        Pm[2 * Pn, 2 * Pn] = Pm[2 * Pn + 1, 2 * Pn + 1] = NUISANCE_VAR
        return mm, Pm

    def posterior(self, s, mm, Pm, gram=None, rhs=None):
        """Posterior for season s from the transition prior (mm, Pm): the BPM measurement folds into the prior
        first (so it is conditioned on, never scored as data), then the season's stint rows (its full gram or a
        training-fold one). Returns (m, P, logdet term, quadratic form, n, prior mean after BPM); the likelihood
        pieces are None for a fold."""
        data, par = self.data, self.par
        cols = data.cols[s]
        G = data.gram[s] if gram is None else gram
        b = data.rhs[s] if rhs is None else rhs
        A, _ = _chol_inv(Pm)                                   # transition prior precision
        mi, z = data.meas_idx[s], par["prior_scale"] * data.meas_val[s]
        lam_b = par["lambda_b"]
        Ap = A.copy()
        Ap[mi, mi] += lam_b                                    # + the BPM measurement = the prior the stints see
        rp = A @ mm
        rp[mi] += lam_b * z
        c1, low1 = sla.cho_factor(Ap, lower=True, check_finite=False)
        mmp = sla.cho_solve((c1, low1), rp, check_finite=False)
        logdet_Ap = 2.0 * float(np.sum(np.log(np.diag(c1))))
        Lam = Ap.copy()
        Lam[np.ix_(cols, cols)] += G
        r = rp.copy()
        r[cols] += b
        c, low = sla.cho_factor(Lam, lower=True, check_finite=False)
        m = sla.cho_solve((c, low), r, check_finite=False)
        logdet_post = 2.0 * float(np.sum(np.log(np.diag(c))))
        Pinv, info = sla.lapack.dpotri(c, lower=1)
        if info:
            raise np.linalg.LinAlgError(f"dpotri failed ({info})")
        P = np.tril(Pinv) + np.tril(Pinv, -1).T
        if gram is not None:
            return m, P, None, None, None, mmp
        # Prediction errors of the stint rows against the prior (after BPM), through the gram: y'Wy - 2 m'g + m'Gm,
        # minus what the update explained, u' (m_post - m_prior) with u = g - G m_prior (matrix determinant lemma).
        mc = mmp[cols]
        q = data.yWy[s] - 2.0 * float(mc @ b) + float(mc @ G @ mc)
        u = r - Lam @ mmp
        q -= float(u @ (m - mmp))
        return m, P, logdet_post - logdet_Ap, q, data.n_rows[s], mmp

    def _run(self):
        mm, Pm = self._initial_prior()
        for i, s in enumerate(self.seasons):
            if i > 0:
                mm, Pm = self._transition(self.seasons[i - 1], self.m[self.seasons[i - 1]], self.P[self.seasons[i - 1]])
            m, P, logdet, q, n, mmp = self.posterior(s, mm, Pm)
            self.pieces[s] = {"n": n, "quad": q, "logdet": logdet, "sum_log_w": self.data.sum_log_w[s],
                              "n_bpm": int(len(self.data.meas_idx[s]) // 2)}
            if self.keep:
                self.m_prior[s], self.P_prior[s] = mm, Pm
                self.m_prior_bpm[s] = mmp
            self.m[s], self.P[s] = m, P
            if not self.keep and i > 0:
                del self.P[self.seasons[i - 1]]

    # ---- likelihood -------------------------------------------------------------
    def neg2ll(self, seasons=None):
        """-2 log likelihood of the stint rows of `seasons` given the BPM priors, sigma^2 profiled out; returns (value, sigma2)."""
        seasons = list(seasons or self.seasons)
        N = sum(self.pieces[s]["n"] for s in seasons)
        Q = sum(self.pieces[s]["quad"] for s in seasons)
        logdet = sum(self.pieces[s]["logdet"] for s in seasons)
        slw = sum(self.pieces[s]["sum_log_w"] for s in seasons)
        sigma2 = Q / N
        return N * math.log(2 * math.pi * sigma2) + N - slw + logdet, sigma2

    # ---- what the models read ---------------------------------------------------
    def ratings(self, s, which="post"):
        """({player: offence}, {player: defence}) of the players active by season s: the posterior after s
        ('post', uses nothing after s), the prior before s's games ('prior', needs keep=True) or the prior
        after s's BPM measurement ('prior_bpm')."""
        m = {"post": self.m, "prior": self.m_prior, "prior_bpm": self.m_prior_bpm}[which][s]
        act = self.data.active[s]
        P = self.data.P
        o = {p: float(m[i]) for p, i in self.data.gidx.items() if act[i]}
        d = {p: float(m[P + i]) for p, i in self.data.gidx.items() if act[i]}
        return o, d

    def nuisance(self, s):
        """(intercept, home coefficient) of season s's posterior."""
        P = self.data.P
        return float(self.m[s][2 * P]), float(self.m[s][2 * P + 1])

    def sds(self, s, sigma2):
        """Per-player (offence sd, defence sd, total sd) of the posterior after s, in points per 100."""
        P, Pst = self.data.P, self.P[s]
        i = np.arange(P)
        vo, vd, cov = Pst[i, i], Pst[P + i, P + i], Pst[i, P + i]
        return np.sqrt(sigma2 * vo), np.sqrt(sigma2 * vd), np.sqrt(sigma2 * np.maximum(vo + vd + 2 * cov, 0.0))

    def predict(self, s, design_next):
        """Season s's posterior ratings on another season's rows at scale 1, with s's intercept and home term
        (paper_eval's predict_next for every impact model)."""
        o, d = self.ratings(s)
        ic, hm = self.nuisance(s)
        return design_next.X @ design_next.rating_vector(o, d) + ic + hm * design_next.sign

    def heldout_beta(self, s, fold):
        """Local coefficient vector (build_rapm.Design column order) for season s fitted on every fold but
        `fold`, from the same prior the full-season fit used. Needs TrackerData(folds=True)."""
        Gk, bk = self.data.fold_gram[s][fold]
        m = self.posterior(s, self.m_prior[s], self.P_prior[s], gram=self.data.gram[s] - Gk, rhs=self.data.rhs[s] - bk)[0]
        return m[self.data.cols[s]]

    def local_beta(self, s):
        return self.m[s][self.data.cols[s]]

    # ---- the smoother -----------------------------------------------------------
    def smooth(self):
        """Rauch-Tung-Striebel: (means, covariances) per season using every season (needs keep=True)."""
        assert self.keep
        seasons = self.seasons
        ms = {seasons[-1]: self.m[seasons[-1]].copy()}
        Ps = {seasons[-1]: self.P[seasons[-1]].copy()}
        phi = self.par["phi"]
        for t_prev, t in zip(reversed(seasons[:-1]), reversed(seasons[1:])):
            act = np.where(self.data.active[t_prev])[0]
            # J = P_t Phi' (P_prior_{t+1})^{-1}; Phi is phi on the entries carried over, 0 elsewhere
            PPhiT = np.zeros_like(self.P[t_prev])
            PPhiT[:, act] = phi * self.P[t_prev][:, act]
            c, low = sla.cho_factor(self.P_prior[t], lower=True, check_finite=False)
            J = sla.cho_solve((c, low), PPhiT.T, check_finite=False).T
            ms[t_prev] = self.m[t_prev] + J @ (ms[t] - self.m_prior[t])
            Ps[t_prev] = self.P[t_prev] + J @ (Ps[t] - self.P_prior[t]) @ J.T
        return ms, Ps


# ── Estimation ───────────────────────────────────────────────────────────────

def pack(par):
    return np.array([math.log(par["lambda0"]), math.log(par["lambda_q"]), math.log(par["lambda_b"]), par["prior_scale"], par["phi"]])


def unpack(x):
    return {"lambda0": math.exp(x[0]), "lambda_q": math.exp(x[1]), "lambda_b": math.exp(x[2]), "prior_scale": float(x[3]), "phi": float(x[4])}


def objective(data, par, seasons):
    """-2 log likelihood over `seasons` at `par` (a fresh filter each time; nothing cached). Returns (value, sigma2)."""
    f = Filter(data, par, seasons=seasons, keep=False)
    return f.neg2ll(seasons)


def next_rmse(data, par, pairs):
    """Pooled next-season game-margin RMSE over (S, S+1) pairs from the filtered ratings after each S
    (season S's intercept and home term carried, as paper_eval does for every impact model). Returns (rmse, games)."""
    last = max(s for s, _ in pairs)
    f = Filter(data, par, seasons=data.seasons[:data.seasons.index(last) + 1], keep=False)
    p_all, a_all = [], []
    for s, nxt in pairs:
        d = data.designs[nxt]
        p, a = R.game_margins(d, np.ones(d.n, bool), f.predict(s, d))
        p_all.append(p)
        a_all.append(a)
    p, a = np.concatenate(p_all), np.concatenate(a_all)
    return float(np.sqrt(np.mean((p - a) ** 2))), int(len(a))


def tune_pairs(seasons=TUNE_SEASONS):
    """The (S, S+1) pairs inside the tune seasons: the protocol's tune pairs."""
    return [(s, s + 1) for s in seasons if s + 1 in seasons]


def estimate(data, criterion, starts=STARTS, log=print, maxfev=400, name=""):
    """Minimise `criterion(par)` (a float) over the five hyperparameters: Powell with bounds, log-scale lambdas,
    from each start; the best kept. Returns (par, info) with every evaluation counted."""
    evals = []

    def f(x):
        par = unpack(x)
        v = float(criterion(par))
        evals.append((dict(par), v))
        return v

    bounds = [(math.log(BOUNDS["lambda0"][0]), math.log(BOUNDS["lambda0"][1])),
              (math.log(BOUNDS["lambda_q"][0]), math.log(BOUNDS["lambda_q"][1])),
              (math.log(BOUNDS["lambda_b"][0]), math.log(BOUNDS["lambda_b"][1])),
              BOUNDS["prior_scale"], BOUNDS["phi"]]
    results = []
    for i, start in enumerate(starts):
        n0 = len(evals)
        # the criterion is flat near its minimum (a 0.01 change in RMSE is far inside any interval), so the
        # tolerances stop the search once a line search moves it by under 1e-5 relative or 1% in a parameter
        res = minimize(f, pack(start), method="Powell", bounds=bounds,
                       options={"xtol": 1e-2, "ftol": 1e-5, "maxfev": maxfev})
        par = unpack(res.x)
        v = float(criterion(par))
        results.append({"start": dict(start), "par": par, "value": v, "evaluations": len(evals) - n0,
                        "converged": bool(res.success), "message": str(res.message)})
        log(f"  {name} start {i + 1}: {fmt(par)} -> {v:,.4f} ({len(evals) - n0} evaluations, {res.message})")
    best = min(results, key=lambda r: r["value"])
    return best["par"], {"starts": results, "best_start": results.index(best), "evaluations": len(evals), "value": best["value"],
                         "method": "Powell with bounds, log-scale lambdas", "bounds": {k: list(v) for k, v in BOUNDS.items()}}


def fmt(par):
    return (f"lambda0 {par['lambda0']:,.0f}, lambda_q {par['lambda_q']:,.0f}, lambda_b {par['lambda_b']:,.0f}, "
            f"k {par['prior_scale']:.3f}, phi {par['phi']:.3f}")


def drift_profile(data, par, pairs, seasons=TUNE_SEASONS, grid=DRIFT_GRID):
    """Along a grid of lambda_q (the drift) with the other hyperparameters at `par`: the pooled next-season RMSE
    over `pairs` and -2 log likelihood over `seasons`; `par`'s own lambda_q is added to the grid."""
    seasons = [s for s in data.seasons if s in seasons]
    out = []
    for lq in sorted(set(list(grid) + [par["lambda_q"]])):
        p_ = {**par, "lambda_q": float(lq)}
        rm, ng = next_rmse(data, p_, pairs)
        v, s2 = objective(data, p_, seasons)
        out.append({"lambda_q": float(lq), "next_rmse": rm, "next_games": ng, "neg2ll": v, "sigma2": s2,
                    "chosen": lq == par["lambda_q"]})
    return out


# ── Checks ───────────────────────────────────────────────────────────────────

def ridge_equivalent(design, bpm, s, lam, scale):
    """build_rapm's one-season ridge toward scale x BPM (its prior version) through the tracker, for season s on
    its own: with phi = 0 and lambda_q = lambda0 seasons are independent; with lambda0 + lambda_b = lam and
    k = lam * scale / lambda_b the posterior mean is exactly that ridge, once every player in the season carries a
    BPM measurement (0 where Basketball-Reference has none, which is what build_rapm's zero prior means).
    Returns (tracker local beta, ridge local beta)."""
    bpm_full = {(p, s): (bpm[(p, s)][0], bpm[(p, s)][1]) if (p, s) in bpm else (0.0, 0.0) for p in design.players}
    data = TrackerData({s: design}, bpm_full)
    lam_b = lam / 2.0
    par = {"lambda0": lam - lam_b, "lambda_q": lam - lam_b, "lambda_b": lam_b, "prior_scale": lam * scale / lam_b, "phi": 0.0}
    f = Filter(data, par, keep=False)
    pv = design.prior_vector({p: (bpm[(p, s)][0], bpm[(p, s)][1]) for p in design.players if (p, s) in bpm}) * scale
    G, b, _ = design.gram()
    # the tracker's intercept and home term have prior variance NUISANCE_VAR, the ridge's are flat: same tiny penalty
    pen = design.penalty(lam)
    pen[2 * design.P, 2 * design.P] = pen[2 * design.P + 1, 2 * design.P + 1] = 1.0 / NUISANCE_VAR
    ridge = pv + np.linalg.solve(G + pen, b - G @ pv)
    return f.local_beta(s), ridge
