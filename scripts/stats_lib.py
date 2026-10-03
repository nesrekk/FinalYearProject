"""
stats_lib.py
=============
Small shared statistics helpers that this project would otherwise pull
from statsmodels (not installed; not worth a new dependency for one
estimator).

wls_cluster(): weighted least squares with cluster-robust ("sandwich")
standard errors, using the standard CR1 small-sample correction
G/(G-1) * (n-1)/(n-k) and t(G-1) intervals — the same defaults
statsmodels uses for cov_type='cluster'. Checked by simulation when
written (see build_gravity_index.py's docstring): the reported SE matched
the real spread of estimates across repeated simulated samples.
"""

import numpy as np
from scipy import stats


def wls_cluster(y, X, w, groups):
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    w = np.asarray(w, float)
    sw = np.sqrt(w)
    Xw, yw = X * sw[:, None], y * sw
    beta, *_ = np.linalg.lstsq(Xw, yw, rcond=None)
    resid_w = yw - Xw @ beta
    bread = np.linalg.pinv(Xw.T @ Xw)
    groups = np.asarray(groups)
    uniq = np.unique(groups)
    meat = np.zeros((X.shape[1], X.shape[1]))
    for g in uniq:
        m = groups == g
        s = Xw[m].T @ resid_w[m]
        meat += np.outer(s, s)
    n, k, G = X.shape[0], X.shape[1], len(uniq)
    c = G / (G - 1) * (n - 1) / (n - k)
    cov = c * bread @ meat @ bread
    se = np.sqrt(np.diag(cov))
    tcrit = stats.t.ppf(0.975, G - 1)
    tstat = beta / se
    p = 2 * stats.t.sf(np.abs(tstat), G - 1)
    ybar = np.average(y, weights=w)
    r2 = 1 - np.sum(w * (y - X @ beta) ** 2) / np.sum(w * (y - ybar) ** 2)
    return {
        "beta": beta, "se": se, "p": p,
        "ci_low": beta - tcrit * se, "ci_high": beta + tcrit * se,
        "r2": float(r2), "n": int(n), "n_clusters": int(G),
        "cov": cov, "tcrit": float(tcrit),
    }
