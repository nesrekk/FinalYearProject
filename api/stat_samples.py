"""
stat_samples.py
================
How big a player-season's sample is for each Leaderboard stat, in the unit
build_stat_stability.py measured that stat's stability in, as SQL over
player_season_stats (per-game columns times gp). Shared by that script
(which uses it to put its sample sizes on the same scale as these season
totals) and routers/leaderboard.py (reliability of each row), so both use
one definition.

Rate stats whose denominator NBA.com doesn't publish (rebound, offensive
rebound, assist and usage %) get it back as own count / rate: e.g. a
player's rebounds divided by his rebound % is the number of rebounds
available while he was on the floor. Stats missing here have no sample
size in the season table (BPM, VORP, impact score): the page shows only
their year-to-year correlation.
"""

GAMES = "gp"

SEASON_SAMPLE_SQL = {
    **{k: GAMES for k in ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fg3a", "fta", "oreb", "min",
                          "plus_minus")},
    "fg_pct": "fga * gp",
    "efg_pct": "fga * gp",
    "fg3_pct": "fg3a * gp",
    "ft_pct": "fta * gp",
    "ts_pct": "(fga + 0.44 * fta) * gp",
    "reb_pct": "reb * gp / NULLIF(reb_pct, 0)",
    "oreb_pct": "oreb * gp / NULLIF(oreb_pct, 0)",
    "ast_pct": "ast * gp / NULLIF(ast_pct, 0)",
    "usg_pct": "(fga + 0.44 * fta + tov) * gp / NULLIF(usg_pct, 0)",
    "tov_pct": "(fga + 0.44 * fta + ast + tov) * gp",
    "off_rating": "poss",
    "def_rating": "poss",
    "net_rating": "poss",
}
