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
