"""Forecast Ledger, live scoring (round 6 step 2): what scripts/ledger_update.py stores each night and
how api/routers/ledger.py scores it. Shared by both, so the page and the stored tests can't disagree.
Not part of the lock: nothing here computes a forecast (that is the frozen code at the lock's git tag,
which ledger_update.py imports from the tag itself); this file only picks and scores stored numbers.

Versions scored, all as P(home wins) for a game:
  as_is, roster          in-season odds: each forecast's locked prior updated by the final scores of
                         every game before the game's date (ledger_lib.ratings_on + game_odds at the
                         tag), logged by ledger_update.py with the time they were computed
  record                 the record-only baseline, logged the same way: log5 of the two teams' win %
                         before the date (season_sim_lib.record_p_matrix at the tag: no home court,
                         win % clipped to 0.05-0.95, 0.5 for a team with no games yet)
  as_is_pre, roster_pre  the locked preseason odds (ledger_forecasts), never updated

The official in-season number for a game = the earliest logged row for that game and version whose
date is the date the game was actually played (a game postponed after it was logged gets new odds
for its new date). `before_tip` says whether it was logged before ESPN's tip time; a day the update
didn't run is recomputed later by the same rule and labelled so.
"""

import math

import numpy as np
import pandas as pd

IN_SEASON = ("as_is", "roster", "record")
PRESEASON = ("as_is_pre", "roster_pre")
VERSIONS = ("roster", "as_is", "record", "roster_pre", "as_is_pre")
VERSION_LABELS = {
    "roster": "Roster-aware, updated nightly",
    "as_is": "As is, updated nightly",
    "record": "Record only (log5 of win %)",
    "roster_pre": "Roster-aware, locked opening-day odds",
    "as_is_pre": "As is, locked opening-day odds",
}
# Paired comparisons (A - B: negative = A has the lower error). The first two are the plan's headline.
PAIRS = (("as_is", "record"), ("roster", "as_is"), ("roster", "record"), ("roster_pre", "as_is_pre"),
         ("as_is", "as_is_pre"), ("roster", "roster_pre"))
METRICS = ("brier", "log_loss")
CLIP = 1e-6                       # log loss clip, as season_sim_lib.log_loss and paper_tests
MIN_TEST_GAMES = 100              # judgment call: no interval is stored before 100 common games (~2 weeks)
CAL_EDGES = tuple(i / 10 for i in range(11))
LIVE_TABLES = ["ledger_results", "ledger_game_log", "ledger_team_log", "ledger_runs", "ledger_tests"]

# The official in-season row per (game, version) and the locked preseason odds, for final games that
# count in the standings. One row per game and version.
SCORED_SQL = """
WITH res AS (
    SELECT espn_id, game_date, tip_utc, home, away, home_pts, away_pts, in_lock
    FROM ledger_results
    WHERE season = %(season)s AND counts AND completed AND home_pts IS NOT NULL
), official AS (
    SELECT DISTINCT ON (g.espn_id, g.forecast) g.espn_id, g.forecast AS version, g.p_home, g.before_tip,
           g.computed_at, g.exp_margin
    FROM ledger_game_log g JOIN res r ON r.espn_id = g.espn_id AND r.game_date = g.game_date
    WHERE g.season = %(season)s
    ORDER BY g.espn_id, g.forecast, g.computed_at
)
SELECT r.espn_id, r.game_date, r.tip_utc, r.home, r.away, r.home_pts, r.away_pts, r.in_lock,
       o.version, o.p_home, o.before_tip, o.computed_at, o.exp_margin
FROM res r JOIN official o ON o.espn_id = r.espn_id
UNION ALL
SELECT r.espn_id, r.game_date, r.tip_utc, r.home, r.away, r.home_pts, r.away_pts, r.in_lock,
       f.forecast || '_pre', f.p_home, TRUE, f.locked_at, f.exp_margin
FROM res r JOIN ledger_forecasts f ON f.season = %(season)s AND f.kind = 'game' AND f.key = r.espn_id
"""


def live_tables_exist(cur):
    # Unqualified names follow the session's search_path (public by default), so the game-day test can
    # run the update on copies in a zz_ schema (api/tests/test_ledger_gameday.py).
    cur.execute("SELECT to_regclass('ledger_game_log') IS NOT NULL AND to_regclass('ledger_results') IS NOT NULL")
    return bool(cur.fetchone()[0])


def scored(conn, season):
    """Long frame: one row per scored game and version (p = P(home wins), y = home won)."""
    df = pd.read_sql(SCORED_SQL, conn, params={"season": season})
    df["y"] = (df.home_pts > df.away_pts).astype(float)
    df["p"] = df.p_home.astype(float)
    return df


def common(df, versions=VERSIONS):
    """Only games scored under every one of `versions` (the like-for-like set)."""
    d = df[df.version.isin(versions)]
    n = d.groupby("espn_id").version.nunique()
    return d[d.espn_id.isin(n.index[n == len(versions)])]


def losses(p, y):
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    q = np.clip(p, CLIP, 1 - CLIP)
    return (p - y) ** 2, -(y * np.log(q) + (1 - y) * np.log(1 - q))


def metrics(df):
    """Per version: games, Brier, log loss, share of games the favourite (p >= 0.5 side) won."""
    out = []
    for v in VERSIONS:
        d = df[df.version == v]
        if not len(d):
            continue
        b, ll = losses(d.p, d.y)
        fav = np.where(d.p >= 0.5, d.y, 1 - d.y)
        out.append({"version": v, "label": VERSION_LABELS[v], "n": int(len(d)),
                    "n_before_tip": int(d.before_tip.sum()), "brier": float(b.mean()), "log_loss": float(ll.mean()),
                    "favourite_won": float(fav.mean())})
    return out


def running(df):
    """Cumulative Brier and log loss after each date, per version (df: one row per game and version)."""
    out = []
    for v in VERSIONS:
        d = df[df.version == v].sort_values(["game_date", "espn_id"])
        if not len(d):
            continue
        b, ll = losses(d.p, d.y)
        g = pd.DataFrame({"game_date": d.game_date.to_numpy(), "b": b, "ll": ll}).groupby("game_date").agg(
            n=("b", "size"), b=("b", "sum"), ll=("ll", "sum")).cumsum()
        for day, r in g.iterrows():
            out.append({"version": v, "date": str(day), "n": int(r.n), "brier": float(r.b / r.n),
                        "log_loss": float(r.ll / r.n)})
    return out


def wilson(k, n, z=1.959964):
    """95% Wilson score interval for k successes in n."""
    if n == 0:
        return None, None
    ph = k / n
    den = 1 + z * z / n
    mid = (ph + z * z / (2 * n)) / den
    half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def calibration(df, version):
    """Ten fixed-width bins of P(home wins): games, mean predicted, home-win rate with its Wilson interval."""
    d = df[df.version == version]
    p, y = d.p.to_numpy(float), d.y.to_numpy(float)
    out = []
    for lo, hi in zip(CAL_EDGES[:-1], CAL_EDGES[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        n = int(m.sum())
        k = int(y[m].sum())
        wl, wh = wilson(k, n)
        out.append({"lo": lo, "hi": hi, "n": n, "predicted": float(p[m].mean()) if n else None,
                    "actual": k / n if n else None, "ci_lo": wl, "ci_hi": wh})
    return out
