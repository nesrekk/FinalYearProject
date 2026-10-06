"""
build_hot_streak_persistence.py
================================
How much of a hot (or cold) run carries on? For every player-season in
player_game_lines (regular season 2020-21 to 2025-26) and every point in it,
the last N games (N = 5, 10, 20) are compared with his baseline, and so are
the next N games. Across all of them, the slope of "next N minus baseline"
on "last N minus baseline" is the share of a run's gap that carried on:
0 = pure noise, 1 = a new level. Stored per stat and window in
hot_streak_persistence, read by routers/hot_streaks.py.

Definitions (api/hot_streaks.py, shared with the API):
  - a window counts when he has 10+ games this season before it, averaging
    15+ minutes, and shooting stats clear their attempt floors in both;
  - baseline = this season's games before the window + his previous season
    (player_season_stats, 20+ games) counted as `prior_games` games' worth.

The weight is chosen per stat and window on held-out data: fit on 2020-21
to 2022-23, score each weight's next-N error on 2023-24, keep the best;
then refit on 2020-21 to 2023-24 and report the error on 2024-25 and
2025-26 (never used for choosing) against two simple rules: "he'll be at
his baseline" and "he'll keep his last-N rate". The stored slope is then
fitted on every season, with a 95% range from 300 bootstrap resamples of
players (each player's windows overlap, so they aren't independent).

Also stored for comparison: what stat_stability's between-player
reliability, n / (n + M), would predict for a typical window. It answers a
different question (how well a sample separates players), and for per-game
counting stats it's far too high as a persistence estimate.

Windows are counted once per game they end on, so they overlap: the
counts are windows, not independent samples.

The shuffled-null centre (round 8 step 7, 2026-10-06): the slope is not
zero when nothing carries on. Shuffling each player's games within his
season (no streaks by construction) still gives 7-86% depending on the
stat and window, because both gaps are measured against the same noisy
baseline from earlier games of the season. So every row also stores the
mean slope over 2,000 such shuffles (null_slope, with its 2.5-97.5%
range; the same for the season-only baseline) and net_share = slope -
null_slope: the share of a run that carries on beyond that. The shuffles
are api/hot_streaks.py's SeasonMatrix, drawn from the same stream as
scripts/paper_beliefs.py's hot-streak family, so the stored null centre
equals the paper's (test_round8_step7.py checks it). The slope itself is
still the right coefficient for the expected-next-N prediction (it is
validated on held-out seasons); net_share is the "how much was real"
number. ~5 min of the build's ~6.

Usage:
    cd scripts && python3 build_hot_streak_persistence.py
"""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from hot_streaks import (LINES_SQL, MIN_BASE_GAMES, MIN_BASE_MPG, MIN_PRIOR_GAMES, NULL_PERMS, PRIOR_SQL,  # noqa: E402
                         PRIOR_WEIGHTS, STABILITY, STATS, WINDOWS, SeasonMatrix, add_columns, prior_share)

TRAIN_TO, VALID, TEST_FROM = 2023, 2024, 2025
BOOT = 300
RNG = np.random.default_rng(20260928)


def load(conn, through=None):
    """Lines and previous-season priors; `through` (an end year) keeps the seasons up to it (the paper's
    scripts pass paper_freeze.MAX_PAPER_SEASON, round 9 step 1; this build reads every season)."""
    cap = f" AND l.season <= {int(through)}" if through else ""
    lines = add_columns(pd.read_sql(LINES_SQL.format(where=cap), conn))
    cols = ", ".join(f"{n} AS {k}_n, {d} AS {k}_d" for k, (n, d) in PRIOR_SQL.items())
    prior_cap = f" AND season + 1 <= {int(through)}" if through else ""
    prior = pd.read_sql(f"SELECT player_id, season + 1 AS season, gp, {cols} FROM player_season_stats WHERE gp >= %s{prior_cap}",
                        conn, params=(MIN_PRIOR_GAMES,))
    return lines, {(int(r.player_id), int(r.season)): r for r in prior.itertuples(index=False)}


def windows(lines, prior):
    """Every qualifying (window, next window) pair, with the baseline at each prior weight."""
    out = []
    for (pid, season), g in lines.groupby(["player_id", "season"], sort=False):
        n = len(g)
        p = prior.get((int(pid), int(season)))
        cmin = np.concatenate([[0.0], np.cumsum(g["min"].to_numpy(float))])
        for stat, (_label, a, b, _fmt, lo) in STATS.items():
            cn = np.concatenate([[0.0], np.cumsum(g[a].to_numpy(float))])
            cd = np.concatenate([[0.0], np.cumsum(g[b].to_numpy(float))])
            pn = getattr(p, f"{stat}_n") if p is not None else None
            pdn = getattr(p, f"{stat}_d") if p is not None else None
            has_prior = p is not None and pn is not None and pdn is not None and pdn > 0 and not np.isnan(pdn)
            for N in WINDOWS:
                t = np.arange(MIN_BASE_GAMES + N, n - N + 1)
                if not len(t):
                    continue
                s0 = t - N
                bn, bd = cn[s0], cd[s0]
                wn, wd = cn[t] - cn[s0], cd[t] - cd[s0]
                fn, fd = cn[t + N] - cn[t], cd[t + N] - cd[t]
                ok = (cmin[s0] / s0 >= MIN_BASE_MPG) & (bd > 0) & (wd > 0) & (fd > 0)
                if lo is not None:
                    ok &= (bd >= lo * s0) & (wd >= lo * N)
                if not ok.any():
                    continue
                t, s0, bn, bd, wn, wd, fn, fd = (x[ok] for x in (t, s0, bn, bd, wn, wd, fn, fd))
                row = {"player_id": pid, "season": season, "stat": stat, "N": N, "base_games": s0,
                       "win": wn / wd, "fut": fn / fd, "wden": wd, "has_prior": has_prior}
                for w in PRIOR_WEIGHTS:
                    share = prior_share(w, p.gp) if has_prior else 0.0
                    num = bn + (share * pn if share else 0.0)
                    den = bd + (share * pdn if share else 0.0)
                    row[f"b{w}"] = num / den
                out.append(pd.DataFrame(row))
    return pd.concat(out, ignore_index=True)


def fit(D, F):
    """OLS slope and intercept of F on D."""
    D, F = np.asarray(D), np.asarray(F)
    dm = D - D.mean()
    slope = float((dm * (F - F.mean())).sum() / (dm ** 2).sum())
    return slope, float(F.mean() - slope * D.mean())


def rmse(pred, actual):
    return float(np.sqrt(np.mean((np.asarray(actual) - np.asarray(pred)) ** 2)))


def boot_slope(g, base_col):
    """95% range of the slope, resampling players."""
    D = (g["win"] - g[base_col]).to_numpy()
    F = (g["fut"] - g[base_col]).to_numpy()
    codes, _ = pd.factorize(g["player_id"])
    k = codes.max() + 1
    sums = np.stack([np.bincount(codes, weights=w, minlength=k) for w in (np.ones_like(D), D, F, D * D, D * F)])
    draws = []
    for _ in range(BOOT):
        s = sums[:, RNG.integers(0, k, k)].sum(axis=1)
        n, sd, sf, sdd, sdf = s
        draws.append((sdf - sd * sf / n) / (sdd - sd * sd / n))
    return float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))


def evaluate(g, base_col):
    """Fit on the training seasons, score on the held-out ones."""
    tr, te = g[g.season <= VALID], g[g.season >= TEST_FROM]
    s, i = fit(tr.win - tr[base_col], tr.fut - tr[base_col])
    pred = te[base_col] + i + s * (te.win - te[base_col])
    return {"model": rmse(pred, te.fut), "baseline": rmse(te[base_col], te.fut), "window": rmse(te.win, te.fut),
            "n": len(te)}


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    lines, prior = load(conn)
    print(f"{len(lines):,} player-games, {len(prior):,} previous seasons")
    w = windows(lines, prior)
    print(f"{len(w):,} windows")
    cur = conn.cursor()
    cur.execute("SELECT stat, stable_n FROM stat_stability WHERE variant = 'catalogue'")
    stable = dict(cur.fetchall())

    rows = []
    for (stat, N), g in w.groupby(["stat", "N"]):
        gp = g[g.has_prior]
        # Pick the previous-season weight: train on <= 2022-23, score 2023-24.
        best, best_err = 0, None
        tr, va = gp[gp.season <= TRAIN_TO], gp[gp.season == VALID]
        for wt in PRIOR_WEIGHTS:
            col = f"b{wt}"
            s, i = fit(tr.win - tr[col], tr.fut - tr[col])
            err = rmse(va[col] + i + s * (va.win - va[col]), va.fut)
            if best_err is None or err < best_err:
                best, best_err = wt, err
        col = f"b{best}"
        ev = evaluate(gp, col)
        ev0 = evaluate(gp, "b0")  # same windows, season-only baseline
        slope, icpt = fit(gp.win - gp[col], gp.fut - gp[col])
        lo, hi = boot_slope(gp, col)
        # Season-only persistence, for players without a previous season: fitted on every window.
        s0, i0 = fit(g.win - g.b0, g.fut - g.b0)
        lo0, hi0 = boot_slope(g, "b0")
        st, factor = STABILITY[stat]
        typical = float(np.median(g.wden)) * factor
        rel = typical / (typical + stable[st]) if st in stable else None
        rows.append((stat, N, best, slope, lo, hi, icpt, s0, lo0, hi0, i0, len(gp), gp.player_id.nunique(),
                     len(g), ev["n"], ev["model"], ev["baseline"], ev["window"], ev0["model"], typical, rel,
                     date.today()))
        print(f"{stat:>8} N={N:<2} prior={best:<2} slope {slope:.3f} ({lo:.3f}-{hi:.3f}) season-only {s0:.3f} | "
              f"test rmse model {ev['model']:.4f} baseline {ev['baseline']:.4f} window {ev['window']:.4f} "
              f"season-only model {ev0['model']:.4f} | stability predicts {rel if rel is None else round(rel, 3)}")

    # The shuffled-null centre of each slope (same windows, same weights, games shuffled within each season).
    weights = {(r[0], r[1]): r[2] for r in rows}
    mat = SeasonMatrix(add_columns(lines), prior, weights)
    print(f"null centre: {NULL_PERMS} within-season shuffles of {mat.P:,} player-seasons")
    nulls = mat.null_slopes(NULL_PERMS, log=print)
    out = []
    for r in rows:
        dr, dr0 = nulls[(r[0], r[1])]
        m, m0 = float(dr.mean()), float(dr0.mean())
        slope, lo, hi, s0, lo0, hi0 = r[3], r[4], r[5], r[7], r[8], r[9]
        out.append(r + (m, float(np.percentile(dr, 2.5)), float(np.percentile(dr, 97.5)), slope - m, lo - m, hi - m,
                        m0, float(np.percentile(dr0, 2.5)), float(np.percentile(dr0, 97.5)), s0 - m0, lo0 - m0, hi0 - m0,
                        NULL_PERMS))
        print(f"{r[0]:>8} N={r[1]:<2} slope {slope:.3f} null {m:.3f} net {slope - m:+.3f} | "
              f"season-only {s0:.3f} null {m0:.3f} net {s0 - m0:+.3f}")
    rows = out

    cur.execute("DROP TABLE IF EXISTS hot_streak_persistence;")
    cur.execute("""CREATE TABLE hot_streak_persistence (
        stat TEXT NOT NULL, window_games INTEGER NOT NULL, prior_games INTEGER NOT NULL,
        slope DOUBLE PRECISION, slope_lo DOUBLE PRECISION, slope_hi DOUBLE PRECISION, intercept DOUBLE PRECISION,
        slope_season_only DOUBLE PRECISION, slope_season_only_lo DOUBLE PRECISION,
        slope_season_only_hi DOUBLE PRECISION, intercept_season_only DOUBLE PRECISION,
        windows INTEGER, players INTEGER, windows_season_only INTEGER, test_windows INTEGER,
        rmse_model DOUBLE PRECISION, rmse_baseline DOUBLE PRECISION, rmse_window DOUBLE PRECISION,
        rmse_season_only_model DOUBLE PRECISION, typical_window_sample DOUBLE PRECISION,
        stability_predicted DOUBLE PRECISION, built_on DATE,
        null_slope DOUBLE PRECISION, null_slope_lo DOUBLE PRECISION, null_slope_hi DOUBLE PRECISION,
        net_share DOUBLE PRECISION, net_share_lo DOUBLE PRECISION, net_share_hi DOUBLE PRECISION,
        null_slope_season_only DOUBLE PRECISION, null_slope_season_only_lo DOUBLE PRECISION,
        null_slope_season_only_hi DOUBLE PRECISION, net_share_season_only DOUBLE PRECISION,
        net_share_season_only_lo DOUBLE PRECISION, net_share_season_only_hi DOUBLE PRECISION,
        null_shuffles INTEGER,
        PRIMARY KEY (stat, window_games));""")
    psycopg2.extras.execute_values(cur, "INSERT INTO hot_streak_persistence VALUES %s", rows)
    conn.commit()
    conn.close()
    print(f"hot_streak_persistence: {len(rows)} rows")


if __name__ == "__main__":
    main()
