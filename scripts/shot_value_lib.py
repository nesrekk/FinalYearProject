"""
shot_value_lib.py
=================
Shooter-aware shot pricing (round 6, step 8): what an attempt was worth before
the game, given where it was taken *and who took it*. Round 5's expected-points
RAPM (scripts/paper_xrapm.py) priced every field goal with a shooter-blind
location model and so removed shooting skill along with shooting luck; this
module adds the shooter back, using only what was known before each game.
Shared by scripts/build_shot_value.py (the app's tables) and
scripts/paper_xrapm.py (the shooter-aware RAPM target), so the two can't
diverge. Import-safe: no database work at import.

The model, per shooter and skill class c (CLASSES: rim = two-pointers in the
restricted area, mid = every other two, three, ft = free throws):

    P(make) = expit(a + delta + theta)

  a      the shooter-blind log-odds of the attempt. Field goal: the location
         model's (build_shot_making's gradient boosting, same features and
         parameters) from a fit on every regular-season shot of the seasons
         *before* the priced one, by none of the shooter's fold of players
         (build_shot_value.py fits five folds per priced season). Free throw:
         the league FT% of the season before.
  delta  the league's level so far this season, per class: the one-step
         log-odds residual of every earlier attempt of the class this season
         against a, shrunk toward 0 by the year-to-year spread of the league
         rate (delta_var), so opening night has delta = 0 and a league-wide
         change in accuracy isn't credited to every shooter.
  theta  the shooter's skill on the log-odds scale, a hidden state: at the
         player's NBA debut theta ~ N(mu0, v0); between consecutive seasons
         theta' = phi * theta + eta, eta ~ N(0, q) (seasons he missed are
         transitions with no data); constant within a season. Each class has
         its own (mu0, v0, phi, q), estimated by empirical Bayes: the Laplace
         marginal likelihood of the 2010-11 to 2019-20 attempts (FIT_SEASONS),
         priced by the 2020-21 models, so no priced season informs them
         (Nelder-Mead from three starts, then a polish from the best).

Updating: the posterior after a batch of attempts is the Laplace approximation
(Newton from the prior mean to the mode; precision = prior precision + the
Fisher information there). Seasons before the priced one are one batch each;
in the priced season the batches are game dates, in order, and an attempt is
priced with the state *before* its date, so its own outcome and every later
game are never in its price. A price is the plug-in expit(a + delta + mean),
not the posterior expectation (the difference is of the order of the posterior
variance, ~1e-3 in probability).

Judgment calls (also on the Methodology card and in shot_value_fit):
  * four classes, not one skill per shooter: finishing at the rim, other twos,
    threes and free throws are different skills; one offset would spread a
    shooter's threes over his layups;
  * the state starts at the NBA debut (player_first_season, or the first season
    seen in the chart or the game lines if earlier or missing), so a veteran in
    2010-11 enters with the debut prior carried through his earlier seasons,
    not as a rookie; field goals are read from 2010-11 (WINDOW_FROM: before it
    a quarter of shots have no location), free throws too;
  * delta uses one shrinkage per class (the variance of the year-to-year
    change of the league log-odds, 2010-11 to 2019-20), so the first weeks of a
    season move it little;
  * skill doesn't move within a season (no in-season drift term): the in-season
    updates are ordinary Bayesian updates of a fixed season skill.

Fold exclusion (for the protocol's held-out-games task): every function that
updates takes `exclude` (a boolean per attempt row): excluded attempts are
priced but update neither the shooter nor the league level, so a fit on four
folds of games never sees the fifth fold's outcomes through the prices.
"""

import numpy as np
import pandas as pd

CLASSES = ("rim", "mid", "three", "ft")
FG_CLASSES = ("rim", "mid", "three")
PARAMS = ("mu0", "v0", "phi", "q")
WINDOW_FROM = 2011                    # 2010-11: first season with every chart shot located
FIT_SEASONS = tuple(range(2011, 2021))  # hyperparameters: 2010-11 to 2019-20
PRICED = tuple(range(2021, 2027))     # 2020-21 to 2025-26 (the lineup-stint seasons)
NEWTON = 8                            # Newton steps per Laplace update (converged to 1e-10 on the data)
A_CLIP = 8.0                          # |log-odds| cap on a blind price
PHI_MAX = 0.999
# paper_xrapm_stints.fold_xpts: per stint, the lf and sa targets with each held-out fold's games excluded from every
# price update, five entries (folds 0-4) per (version, side) in this order. Written by paper_xrapm.py, read by
# paper_eval.py.
FOLD_LAYOUT = (("lf", "home"), ("lf", "away"), ("sa", "home"), ("sa", "away"))


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p) - np.log1p(-p)


def expit(x):
    return 0.5 * (1.0 + np.tanh(0.5 * np.asarray(x, float)))


def log_expit(x):
    return -np.logaddexp(0.0, -x)


def shot_class(zone, is3):
    """0 rim (a two in the restricted area, build_shot_making.zone_codes 0), 1 mid (any other two), 2 three."""
    zone, is3 = np.asarray(zone), np.asarray(is3)
    return np.where(is3 == 1, 2, np.where(zone == 0, 0, 1)).astype(np.int8)


# ── The state ────────────────────────────────────────────────────────────────

def transition(m, v, gap, par):
    """The state after `gap` seasons (array or scalar, >= 0) with no data."""
    phi, q = par["phi"], par["q"]
    gap = np.asarray(gap, float)
    f = phi ** gap
    add = q * gap if phi == 1.0 else q * (1.0 - phi ** (2.0 * gap)) / (1.0 - phi * phi)
    return m * f, v * f * f + add


def laplace(m, v, g, a, y, w, iters=NEWTON):
    """Posterior mode, variance and Laplace log marginal likelihood per group, from a prior N(m, v) per group
    (arrays over groups) and Bernoulli attempts (log-odds offset a, outcome y in {0, 1}, count w) in group g."""
    G = len(m)
    P = 1.0 / v
    th = np.array(m, float)
    for _ in range(iters):
        p = expit(a + th[g])
        grad = np.bincount(g, weights=w * (y - p), minlength=G) - P * (th - m)
        hess = np.bincount(g, weights=w * p * (1.0 - p), minlength=G) + P
        th = th + grad / hess
    x = a + th[g]
    p = expit(x)
    info = np.bincount(g, weights=w * p * (1.0 - p), minlength=G)
    ll = np.bincount(g, weights=w * (y * log_expit(x) + (1.0 - y) * log_expit(-x)), minlength=G)
    lml = ll - 0.5 * P * (th - m) ** 2 + 0.5 * np.log(P / (P + info))
    return th, 1.0 / (P + info), lml


class Attempts:
    """One class's attempts of several seasons: player index, season, log-odds offset, outcome, count. Free throws
    come as two rows per player and batch (made with w = makes, missed with w = misses)."""

    def __init__(self, player, season, a, y, w):
        self.player = np.asarray(player, np.int64)
        self.season = np.asarray(season, np.int64)
        self.a = np.clip(np.asarray(a, float), -A_CLIP, A_CLIP)
        self.y = np.asarray(y, float)
        self.w = np.asarray(w, float)
        keep = self.w > 0
        for k in ("player", "season", "a", "y", "w"):
            setattr(self, k, getattr(self, k)[keep])
        order = np.argsort(self.season, kind="stable")
        for k in ("player", "season", "a", "y", "w"):
            setattr(self, k, getattr(self, k)[order])
        self.seasons = np.unique(self.season)
        self.bounds = {int(s): (int(np.searchsorted(self.season, s, "left")), int(np.searchsorted(self.season, s, "right")))
                       for s in self.seasons}


def season_filter(par, debut, att, until):
    """Season-by-season filter over att's seasons < until. debut: array over players (NBA debut season).
    Returns (m, v, at, lml): the state per player as of season `at` (the last season with data, or the debut with
    the debut prior when there was none) and the summed Laplace log marginal likelihood of the attempts."""
    n = len(debut)
    m = np.full(n, par["mu0"], float)
    v = np.full(n, par["v0"], float)
    at = np.asarray(debut, np.int64).copy()
    total = 0.0
    for s in att.seasons:
        if s >= until:
            break
        lo, hi = att.bounds[int(s)]
        pl = att.player[lo:hi]
        idx, g = np.unique(pl, return_inverse=True)
        gap = s - at[idx]
        assert (gap >= 0).all(), "an attempt before the player's debut"
        mp, vp = transition(m[idx], v[idx], gap, par)
        th, vv, lml = laplace(mp, vp, g, att.a[lo:hi], att.y[lo:hi], att.w[lo:hi])
        m[idx], v[idx], at[idx] = th, vv, s
        total += float(lml.sum())
    return m, v, at, total


def season_start(par, debut, att, season):
    """Every player's prior for `season` (mean, variance) from the seasons before it; a player who debuts in it
    starts at the debut prior."""
    m, v, at, _ = season_filter(par, debut, att, season)
    gap = np.maximum(season - at, 0)
    return transition(m, v, gap, par)


def fit_class(debut, att, seasons, start=None):
    """Empirical Bayes: (mu0, v0, phi, q) maximising the Laplace marginal likelihood of the attempts in `seasons`
    (the filter starts at the first of them). Returns (params, -2 log likelihood, optimiser report)."""
    from scipy.optimize import minimize

    last = max(seasons) + 1
    keep = np.isin(att.season, list(seasons))
    sub = Attempts(att.player[keep], att.season[keep], att.a[keep], att.y[keep], att.w[keep])

    def unpack(x):
        return {"mu0": float(x[0]), "v0": float(np.exp(x[1])), "phi": float(PHI_MAX * expit(x[2])), "q": float(np.exp(x[3]))}

    def f(x):
        return -2.0 * season_filter(unpack(x), debut, sub, last)[3]

    s = start or {"mu0": 0.0, "v0": 0.05, "phi": 0.8, "q": 0.01}
    x0 = np.array([s["mu0"], np.log(s["v0"]), logit(s["phi"] / PHI_MAX), np.log(s["q"])])
    best, starts = None, []
    for x_start in (x0, x0 + np.array([0.0, 1.0, 1.0, 1.0]), x0 - np.array([0.0, 1.0, 1.0, 1.0])):
        r = minimize(f, x_start, method="Nelder-Mead", options={"xatol": 1e-5, "fatol": 1e-4, "maxiter": 4000, "maxfev": 6000})
        starts.append({"start": {k: round(v, 6) for k, v in unpack(x_start).items()}, "n2ll": float(r.fun),
                       "params": {k: round(v, 6) for k, v in unpack(r.x).items()}, "evaluations": int(r.nfev), "converged": bool(r.success)})
        if best is None or r.fun < best.fun:
            best = r
    # A start can stall where phi meets PHI_MAX (the logistic transform is flat there; for threes two of three did, 18-22
    # units of -2LL worse): one more run from the best point confirms it is a minimum and not a stop.
    r = minimize(f, best.x, method="Nelder-Mead", options={"xatol": 1e-6, "fatol": 1e-5, "maxiter": 4000, "maxfev": 6000})
    starts.append({"start": "polish from the best", "n2ll": float(r.fun), "params": {k: round(v, 6) for k, v in unpack(r.x).items()},
                   "evaluations": int(r.nfev), "converged": bool(r.success)})
    if r.fun < best.fun:
        best = r
    par = unpack(best.x)
    return par, float(best.fun), {"evaluations": int(best.nfev), "converged": bool(best.success), "message": str(best.message),
                                  "starts": starts}


# ── The priced season ────────────────────────────────────────────────────────

def in_season(par, start_m, start_v, att_player, att_date, att_a, att_y, att_w, delta_var, exclude=None, update=True):
    """One class, one priced season, game date by game date. Each attempt row is priced with the league level and
    the shooter's state from the dates before its own. Returns a dict:
      delta, theta  per attempt row (league level and shooter mean before its date);
      log           DataFrame (player, date, mean, var): the state after each date the player had an update;
      end_m, end_v  the state after the season;
      league        DataFrame (date, delta_after) for the class.
    `exclude` rows are priced but update nothing; `update=False` keeps every shooter at his season-start state."""
    n = len(att_player)
    excl = np.zeros(n, bool) if exclude is None else np.asarray(exclude, bool)
    a = np.clip(np.asarray(att_a, float), -A_CLIP, A_CLIP)
    y, w = np.asarray(att_y, float), np.asarray(att_w, float)
    pl = np.asarray(att_player, np.int64)
    order = np.argsort(np.asarray(att_date), kind="stable")
    dates = np.asarray(att_date)[order]
    cut = np.flatnonzero(np.r_[True, dates[1:] != dates[:-1], True])
    m, v = np.array(start_m, float), np.array(start_v, float)
    delta = np.zeros(n)
    theta = np.zeros(n)
    R = V = 0.0
    prec_delta = 1.0 / delta_var
    logs, league = [], []
    for i in range(len(cut) - 1):
        rows = order[cut[i]:cut[i + 1]]
        d_now = R / (V + prec_delta)
        delta[rows] = d_now
        theta[rows] = m[pl[rows]]
        use = rows[~excl[rows]]
        if len(use):
            p0 = expit(a[use])
            R += float(np.sum(w[use] * (y[use] - p0)))
            V += float(np.sum(w[use] * p0 * (1.0 - p0)))
            if update:
                idx, g = np.unique(pl[use], return_inverse=True)
                th, vv, _ = laplace(m[idx], v[idx], g, a[use] + d_now, y[use], w[use])
                m[idx], v[idx] = th, vv
                logs.append(pd.DataFrame({"player": idx, "date": dates[cut[i]], "mean": th, "var": vv}))
        league.append((dates[cut[i]], R / (V + prec_delta)))
    log = pd.concat(logs, ignore_index=True) if logs else pd.DataFrame(columns=["player", "date", "mean", "var"])
    return {"delta": delta, "theta": theta, "log": log, "end_m": m, "end_v": v,
            "league": pd.DataFrame(league, columns=["date", "delta_after"])}


def state_before(log, start_m, start_v, player, date):
    """The shooter's (mean, var) before `date` (strictly earlier updates only), for arbitrary (player, date)
    queries: the last logged state on an earlier date, else the season-start state."""
    q = pd.DataFrame({"player": np.asarray(player, np.int64), "date": np.asarray(date), "k": np.arange(len(player))})
    out_m = np.asarray(start_m, float)[q.player.to_numpy()]
    out_v = np.asarray(start_v, float)[q.player.to_numpy()]
    if len(log):
        lg = log.copy()
        lg["player"] = lg.player.astype(np.int64)
        lg = lg.sort_values("date")
        qs = q.sort_values("date")
        mg = pd.merge_asof(qs, lg, on="date", by="player", allow_exact_matches=False, direction="backward")
        hit = mg["mean"].notna().to_numpy()
        k = mg.k.to_numpy()
        out_m[k[hit]] = mg["mean"].to_numpy()[hit]
        out_v[k[hit]] = mg["var"].to_numpy()[hit]
    return out_m, out_v


def league_delta_var(rates):
    """delta's prior variance for one class: the variance of the year-to-year change of the league log-odds over
    the given seasons ({season: league make rate})."""
    s = sorted(rates)
    d = np.diff(logit([rates[k] for k in s]))
    return float(np.mean(d ** 2))


def in_fg_pct(theta, base):
    """A log-odds skill as percentage points at a reference make rate."""
    return 100.0 * (expit(logit(base) + np.asarray(theta, float)) - base)
