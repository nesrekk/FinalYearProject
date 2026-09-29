"""
build_shot_making.py
====================
Expected FG% for every regular-season shot on file, and from it, per
player-season:

  shot quality  = the eFG% an average shooter would post on that player's
                  own shots (the model's expected eFG%);
  shot-making   = the player's actual eFG% minus that expected eFG%, in
                  eFG points, with a binomial standard error;
  points above  = 2 × FGA × shot-making: points scored beyond what an
                  average shooter would have scored on the same shots.

Writes player_shot_making, shot_making_league and shot_making_validation,
read by GET /shots/shot-making/* (routers/shot_making.py) and the player
profile.

Data: every regular-season shot in player_shots (game_id '002…'), 1996-97
to 2025-26 (~5.93M shots). shot_distance is not used: 11-17% of threes a
season carry 0 there while their coordinates say 23 ft, so distance is computed
from loc_x/loc_y. Before 2010-11 about a quarter of shots (nearly all at
the rim) have no exact location and sit at (0, 0); they are kept, and the
season input lets the model treat them era by era.

Per-shot inputs: x, y, |x|, distance, angle, zone (the same
shots_lib.classify_zone rule as every other shot feature, vectorised and
checked against it), 2- or 3-pointer, period, seconds left in the period,
season. NOT on file for any shot: closest-defender distance and shot type
(catch-and-shoot vs pull-up), so "shot-making" also carries the defence a
player faced and the shots he created for himself.

Model choice (stored in shot_making_validation, scope 'holdout'): a
logistic regression on engineered terms and a HistGradientBoosting
classifier are both trained on 1996-97 to 2024-25 and scored on 2025-26,
which neither saw, by log loss, next to a constant and a zone baseline
(last training season's FG% overall / per zone). Reliability bins on the
held-out season show calibration. The better model is used below.

Player values are cross-fitted by player (5 folds): every shot is scored
by a model that never saw any shot by that player, so a great shooter's
own makes can't raise the bar he's measured against. The cross-fit
predictions are scored again over all seasons (scope 'crossfit'), with
per-season calibration in shot_making_league.

Shot quality map (added 2026-09-29): while the cross-fitted predictions are
in memory, every shot with a recorded location is also binned into the
hexagon grid of api/shot_hex.py (2.08 ft across) and, per qualified
player-season (200+ FGA) and per league season, the cell's attempts, makes and
expected makes are stored (player_shot_hex, shot_hex_league, shot_hex_meta:
per-cell arrays keep it ~20 MB instead of ~100). Shots beyond half court and,
before 2010-11, the ~25% the NBA gave no location (stored at exactly (0, 0))
are in no cell; each player-season keeps their counts ("off the map") so the
totals reconcile with player_shot_making. This adds tables only: player_shot_making,
shot_making_league and shot_making_validation come out identical to a run
without it (checked against a snapshot).

Runtime: about 15-25 minutes (six boosting fits on ~5M shots each).

Usage:
    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 scripts/build_shot_making.py
"""

import io
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from db_config import DB_CONFIG

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
import shot_hex as H  # noqa: E402
from shots_lib import ZONES, classify_zone  # noqa: E402

MIN_FGA = 200            # a season below this is stored but not ranked (greyed out in the UI)
N_FOLDS = 5              # cross-fitting folds, split by player
HOLDOUT_SEASON = 2026    # 2025-26: scored by models trained on every earlier season
LOGREG_SAMPLE = 3_000_000  # logistic regression fits on a random subset of training shots (memory)
SEED = 0

HGB_PARAMS = dict(
    max_iter=600, learning_rate=0.08, max_leaf_nodes=63, min_samples_leaf=500,
    l2_regularization=1.0, early_stopping=True, validation_fraction=0.05,
    n_iter_no_change=25, random_state=SEED,
)
# Column order of the boosting feature matrix.
HGB_FEATURES = ["x", "y", "ax", "dist", "angle", "zone", "is3", "period", "clock", "season"]
HGB_CATEGORICAL = [HGB_FEATURES.index("zone"), HGB_FEATURES.index("period")]


def label(season):
    return f"{season - 1}-{str(season)[-2:]}"


# ─── Load ───────────────────────────────────────────────────────────────────

def load_shots(conn):
    # ORDER BY id: the boosting bin thresholds and the logistic-regression subsample depend on row order,
    # so reading in the table's physical order made two builds of the same data differ slightly (found
    # 2026-09-29); id order makes a rebuild reproducible whatever the physical order.
    t = time.time()
    buf = io.StringIO()
    with conn.cursor() as cur:
        cur.copy_expert(
            """COPY (SELECT player_id, player_name, substr(season, 1, 4)::int + 1 AS season,
                            loc_x, loc_y, shot_made_flag AS made,
                            (shot_type = '3PT Field Goal')::int AS is3, period,
                            minutes_remaining * 60 + seconds_remaining AS clock
                     FROM player_shots WHERE game_id LIKE '002%' ORDER BY id)
               TO STDOUT WITH (FORMAT CSV, HEADER)""",
            buf,
        )
    buf.seek(0)
    df = pd.read_csv(buf, dtype={
        "player_id": "int32", "player_name": "string", "season": "int16", "loc_x": "int32", "loc_y": "int32",
        "made": "int8", "is3": "int8", "period": "int8", "clock": "int16",
    })
    del buf
    n0 = len(df)
    # Impossible locations (a handful of rows such as loc_x = -16398).
    bad = (df.loc_x.abs() > 300) | (df.loc_y < -60) | (df.loc_y > 950)
    df = df[~bad].reset_index(drop=True)
    print(f"loaded {n0:,} regular-season shots in {time.time() - t:.0f}s; dropped {int(bad.sum())} with impossible locations")
    return df


def zone_codes(x, y, is3):
    """Vectorised shots_lib.classify_zone (coordinate rule; every stored row
    has a NULL shot_zone_basic). 0 RA, 1 paint, 2 mid, 3 corner 3, 4 ATB 3."""
    ax = np.abs(x)
    z = np.full(len(x), 2, dtype=np.int8)
    z[np.hypot(x, y) <= 40] = 0
    z[(ax <= 80) & (y <= 137.5) & (z != 0)] = 1
    three = is3 == 1
    z[three] = 4
    z[three & (ax > 220) & (y < 92)] = 3
    return z


def add_features(df):
    x = df.loc_x.to_numpy(dtype=np.float32)
    y = df.loc_y.to_numpy(dtype=np.float32)
    is3 = df.is3.to_numpy()
    df["x"] = x
    df["y"] = y
    df["ax"] = np.abs(x)
    df["dist"] = (np.hypot(x, y) / 10).astype(np.float32)
    df["angle"] = np.arctan2(np.abs(x), y).astype(np.float32)   # 0 straight on, ~pi/2 along the baseline
    df["zone"] = zone_codes(x, y, is3)
    df["period"] = np.minimum(df.period.to_numpy(), 5).astype(np.int8)   # 5 = any overtime
    df["clock"] = df.clock.to_numpy().astype(np.int16)

    # Parity check against the shared classifier on a random sample.
    rng = np.random.default_rng(SEED)
    idx = rng.choice(len(df), size=min(200_000, len(df)), replace=False)
    sample = df.iloc[idx]
    ref = np.array([ZONES.index(classify_zone(int(a), int(b), None, "3PT" if c else "2PT", None))
                    for a, b, c in zip(sample.loc_x, sample.loc_y, sample.is3)], dtype=np.int8)
    mism = int((ref != sample.zone.to_numpy()).sum())
    assert mism == 0, f"vectorised zones disagree with shots_lib.classify_zone on {mism} of {len(idx)} sampled shots"
    print(f"zone rule matches shots_lib.classify_zone on all {len(idx):,} sampled shots")
    return df


# ─── Models ─────────────────────────────────────────────────────────────────

def hgb_matrix(df):
    return np.column_stack([df[c].to_numpy(dtype=np.float32) for c in HGB_FEATURES])


def fit_hgb(X, y):
    model = HistGradientBoostingClassifier(categorical_features=HGB_CATEGORICAL, **HGB_PARAMS)
    model.fit(X, y)
    return model


def logreg_matrix(df, seasons):
    """Engineered, interpretable terms: distance polynomial (separately for
    twos and threes), zone, angle, behind-the-backboard, period, end-of-
    period clock flags and a season dummy (a season outside the training
    range is scored as the nearest training season)."""
    dist = df.dist.to_numpy(dtype=np.float64)
    is3 = df.is3.to_numpy(dtype=np.float64)
    angle = df.angle.to_numpy(dtype=np.float64)
    y = df.y.to_numpy(dtype=np.float64)
    clock = df.clock.to_numpy(dtype=np.float64)
    period = df.period.to_numpy()
    zone = df.zone.to_numpy()
    season = np.clip(df.season.to_numpy(), seasons[0], seasons[-1])
    cols = [
        dist / 10, (dist / 10) ** 2, (dist / 10) ** 3,
        is3, is3 * dist / 10,
        angle, angle ** 2, (y < 0).astype(float),
        (clock <= 3).astype(float), (clock <= 24).astype(float),
    ]
    cols += [(zone == z).astype(float) for z in range(1, 5)]
    cols += [(period == p).astype(float) for p in range(2, 6)]
    cols += [(season == s).astype(float) for s in seasons[1:]]
    return np.column_stack(cols)


def fit_logreg(X, y):
    mean, std = X.mean(axis=0), X.std(axis=0)
    std[std == 0] = 1
    model = LogisticRegression(C=1.0, max_iter=300, tol=1e-5)
    model.fit((X - mean) / std, y)
    return model, mean, std


def predict_logreg(fit, X):
    model, mean, std = fit
    return model.predict_proba((X - mean) / std)[:, 1]


def reliability_bins(probs, y_true):
    """Ten predicted-probability buckets vs the observed make rate, with n."""
    edges = np.linspace(0, 1, 11)
    out = []
    for i in range(10):
        lo, hi = float(edges[i]), float(edges[i + 1])
        mask = (probs >= lo) & (probs < hi) if i < 9 else (probs >= lo) & (probs <= hi)
        n = int(mask.sum())
        if n == 0:
            continue
        out.append({"bucket_lo": lo, "bucket_hi": hi, "n": n,
                    "predicted_mean": float(probs[mask].mean()), "observed_rate": float(y_true[mask].mean())})
    return out


def score(model_type, scope, probs, y_true, n_train, deployed=False, notes=None):
    probs = np.clip(probs, 1e-6, 1 - 1e-6)
    notes = dict(notes or {})
    if scope == "holdout":
        notes["holdout_season"] = label(HOLDOUT_SEASON)
    row = {
        "model_type": model_type, "scope": scope, "deployed": deployed,
        "n_train": int(n_train), "n_test": int(len(y_true)),
        "log_loss": float(log_loss(y_true, probs, labels=[0, 1])),
        "brier": float(brier_score_loss(y_true, probs)),
        "roc_auc": float(roc_auc_score(y_true, probs)),
        "reliability_bins": reliability_bins(probs, y_true),
        "notes": notes,
    }
    print(f"  {model_type:<14} {scope:<10} n={row['n_test']:>9,}  log loss {row['log_loss']:.4f}  "
          f"Brier {row['brier']:.4f}  AUC {row['roc_auc']:.3f}")
    return row


# ─── Stage 1: model selection on a held-out season ──────────────────────────

def select_model(df):
    train = df[df.season < HOLDOUT_SEASON]
    test = df[df.season == HOLDOUT_SEASON]
    y_tr, y_te = train.made.to_numpy(), test.made.to_numpy()
    seasons = sorted(train.season.unique().tolist())
    print(f"\nStage 1: train {len(train):,} shots ({label(seasons[0])} to {label(seasons[-1])}), "
          f"held-out {label(HOLDOUT_SEASON)} {len(test):,} shots")
    rows = []

    last = train[train.season == seasons[-1]]
    const = np.full(len(test), last.made.mean())
    rows.append(score("constant", "holdout", const, y_te, len(train),
                      notes={"description": f"{label(seasons[-1])} league FG% for every shot"}))
    zone_fg = last.groupby("zone").made.mean()
    zb = zone_fg.reindex(test.zone.to_numpy()).to_numpy()
    rows.append(score("zone_baseline", "holdout", zb, y_te, len(train),
                      notes={"description": f"{label(seasons[-1])} league FG% in the shot's zone",
                             "zone_fg_pct": {ZONES[z]: round(float(v), 4) for z, v in zone_fg.items()}}))

    t = time.time()
    rng = np.random.default_rng(SEED)
    sub = rng.choice(len(train), size=min(LOGREG_SAMPLE, len(train)), replace=False)
    X_lr = logreg_matrix(train.iloc[sub], seasons)
    lr = fit_logreg(X_lr, y_tr[sub])
    del X_lr
    p_lr = predict_logreg(lr, logreg_matrix(test, seasons))
    rows.append(score("logreg", "holdout", p_lr, y_te, len(sub),
                      notes={"description": "logistic regression on engineered terms",
                             "fit_seconds": round(time.time() - t), "training_subsample": int(len(sub)),
                             "n_terms": int(lr[0].coef_.shape[1])}))

    t = time.time()
    hgb = fit_hgb(hgb_matrix(train), y_tr)
    p_hgb = hgb.predict_proba(hgb_matrix(test))[:, 1]
    rows.append(score("hgb", "holdout", p_hgb, y_te, len(train),
                      notes={"description": "HistGradientBoostingClassifier", "fit_seconds": round(time.time() - t),
                             "iterations": int(hgb.n_iter_), "params": {k: v for k, v in HGB_PARAMS.items()},
                             "features": HGB_FEATURES}))
    by_type = {r["model_type"]: r for r in rows}
    chosen = "hgb" if by_type["hgb"]["log_loss"] <= by_type["logreg"]["log_loss"] else "logreg"
    print(f"chosen by held-out log loss: {chosen}")
    return rows, chosen, seasons


# ─── Stage 2: cross-fitted expected values for every shot ───────────────────

def crossfit(df, chosen):
    players = np.unique(df.player_id.to_numpy())
    rng = np.random.default_rng(SEED)
    rng.shuffle(players)
    fold_of_player = pd.Series(np.arange(len(players)) % N_FOLDS, index=players)
    fold = fold_of_player.reindex(df.player_id.to_numpy()).to_numpy()
    y = df.made.to_numpy()
    p = np.zeros(len(df), dtype=np.float32)
    seasons = sorted(df.season.unique().tolist())
    X = hgb_matrix(df) if chosen == "hgb" else None
    iterations = []
    for k in range(N_FOLDS):
        t = time.time()
        tr, te = fold != k, fold == k
        if chosen == "hgb":
            model = fit_hgb(X[tr], y[tr])
            p[te] = model.predict_proba(X[te])[:, 1]
            iterations.append(int(model.n_iter_))
        else:
            sub = np.flatnonzero(tr)
            sub = rng.choice(sub, size=min(LOGREG_SAMPLE, len(sub)), replace=False)
            fit = fit_logreg(logreg_matrix(df.iloc[sub], seasons), y[sub])
            p[te] = predict_logreg(fit, logreg_matrix(df[te], seasons))
        print(f"  fold {k + 1}/{N_FOLDS}: {int(tr.sum()):,} train shots, {int(te.sum()):,} scored, {time.time() - t:.0f}s")
    return p, fold, {"folds": N_FOLDS, "split": "by player", "iterations": iterations}


# ─── Aggregate to player-seasons ────────────────────────────────────────────

def aggregate(df, p, conn):
    w = np.where(df.is3.to_numpy() == 1, 1.5, 1.0)
    made = df.made.to_numpy().astype(np.float64)
    is3 = df.is3.to_numpy().astype(np.float64)
    g = pd.DataFrame({
        "player_id": df.player_id.to_numpy(), "season": df.season.to_numpy().astype(np.int32),
        "fga": 1, "fgm": made, "fg3a": is3, "fg3m": made * is3,
        "xp": p.astype(np.float64), "xpw": p * w, "var": (w ** 2) * p * (1 - p),
        "xp3": p * is3, "xp2": p * (1 - is3),
    }).groupby(["player_id", "season"], as_index=False).sum()
    g["fga"] = g.fga.astype(int)
    for c in ("fgm", "fg3a", "fg3m"):
        g[c] = g[c].astype(int)
    g["fg2a"] = g.fga - g.fg3a
    g["efg_pct"] = (g.fgm + 0.5 * g.fg3m) / g.fga
    g["x_efg_pct"] = g.xpw / g.fga
    g["shot_making"] = g.efg_pct - g.x_efg_pct
    g["se"] = np.sqrt(g["var"]) / g.fga
    g["pts_above"] = 2 * g.fga * g.shot_making
    g["fg_pct"] = g.fgm / g.fga
    g["x_fg_pct"] = g.xp / g.fga
    g["fg3_pct"] = np.where(g.fg3a > 0, g.fg3m / g.fg3a.replace(0, np.nan), np.nan)
    g["x_fg3_pct"] = np.where(g.fg3a > 0, g.xp3 / g.fg3a.replace(0, np.nan), np.nan)
    g["fg2_pct"] = np.where(g.fg2a > 0, (g.fgm - g.fg3m) / g.fg2a.replace(0, np.nan), np.nan)
    g["x_fg2_pct"] = np.where(g.fg2a > 0, g.xp2 / g.fg2a.replace(0, np.nan), np.nan)
    g["qualified"] = g.fga >= MIN_FGA
    q = g[g.qualified]
    g["rank"] = q.groupby("season").shot_making.rank(ascending=False, method="min").reindex(g.index)
    g["quality_rank"] = q.groupby("season").x_efg_pct.rank(ascending=False, method="min").reindex(g.index)
    g["pool"] = q.groupby("season").fga.transform("size").reindex(g.index)

    # Names and teams from the season table (latest team for a traded season);
    # shot rows carry the name as a fallback for players without a season row.
    names = df.groupby("player_id").player_name.last()
    with conn.cursor() as cur:
        cur.execute("SELECT player_id, season, player_name, team_abbreviation FROM player_season_stats WHERE season >= %s",
                    (int(g.season.min()),))
        pss = {(pid, s): (n, t) for pid, s, n, t in cur.fetchall()}
    g["player_name"] = [pss.get((pid, s), (names.get(pid), None))[0] for pid, s in zip(g.player_id, g.season)]
    g["team_abbreviation"] = [pss.get((pid, s), (None, None))[1] for pid, s in zip(g.player_id, g.season)]
    return g


def league_rows(df, p, g):
    y = df.made.to_numpy()
    out = []
    pc = np.clip(p, 1e-6, 1 - 1e-6)
    for s in sorted(df.season.unique().tolist()):
        m = (df.season == s).to_numpy()
        w = np.where(df.is3.to_numpy()[m] == 1, 1.5, 1.0)
        gs = g[(g.season == s) & g.qualified]
        out.append({
            "season": int(s), "fga": int(m.sum()), "fgm": int(y[m].sum()),
            "efg_pct": float((y[m] * w).sum() / m.sum()), "x_efg_pct": float((p[m] * w).sum() / m.sum()),
            "log_loss": float(log_loss(y[m], pc[m], labels=[0, 1])),
            "n_qualified": int(len(gs)),
            "sd_shot_making": float(gs.shot_making.std(ddof=1)) if len(gs) > 1 else None,
            "sd_quality": float(gs.x_efg_pct.std(ddof=1)) if len(gs) > 1 else None,
        })
    return out


def year_to_year(g):
    """Correlation of a player's value in consecutive seasons (both with the
    attempts floor), for shot-making, shot quality and raw eFG%."""
    out = {}
    for floor in (MIN_FGA, 500):
        q = g[g.fga >= floor][["player_id", "season", "shot_making", "x_efg_pct", "efg_pct"]]
        nxt = q.copy()
        nxt["season"] = nxt.season - 1
        pairs = q.merge(nxt, on=["player_id", "season"], suffixes=("", "_next"))
        out[str(floor)] = {"n_pairs": int(len(pairs))}
        for col, key in (("shot_making", "shot_making"), ("x_efg_pct", "quality"), ("efg_pct", "efg")):
            out[str(floor)][key] = round(float(np.corrcoef(pairs[col], pairs[f"{col}_next"])[0, 1]), 3) if len(pairs) > 2 else None
    return out


# ─── Shot quality map: hexagon aggregates ───────────────────────────────────

def hex_tables(df, p, g):
    """(player rows, league rows, meta rows) for the map. Cells hold makes and
    expected makes (the cross-fitted P(make) summed), so FG% - expected FG% and
    FG% - league FG% can both be read per cell."""
    cid = H.cell_id(df.loc_x.to_numpy(), df.loc_y.to_numpy())
    off = H.off_map(df.loc_x.to_numpy(), df.loc_y.to_numpy(), df.season.to_numpy())
    d = pd.DataFrame({"player_id": df.player_id.to_numpy(), "season": df.season.to_numpy().astype(np.int32),
                      "cell": cid, "made": df.made.to_numpy().astype(np.int32), "xm": p.astype(np.float64), "off": off})
    on = d[~d.off]
    print(f"map: {len(on):,} of {len(d):,} shots in a cell; off the map {int(d.off.sum()):,} "
          f"({int((df.loc_y.to_numpy() > H.Y_MAX).sum()):,} beyond half court, "
          f"{int((d.off & (df.loc_y.to_numpy() <= H.Y_MAX)).sum()):,} with no recorded location)")

    league = on.groupby(["season", "cell"], as_index=False).agg(fga=("made", "size"), fgm=("made", "sum"), xm=("xm", "sum"))

    qual = g[g.qualified][["player_id", "season"]]
    dq = d.merge(qual, on=["player_id", "season"])
    offs = dq[dq.off].groupby(["player_id", "season"]).agg(off_fga=("made", "size"), off_fgm=("made", "sum"), off_xm=("xm", "sum"))
    cells = dq[~dq.off].groupby(["player_id", "season", "cell"], as_index=False).agg(
        fga=("made", "size"), fgm=("made", "sum"), xm=("xm", "sum")).sort_values(["player_id", "season", "cell"])
    rows = []
    for (pid, season), c in cells.groupby(["player_id", "season"], sort=False):
        o = offs.loc[(pid, season)] if (pid, season) in offs.index else None
        rows.append((int(pid), int(season), c.cell.astype(int).tolist(), c.fga.astype(int).tolist(),
                     c.fgm.astype(int).tolist(), [float(v) for v in c.xm],
                     int(o.off_fga) if o is not None else 0, int(o.off_fgm) if o is not None else 0,
                     float(o.off_xm) if o is not None else 0.0))
    seen = {(r[0], r[1]) for r in rows}
    for (pid, season), o in offs.iterrows():   # a player-season with every shot off the map
        if (pid, season) not in seen:
            rows.append((int(pid), int(season), [], [], [], [], int(o.off_fga), int(o.off_fgm), float(o.off_xm)))
    n_cells = int(cells.shape[0])
    print(f"player_shot_hex: {len(rows):,} player-seasons, {n_cells:,} cells; shot_hex_league: {len(league):,} rows")
    # Every cell count must fit the column types used below.
    assert cells.fga.max() < 32767 and league.fga.max() < 2**31
    meta = [("size_tenths", H.SIZE, "hexagon circumradius, tenths of a foot"),
            ("y_max_tenths", H.Y_MAX, "half court: shots beyond this are off the map"),
            ("q_min", H.Q_MIN, "axial q of id 0"), ("q_span", H.Q_SPAN, "cells per row in the id packing"),
            ("r_min", H.R_MIN, "axial r of the first row"),
            ("last_unlocated_season", H.LAST_UNLOCATED_SEASON,
             "seasons up to this (end year) have shots with no recorded location, stored at (0, 0) and left off the map"),
            ("min_fga", MIN_FGA, "attempts a player-season needs to have a map")]
    return rows, league, meta


# ─── Store ──────────────────────────────────────────────────────────────────

def save_hex(conn, rows, league, meta):
    with conn.cursor() as cur:
        cur.execute("""
            DROP TABLE IF EXISTS player_shot_hex, shot_hex_league, shot_hex_meta;
            CREATE TABLE player_shot_hex (
                player_id integer NOT NULL, season smallint NOT NULL,
                cells smallint[] NOT NULL, fga smallint[] NOT NULL, fgm smallint[] NOT NULL, xm real[] NOT NULL,
                off_fga integer NOT NULL, off_fgm integer NOT NULL, off_xm real NOT NULL,
                PRIMARY KEY (player_id, season)
            );
            CREATE TABLE shot_hex_league (
                season smallint NOT NULL, cell smallint NOT NULL, fga integer NOT NULL, fgm integer NOT NULL, xm real NOT NULL,
                PRIMARY KEY (season, cell)
            );
            CREATE TABLE shot_hex_meta (name text PRIMARY KEY, value double precision NOT NULL, note text NOT NULL);
        """)
        psycopg2.extras.execute_values(cur, "INSERT INTO player_shot_hex VALUES %s", rows, page_size=500)
        psycopg2.extras.execute_values(
            cur, "INSERT INTO shot_hex_league VALUES %s",
            [(int(r.season), int(r.cell), int(r.fga), int(r.fgm), float(r.xm)) for r in league.itertuples()], page_size=5000)
        psycopg2.extras.execute_values(cur, "INSERT INTO shot_hex_meta VALUES %s", meta)
    conn.commit()


def save(conn, g, league, validation):
    def f(v):
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

    def i(v):
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else int(v)

    def t(v):
        # pandas 3 keeps a missing string as NaN, which psycopg2 would insert as the text 'NaN'.
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)

    with conn.cursor() as cur:
        cur.execute("""
            DROP TABLE IF EXISTS player_shot_making;
            CREATE TABLE player_shot_making (
                player_id integer NOT NULL,
                season integer NOT NULL,
                player_name text,
                team_abbreviation text,
                fga integer NOT NULL, fgm integer NOT NULL, fg3a integer NOT NULL, fg3m integer NOT NULL,
                efg_pct real, x_efg_pct real, shot_making real, se real, pts_above real,
                fg_pct real, x_fg_pct real, fg3_pct real, x_fg3_pct real, fg2_pct real, x_fg2_pct real,
                qualified boolean NOT NULL, rank integer, quality_rank integer, pool integer,
                PRIMARY KEY (player_id, season)
            );
            CREATE INDEX player_shot_making_season_idx ON player_shot_making (season, qualified, shot_making);
            DROP TABLE IF EXISTS shot_making_league;
            CREATE TABLE shot_making_league (
                season integer PRIMARY KEY,
                fga integer, fgm integer, efg_pct real, x_efg_pct real, log_loss real,
                n_qualified integer, sd_shot_making real, sd_quality real
            );
            DROP TABLE IF EXISTS shot_making_validation;
            CREATE TABLE shot_making_validation (
                computed_at timestamptz NOT NULL,
                model_type text NOT NULL,
                scope text NOT NULL,
                deployed boolean NOT NULL DEFAULT false,
                n_train bigint, n_test bigint,
                log_loss double precision, brier double precision, roc_auc double precision,
                reliability_bins jsonb, notes jsonb
            );
        """)
        rows = [(
            int(r.player_id), int(r.season), t(r.player_name), t(r.team_abbreviation),
            int(r.fga), int(r.fgm), int(r.fg3a), int(r.fg3m),
            f(r.efg_pct), f(r.x_efg_pct), f(r.shot_making), f(r.se), f(r.pts_above),
            f(r.fg_pct), f(r.x_fg_pct), f(r.fg3_pct), f(r.x_fg3_pct), f(r.fg2_pct), f(r.x_fg2_pct),
            bool(r.qualified), i(r["rank"]), i(r.quality_rank), i(r.pool),
        ) for _, r in g.iterrows()]
        psycopg2.extras.execute_values(cur, "INSERT INTO player_shot_making VALUES %s", rows, page_size=5000)
        psycopg2.extras.execute_values(
            cur, "INSERT INTO shot_making_league VALUES %s",
            [(r["season"], r["fga"], r["fgm"], r["efg_pct"], r["x_efg_pct"], r["log_loss"], r["n_qualified"],
              r["sd_shot_making"], r["sd_quality"]) for r in league])
        now = datetime.now(timezone.utc)
        psycopg2.extras.execute_values(
            cur, "INSERT INTO shot_making_validation VALUES %s",
            [(now, r["model_type"], r["scope"], r["deployed"], r["n_train"], r["n_test"], r["log_loss"], r["brier"],
              r["roc_auc"], psycopg2.extras.Json(r["reliability_bins"]), psycopg2.extras.Json(r["notes"]))
             for r in validation])
    conn.commit()


# ─── Sniff tests ────────────────────────────────────────────────────────────

def sniff(g, league):
    def show(name, season):
        r = g[(g.player_name == name) & (g.season == season)]
        if r.empty:
            print(f"  {name} {label(season)}: not found")
            return
        r = r.iloc[0]
        print(f"  {name} {label(season)}: {r.fga} FGA, eFG {r.efg_pct:.3f} vs expected {r.x_efg_pct:.3f} -> "
              f"shot-making {r.shot_making:+.3f} (SE {r.se:.3f}), {r.pts_above:+.0f} pts, "
              f"rank {r['rank']:.0f}/{r.pool:.0f}, quality rank {r.quality_rank:.0f}")

    print("\nSniff tests:")
    for name, season in [("Stephen Curry", 2016), ("Stephen Curry", 2025), ("Rudy Gobert", 2025), ("DeAndre Jordan", 2015),
                         ("Nikola Jokić", 2025), ("Kevin Durant", 2014), ("Josh Smith", 2014), ("Russell Westbrook", 2023),
                         ("Kyle Korver", 2015), ("Shaquille O'Neal", 2001)]:
        show(name, season)
    top = g[g.qualified].sort_values("shot_making", ascending=False).head(8)
    print("  best qualified seasons:", [(r.player_name, label(r.season), round(r.shot_making, 3)) for _, r in top.iterrows()])
    worst = g[g.qualified].sort_values("shot_making").head(5)
    print("  worst qualified seasons:", [(r.player_name, label(r.season), round(r.shot_making, 3)) for _, r in worst.iterrows()])
    off = max(abs(r["efg_pct"] - r["x_efg_pct"]) for r in league)
    print(f"  largest season-level |eFG - expected eFG| (calibration): {off:.4f}")
    total = g.pts_above.sum() / g.fga.sum()
    print(f"  league-wide points above expected per shot, all seasons: {total:+.5f}")


def sniff_hex(rows, league, g):
    """Curry from 26-30 ft, Korver's corners, a rim-runner: read off the stored cells."""
    by = {(r[0], r[1]): r for r in rows}
    name = g.drop_duplicates("player_id").set_index("player_id").player_name

    def region(player, season, pred, label_):
        pid = g[(g.player_name == player) & (g.season == season)].player_id
        if pid.empty or (int(pid.iloc[0]), season) not in by:
            print(f"  {player} {label(season)}: not found")
            return
        _, _, cells, fga, fgm, xm, *_ = by[(int(pid.iloc[0]), season)]
        cx, cy = H.center(np.array(cells))
        m = pred(cx / 10, cy / 10)
        a, k, e = int(np.array(fga)[m].sum()), int(np.array(fgm)[m].sum()), float(np.array(xm)[m].sum())
        lg = league[(league.season == season)]
        lcx, lcy = H.center(lg.cell.to_numpy())
        lm = pred(lcx / 10, lcy / 10)
        la, lk = int(lg.fga.to_numpy()[lm].sum()), int(lg.fgm.to_numpy()[lm].sum())
        print(f"  {player} {label(season)} {label_}: {k}-{a} ({k / a:.3f}), expected {e / a:.3f}, league {lk / la:.3f}" if a else
              f"  {player} {label(season)} {label_}: no shots")

    dist = lambda x, y: np.hypot(x, y)  # noqa: E731
    print("\nHex sniff tests:")
    region("Stephen Curry", 2016, lambda x, y: (dist(x, y) >= 26) & (dist(x, y) < 30), "26-30 ft")
    region("Kyle Korver", 2015, lambda x, y: (np.abs(x) > 21) & (y < 9), "corner threes")
    region("Rudy Gobert", 2025, lambda x, y: dist(x, y) <= 4, "0-4 ft")
    region("Rudy Gobert", 2025, lambda x, y: dist(x, y) > 10, "beyond 10 ft")
    region("DeAndre Jordan", 2015, lambda x, y: dist(x, y) <= 4, "0-4 ft")


def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    df = add_features(load_shots(conn))

    validation, chosen, _ = select_model(df)

    print(f"\nStage 2: cross-fitting the {chosen} model by player")
    p, fold, cf_notes = crossfit(df, chosen)
    y = df.made.to_numpy()
    g = aggregate(df, p, conn)
    league = league_rows(df, p, g)
    y2y = year_to_year(g)
    cf_notes.update({
        "description": f"every shot scored by a {chosen} model trained on the other {N_FOLDS - 1} player folds",
        "seasons": [label(league[0]["season"]), label(league[-1]["season"])],
        "n_player_seasons": int(len(g)), "n_qualified": int(g.qualified.sum()), "min_fga": MIN_FGA,
        "year_to_year": y2y, "features": HGB_FEATURES if chosen == "hgb" else "engineered terms",
    })
    print("\nCross-fit score, all seasons:")
    validation.append(score(chosen, "crossfit", p, y, len(df) * (N_FOLDS - 1) // N_FOLDS, deployed=True, notes=cf_notes))
    for r in validation:
        if r["scope"] == "holdout" and r["model_type"] == chosen:
            r["deployed"] = True
    print("year-to-year r:", json.dumps(y2y))

    sniff(g, league)
    hex_rows, hex_league, hex_meta = hex_tables(df, p, g)
    sniff_hex(hex_rows, hex_league, g)
    save(conn, g, league, validation)
    save_hex(conn, hex_rows, hex_league, hex_meta)
    print(f"\nplayer_shot_making: {len(g):,} player-seasons ({int(g.qualified.sum()):,} with {MIN_FGA}+ FGA); "
          f"shot_making_league: {len(league)} seasons; shot_making_validation: {len(validation)} rows. "
          f"{(time.time() - t0) / 60:.1f} min")
    conn.close()


if __name__ == "__main__":
    main()
