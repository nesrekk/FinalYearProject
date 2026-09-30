"""Season simulator and pre-game odds: the math shared by
scripts/build_season_sim.py (fits the pre-game model, stores every game's
odds and backtests the simulator) and api/routers/season_sim.py (simulates
any past date live with the stored coefficients, so the page and the
backtest can't drift apart). Builds on api/luck_lib.py (SRS ratings,
shrinkage, the probit baseline).

Pre-game odds. P(home team wins) from a logistic regression with one row
per game and no intercept:

    logit p = b_margin * expected margin + b_home_b2b * home_b2b + b_away_b2b * away_b2b

where expected margin = r_home - r_away + venue * home court, everything as
of the morning of the game (only earlier games are used). Four forms are
compared by leave-one-season-out log loss:

    baseline    luck_lib.win_chance as published on the Luck & Schedule page:
                this season's SRS shrunk toward zero (luck_lib.shrink_factor),
                probit with this season's home court and game SD; before 30
                games, home court only
    current     the same shrunk ratings in the logistic (no prior, no rest)
    prior       ratings blended with last season's final SRS (ratings_as_of)
    prior_rest  prior + back-to-back flags for both sides

Ratings as of a date (ratings_as_of): this season's SRS from the games so
far (raw venue-adjusted margins until 30 games have been played) combined
with last season's final SRS as a Bayesian prior. Prior mean = carry * last
season's rating (carry = the slope of one season's SRS on the previous
season's, fitted on all franchise pairs), prior variance tau2 = the residual
variance of that regression. A rating from g games carries about sigma^2 / g
of noise (sigma = last season's game-to-game SD), so the posterior mean is
the precision-weighted average and its variance 1 / (g / sigma^2 + 1 / tau2).
The home-court term is this season's so far blended with last season's,
weighted as hca_n0 games' worth of prior.

Season simulation (simulate): each run first draws every team's true
rating from its posterior (so a team's games are correlated through its
rating, which is what keeps win-total ranges honest), then every remaining
game with the pre-game model. Standings by win%; ties broken by head-to-head
record, then conference record, then a coin flip (the NBA's full procedure -
division winners, record against playoff teams - is not applied). Top 8 per
conference make the playoffs before 2020-21; from 2020-21 the top 6 go
straight in and seeds 7-10 play the play-in (7 v 8, 9 v 10, then the loser
of the first against the winner of the second; higher seed at home, same
pre-game model without rest terms).
"""

import hashlib

import numpy as np
import pandas as pd

from luck_lib import FRANCHISE, one_row_per_game, prepare, shrink_factor, srs_fit, win_chance

GAMES_REST_SQL = """SELECT g.game_id, g.season, g.game_date, g.team_abbreviation, g.opponent, g.is_home,
                           g.neutral_site, g.pts_for, g.pts_against, g.periods, f.rest_days
                    FROM game_scores g
                    LEFT JOIN team_game_fatigue f
                           ON f.game_id = g.game_id AND f.team_abbreviation = g.team_abbreviation
                    {where} ORDER BY g.game_date, g.game_id, g.team_abbreviation"""

# Conferences have not changed since the 2004-05 realignment, so one map
# covers every season on file (2009-10 on). NJN and NOH are the Nets' and
# Hornets/Pelicans' codes before 2012-13 / 2013-14. Matches
# impact_core.TEAM_META for the 30 current codes (the smoke test checks).
CONFERENCE = {
    "ATL": "East", "BOS": "East", "BKN": "East", "NJN": "East", "CHA": "East", "CHI": "East",
    "CLE": "East", "DET": "East", "IND": "East", "MIA": "East", "MIL": "East", "NYK": "East",
    "ORL": "East", "PHI": "East", "TOR": "East", "WAS": "East",
    "DAL": "West", "DEN": "West", "GSW": "West", "HOU": "West", "LAC": "West", "LAL": "West",
    "MEM": "West", "MIN": "West", "NOP": "West", "NOH": "West", "OKC": "West", "PHX": "West",
    "POR": "West", "SAC": "West", "SAS": "West", "UTA": "West",
}
CONFERENCES = ("East", "West")
PLAY_IN_FROM = 2021        # first season (end year) with the play-in tournament
PLAYOFF_SPOTS = 8
DIRECT_SPOTS = 6           # top 6 skip the play-in from 2020-21
MIN_GAMES_FOR_SRS = 30     # luck_lib.as_of's floor: below it ratings are raw margins
FORMS = ("baseline", "current", "prior", "prior_rest")
FEATURES = {
    "current": ["exp_margin_cur"],
    "prior": ["exp_margin"],
    "prior_rest": ["exp_margin", "home_b2b", "away_b2b"],
}
FORM_LABELS = {
    "baseline": "Luck & Schedule's projection (this season's ratings shrunk toward zero, probit)",
    "current": "This season's ratings only (logistic)",
    "prior": "Ratings blended with last season's",
    "prior_rest": "Ratings blended with last season's, plus back-to-backs",
}
DEFAULT_RUNS = 10000
TIE_RULE = ("Ties: head-to-head record, then conference record, then a coin flip (the NBA's full procedure, "
            "division winners and records against playoff teams, is not applied).")


def franchise(team):
    return FRANCHISE.get(team, team)


def prepare_rest(df):
    """luck_lib.prepare() plus back-to-back flags for both sides of every team-game row."""
    df = prepare(df)
    df["b2b"] = df.rest_days.fillna(99).astype(int) == 0
    opp = df[["game_id", "team_abbreviation", "b2b"]].rename(
        columns={"team_abbreviation": "opponent", "b2b": "opp_b2b"})
    return df.merge(opp, on=["game_id", "opponent"], how="left").fillna({"opp_b2b": False})


def home_rows(df):
    """One row per game from the home side (alphabetically first team at a neutral site)."""
    g = one_row_per_game(df).rename(columns={"team_abbreviation": "home", "opponent": "away",
                                              "b2b": "home_b2b", "opp_b2b": "away_b2b"})
    g = g.assign(home_won=g.margin > 0)
    return g


def season_prior(prev_games):
    """Last season's final ratings keyed by franchise, its home court and game SD."""
    ratings, hca, sigma, _ = srs_fit(prev_games)
    return {"ratings": {franchise(t): r for t, r in ratings.items()}, "hca": hca, "sigma": sigma}


def fit_params(season_games):
    """The three constants the prior needs, from every pair of consecutive seasons on file:
    carry (slope of a season's SRS on the previous season's, through the origin), tau2 (the
    residual variance around it) and hca_n0 (how many games' worth of prior last season's
    home court is worth: game variance / variance of year-to-year home-court changes)."""
    fits = {}
    for season, sg in season_games.items():
        ratings, hca, sigma, _ = srs_fit(sg)
        fits[season] = ({franchise(t): r for t, r in ratings.items()}, hca, sigma)
    xs, ys, dh, sig = [], [], [], []
    for season in sorted(fits):
        if season - 1 not in fits:
            continue
        prev, cur = fits[season - 1][0], fits[season][0]
        for f, r in cur.items():
            if f in prev:
                xs.append(prev[f])
                ys.append(r)
        dh.append(fits[season][1] - fits[season - 1][1])
        sig.append(fits[season][2])
    xs, ys = np.array(xs), np.array(ys)
    carry = float((xs * ys).sum() / (xs * xs).sum())
    tau2 = float(np.mean((ys - carry * xs) ** 2))
    hca_n0 = float(np.mean(np.array(sig) ** 2) / np.var(dh, ddof=1))
    return {"carry": carry, "tau2": tau2, "hca_n0": hca_n0, "pairs": int(len(xs)),
            "next_srs_r": float(np.corrcoef(xs, ys)[0, 1])}


def ratings_as_of(played, teams, prior, params):
    """Every team's rating as of a morning, from the team-game rows played before it.

    Returns a dict with, per team (dicts keyed by team code): `r_cur` (this season's SRS shrunk
    toward zero as on the Luck & Schedule page, 0 before 30 games), `var_cur` (its noise
    variance), `r_post` / `var_post` (the blend with last season's prior), `games`; plus `hca`
    (blended home court), `hca_cur`, `sigma` (last season's game SD, used for every weight),
    `shrink` and `n_games`."""
    sigma = prior["sigma"]
    sigma2 = sigma ** 2
    n = int(one_row_per_game(played).shape[0]) if len(played) else 0
    games = played.groupby("team_abbreviation").size().reindex(teams).fillna(0).astype(int).to_dict()
    if n >= MIN_GAMES_FOR_SRS:
        raw, hca_cur, sigma_cur, per_team = srs_fit(played)
        k = shrink_factor(raw, sigma_cur, per_team)
        raw = {t: raw.get(t, 0.0) for t in teams}
        hca = (n * hca_cur + params["hca_n0"] * prior["hca"]) / (n + params["hca_n0"])
        r_cur = {t: k * raw[t] for t in teams}
        # Noise left after shrinking: k * sigma^2 / g (empirical Bayes posterior variance).
        var_cur = {t: (k * sigma2 / games[t] if games[t] else 0.0) for t in teams}
    else:
        if n:
            adj = played.margin - played.venue * prior["hca"]
            raw = adj.groupby(played.team_abbreviation).mean().reindex(teams).fillna(0.0).to_dict()
        else:
            raw = {t: 0.0 for t in teams}
        hca_cur, k = prior["hca"], 0.0
        hca = prior["hca"]
        r_cur = {t: 0.0 for t in teams}
        var_cur = {t: 0.0 for t in teams}
    r_post, var_post = {}, {}
    for t in teams:
        prior_mean = params["carry"] * prior["ratings"].get(franchise(t), 0.0)
        prec = games[t] / sigma2 + 1.0 / params["tau2"]
        r_post[t] = (games[t] / sigma2 * raw[t] + prior_mean / params["tau2"]) / prec
        var_post[t] = 1.0 / prec
    return {"r_cur": r_cur, "var_cur": var_cur, "r_post": r_post, "var_post": var_post, "games": games,
            "hca": float(hca), "hca_cur": float(hca_cur), "sigma": float(sigma), "shrink": float(k),
            "n_games": n, "sigma_cur": float(sigma_cur) if n >= MIN_GAMES_FOR_SRS else None}


def baseline_chance(rows, rat):
    """luck_lib's own projection for these home rows: shrunk ratings, probit with this season's
    home court and game SD; before 30 games only the home court is known."""
    if rat["n_games"] >= MIN_GAMES_FOR_SRS:
        hca, sigma = rat["hca_cur"], rat["sigma_cur"]
        r_h = rows.home.map(rat["r_cur"]).to_numpy(float)
        r_a = rows.away.map(rat["r_cur"]).to_numpy(float)
    else:
        hca, sigma = rat["hca"], rat["sigma"]
        r_h = r_a = np.zeros(len(rows))
    return win_chance(r_h, r_a, rows.venue.to_numpy(float), hca, sigma)


def game_features(rows, rat):
    """Feature columns for home rows, from ratings as of their morning."""
    out = rows.copy()
    venue = out.venue.to_numpy(float)
    out["r_home"] = out.home.map(rat["r_post"]).to_numpy(float)
    out["r_away"] = out.away.map(rat["r_post"]).to_numpy(float)
    out["exp_margin"] = out.r_home - out.r_away + venue * rat["hca"]
    out["exp_margin_cur"] = (out.home.map(rat["r_cur"]).to_numpy(float) - out.away.map(rat["r_cur"]).to_numpy(float)
                             + venue * rat["hca"])
    out["home_b2b"] = out.home_b2b.astype(float)
    out["away_b2b"] = out.away_b2b.astype(float)
    out["p_baseline"] = baseline_chance(rows, rat)
    out["home_games"] = out.home.map(rat["games"])
    out["away_games"] = out.away.map(rat["games"])
    return out


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def logit_fit(X, y, ridge=1e-6, iters=50):
    """Logistic regression by Newton's method (no intercept: the home term is a feature)."""
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        p = sigmoid(X @ beta)
        w = p * (1 - p)
        H = X.T @ (X * w[:, None]) + ridge * np.eye(X.shape[1])
        step = np.linalg.solve(H, X.T @ (y - p) - ridge * beta)
        beta = beta + step
        if np.abs(step).max() < 1e-10:
            break
    return beta


def log_loss(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(p, y):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def calibration_bins(p, y, edges):
    """Rows of (bin_low, bin_high, n, mean predicted, actual rate)."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < edges[-1] else (p <= hi))
        out.append({"lo": float(lo), "hi": float(hi), "n": int(m.sum()),
                    "predicted": float(p[m].mean()) if m.any() else None,
                    "actual": float(y[m].mean()) if m.any() else None})
    return out


def win_prob(beta, exp_margin, home_b2b=None, away_b2b=None):
    """The fitted form's P(home wins). `beta` is {feature: coefficient}."""
    z = beta.get("exp_margin", beta.get("exp_margin_cur", 0.0)) * exp_margin
    if home_b2b is not None and "home_b2b" in beta:
        z = z + beta["home_b2b"] * home_b2b + beta["away_b2b"] * away_b2b
    return sigmoid(z)


def sim_seed(season, as_of, method="model"):
    """A fixed seed per (season, date, method), so the same link gives the same numbers."""
    h = hashlib.md5(f"{season}|{as_of}|{method}".encode()).hexdigest()
    return int(h[:8], 16)


def checkpoint_dates(season_games):
    """Opening day, the halfway date (the season's middle game) and the date about 60 of 82
    games in (the same share of a shortened season)."""
    per_game = season_games.drop_duplicates("game_id").sort_values(["game_date", "game_id"])
    dates = per_game.game_date.to_numpy()
    return {"opening": dates[0], "halfway": dates[len(dates) // 2],
            "sixty": dates[int(len(dates) * 60 / 82)]}


class Standings:
    """Wins, games, head-to-head and conference records from team-game rows, as arrays indexed
    by team position in `teams`."""

    def __init__(self, teams, played):
        self.teams = list(teams)
        self.idx = {t: i for i, t in enumerate(self.teams)}
        T = len(self.teams)
        self.wins = np.zeros(T)
        self.games = np.zeros(T)
        self.h2h = np.zeros((T, T))
        self.conf_wins = np.zeros(T)
        self.conf_games = np.zeros(T)
        self.pts_diff = np.zeros(T)
        if len(played):
            ti = played.team_abbreviation.map(self.idx).to_numpy()
            oi = played.opponent.map(self.idx).to_numpy()
            w = played.win.to_numpy(float)
            np.add.at(self.wins, ti, w)
            np.add.at(self.games, ti, 1)
            np.add.at(self.h2h, (ti, oi), w)
            same = (played.team_abbreviation.map(CONFERENCE) == played.opponent.map(CONFERENCE)).to_numpy()
            np.add.at(self.conf_wins, ti[same], w[same])
            np.add.at(self.conf_games, ti[same], 1)
            np.add.at(self.pts_diff, ti, played.margin.to_numpy(float))
        self.conf_idx = {c: np.array([i for i, t in enumerate(self.teams) if CONFERENCE[t] == c]) for c in CONFERENCES}


def rank_conference(wp, cwp, h2h, idx, rng):
    """Order the conference's teams (positions in `idx`) in every run: by win%, ties by
    head-to-head, then conference win%, then a coin flip. wp, cwp: (N, T); h2h: (N, T, T).
    Returns (N, len(idx)) team indices, best first."""
    N = wp.shape[0]
    w = wp[:, idx]
    c = cwp[:, idx]
    jitter = rng.random(w.shape)
    order = np.lexsort((jitter, -w), axis=1)
    n = np.arange(N)
    m = len(idx)
    for _ in range(m):
        swapped = False
        for k in range(m - 1):
            a, b = order[:, k].copy(), order[:, k + 1].copy()   # copies: the swap below rewrites both columns
            tie = np.isclose(w[n, a], w[n, b])
            if not tie.any():
                continue
            ta, tb = idx[a], idx[b]
            hab, hba = h2h[n, ta, tb], h2h[n, tb, ta]
            better_b = tie & ((hba > hab) | ((hba == hab) & (c[n, b] > c[n, a])))
            if better_b.any():
                order[better_b, k] = b[better_b]
                order[better_b, k + 1] = a[better_b]
                swapped = True
        if not swapped:
            break
    return order


def simulate(st, remaining, season, p_matrix, ratings_draw, beta, hca, runs, rng, extra_games=None):
    """Monte Carlo the rest of a season.

    st: Standings before the date. remaining: home rows still to play (home, away, venue,
    home_b2b, away_b2b). p_matrix(r): function of the per-run rating draws (N, T) returning
    P(home wins) per remaining game as (N, G) (or a (G,) vector to broadcast). ratings_draw:
    (N, T) draws of every team's true rating (used again for the play-in) or None.
    extra_games: optional list of (team, venue) for games whose opponent isn't known yet (the
    Forecast Ledger's NBA Cup placeholders): played against a league-average opponent (rating 0)
    with the same model, counted in wins and games only (not head-to-head or conference records).
    Without it the random draws, and so every result, are exactly as before.
    Returns a dict of per-run arrays: wins (N, T), position (N, T; 1-15 within the conference),
    playoffs, top6, playin (bools), seed (N, T; playoff seed 1-8 after the play-in, 0 = out),
    plus the games each team ends with."""
    T = len(st.teams)
    N = runs
    G = len(remaining)
    hi = remaining.home.map(st.idx).to_numpy()
    ai = remaining.away.map(st.idx).to_numpy()
    p = p_matrix(ratings_draw)
    u = rng.random((N, G), dtype=np.float32)
    home_win = (u < p).astype(np.float32)
    H = np.zeros((G, T), np.float32)
    A = np.zeros((G, T), np.float32)
    H[np.arange(G), hi] = 1
    A[np.arange(G), ai] = 1
    wins = st.wins[None, :] + home_win @ H + (1 - home_win) @ A
    games = st.games + H.sum(0) + A.sum(0)
    if extra_games:
        ei = np.array([st.idx[t] for t, _ in extra_games])
        ev = np.array([v for _, v in extra_games], np.float32)
        if ratings_draw is None:
            wp0 = np.where(st.games > 0, st.wins / np.where(st.games > 0, st.games, 1), 0.5)
            pe = np.broadcast_to(wp0[ei].astype(np.float32), (N, len(ei)))
        else:
            pe = win_prob(beta, ratings_draw[:, ei] + ev[None, :] * hca)
        ew = (rng.random((N, len(ei)), dtype=np.float32) < pe).astype(np.float32)
        E = np.zeros((len(ei), T), np.float32)
        E[np.arange(len(ei)), ei] = 1
        wins = wins + ew @ E
        games = games + E.sum(0)
    games_safe = np.where(games > 0, games, 1)
    wp = wins / games_safe
    # Head-to-head per run: base plus the simulated games, aggregated by ordered pair.
    pair_key = hi * T + ai
    pairs, pair_inv = np.unique(pair_key, return_inverse=True)
    P = np.zeros((G, len(pairs)), np.float32)
    P[np.arange(G), pair_inv] = 1
    home_pair_wins = home_win @ P                      # (N, pairs)
    away_pair_wins = (1 - home_win) @ P
    h2h = np.broadcast_to(st.h2h, (N, T, T)).copy()
    ph, pa = pairs // T, pairs % T
    h2h[:, ph, pa] += home_pair_wins
    h2h[:, pa, ph] += away_pair_wins
    same = np.array([CONFERENCE[st.teams[h]] == CONFERENCE[st.teams[a]] for h, a in zip(hi, ai)], np.float32)
    conf_wins = st.conf_wins[None, :] + (home_win * same) @ H + ((1 - home_win) * same) @ A
    conf_games = st.conf_games + (H * same[:, None]).sum(0) + (A * same[:, None]).sum(0)
    cwp = conf_wins / np.where(conf_games > 0, conf_games, 1)

    position = np.zeros((N, T), np.int16)
    playoffs = np.zeros((N, T), bool)
    top6 = np.zeros((N, T), bool)
    playin = np.zeros((N, T), bool)
    seed = np.zeros((N, T), np.int8)
    n = np.arange(N)
    for conf in CONFERENCES:
        idx = st.conf_idx[conf]
        order = rank_conference(wp, cwp, h2h, idx, rng)
        teams_in_order = idx[order]                     # (N, 15) team indices
        for k in range(len(idx)):
            position[n, teams_in_order[:, k]] = k + 1
        if season >= PLAY_IN_FROM:
            for k in range(DIRECT_SPOTS):
                top6[n, teams_in_order[:, k]] = True
                playoffs[n, teams_in_order[:, k]] = True
                seed[n, teams_in_order[:, k]] = k + 1
            for k in range(DIRECT_SPOTS, DIRECT_SPOTS + 4):
                playin[n, teams_in_order[:, k]] = True
            s7, s8, s9, s10 = (teams_in_order[:, k] for k in range(6, 10))
            r = ratings_draw if ratings_draw is not None else None

            def p_game(home, away):
                if r is None:   # record method: log5 on final win%
                    a, b = np.clip(wp[n, home], 0.05, 0.95), np.clip(wp[n, away], 0.05, 0.95)
                    return a * (1 - b) / (a * (1 - b) + b * (1 - a))
                return win_prob(beta, r[n, home] - r[n, away] + hca)

            win_a = rng.random(N) < p_game(s7, s8)
            seed7 = np.where(win_a, s7, s8)
            loser_a = np.where(win_a, s8, s7)
            win_b = rng.random(N) < p_game(s9, s10)
            winner_b = np.where(win_b, s9, s10)
            win_c = rng.random(N) < p_game(loser_a, winner_b)
            seed8 = np.where(win_c, loser_a, winner_b)
            playoffs[n, seed7] = True
            playoffs[n, seed8] = True
            seed[n, seed7] = 7
            seed[n, seed8] = 8
        else:
            for k in range(PLAYOFF_SPOTS):
                playoffs[n, teams_in_order[:, k]] = True
                seed[n, teams_in_order[:, k]] = k + 1
    return {"wins": wins, "games": games, "position": position, "playoffs": playoffs, "top6": top6,
            "playin": playin, "seed": seed, "remaining_games": G}


# First-round pairs in bracket order: the 1/8 winner meets the 4/5 winner, the 2/7 winner the 3/6 winner
# (a fixed bracket, no reseeding; Wikipedia "2025 NBA playoffs" and "NBA playoffs", read 2026-09-30).
BRACKET = ((1, 8), (4, 5), (3, 6), (2, 7))
SERIES_HOME_GAMES = 4      # 2-2-1-1-1: the team with home court hosts games 1, 2, 5 and 7
SERIES_AWAY_GAMES = 3


def play_series(high, low, r, beta, hca, rng):
    """Best-of-seven series between two arrays of team indices (one pair per run), `high` holding home
    court. Every game from the run's ratings with the pre-game model (no rest terms). Playing all seven
    games and taking whoever wins four gives the same winner as stopping at four. Returns the winners."""
    n = np.arange(len(high))
    p_high_home = win_prob(beta, r[n, high] - r[n, low] + hca)
    p_low_home = win_prob(beta, r[n, low] - r[n, high] + hca)
    w = ((rng.random((len(high), SERIES_HOME_GAMES)) < p_high_home[:, None]).sum(1)
         + (rng.random((len(high), SERIES_AWAY_GAMES)) >= p_low_home[:, None]).sum(1))
    return np.where(w >= 4, high, low)


def simulate_playoffs(sim, st, ratings_draw, beta, hca, rng):
    """The playoffs after a simulated regular season: the seeds from `simulate`, a fixed bracket per
    conference, home court to the better seed, and in the Finals to the better regular-season record in
    that run (ties by a coin flip; the NBA's head-to-head and inter-conference tiebreaks are not applied).
    Needs the per-run rating draws. Returns bool arrays (N, T): won a first-round series (round2),
    reached the conference finals, reached the Finals, won the title."""
    N, T = sim["seed"].shape
    n = np.arange(N)
    out = {k: np.zeros((N, T), bool) for k in ("round2", "conf_finals", "finals", "title")}
    champs = {}
    for conf in CONFERENCES:
        idx = st.conf_idx[conf]
        seeds = sim["seed"][:, idx]
        by_seed = {}
        for s in range(1, PLAYOFF_SPOTS + 1):
            hit = seeds == s
            if not (hit.sum(1) == 1).all():
                raise ValueError(f"{conf}: seed {s} is not filled exactly once in every run")
            by_seed[s] = idx[hit.argmax(1)]
        winners = []
        for a, b in BRACKET:
            w = play_series(by_seed[a], by_seed[b], ratings_draw, beta, hca, rng)
            out["round2"][n, w] = True
            winners.append((w, np.where(w == by_seed[a], a, b)))
        semis = []
        for (w1, s1), (w2, s2) in (winners[0:2], winners[2:4]):
            high = np.where(s1 < s2, w1, w2)
            low = np.where(s1 < s2, w2, w1)
            w = play_series(high, low, ratings_draw, beta, hca, rng)
            out["conf_finals"][n, w] = True
            semis.append((w, np.where(w == high, np.minimum(s1, s2), np.maximum(s1, s2))))
        (w1, s1), (w2, s2) = semis
        high = np.where(s1 < s2, w1, w2)
        low = np.where(s1 < s2, w2, w1)
        champ = play_series(high, low, ratings_draw, beta, hca, rng)
        out["finals"][n, champ] = True
        champs[conf] = champ
    wp = sim["wins"] / np.where(sim["games"] > 0, sim["games"], 1)
    e, w = champs["East"], champs["West"]
    coin = rng.random(N) < 0.5
    east_home = (wp[n, e] > wp[n, w]) | (np.isclose(wp[n, e], wp[n, w]) & coin)
    high = np.where(east_home, e, w)
    low = np.where(east_home, w, e)
    title = play_series(high, low, ratings_draw, beta, hca, rng)
    out["title"][n, title] = True
    return out


def summarize(sim, st, season):
    """Per-team odds from the run arrays (dicts keyed by team code)."""
    wins = sim["wins"]
    out = {}
    T = len(st.teams)
    for i, t in enumerate(st.teams):
        w = wins[:, i]
        pos = sim["position"][:, i]
        hist = np.bincount(w.astype(int), minlength=int(sim["games"][i]) + 1)
        out[t] = {
            "team": t, "conference": CONFERENCE[t],
            "games_final": int(sim["games"][i]),
            "mean_wins": float(w.mean()), "sd_wins": float(w.std()),
            "wins_p10": float(np.percentile(w, 10)), "wins_p50": float(np.percentile(w, 50)),
            "wins_p90": float(np.percentile(w, 90)),
            "wins_hist": hist.astype(int).tolist(),
            "p_playoffs": float(sim["playoffs"][:, i].mean()),
            "p_top6": float(sim["top6"][:, i].mean()) if season >= PLAY_IN_FROM else None,
            "p_playin": float(sim["playin"][:, i].mean()) if season >= PLAY_IN_FROM else None,
            "p_first": float((pos == 1).mean()),
            "p_seed": [float((pos == k).mean()) for k in range(1, T // 2 + 1)],
        }
    return out


def model_p_matrix(remaining, st, beta, hca):
    """P(home wins) for the remaining games as a function of per-run rating draws."""
    hi = remaining.home.map(st.idx).to_numpy()
    ai = remaining.away.map(st.idx).to_numpy()
    venue = remaining.venue.to_numpy(np.float32)
    hb = remaining.home_b2b.to_numpy(np.float32)
    ab = remaining.away_b2b.to_numpy(np.float32)

    def f(r):
        exp_margin = r[:, hi] - r[:, ai] + venue[None, :] * hca
        return win_prob(beta, exp_margin.astype(np.float32), hb[None, :], ab[None, :]).astype(np.float32)
    return f


def record_p_matrix(remaining, st):
    """The standings' own odds: log5 of the two teams' current win% (no home court, no ratings)."""
    hi = remaining.home.map(st.idx).to_numpy()
    ai = remaining.away.map(st.idx).to_numpy()
    wp = np.where(st.games > 0, st.wins / np.where(st.games > 0, st.games, 1), 0.5)
    wp = np.clip(wp, 0.05, 0.95)
    a, b = wp[hi], wp[ai]
    p = (a * (1 - b) / (a * (1 - b) + b * (1 - a))).astype(np.float32)
    return lambda r: p[None, :]


def draw_ratings(r_post, var_post, teams, runs, rng):
    mean = np.array([r_post[t] for t in teams], np.float32)
    sd = np.sqrt(np.array([var_post[t] for t in teams], np.float32))
    return mean[None, :] + sd[None, :] * rng.standard_normal((runs, len(teams)), dtype=np.float32)


def current_positions(st, season, rng):
    """Where each team stands today by the same ranking rule (one run, no games simulated)."""
    wp = np.where(st.games > 0, st.wins / np.where(st.games > 0, st.games, 1), 0.0)[None, :]
    cwp = np.where(st.conf_games > 0, st.conf_wins / np.where(st.conf_games > 0, st.conf_games, 1), 0.0)[None, :]
    h2h = st.h2h[None, :, :]
    pos = {}
    for conf in CONFERENCES:
        idx = st.conf_idx[conf]
        order = rank_conference(wp, cwp, h2h, idx, rng)[0]
        for k, j in enumerate(order):
            pos[st.teams[idx[j]]] = k + 1
    return pos
