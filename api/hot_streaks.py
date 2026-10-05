"""
hot_streaks.py
===============
Shared definitions for the Hot Streak Checker: which stats, how a window
and a baseline are measured, and who qualifies. Used by
scripts/build_hot_streak_persistence.py (which measures how much of a hot
or cold window carried on, historically) and routers/hot_streaks.py (which
applies it to one player's last N games), so both use one definition.

Every stat is a ratio of two per-game sums: counting stats over games
(points per game), shooting % over attempts (3PM / 3PA), usage over the
team's plays while he was on the floor. A window's rate is the ratio of its
sums, so a 3-for-3 night doesn't count as much as a 9-for-10 one.

Baseline = his games this season before the window, plus his previous
season counted as `prior_games` games' worth (scaled down from its full
size). The weight is picked per stat and window by the build script on
held-out seasons: a whole previous season for shooting %, which a quarter
of a season can't pin down, and much less for minutes and usage, which
change with role. Players without a previous season (20+ games) get the
season-only baseline and its own persistence estimate.
"""

import hashlib

import numpy as np
import pandas as pd

WINDOWS = (5, 10, 20)
MIN_BASE_GAMES = 10     # games this season before the window
MIN_BASE_MPG = 15.0     # average minutes in those games
MIN_PRIOR_GAMES = 20    # a previous season this short isn't used
PRIOR_WEIGHTS = (0, 10, 20, 41, 82)  # previous season counted as this many games (82 = all of it)

# key -> (label, numerator column, denominator column, format, minimum denominator per game)
# The minimum applies to both the baseline games and the window, like the
# attempt floors on the leaderboards: a 1-for-1 week isn't a shooting streak.
STATS = {
    "pts": ("Points", "pts", "one", "num", None),
    "reb": ("Rebounds", "reb", "one", "num", None),
    "ast": ("Assists", "ast", "one", "num", None),
    "stl": ("Steals", "stl", "one", "num", None),
    "blk": ("Blocks", "blk", "one", "num", None),
    "tov": ("Turnovers", "tov", "one", "num", None),
    "fg3m": ("Threes made", "fg3m", "one", "num", None),
    "fta": ("Free throw attempts", "fta", "one", "num", None),
    "min": ("Minutes", "min", "one", "num", None),
    "fg_pct": ("FG%", "fgm", "fga", "pct", 5.0),
    "fg3_pct": ("3P%", "fg3m", "fg3a", "pct", 2.0),
    "ft_pct": ("FT%", "ftm", "fta", "pct", 2.0),
    "ts_pct": ("True shooting %", "pts", "tsa2", "pct", 10.0),
    "usg_pct": ("Usage %", "plays", "tm_plays", "pct", None),
}
# stat_stability (between-player split-half reliability) for comparison:
# key -> (stat_stability.stat, factor turning this denominator into its unit)
STABILITY = {
    **{k: (k, 1.0) for k in ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fta", "min")},
    "fg_pct": ("fg_pct", 1.0), "fg3_pct": ("fg3_pct", 1.0), "ft_pct": ("ft_pct", 1.0),
    "ts_pct": ("ts_pct", 0.5),  # its unit is FGA + 0.44 FTA; ours is twice that
    "usg_pct": ("usg_pct", 1.0),
}

# Previous-season totals from player_season_stats, per stat: (numerator SQL, denominator SQL).
PRIOR_SQL = {
    "pts": ("pts * gp", "gp"), "reb": ("reb * gp", "gp"), "ast": ("ast * gp", "gp"), "stl": ("stl * gp", "gp"),
    "blk": ("blk * gp", "gp"), "tov": ("tov * gp", "gp"), "fg3m": ("fg3m * gp", "gp"), "fta": ("fta * gp", "gp"),
    "min": ("min * gp", "gp"),
    "fg_pct": ("fgm * gp", "fga * gp"), "fg3_pct": ("fg3m * gp", "fg3a * gp"), "ft_pct": ("ftm * gp", "fta * gp"),
    "ts_pct": ("pts * gp", "2 * (fga + 0.44 * fta) * gp"),
    "usg_pct": ("(fga + 0.44 * fta + tov) * gp", "(fga + 0.44 * fta + tov) * gp / NULLIF(usg_pct, 0)"),
}

LINES_SQL = """
    SELECT l.player_id, l.season, l.game_date, f.opponent, f.is_home, l.seconds / 60.0 AS min,
           l.pts, l.oreb + l.dreb AS reb, l.ast, l.stl, l.blk, l.tov, l.fgm, l.fga, l.fg3m, l.fg3a, l.ftm, l.fta,
           l.tm_fga, l.tm_fta, l.tm_tov
    FROM player_game_lines l
    JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
    WHERE l.seconds > 0 {where}
    ORDER BY l.player_id, l.season, l.game_date, l.game_id  -- game_id breaks ties: a few ids have two lines on one date
"""


def add_columns(df: pd.DataFrame) -> pd.DataFrame:
    """The denominators the catalogue refers to."""
    df = df.copy()
    df["one"] = 1.0
    df["tsa2"] = 2 * (df["fga"] + 0.44 * df["fta"])
    df["plays"] = df["fga"] + 0.44 * df["fta"] + df["tov"]
    df["tm_plays"] = df["tm_fga"] + 0.44 * df["tm_fta"] + df["tm_tov"]
    return df


def prior_share(weight_games, prior_len):
    """Fraction of the previous season's totals that goes into the baseline."""
    return 0.0 if not prior_len or prior_len < MIN_PRIOR_GAMES else min(1.0, weight_games / prior_len)


def floor_ok(stat, den_total, games):
    lo = STATS[stat][4]
    return den_total > 0 and (lo is None or den_total >= lo * games)


# ─── The windows in matrix form, for shuffles (round 8 step 7) ──────────────
# One row per player-season, one column per game (padded): every window of
# every player-season for a stat and window size in a few array operations,
# so a season's games can be shuffled thousands of times. Moved here from
# scripts/paper_beliefs.py (its hot-streak family) so that the build script,
# which now stores the shuffled-null centre beside each slope, and the paper
# use one implementation. Under shuffling there are no streaks, yet the slope
# is not zero: both gaps are measured against the same noisy baseline from
# earlier games of the season (Miller and Sanjurjo's finite-sample point,
# for this estimator). The mean slope over shuffles is the null centre;
# slope minus null centre is the share of a run that carries on beyond it.

NULL_SEED = 20260929   # = scripts/paper_beliefs.py SEED: the build draws the same shuffles as the paper
NULL_PERMS = 2_000     # = paper_beliefs.STREAK_PERMS
NULL_BLOCK = 25        # shuffles drawn per block (the paper's loop); changing it changes the draws


def null_rng(seed=NULL_SEED):
    """The stream paper_beliefs.rng_for("streak") gives: md5 of "streak|<seed>"."""
    h = hashlib.md5(f"streak|{seed}".encode()).digest()
    return np.random.default_rng(int.from_bytes(h[:8], "little"))


def row_perms(rng, lengths, L, B):
    """B within-row permutations of a (P, L) padded matrix: entry (b, i, :) is a
    permutation of 0..L-1 whose first lengths[i] positions are a random order of
    the row's real entries (the padding stays at the end)."""
    keys = rng.random((B, len(lengths), L))
    keys[:, np.arange(L)[None, :] >= np.asarray(lengths)[:, None]] = 2.0
    return np.argsort(keys, axis=2)


class SeasonMatrix:
    """Every player-season's games as rows of a padded matrix (rows in LINES_SQL order), with the previous
    season per row and stat, and the previous-season weight per (stat, window) from `weights`."""

    def __init__(self, lines, prior, weights):
        key = lines["player_id"].astype(str) + "_" + lines["season"].astype(str)
        codes, uniq = pd.factorize(key)              # rows in LINES_SQL order (player, season, date, game)
        pos = lines.groupby(codes).cumcount().to_numpy()
        P, L = len(uniq), int(pos.max()) + 1
        self.P, self.L = P, L
        self.lengths = np.bincount(codes, minlength=P)
        self.row_pid = lines.groupby(codes)["player_id"].first().to_numpy().astype(int)
        self.row_season = lines.groupby(codes)["season"].first().to_numpy().astype(int)
        self.cols = sorted({c for _l, a, b, _f, _lo in STATS.values() for c in (a, b)} | {"min"})
        self.M = {}
        for c in self.cols:
            m = np.zeros((P, L))
            m[codes, pos] = lines[c].to_numpy(float)
            self.M[c] = m
        # Prior season per row and stat: (has_prior, numerator, denominator, gp).
        prior_n = {s: np.full(P, np.nan) for s in STATS}
        prior_d = {s: np.full(P, np.nan) for s in STATS}
        gp = np.zeros(P)
        for i in range(P):
            p = prior.get((int(self.row_pid[i]), int(self.row_season[i])))
            if p is None:
                continue
            gp[i] = p.gp
            for s in STATS:
                pn, pdn = getattr(p, f"{s}_n"), getattr(p, f"{s}_d")
                if pn is not None and pdn is not None and not np.isnan(pdn) and pdn > 0:
                    prior_n[s][i], prior_d[s][i] = pn, pdn
        self.gp = gp
        self.pcodes, self.puniq = pd.factorize(self.row_pid)        # row -> player
        self.NP = len(self.puniq)
        self.ident = np.tile(np.arange(L), (P, 1))
        self.has_prior = {s: ~np.isnan(prior_d[s]) for s in STATS}
        self.prior_n = {s: np.where(self.has_prior[s], prior_n[s], 0.0) for s in STATS}
        self.prior_d = {s: np.where(self.has_prior[s], prior_d[s], 0.0) for s in STATS}
        self.share = {}
        for stat, N in [(s, n) for s in STATS for n in WINDOWS]:
            w = weights[(stat, N)]
            self.share[(stat, N)] = np.where(self.has_prior[stat] & (gp >= MIN_PRIOR_GAMES),
                                             np.minimum(1.0, w / np.where(gp > 0, gp, 1)), 0.0)

    def cums(self, perm, rows, which=None):
        """Cumulative sums (with a leading zero) of the columns (all, or `which`) for the given rows in the order perm gives."""
        idx = rows[:, None]
        return {c: np.concatenate([np.zeros((len(rows), 1)), np.cumsum(self.M[c][idx, perm], axis=1)], axis=1)
                for c in (self.cols if which is None else which)}

    def windows(self, C, rows, stat, N):
        """Per (row, t) pair: D and F against the prior-weighted baseline (and the season-only one), with masks."""
        _label, a, b, _fmt, lo = STATS[stat]
        L = self.L
        t = np.arange(MIN_BASE_GAMES + N, L - N + 1)
        s0 = t - N
        cn, cd, cm = C[a], C[b], C["min"]
        bn, bd = cn[:, s0], cd[:, s0]
        wn, wd = cn[:, t] - cn[:, s0], cd[:, t] - cd[:, s0]
        fn, fd = cn[:, t + N] - cn[:, t], cd[:, t + N] - cd[:, t]
        ok = (t + N <= self.lengths[rows][:, None]) & (cm[:, s0] / s0 >= MIN_BASE_MPG) & (bd > 0) & (wd > 0) & (fd > 0)
        if lo is not None:
            ok &= (bd >= lo * s0) & (wd >= lo * N)
        sh = self.share[(stat, N)][rows][:, None]
        with np.errstate(divide="ignore", invalid="ignore"):
            base = (bn + sh * self.prior_n[stat][rows][:, None]) / (bd + sh * self.prior_d[stat][rows][:, None])
            base0 = bn / bd
            D, F = wn / wd - base, fn / fd - base
            D0, F0 = wn / wd - base0, fn / fd - base0
        return ok & self.has_prior[stat][rows][:, None], D, F, ok, D0, F0

    @staticmethod
    def slope(D, F, ok):
        d, f = D[ok], F[ok]
        dm = d - d.mean()
        return float((dm * (f - f.mean())).sum() / (dm ** 2).sum())

    def player_sums(self, D, F, ok, rows):
        """Per player: n, sum D, sum F, sum D^2, sum DF over his windows (rows = the row subset D/F cover)."""
        r = np.nonzero(ok)[0]
        d, f = D[ok], F[ok]
        pc = self.pcodes[rows[r]]
        NP = self.NP
        return np.stack([np.bincount(pc, minlength=NP), np.bincount(pc, weights=d, minlength=NP),
                         np.bincount(pc, weights=f, minlength=NP), np.bincount(pc, weights=d * d, minlength=NP),
                         np.bincount(pc, weights=d * f, minlength=NP)])

    @staticmethod
    def player_slopes(S):
        n, sd, sf, sdd, sdf = S
        with np.errstate(divide="ignore", invalid="ignore"):
            return (sdf - sd * sf / n) / (sdd - sd * sd / n)

    def null_slopes(self, perms=NULL_PERMS, rng=None, log=None):
        """League slopes (with a prior season; season-only) per (stat, window) under `perms` within-season shuffles:
        {(stat, N): (draws, draws_season_only)}. Drawn exactly as paper_beliefs' streak family draws them."""
        rng = null_rng() if rng is None else rng
        combos = [(s, n) for s in STATS for n in WINDOWS]
        out = {c: (np.empty(perms), np.empty(perms)) for c in combos}
        rows = np.arange(self.P)
        for start in range(0, perms, NULL_BLOCK):
            b = min(NULL_BLOCK, perms - start)
            blk = row_perms(rng, self.lengths, self.L, b)
            for i in range(b):
                Cp = self.cums(blk[i], rows)
                for stat, N in combos:
                    okp, D, F, ok, D0, F0 = self.windows(Cp, rows, stat, N)
                    out[(stat, N)][0][start + i] = self.slope(D, F, okp)
                    out[(stat, N)][1][start + i] = self.slope(D0, F0, ok)
            if log and (start // NULL_BLOCK) % 8 == 0:
                log(f"  shuffles {start + b}/{perms}")
        return out
