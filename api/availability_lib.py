"""Availability-aware pre-game odds: the math shared by
scripts/build_pregame_availability.py (builds the feature for every game
2020-21 on, fits and scores it) and api/routers/pregame_availability.py
(the what-if tool), so the page and the stored numbers can't drift apart.

The question: how much better are pre-game odds if you know who played?
The Season Simulator's pre-game model (api/season_sim_lib.py) knows each
team only as a team: its SRS so far this season blended with last season's.
A team missing its best player is rated as if he played.

Lineup strength of one team in one game

    S = 5 x (sum_p r_p m_p + max(0, 240 - sum_p m_p) x r_fill) / max(240, sum_p m_p)

over the rotation players p who played (expected to play 10+ minutes; the
script's ROTATION_MIN). Minutes their expected minutes don't cover go to a
replacement-level player (r_fill, the rating of unrated players), as in the
Forecast Ledger's depth chart; when they cover more than the game's 240
minutes, every player's share is scaled down. A team down to four regulars
and six call-ups is rated as such, not as the mean of its four regulars.

r_p is the player's rating fixed before the season (a box-score projection
or last season's RAPM, see RATING_SOURCES), in points per 100 possessions,
and m_p his *expected* minutes before tip-off (expected_minutes_table()), never
the minutes he actually played: those carry what happened in the game
(blowouts, foul trouble, an injury in the first quarter). Five times the
minutes-weighted mean is the team-level number (a lineup of five +2 players
rates +10).

What the team rating already knows: the SRS part reflects the lineups the
team used in its games so far, the prior part last season's roster. So the
reference is the same blend,

    ref = w S_prev + (1 - w) mean(S over the team's earlier games this season)
    w   = (1 / tau2) / (n / sigma^2 + 1 / tau2)

with n the team's games so far, tau2 and sigma the prior's own constants
(season_sim_lib.ratings_as_of uses exactly this weight), and S_prev the
team's lineup strength last season (the minutes per team game of last
season's rotation players on that team, with this season's player ratings,
so only personnel changed, not the ratings).

The feature is the home side's deviation minus the away side's,

    avail = (S_home - ref_home) - (S_away - ref_away),

and the model adds it to the pre-game model's log-odds with one fitted
coefficient (the base model is an offset, unchanged):

    logit p = logit p_base + b * avail.

So the only difference between the two forecasts is the lineup term.
"""

import numpy as np
import pandas as pd

RATING_SOURCES = {
    "bpm": "Box-score projection: the Projections page's Marcel-style BPM for the season, made from earlier seasons only "
           "(projection_backtest_rows)",
    "rapm": "Last season's RAPM with the box-score prior (player_rapm, version prior, season before)",
}
MIN_SEASON = {"bpm": 2021, "rapm": 2022}     # player_game_lines start in 2020-21; RAPM needs a season before


def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


TEAM_MINUTES = 240.0


def strength(ratings, minutes, fill):
    """5 x the minutes-weighted mean rating over a 240-minute game, uncovered minutes at `fill`."""
    r = np.asarray(ratings, float)
    m = np.asarray(minutes, float)
    return float(strength_from_sums((r * m).sum(), m.sum(), fill))


def strength_from_sums(rm, m, fill):
    """The same from sum(r * m) and sum(m) (arrays allowed)."""
    rm, m = np.asarray(rm, float), np.asarray(m, float)
    gap = np.maximum(0.0, TEAM_MINUTES - m)
    return 5.0 * (rm + gap * fill) / np.maximum(TEAM_MINUTES, m)


def prior_weight(n_games, sigma, tau2):
    """Weight of last season's part in the reference: the prior's share of the posterior precision."""
    n = np.asarray(n_games, float)
    return (1.0 / tau2) / (n / sigma ** 2 + 1.0 / tau2)


def reference(s_prev, mean_so_far, n_games, sigma, tau2):
    w = prior_weight(n_games, sigma, tau2)
    so_far = np.where(np.asarray(n_games) > 0, np.nan_to_num(np.asarray(mean_so_far, float)), 0.0)
    return w * np.asarray(s_prev, float) + (1 - w) * so_far


def fit_offset_logit(avail, base_logit, y, iters=50):
    """One coefficient b in logit p = base_logit + b * avail, by Newton's method. Returns (b, se)."""
    x = np.asarray(avail, float)
    off = np.asarray(base_logit, float)
    y = np.asarray(y, float)
    b = 0.0
    for _ in range(iters):
        p = sigmoid(off + b * x)
        h = float((x * x * p * (1 - p)).sum())
        step = float((x * (y - p)).sum()) / h
        b += step
        if abs(step) < 1e-12:
            break
    p = sigmoid(off + b * x)
    return b, float(1.0 / np.sqrt((x * x * p * (1 - p)).sum()))


def predict(p_base, avail, b):
    return sigmoid(logit(p_base) + b * np.asarray(avail, float))


def expected_minutes_table(lines, prev_mpg, default_minutes):
    """Expected minutes of every player in every team-game, from games before it only.

    `lines`: one row per player-game with season, game_date, game_id, team, player_id, minutes (> 0).
    `prev_mpg`: {(season, player_id): minutes per game the season before}.
    Rule, first that applies: his mean minutes in his earlier games this season for this team; for
    any team this season; last season's minutes per game; `default_minutes`. Returns `lines` with
    `exp_min` and `exp_from` (team / season / last_season / default)."""
    d = lines.sort_values(["season", "player_id", "game_date", "game_id"]).reset_index(drop=True)
    g_any = d.groupby(["season", "player_id"], sort=False).minutes
    n_any = g_any.cumcount()
    mean_any = (g_any.cumsum() - d.minutes) / n_any.where(n_any > 0)
    g_team = d.groupby(["season", "player_id", "team"], sort=False).minutes
    n_team = g_team.cumcount()
    mean_team = (g_team.cumsum() - d.minutes) / n_team.where(n_team > 0)
    last = pd.Series([prev_mpg.get((s, p)) for s, p in zip(d.season, d.player_id)], index=d.index, dtype=float)
    d["exp_min"] = mean_team.fillna(mean_any).fillna(last).fillna(default_minutes)
    d["exp_from"] = np.select([mean_team.notna(), mean_any.notna(), last.notna()], ["team", "season", "last_season"], "default")
    return d
