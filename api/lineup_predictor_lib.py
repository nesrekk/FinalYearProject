"""Lineup Predictor: the math shared by scripts/build_lineup_predictor.py
(builds every five-man unit 2021-22 on, fits and scores the models) and
api/routers/lineup_predictor.py (the "try a lineup" panel on Rotations), so
the page and the stored numbers can't drift apart.

The task: predict the net rating (points per 100 possessions, for minus
against) of a five-man unit from its players, using only what was known
before the unit's first game, and score it on the possessions it then
played. A unit is one five-man combination on one team in one season.

Features (all fixed before the unit's first game)

  r_sum      the five players' pre-season ratings summed (RATING_SOURCES;
             a player with no rating gets the replacement value, the
             possession-weighted mean rating of such players on the tune
             seasons, as in the availability model)
  spacing    the five players' Gravity summed (spacing_lab's lineup
             spacing), from the season BEFORE; a player with none gets the
             replacement value the same way
  n_big, n_play, n_scorer, n_wing
             how many of the five had each role the season before
             (player_roles families; FAMILY_GROUPS); bench players and
             players with no role are the base
  two_bigs, no_handler
             2+ bigs; no playmaker (the two "fit" problems coaches name)
  usg_sum    the five players' projected usage summed, minus 1 (five
             average players use 100% of the possessions); unrated players
             at the replacement usage
  td_team    the team's net rating in its games before the unit's first
             game, shrunk: n / (n + K) x net, n = possessions (mean of the
             two sides)
  td_on      the mean over the five of each player's on-court net rating
             for this team in those games, shrunk the same way

Models (nested; MODELS): sum (the five ratings summed as they are, no fit),
scaled (intercept + slope on the sum), fit (+ spacing, roles, usage),
full (+ season to date), plus two floors: zero (every unit 0) and team
(intercept + slope on td_team alone).

Fit: possession-weighted ridge on standardised columns, intercept
unpenalised, penalty alpha x (sum of weights), so alpha is a share of the
data's own scale. Weights: the harmonic mean of the unit's offensive and
defensive possessions (the noise of a net rating is 1/n_off + 1/n_def).
"""

import numpy as np

RATING_SOURCES = {
    "bpm": "Box-score projection for the season (projection_backtest_rows: the Projections page's Marcel BPM, "
           "earlier seasons only)",
    "rapm": "Last season's RAPM with the box-score prior (player_rapm, version prior)",
    "tracker": "Last season's Rating Tracker rating (player_rating_tracker, filtered: the data through that season)",
}

RATING_SHORT = {"bpm": "the BPM projection", "rapm": "last season's RAPM", "tracker": "last season's Rating Tracker"}

FAMILY_GROUPS = {          # player_roles.family -> feature
    "Elite Two-Way Big": "n_big",
    "Rim Protector": "n_big",
    "Playmaker": "n_play",
    "Primary Scorer": "n_scorer",
    "3-and-D Wing": "n_wing",
    "Bench Role Player": None,
}

FEATURE_LABELS = {
    "r_sum": "Five ratings summed",
    "spacing": "Lineup spacing (Gravity summed, season before)",
    "n_big": "Bigs (season before)",
    "n_play": "Playmakers (season before)",
    "n_scorer": "Primary scorers (season before)",
    "n_wing": "3-and-D wings (season before)",
    "two_bigs": "Two or more bigs",
    "no_handler": "No playmaker",
    "usg_sum": "Projected usage summed, minus 100%",
    "td_team": "Team's net rating so far (shrunk)",
    "td_on": "Five players' on-court net so far (mean, shrunk)",
}

FIT_FEATURES = ["r_sum", "spacing", "n_big", "n_play", "n_scorer", "n_wing", "two_bigs", "no_handler", "usg_sum"]
MODELS = {
    "zero": [],
    "sum": None,                       # no fit: the sum itself
    "team": ["td_team"],
    "scaled": ["r_sum"],
    "fit": FIT_FEATURES,
    "full": FIT_FEATURES + ["td_team", "td_on"],
}
MODEL_LABELS = {
    "zero": "Zero (every lineup average)",
    "sum": "Sum of the five ratings",
    "team": "Team's net rating so far",
    "scaled": "Sum of ratings, scaled",
    "fit": "+ spacing, roles, usage",
    "full": "+ season so far",
}


def shrink(net, n, k):
    """n / (n + k) x net (0 when there is nothing yet)."""
    n = np.asarray(n, float)
    return np.where(n > 0, n / (n + k) * np.nan_to_num(np.asarray(net, float)), 0.0)


def net_rating(pf, po, pa, pd):
    """100 x (points per offensive possession - points allowed per defensive possession)."""
    po, pd = np.asarray(po, float), np.asarray(pd, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return 100.0 * (np.asarray(pf, float) / po - np.asarray(pa, float) / pd)


def harmonic_weight(po, pd):
    po, pd = np.asarray(po, float), np.asarray(pd, float)
    with np.errstate(divide="ignore"):
        return 2.0 / (1.0 / po + 1.0 / pd)


def fit_features(ratings, gravity, families, usage):
    """The fit-model features of one five from per-player values (ratings/gravity/usage already filled)."""
    groups = [FAMILY_GROUPS.get(f) for f in families]
    n = {g: sum(1 for x in groups if x == g) for g in ("n_big", "n_play", "n_scorer", "n_wing")}
    return {
        "r_sum": float(np.sum(ratings)),
        "spacing": float(np.sum(gravity)),
        **{k: float(v) for k, v in n.items()},
        "two_bigs": float(n["n_big"] >= 2),
        "no_handler": float(n["n_play"] == 0),
        "usg_sum": float(np.sum(usage) - 1.0),
    }


def fit_ridge(X, y, w, alpha):
    """Weighted ridge on standardised columns, intercept unpenalised.
    Returns {'intercept', 'coef' (raw scale), 'mean', 'sd'}."""
    X = np.asarray(X, float).reshape(len(y), -1)
    y, w = np.asarray(y, float), np.asarray(w, float)
    W = w.sum()
    ybar = float((w * y).sum() / W)
    if X.shape[1] == 0:
        return {"intercept": ybar, "coef": np.zeros(0), "mean": np.zeros(0), "sd": np.ones(0)}
    mu = (w[:, None] * X).sum(0) / W
    sd = np.sqrt((w[:, None] * (X - mu) ** 2).sum(0) / W)
    sd = np.where(sd > 0, sd, 1.0)
    Z = (X - mu) / sd
    A = Z.T @ (w[:, None] * Z) + alpha * W * np.eye(Z.shape[1])
    b = np.linalg.solve(A, Z.T @ (w * (y - ybar)))
    coef = b / sd
    return {"intercept": float(ybar - coef @ mu), "coef": coef, "mean": mu, "sd": sd}


def predict(fit, X):
    X = np.asarray(X, float)
    if X.ndim == 1:
        X = X.reshape(1, -1)
    if len(fit["coef"]) == 0:
        return np.full(X.shape[0], fit["intercept"])
    return fit["intercept"] + X @ np.asarray(fit["coef"], float)
