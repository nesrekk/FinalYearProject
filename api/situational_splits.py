"""
situational_splits.py
======================
Shared definitions for Situational Splits: which situations, which stats,
and who qualifies. Used by scripts/build_situational_splits.py (which
computes every player-season's splits and the league baselines) and
routers/situational_splits.py (which serves them), so both use one
definition.

Every split has two sides, A and B, and the effect is A minus B:

  home     home games            vs away games
  rest     second night of a back-to-back he also played the first night of
                                 vs games after 1+ days of team rest
  travel   after a trip of 1,000+ miles since the team's last game
                                 vs after under 300 miles (including none)
  opp      against that season's top-10 teams by average margin
                                 vs against its bottom-10

Games that fit neither side (a 300-999 mile trip, a middle-10 opponent, a
second night he sat the first night of, the first game of a season) are in
neither. Rest and travel are the team's schedule (team_game_fatigue);
"played the first night" comes from his own game lines.

Every stat is a ratio of two per-game sums, so a side's value is the ratio
of its sums (points per 36 = 36 x points / minutes over the side's games).
"""

# key -> (label, side A label, side B label, pandas condition for A, for B)
SPLITS = {
    "home": ("Home vs. away", "Home", "Away", "is_home", "~is_home"),
    "rest": ("Back-to-back vs. rested", "Back-to-back", "Rested (1+ days)",
             "(rest_days == 0) & played_prev_night", "rest_days >= 1"),
    "travel": ("Long trip vs. short or none", "After 1,000+ miles", "After under 300 miles",
               "travel_miles >= 1000", "travel_miles < 300"),
    "opp": ("Top-10 vs. bottom-10 opponents", "vs. top-10 teams", "vs. bottom-10 teams",
            "opp_rank <= 10", "opp_rank >= 21"),
}

# key -> (label, numerator column, denominator column, scale, format, side floor in denominator units)
# Counting stats are per 36 minutes, so a side where he played fewer minutes
# isn't "worse" just for that; minutes per game is its own row.
STATS = {
    "min": ("Minutes per game", "min", "one", 1.0, "num", None),
    "pts": ("Points per 36", "pts", "min", 36.0, "num", 150.0),
    "reb": ("Rebounds per 36", "reb", "min", 36.0, "num", 150.0),
    "ast": ("Assists per 36", "ast", "min", 36.0, "num", 150.0),
    "stl": ("Steals per 36", "stl", "min", 36.0, "num", 150.0),
    "blk": ("Blocks per 36", "blk", "min", 36.0, "num", 150.0),
    "tov": ("Turnovers per 36", "tov", "min", 36.0, "num", 150.0),
    "fg3a": ("3-point attempts per 36", "fg3a", "min", 36.0, "num", 150.0),
    "ts_pct": ("True shooting %", "pts", "tsa2", 1.0, "pct", 100.0),
    "fg3_pct": ("3P%", "fg3m", "fg3a", 1.0, "pct", 25.0),
    "usg_pct": ("Usage %", "plays", "tm_plays", 1.0, "pct", 200.0),
}

MIN_GAMES = 10       # games on each side to qualify
STORE_GAMES = 3      # rows with fewer games on either side aren't stored
LONG_TRIP, SHORT_TRIP = 1000, 300

LINES_SQL = """
    WITH margin AS (
        SELECT season, team_abbreviation,
               RANK() OVER (PARTITION BY season ORDER BY AVG(plus_minus) DESC) AS opp_rank
        FROM team_game_fatigue GROUP BY season, team_abbreviation
    ), lines AS (
        SELECT l.*, LAG(l.game_date) OVER (PARTITION BY l.player_id ORDER BY l.game_date) AS prev_date
        FROM player_game_lines l WHERE l.seconds > 0
    )
    SELECT l.player_id, l.season, l.game_date, l.team_abbreviation AS team, f.opponent, f.is_home,
           f.rest_days, f.travel_miles_since_last AS travel_miles, m.opp_rank,
           (l.prev_date = l.game_date - 1) AS played_prev_night,
           l.seconds / 60.0 AS min, l.pts, l.oreb + l.dreb AS reb, l.ast, l.stl, l.blk, l.tov,
           l.fga, l.fg3m, l.fg3a, l.fta, l.tm_fga, l.tm_fta, l.tm_tov
    FROM lines l
    JOIN team_game_fatigue f ON f.team_abbreviation = l.team_abbreviation AND f.game_date = l.game_date
    JOIN margin m ON m.season = f.season AND m.team_abbreviation = f.opponent
    ORDER BY l.player_id, l.game_date
"""


def add_columns(df):
    """The denominators the catalogue refers to."""
    df = df.copy()
    df["one"] = 1.0
    df["tsa2"] = 2 * (df["fga"] + 0.44 * df["fta"])
    df["plays"] = df["fga"] + 0.44 * df["fta"] + df["tov"]
    df["tm_plays"] = df["tm_fga"] + 0.44 * df["tm_fta"] + df["tm_tov"]
    df["played_prev_night"] = df["played_prev_night"].fillna(False).astype(bool)
    return df
