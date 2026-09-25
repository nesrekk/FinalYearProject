"""
wpa_lib.py
===========
Shared win-probability model loading + scoring, used by compute_wpa.py
and by the /games/wp-replay* endpoints in api/impact_api.py, so there's
exactly one real implementation of "turn (seconds remaining, score
margin) into a win probability" rather than two copies that could drift
apart. Model files (wpa_model.pkl, wpa_scaler.pkl) are resolved by an
absolute path next to this file, so callers don't need to run from
scripts/.

Also holds the shared real clutch-time definition (final 5 min of
regulation/OT, score within 5 points — the NBA's own convention) and a
period+seconds_remaining -> game-elapsed-seconds conversion, since
seconds_remaining (as stored by fetch_play_by_play.py) resets at the
start of each overtime period rather than continuing to count down
across the whole game.
"""

import math
import os
import pickle

CLUTCH_SECONDS = 300
CLUTCH_MARGIN = 5

PERIOD_SECONDS = 12 * 60
OT_SECONDS = 5 * 60

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# pbp_games holds the same real game twice whenever the nba_api sample
# (fetch_play_by_play.py) and ESPN's full season (fetch_pbp_espn.py)
# overlap — different game_ids, same real game. Checked directly: all 420
# nba_api games (2024-25) have an ESPN twin with the identical final score.
# 418 match on game_date + home_team + away_team; the other 2 are real
# neutral-site games (2024-12-14 NBA Cup semifinal MIL-ATL in Las Vegas,
# 2025-01-25 IND-SAS in Paris) where nba_api's copy has home_team NULL, so
# the match is date + whichever teams are known (a team plays at most once
# per date). The ESPN copy is kept (full-season source, correct home team);
# an nba_api game with no ESPN twin would still be kept. Filter on a
# pbp_games alias `g`: "... FROM pbp_games g WHERE " + PBP_DEDUP_WHERE.
PBP_DEDUP_WHERE = """NOT (
    g.source = 'nba_api'
    AND (g.home_team IS NOT NULL OR g.away_team IS NOT NULL)
    AND EXISTS (
        SELECT 1 FROM pbp_games twin
        WHERE twin.source = 'espn'
          AND twin.game_date = g.game_date
          AND COALESCE(g.home_team, twin.home_team) IN (twin.home_team, twin.away_team)
          AND COALESCE(g.away_team, twin.away_team) IN (twin.home_team, twin.away_team)
    )
)"""


def load_model():
    with open(os.path.join(_SCRIPTS_DIR, "wpa_model.pkl"), "rb") as f:
        model = pickle.load(f)
    with open(os.path.join(_SCRIPTS_DIR, "wpa_scaler.pkl"), "rb") as f:
        scaler = pickle.load(f)
    return model, scaler


def win_prob(model, scaler, seconds_remaining, margin):
    margin_per_sqrt = margin / math.sqrt(max(seconds_remaining, 0) + 1)
    X = scaler.transform([[seconds_remaining, margin, margin_per_sqrt]])
    return float(model.predict_proba(X)[0, 1])


def seconds_elapsed(period: int, seconds_remaining: float) -> float:
    """Real seconds elapsed since tip-off, reconstructed from period +
    the stored seconds_remaining (which — per fetch_play_by_play.py's
    seconds_remaining_in_game() — restarts each OT period rather than
    continuing a single running countdown). Regulation's formula is
    linear and invertible; OT stores the raw period clock directly."""
    if period <= 4:
        clock_in_period = seconds_remaining - (4 - period) * PERIOD_SECONDS
        return (period - 1) * PERIOD_SECONDS + (PERIOD_SECONDS - clock_in_period)
    clock_in_period = seconds_remaining
    return 4 * PERIOD_SECONDS + (period - 5) * OT_SECONDS + (OT_SECONDS - clock_in_period)
