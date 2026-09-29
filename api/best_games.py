"""Best-games definitions, shared by scripts/build_best_games.py (which writes
them) and api/routers/best_games.py (which shows and checks them), so the
formula on the page is the formula that produced the stored numbers.

Excitement of a game (2020-21 on, where play-by-play exists):

    excitement = swing + LEAD_CHANGE_W * lead_changes + OVERTIME_W * overtime_periods
                 - MARGIN_W * final_margin

    swing          sum of |change in the home team's win probability| from one play
                   to the next, over the whole game (Game Replay's win-probability
                   model, on the reconciled score); 1.0 = a 100-point swing in total
    lead_changes   times the lead passed from one team to the other (a tie in
                   between doesn't count as a change, and a tie doesn't break a lead
                   the same team then regains)
    overtime       extra periods played
    final_margin   the winning margin, points

The three weights are a judgment call, not fitted (nothing on file says which games
fans found exciting). The build script stores the rank agreement with the plain
swing so the page can say how much they matter.
"""

LEAD_CHANGE_W = 0.10
OVERTIME_W = 1.0
MARGIN_W = 0.05

FORMULA = (
    "excitement = swing + 0.10 x lead changes + 1.0 x overtime periods - 0.05 x final margin, where swing is the sum of "
    "|change in the home team's win probability| from play to play (Game Replay's model, on the reconciled score)"
)

# Sorts the page offers -> SQL on best_games aliased `b` (the router adds game_id last for a stable order).
SORTS = {
    "excitement": "b.excitement DESC",
    "swing": "b.swing DESC",
    "comeback": "b.comeback DESC, b.win_min_wp",
    "lead_changes": "b.lead_changes DESC, b.excitement DESC",
    "close": "b.final_margin, b.excitement DESC",
    "overtime": "b.periods DESC, b.excitement DESC",
    "newest": "b.game_date DESC, b.excitement DESC",
}
SORT_LABELS = {
    "excitement": "Excitement",
    "swing": "Win-probability swing",
    "comeback": "Biggest comeback",
    "lead_changes": "Most lead changes",
    "close": "Closest finish",
    "overtime": "Most overtimes",
    "newest": "Newest",
}


def excitement(swing, lead_changes, overtime_periods, final_margin):
    return swing + LEAD_CHANGE_W * lead_changes + OVERTIME_W * overtime_periods - MARGIN_W * final_margin
