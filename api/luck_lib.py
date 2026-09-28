"""Luck & schedule strength: the math shared by scripts/build_luck_schedule.py
and api/routers/luck_schedule.py (the endpoint recomputes ratings "as of" a
date with the same functions, so stored and live numbers can't drift apart).

Games come from `game_scores` (real final scores from ESPN, checked equal to
Basketball-Reference's season point totals for every team-season), one row
per team-game with the same rows and abbreviations as `team_game_fatigue`.

  srs_fit()            least-squares ratings: margin = home court + r_team - r_opp,
                       ratings sum to zero, per season (a Simple Rating System with
                       a home-court term; neutral-site games get none)
  expected_win_pct()   season win% from points, by the method the script picked
  project_remaining()  win chances for games not yet played, from ratings shrunk
                       toward zero by how much of their spread is still noise
"""

import numpy as np
import pandas as pd
from scipy.stats import norm

GAMES_SQL = """SELECT game_id, season, game_date, team_abbreviation, opponent, is_home, neutral_site,
                      pts_for, pts_against, periods
               FROM game_scores {where} ORDER BY game_date, game_id, team_abbreviation"""

# The 2019-20 restart games (Orlando bubble, from 2020-07-30) were played at a
# neutral site even though each has a nominal home team.
BUBBLE_START = pd.Timestamp("2020-07-30").date()

# The same franchise under two abbreviations, for season-to-season pairs.
FRANCHISE = {"NJN": "BKN", "NOH": "NOP"}

CLOSE_MARGINS = (3, 5)


def prepare(df):
    """Add margin and a venue sign (+1 home, -1 away, 0 neutral) to team-game rows."""
    df = df.copy()
    df["margin"] = df.pts_for - df.pts_against
    df["win"] = df.margin > 0
    neutral = df.neutral_site | ((df.season == 2020) & (df.game_date >= BUBBLE_START))
    df["venue"] = np.where(neutral, 0, np.where(df.is_home, 1, -1))
    return df


def one_row_per_game(df):
    """Each game once: the home side's row, or the alphabetically first team at a neutral site."""
    keep = (df.venue == 1) | ((df.venue == 0) & (df.team_abbreviation < df.opponent))
    return df[keep]


def srs_fit(games):
    """Least-squares ratings for one season's games (team-game rows, any subset).

    Solves margin = hca * venue + r_team - r_opp over one row per game with the
    ratings constrained to sum to zero. Returns (ratings dict, hca, residual SD
    of a game's margin, games per team)."""
    g = one_row_per_game(games)
    teams = sorted(set(g.team_abbreviation) | set(g.opponent))
    idx = {t: i for i, t in enumerate(teams)}
    n, k = len(g), len(teams)
    X = np.zeros((n + 1, k + 1))
    rows = np.arange(n)
    X[rows, [idx[t] for t in g.team_abbreviation]] = 1
    X[rows, [idx[t] for t in g.opponent]] = -1
    X[rows, k] = g.venue.to_numpy()
    X[n, :k] = 1                                # ratings sum to zero
    y = np.append(g.margin.to_numpy(float), 0.0)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y[:n] - X[:n] @ beta
    dof = max(n - k, 1)
    sigma = float(np.sqrt((resid ** 2).sum() / dof))
    ratings = dict(zip(teams, beta[:k]))
    per_team = pd.concat([g.team_abbreviation, g.opponent]).value_counts().to_dict()
    return ratings, float(beta[k]), sigma, per_team


def shrink_factor(ratings, sigma, games_per_team):
    """How much of the ratings' spread is real: 1 - (noise variance / total
    variance), where a rating from g games carries about sigma^2 / g of noise
    (empirical Bayes, the same idea as regressing a stat toward the mean)."""
    r = np.array(list(ratings.values()))
    g = np.mean(list(games_per_team.values()))
    total = r.var()
    if total <= 0 or g <= 0:
        return 0.0
    return float(np.clip(1 - (sigma ** 2 / g) / total, 0.0, 1.0))


def win_chance(r_team, r_opp, venue, hca, sigma):
    return norm.cdf((r_team - r_opp + hca * venue) / sigma)


def expected_win_pct(method, param, pts_for, pts_against, games):
    """Season win% expected from points. `method` is the one build_luck_schedule.py
    picked by leave-one-season-out error (stored in luck_model_fit)."""
    pts_for = np.asarray(pts_for, float)
    pts_against = np.asarray(pts_against, float)
    games = np.asarray(games, float)
    mov = (pts_for - pts_against) / games
    if method == "linear":
        return np.clip(0.5 + param * mov, 0, 1)
    if method == "normal":
        return norm.cdf(mov / param)
    if method == "pythagorean":
        a, b = pts_for ** param, pts_against ** param
        return a / (a + b)
    raise ValueError(method)


def team_table(games):
    """Per-team record, points, margin and close-game records from team-game rows."""
    d = games.assign(
        ot=games.periods > 4,
        **{f"close{m}": games.margin.abs() <= m for m in CLOSE_MARGINS},
    )
    out = d.groupby("team_abbreviation").agg(
        games=("win", "size"), wins=("win", "sum"),
        pts_for=("pts_for", "sum"), pts_against=("pts_against", "sum"),
    )
    out["losses"] = out.games - out.wins
    for m in CLOSE_MARGINS:
        c = d[d[f"close{m}"]].groupby("team_abbreviation").win.agg(["sum", "size"])
        out[f"close{m}_w"] = c["sum"].reindex(out.index).fillna(0).astype(int)
        out[f"close{m}_l"] = (c["size"] - c["sum"]).reindex(out.index).fillna(0).astype(int)
    ot = d[d.ot].groupby("team_abbreviation").win.agg(["sum", "size"])
    out["ot_w"] = ot["sum"].reindex(out.index).fillna(0).astype(int)
    out["ot_l"] = (ot["size"] - ot["sum"]).reindex(out.index).fillna(0).astype(int)
    out["mov"] = (out.pts_for - out.pts_against) / out.games
    return out


def schedule_strength(games, ratings):
    """Mean rating of the opponents in `games` per team (SOS), and the venue split."""
    d = games.assign(opp_r=games.opponent.map(ratings))
    return d.groupby("team_abbreviation").agg(
        sos=("opp_r", "mean"), n=("opp_r", "size"),
        home=("venue", lambda v: int((v == 1).sum())), away=("venue", lambda v: int((v == -1).sum())),
    )


def as_of(season_games, cutoff, method, param):
    """Standings, ratings and the rest of the schedule for one season as of the
    morning of `cutoff` (games on that date not yet played). Returns
    (DataFrame per team, dict of season numbers). Needs ratings from played games."""
    played = season_games[season_games.game_date < cutoff]
    left = season_games[season_games.game_date >= cutoff]
    if one_row_per_game(played).shape[0] < 30:
        return None, None
    ratings, hca, sigma, per_team = srs_fit(played)
    k = shrink_factor(ratings, sigma, per_team)
    tbl = team_table(played)
    tbl["exp_win_pct"] = expected_win_pct(method, param, tbl.pts_for, tbl.pts_against, tbl.games)
    tbl["srs"] = pd.Series(ratings)
    tbl = tbl.join(schedule_strength(played, ratings)[["sos"]])
    rest = schedule_strength(left, ratings).rename(columns={"sos": "rem_sos", "n": "rem_games",
                                                            "home": "rem_home", "away": "rem_away"})
    tbl = tbl.join(rest, how="outer")
    # Projection for the games left: shrunk ratings, home court, game-level SD.
    shrunk = {t: r * k for t, r in ratings.items()}
    lp = left.assign(p=win_chance(left.team_abbreviation.map(shrunk).fillna(0).to_numpy(),
                                  left.opponent.map(shrunk).fillna(0).to_numpy(),
                                  left.venue.to_numpy(), hca, sigma))
    tbl["rem_exp_wins"] = lp.groupby("team_abbreviation").p.sum()
    tbl["rem_actual_wins"] = left.groupby("team_abbreviation").win.sum()
    for c in ("games", "wins", "losses", "rem_games", "rem_home", "rem_away", "rem_actual_wins"):
        tbl[c] = tbl[c].fillna(0).astype(int)
    tbl["rem_exp_wins"] = tbl.rem_exp_wins.fillna(0.0)
    tbl["proj_wins"] = tbl.wins + tbl.rem_exp_wins
    info = {"hca": hca, "sigma": sigma, "shrink": k, "played_games": int(one_row_per_game(played).shape[0]),
            "left_games": int(one_row_per_game(left).shape[0])}
    return tbl, info
