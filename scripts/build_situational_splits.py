"""
build_situational_splits.py
============================
Situational splits for every player-season, regular season 2020-21 to
2025-26: home vs. away, back-to-back vs. rested, long trip vs. short, and
top-10 vs. bottom-10 opponents (definitions in api/situational_splits.py).
From `player_game_lines` (per player-game lines rebuilt from ESPN
play-by-play) joined to `team_game_fatigue` on (team, date).

Per player-season, split and stat:
  value on each side   ratio of the side's sums (36 x points / minutes, ...);
  effect               side A minus side B;
  95% interval         game-clustered: each game is one draw, so the
                       variance is the ratio estimator's (sum of squared
                       residuals num - R x den over the side's games, times
                       n/(n-1), over the squared denominator), the two
                       sides added;
  vs. league           his effect minus that season's league effect, and
                       its z (divided by his own SE).

Per season (and all seasons pooled, season = 0), split and stat:
  league effect        the average player's effect: a weighted mean of the
                       qualified players' effects (weights = harmonic mean
                       of the two sides' denominators, e.g. minutes), with a
                       bootstrap interval that resamples team-seasons (the
                       players on a team share one schedule, so they aren't
                       independent draws);
  within-player fit    the same effect from a weighted regression on every
                       game with player-season fixed effects, once as is and
                       once also controlling for home/away (cluster-robust
                       SEs by team-season). The control matters for travel
                       (long trips end in a home game 37% of the time, short
                       ones 76%) and a little for rest and opponents;
  chance check         how many qualified players land outside 95% of the
                       league effect, against the 5% expected if everyone
                       had the league's effect and only noise differed;
  year to year         correlation of a player's effect (vs. league) with
                       his effect the next season, qualified both years:
                       near zero means the split is mostly noise.

Tables written (dropped and rebuilt):
  player_situational_splits   one row per player, season, split, stat
                              (rows with 3+ games on each side; `qualified`
                              = 10+ games a side and the stat's side floor);
  situational_split_league    one row per season (0 = pooled), split, stat.

Usage:
    cd scripts && python3 build_situational_splits.py
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

from db_config import DB_CONFIG
from stats_lib import wls_cluster

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
from situational_splits import LINES_SQL, MIN_GAMES, SPLITS, STATS, STORE_GAMES, add_columns  # noqa: E402

BOOT = 2000
PERMS = 50
RNG = np.random.default_rng(20260928)
Z95 = 1.959964


def side_sums(df, mask, side):
    """Per player-season sums a ratio estimator and its variance need."""
    d = df[mask]
    agg = {"games": ("player_id", "size"), f"minutes": ("min", "sum"), "home_games": ("is_home", "sum")}
    parts = [d.groupby(["player_id", "season"]).agg(**agg)]
    for stat, (_l, num, den, _s, _f, _lo) in STATS.items():
        x, y = d[num].astype(float), d[den].astype(float)
        tmp = pd.DataFrame({"player_id": d.player_id, "season": d.season,
                            "n": x, "d": y, "nn": x * x, "nd": x * y, "dd": y * y, "g": (y > 0).astype(float)})
        s = tmp.groupby(["player_id", "season"]).sum()
        s.columns = [f"{stat}_{c}" for c in s.columns]
        parts.append(s)
    out = pd.concat(parts, axis=1)
    out.columns = [f"{c}_{side}" for c in out.columns]
    return out


def ratio_and_var(s, stat, side):
    """R = sum(num)/sum(den) and its game-clustered variance (ratio estimator)."""
    n, d = s[f"{stat}_n_{side}"], s[f"{stat}_d_{side}"]
    nn, nd, dd, g = s[f"{stat}_nn_{side}"], s[f"{stat}_nd_{side}"], s[f"{stat}_dd_{side}"], s[f"{stat}_g_{side}"]
    r = n / d.where(d > 0)
    ssr = (nn - 2 * r * nd + r * r * dd).clip(lower=0)
    var = ssr * g / (g - 1).where(g > 1) / (d * d)
    return r, var


def weighted_boot(d, w, clusters):
    """Weighted mean of d and its 95% interval, resampling clusters."""
    codes, _ = pd.factorize(clusters)
    k = codes.max() + 1
    sw = np.bincount(codes, weights=w, minlength=k)
    swd = np.bincount(codes, weights=w * d, minlength=k)
    idx = RNG.integers(0, k, (BOOT, k))
    draws = swd[idx].sum(axis=1) / sw[idx].sum(axis=1)
    est = swd.sum() / sw.sum()
    return float(est), float(draws.std(ddof=1)), float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))


def fe_fit(g, stat, with_home):
    """Within-player-season weighted regression on the side dummy (+ home)."""
    _l, num, den, scale, _f, _lo = STATS[stat]
    g = g[g[den] > 0]
    w = g[den].to_numpy(float)
    y = scale * g[num].to_numpy(float) / w
    cols = [g["side_a"].to_numpy(float)] + ([g["is_home"].to_numpy(float)] if with_home else [])
    key = g["ps"].to_numpy()
    codes, _ = pd.factorize(key)
    sw = np.bincount(codes, weights=w)

    def demean(v):
        return v - (np.bincount(codes, weights=w * v) / sw)[codes]
    X = np.column_stack([demean(c) for c in cols])
    fit = wls_cluster(demean(y), X, w, g["cluster"].to_numpy())
    return float(fit["beta"][0]), float(fit["se"][0])


def outside_counts(codes, seasons, side, num, den, scale, floor):
    """Per season (0 = pooled): qualified players and how many land outside
    95% of that season's league effect, for one labelling of the games."""
    k = seasons.shape[0]
    out = {}
    stats_ = []
    for s in (side, ~side):
        g = np.bincount(codes, weights=s.astype(float), minlength=k)
        has = s & (den > 0)
        sums = [np.bincount(codes, weights=v * s, minlength=k) for v in (num, den, num * num, num * den, den * den)]
        gd = np.bincount(codes, weights=has.astype(float), minlength=k)
        n, d, nn, nd, dd = sums
        with np.errstate(divide="ignore", invalid="ignore"):
            r = n / d
            var = np.clip(nn - 2 * r * nd + r * r * dd, 0, None) * gd / (gd - 1) / (d * d)
        stats_.append((g, d, r, var))
    (ga, da, ra, va), (gb, db, rb, vb) = stats_
    diff, se = scale * (ra - rb), scale * np.sqrt(va + vb)
    ok = (ga >= MIN_GAMES) & (gb >= MIN_GAMES) & np.isfinite(diff) & np.isfinite(se) & (se > 0)
    if floor is not None:
        ok &= (da >= floor) & (db >= floor)
    w = np.where(ok, 2 / (1 / np.where(da > 0, da, np.nan) + 1 / np.where(db > 0, db, np.nan)), 0)
    total = 0
    for season in np.unique(seasons[ok]):
        m = ok & (seasons == season)
        lg = np.sum(w[m] * diff[m]) / np.sum(w[m])
        out[int(season)] = int((np.abs((diff[m] - lg) / se[m]) > Z95).sum())
        total += out[int(season)]
    out[0] = total
    return out


def chance_outside(sub, stat):
    """Mean count outside 95% when each player's own games are shuffled
    between the two sides (no real split effect for anyone), same formula."""
    _l, num, den, scale, _f, floor = STATS[stat]
    codes, uniq = pd.factorize(sub["ps"])
    seasons = sub.groupby(codes)["season"].first().to_numpy()
    side = sub["side_a"].to_numpy(bool)
    x, y = sub[num].to_numpy(float), sub[den].to_numpy(float)
    actual = outside_counts(codes, seasons, side, x, y, scale, floor)
    base = np.argsort(codes, kind="stable")
    draws = []
    for _ in range(PERMS):
        shuffled = np.empty_like(side)
        shuffled[np.lexsort((RNG.random(len(side)), codes))] = side[base]
        draws.append(outside_counts(codes, seasons, shuffled, x, y, scale, floor))
    chance = {s: float(np.mean([d.get(s, 0) for d in draws])) for s in actual}
    return actual, chance


def main():
    t0 = time.time()
    conn = psycopg2.connect(**DB_CONFIG)
    lines = add_columns(pd.read_sql(LINES_SQL, conn))
    lines["ps"] = lines.player_id.astype(str) + "_" + lines.season.astype(str)
    print(f"{len(lines):,} player-game lines, {lines.season.min()}-{lines.season.max()} ({time.time() - t0:.0f}s)")

    # Main team per player-season (most games) = the bootstrap cluster; all teams for display.
    tg = lines.groupby(["player_id", "season", "team"]).size().rename("n").reset_index()
    main_team = tg.sort_values("n", ascending=False).drop_duplicates(["player_id", "season"])
    main_team = main_team.set_index(["player_id", "season"])["team"]
    teams = tg.sort_values(["n", "team"], ascending=[False, True]).groupby(["player_id", "season"])["team"].agg("/".join)
    lines = lines.merge(main_team.rename("main_team").reset_index(), on=["player_id", "season"], how="left")
    lines["cluster"] = lines.main_team + "_" + lines.season.astype(str)

    player_rows, league_rows = [], []
    for split, (s_label, _a, _b, cond_a, cond_b) in SPLITS.items():
        mask_a, mask_b = lines.eval(cond_a), lines.eval(cond_b)
        both = pd.concat([side_sums(lines, mask_a, "a"), side_sums(lines, mask_b, "b")], axis=1, join="inner")
        both = both[(both.games_a >= STORE_GAMES) & (both.games_b >= STORE_GAMES)]
        sub = lines[mask_a | mask_b].copy()
        sub["side_a"] = mask_a[mask_a | mask_b]
        for stat, (label, _num, den, scale, _fmt, floor) in STATS.items():
            ra, va = ratio_and_var(both, stat, "a")
            rb, vb = ratio_and_var(both, stat, "b")
            df = pd.DataFrame({
                "games_a": both.games_a, "games_b": both.games_b,
                "minutes_a": both.minutes_a, "minutes_b": both.minutes_b,
                "den_a": both[f"{stat}_d_a"], "den_b": both[f"{stat}_d_b"],
                "value_a": scale * ra, "value_b": scale * rb,
            })
            df["diff"] = df.value_a - df.value_b
            df["se"] = scale * np.sqrt(va + vb)
            ok = (df.games_a >= MIN_GAMES) & (df.games_b >= MIN_GAMES) & df["diff"].notna() & (df.se > 0)
            if floor is not None:
                ok &= (df.den_a >= floor) & (df.den_b >= floor)
            df["qualified"] = ok
            df = df.reset_index()
            df["cluster"] = [f"{main_team.get((p, s))}_{s}" for p, s in zip(df.player_id, df.season)]
            df["w"] = 2 / (1 / df.den_a + 1 / df.den_b)

            # League effect per season and pooled.
            q = df[df.qualified]
            league = {}
            for season in [0] + sorted(q.season.unique().tolist()):
                qs = q if season == 0 else q[q.season == season]
                est, se, lo, hi = weighted_boot(qs["diff"].to_numpy(float), qs.w.to_numpy(float), qs.cluster)
                league[season] = est
                # Pooled rates on each side (all qualified player-seasons' games together).
                num_a, num_b = qs.value_a * qs.den_a / scale, qs.value_b * qs.den_b / scale
                g = sub[sub.ps.isin(qs.player_id.astype(str) + "_" + qs.season.astype(str))]
                fe, fe_se = fe_fit(g, stat, with_home=False)
                adj, adj_se = fe_fit(g, stat, with_home=True) if split != "home" else (None, None)
                ga, gb = g[g.side_a], g[~g.side_a]
                league_rows.append({
                    "season": int(season), "split": split, "stat": stat, "players": len(qs),
                    "league_value_a": float(scale * num_a.sum() / qs.den_a.sum()),
                    "league_value_b": float(scale * num_b.sum() / qs.den_b.sum()),
                    "league_diff": est, "league_se": se, "league_ci_low": lo, "league_ci_high": hi,
                    "fe_diff": fe, "fe_se": fe_se, "venue_adj_diff": adj, "venue_adj_se": adj_se,
                    "games_a": len(ga), "games_b": len(gb),
                    "home_share_a": float(ga.is_home.mean()), "home_share_b": float(gb.is_home.mean()),
                })
            df["league_diff"] = df.season.map(league)
            df["vs_league"] = df["diff"] - df.league_diff
            df["z"] = df.vs_league / df.se
            df.loc[~df.qualified, "z"] = np.nan

            # Chance check and year-to-year persistence.
            qz = df[df.qualified]
            stored = set(df.player_id.astype(str) + "_" + df.season.astype(str))
            actual, chance = chance_outside(sub[sub.ps.isin(stored)], stat)
            for row in league_rows[-(qz.season.nunique() + 1):]:
                zz = qz if row["season"] == 0 else qz[qz.season == row["season"]]
                row["outside_95"] = int((zz.z.abs() > Z95).sum())
                row["expected_outside_95"] = round(0.05 * len(zz), 1)
                row["chance_outside_95"] = round(chance.get(row["season"], 0.0), 1)
                assert actual.get(row["season"]) == row["outside_95"], (split, stat, row["season"], actual, row)
                row["outside_high"] = int((zz.z > Z95).sum())
                row["outside_low"] = int((zz.z < -Z95).sum())
                row["yoy_r"] = row["yoy_n"] = None
            nxt = qz[["player_id", "season", "vs_league"]].assign(season=lambda x: x.season - 1)
            pairs = qz[["player_id", "season", "vs_league"]].merge(nxt, on=["player_id", "season"], suffixes=("", "_next"))
            pooled = league_rows[-(qz.season.nunique() + 1)]
            assert pooled["season"] == 0
            if len(pairs) >= 20:
                pooled["yoy_r"] = float(np.corrcoef(pairs.vs_league, pairs.vs_league_next)[0, 1])
                pooled["yoy_n"] = len(pairs)

            df["split"], df["stat"] = split, stat
            df["teams"] = [teams.get((p, s)) for p, s in zip(df.player_id, df.season)]
            player_rows.append(df)
        print(f"  {split}: {len(both):,} player-seasons with {STORE_GAMES}+ games a side ({time.time() - t0:.0f}s)")

    P = pd.concat(player_rows, ignore_index=True)
    L = pd.DataFrame(league_rows)
    pcols = ["player_id", "season", "split", "stat", "teams", "games_a", "games_b", "minutes_a", "minutes_b",
             "den_a", "den_b", "value_a", "value_b", "diff", "se", "league_diff", "vs_league", "z", "qualified"]
    P = P[pcols].copy()
    P["ci_low"], P["ci_high"] = P["diff"] - Z95 * P.se, P["diff"] + Z95 * P.se

    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS player_situational_splits;")
    cur.execute("""CREATE TABLE player_situational_splits (
        player_id BIGINT, season INT, split TEXT, stat TEXT, teams TEXT,
        games_a INT, games_b INT, minutes_a DOUBLE PRECISION, minutes_b DOUBLE PRECISION,
        den_a DOUBLE PRECISION, den_b DOUBLE PRECISION, value_a DOUBLE PRECISION, value_b DOUBLE PRECISION,
        diff DOUBLE PRECISION, se DOUBLE PRECISION, league_diff DOUBLE PRECISION, vs_league DOUBLE PRECISION,
        z DOUBLE PRECISION, qualified BOOLEAN, ci_low DOUBLE PRECISION, ci_high DOUBLE PRECISION,
        PRIMARY KEY (player_id, season, split, stat))""")

    def clean(v):
        if isinstance(v, (np.floating, float)):
            return None if np.isnan(v) else float(v)
        if isinstance(v, np.integer):
            return int(v)
        if isinstance(v, np.bool_):
            return bool(v)
        return v
    cols = pcols + ["ci_low", "ci_high"]
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO player_situational_splits ({', '.join(cols)}) VALUES %s",
        [tuple(clean(v) for v in r) for r in P[cols].itertuples(index=False)], page_size=5000)
    cur.execute("CREATE INDEX ON player_situational_splits (season, split, stat)")

    lcols = list(L.columns)
    cur.execute("DROP TABLE IF EXISTS situational_split_league;")
    cur.execute(f"""CREATE TABLE situational_split_league (
        season INT, split TEXT, stat TEXT, players INT,
        {', '.join(f'{c} DOUBLE PRECISION' for c in lcols if c not in ('season', 'split', 'stat', 'players', 'games_a', 'games_b', 'outside_95', 'outside_high', 'outside_low', 'yoy_n'))},
        games_a INT, games_b INT, outside_95 INT, outside_high INT, outside_low INT, yoy_n INT,
        PRIMARY KEY (season, split, stat))""")
    psycopg2.extras.execute_values(
        cur, f"INSERT INTO situational_split_league ({', '.join(lcols)}) VALUES %s",
        [tuple(clean(v) for v in r) for r in L[lcols].itertuples(index=False)])
    conn.commit()
    print(f"Wrote {len(P):,} player rows ({int(P.qualified.sum()):,} qualified), {len(L)} league rows "
          f"({time.time() - t0:.0f}s)")

    # Checks the README quotes.
    pooled = L[L.season == 0].set_index(["split", "stat"])
    print("\nPooled league effects (A - B), weighted mean [95%], within-player fit, venue-controlled, chance, yoy r:")
    for (split, stat), r in pooled.iterrows():
        adj = "" if pd.isna(r.venue_adj_diff) else f" adj {r.venue_adj_diff:+.3f}"
        print(f"  {split:6} {stat:8} {r.league_diff:+.3f} [{r.league_ci_low:+.3f}, {r.league_ci_high:+.3f}] "
              f"fe {r.fe_diff:+.3f}{adj}  n={r.players}  out {r.outside_95} (5%: {r.expected_outside_95:.0f}, "
              f"shuffled: {r.chance_outside_95:.0f})  "
              f"yoy r={r.yoy_r if r.yoy_r is None or pd.isna(r.yoy_r) else round(r.yoy_r, 3)} (n={r.yoy_n})")
    print("\nHome effect on points per 36 by season:")
    for _, r in L[(L.split == "home") & (L.stat == "pts") & (L.season > 0)].iterrows():
        print(f"  {r.season}: {r.league_diff:+.3f} [{r.league_ci_low:+.3f}, {r.league_ci_high:+.3f}]")
    print(f"Done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
