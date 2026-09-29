"""
Play Finder definitions, shared by scripts/build_play_finder.py (which writes
the play_finder_events codes) and api/routers/play_finder.py (which filters
on them), so the two can't drift apart.

One stored row is one player's part in one play-by-play event: a shot, the
assist or block on it, a free throw, a rebound, a turnover or the steal on
it, a foul. `cat` is the code below; its points are what the play added to
that player's team's score (0 for everything that doesn't score).
"""

# code -> (key, label, points for the row's team)
CATS = {
    1: ("made2", "Made 2", 2),
    2: ("made3", "Made 3", 3),
    3: ("miss2", "Missed 2", 0),
    4: ("miss3", "Missed 3", 0),
    5: ("ast2", "Assist on a 2", 2),
    6: ("ast3", "Assist on a 3", 3),
    7: ("blk", "Block", 0),
    8: ("stl", "Steal", 0),
    9: ("tov", "Turnover", 0),
    10: ("foul", "Foul", 0),
    11: ("ftm", "Made free throw", 1),
    12: ("ftx", "Missed free throw", 0),
    13: ("oreb", "Offensive rebound", 0),
    14: ("dreb", "Defensive rebound", 0),
}
CODE = {key: code for code, (key, _, _) in CATS.items()}
SHOT_CODES = (1, 2, 3, 4, 5, 6, 7)   # rows that carry the shot's distance

# What the finder offers: filter key -> (label, codes). Order = the menu's order.
FILTERS = {
    "fga": ("Any shot", (1, 2, 3, 4)),
    "made": ("Made shot", (1, 2)),
    "made2": ("Made 2", (1,)),
    "made3": ("Made 3", (2,)),
    "miss": ("Missed shot", (3, 4)),
    "miss2": ("Missed 2", (3,)),
    "miss3": ("Missed 3", (4,)),
    "ast": ("Assist", (5, 6)),
    "ast3": ("Assist on a 3", (6,)),
    "blk": ("Block", (7,)),
    "stl": ("Steal", (8,)),
    "tov": ("Turnover", (9,)),
    "foul": ("Foul", (10,)),
    "ft": ("Free throw", (11, 12)),
    "ftm": ("Made free throw", (11,)),
    "ftx": ("Missed free throw", (12,)),
    "reb": ("Rebound", (13, 14)),
    "oreb": ("Offensive rebound", (13,)),
    "dreb": ("Defensive rebound", (14,)),
}

# ESPN action types that are fouls when the parser doesn't read the event as
# anything else (shots, free throws, rebounds and turnovers come first, so
# "Offensive Foul Turnover" stays a turnover). Review outcomes ("No Foul") and
# lane violations aren't fouls.
def is_foul(action):
    if not action or action == "No Foul" or action.startswith("Free Throw") or "Turnover" in action:
        return False
    return "Foul" in action or "Technical" in action or action == "Offensive Charge"
