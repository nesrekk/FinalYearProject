"""
impact_api.py
==============
FastAPI backend for NBA Impact Score queries — the app entrypoint only.

The ~5,500 lines of shared setup (DB pool, loaded models, fetch_*/helper
functions) live in impact_core.py, and every route lives in its own file
under routers/, one per feature (A6: split out of what used to be one
giant file). This file just builds the FastAPI app, wires CORS, and
mounts every router — no route bodies here, so route paths are
guaranteed identical to before the split (each was moved verbatim).

Usage:
    uvicorn impact_api:app --port 8002 --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import (
    aging,
    blurred_player,
    clutch_wpa,
    college,
    contract_value,
    dad_index,
    draft_prospects,
    draft_value,
    era,
    explore,
    game_log,
    games_misc,
    greats,
    garbage_time,
    hot_streaks,
    guess_the_game,
    guess_the_player,
    hall_of_fame,
    heliocentricity,
    learn,
    leaderboard,
    higher_lower,
    impact_rankings,
    leaders,
    length_study,
    lineup_chemistry,
    luck_schedule,
    matchup_finder,
    media,
    meta,
    news,
    on_off,
    pair_chemistry,
    pair_synergy,
    player_comparison,
    player_impact,
    player_profile,
    players_search_profile,
    projections,
    players_table,
    playoff_forecaster,
    playtype_hustle,
    radar,
    referee_tendencies,
    role_finder,
    root,
    schedule_fatigue,
    scouting_report,
    shot_charts,
    shot_making,
    situational_splits,
    spacing_lab,
    team_comparison,
    team_profile,
    trade_analyzer,
    trade_impact,
    trend_analysis,
    trivia,
    vegas_scanner,
    with_without_star,
    wp_replay,
)

app = FastAPI(
    title="NBA Impact Score API",
    description="Query raw and star impact scores for NBA player-seasons.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for _router_module in (
    root, playoff_forecaster, draft_prospects, length_study, heliocentricity,
    clutch_wpa, wp_replay, guess_the_game, lineup_chemistry, pair_synergy,
    with_without_star, schedule_fatigue, playtype_hustle, matchup_finder,
    referee_tendencies, shot_charts, radar, player_comparison, guess_the_player,
    higher_lower, blurred_player, trivia, trend_analysis, trade_analyzer,
    players_table, impact_rankings, draft_value, player_impact, meta, media,
    players_search_profile, leaders, games_misc, news, vegas_scanner,
    team_comparison, hall_of_fame, garbage_time, dad_index, scouting_report,
    spacing_lab, contract_value, learn, college, greats, leaderboard, explore, era,
    pair_chemistry,
    role_finder,
    trade_impact,
    player_profile,
    shot_making,
    game_log,
    aging,
    on_off,
    hot_streaks,
    situational_splits,
    luck_schedule,
    projections,
    team_profile,
):
    app.include_router(_router_module.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("impact_api:app", host="0.0.0.0", port=8002, reload=True)
