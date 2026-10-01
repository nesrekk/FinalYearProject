"""
coaching_lib.py
===============
Shared by scripts/build_coaching_decisions.py (which stores the tests) and api/routers/coaching.py (which
re-reads the stored decision points by team and season): the four decisions' names and labels, and the
stratified effect on the treated, so there is one implementation of the estimator.

ATT = sum over strata of n_treated x (treated mean - control mean) / sum of n_treated, over the strata that hold
both treated and control units; with weights `w` (a bootstrap resample), every count and sum is weighted.
"""

import math

import numpy as np

DECISIONS = ("timeout", "challenge", "foul_up3", "twoforone")
LABELS = {
    "timeout": "Timeout after an 8-0 run: victim's net points, next 6 possessions",
    "challenge": "Coach's challenge won vs lost: challenger's win-probability change over two possessions",
    "foul_up3": "Up 3 late, foul vs defend: leader's chance of winning",
    "twoforone": "End of quarters 1-3, shoot early (2-for-1) vs not: net points to the end of the quarter",
}
UNIT_LABELS = {"timeout:team": "Timeout after a run, per team", "twoforone:team": "2-for-1, per team",
               "challenge:team": "Challenge success above the league's, per team"}


def att(y, t, s, n_strata=None, w=None):
    """(ATT, treated mean, matched control mean, strata used) for outcomes `y`, treatment flags `t` and integer
    stratum codes `s` (0..n_strata-1). NaNs when no stratum holds both groups."""
    y = np.asarray(y, float)
    t = np.asarray(t, bool)
    s = np.asarray(s, int)
    S = int(s.max()) + 1 if n_strata is None and len(s) else (n_strata or 0)
    w = np.ones(len(y)) if w is None else np.asarray(w, float)
    n1 = np.bincount(s, w * t, S)
    n0 = np.bincount(s, w * ~t, S)
    s1 = np.bincount(s, w * t * y, S)
    s0 = np.bincount(s, w * ~t * y, S)
    ok = (n1 > 0) & (n0 > 0)
    if not ok.any():
        return math.nan, math.nan, math.nan, 0
    m1 = s1[ok] / n1[ok]
    m0 = s0[ok] / n0[ok]
    wt = n1[ok] / n1[ok].sum()
    return float(np.sum(wt * (m1 - m0))), float(np.sum(wt * m1)), float(np.sum(wt * m0)), int(ok.sum())


def wilson(k, n, z=1.959964):
    """95% Wilson interval for k successes in n."""
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h
