// Central metadata for the Analytics bento index — one entry per tool.
// `about` is real methodology copy condensed from that tool's own existing
// <InfoTooltip> content (see each component), never invented here.
export const TOOL_META = {
    similarity: {
        group: 'Models',
        visual: 'scatter',
        tagline: 'Find the closest statistical match to any player-season.',
        about: 'Each player-season is represented as a normalized feature vector (stats like scoring, efficiency, usage, and impact signals). Similarity is computed using cosine similarity, which compares the direction of two vectors (the stat "profile") and returns the closest matches.',
    },
    mvp: {
        group: 'Models',
        visual: 'bar',
        tagline: 'Real-time MVP, DPOY, ROY and All-NBA probabilities.',
        about: 'MVP probabilities come from a logistic regression model trained on historical player-season data (points, TS%, usage%, offensive/defensive rating, net rating, win%, minutes, age), ranking the entire league with no candidate-pool restriction. DPOY is restricted to players averaging 24+ minutes across 40+ games (backtested top-5 accuracy: 80% over 15 seasons); ROY is restricted to each player’s rookie season only (top-5 accuracy: 100% over 14 seasons, top-1: 64%); All-NBA predicts the 15-player pool trained on 240 real historical selections (2009-10 to 2024-25), with team reconstructed by simple rank cutoff (leave-one-season-out backtest: 78% precision@15, ROC-AUC 0.98; real held-out 2024-25 season: 10/15 correct).',
    },
    impact: {
        group: 'Models',
        visual: 'bar',
        tagline: 'A single blended score for ranking player value.',
        about: 'Impact scores are computed from a weighted blend of player stats (Raw vs. Star variants), normalized so seasons are comparable. BPM/VORP is an independent reproduction of the published Box Plus/Minus 2.0 methodology, verified sound on relative ranking against real 2024-25 results but running somewhat hotter than basketball-reference.com’s own absolute scale — treat it as "who’s better than whom, roughly by how much."',
    },
    validation: {
        group: 'Models',
        visual: 'scatter',
        tagline: 'How accurate are these models, really?',
        about: 'For each historical season, every model is retrained on the other seasons only and asked to predict the one it has never seen (leave-one-season-out backtesting) — more honest than re-scoring a model on data it already trained on. Includes ROC curves, per-model accuracy comparisons (Top-1/3/5, MRR, ROC-AUC), feature importance, and SHAP-based explanations for individual predictions.',
    },
    ledger: {
        group: 'Models',
        visual: 'line',
        tagline: 'What the live model actually said, checked later.',
        about: 'Every real logged snapshot records the live model’s actual predicted probability for every real candidate at a real point in time. Once a season finishes and a real winner is recorded, each logged prediction is graded with a real Brier score — a growing record of what the live model actually said, checked against what actually happened.',
    },
    archetypes: {
        group: 'Player Analysis',
        visual: 'scatter',
        tagline: 'Statistical player styles, discovered not assigned.',
        about: 'Unlike the award models, this has no "correct answer" fed in advance — K-Means clustering groups players by 11 style stats (scoring, playmaking, rebounding, defense, efficiency) into statistically similar groups, with only the archetype names using human basketball knowledge. League Evolution aggregates real historical archetype shares and league-average trends, no modeling or projection involved.',
    },
    radar: {
        group: 'Player Analysis',
        visual: 'radar',
        tagline: 'Percentile-rank profile, any two players overlaid.',
        about: 'Each axis is the player’s percentile rank (0–100) on that stat among all qualified players that season, not the raw number, so different stats are directly comparable on one scale. Shot Proficiency and Spacing are CraftedNBA-style metrics with disclosed formulas.',
    },
    trends: {
        group: 'Player Analysis',
        visual: 'line',
        tagline: 'A player or team’s real numbers, season over season.',
        about: 'Player trends use every season on record for that player, unfiltered, so the trajectory is honest rather than cherry-picked. Team trends use minutes-weighted roster aggregation for the Four Factors, since the database has no official team-level game log.',
    },
    trajectory: {
        group: 'Player Analysis',
        visual: 'line',
        tagline: 'What similar careers did next, at this age.',
        about: 'Finds a player’s closest real statistical matches at the same age, then plots what those real comps actually did in their own following seasons, weighted by similarity. The shaded band is the real range between the best and worst outcome among the comps, not a statistical confidence interval.',
    },
    helio: {
        group: 'Player Analysis',
        visual: 'radial',
        tagline: 'How much of the offense runs through one player.',
        about: 'A simple, fully disclosed average of four real percentile ranks: time-of-possession share, touches per game, usage%, and assist% — equal weights, no fitted model. It measures how much a team’s offense runs through one player; it does not simulate what happens if that player sits out.',
    },
    wpa: {
        group: 'Player Analysis',
        visual: 'line',
        tagline: 'Who actually swings winnable games late.',
        about: 'A real logistic regression win-probability model trained on real play-by-play (running score, game clock, final winner). WPA per play is the model’s output after the play minus its output before the play, credited to whoever made it. Clutch time uses the NBA’s own definition (final 5 minutes, score within 5 points).',
    },
    matchups: {
        group: 'Player Analysis',
        visual: 'radar',
        tagline: 'How a defender actually fares against a specific scorer.',
        about: 'Real player-vs-player matchup data from the NBA’s own player-tracking cameras — real partial possessions and the real FG% the offensive player shot in those specific matchups. Pairs below 20 partial possessions are greyed out and marked small sample. Full league-wide coverage starts at the 2017-18 season.',
    },
    garbage: {
        group: 'Player Analysis',
        visual: 'line',
        tagline: 'How much of a player’s scoring came when the game was already decided.',
        about: 'Every real play in ESPN’s full-season play-by-play is given a Leverage Index: the expected win-probability swing of the next play at that moment (time left × score margin), from this project’s own win-probability model, scaled so the average play is 1.0. Plays are bucketed as garbage time, low, medium or high leverage, and each player’s real points, shots, rebounds, assists and turnovers are split by bucket. Filtered PPG drops garbage-time and low-leverage points. Rebuilt per-game scoring is checked against the official per-game line every season.',
    },
    dad: {
        group: 'Player Analysis',
        visual: 'scatter',
        tagline: 'How hard each defender’s real assignments actually were.',
        about: 'DAD Index (OBPM-weighted): for every defender, the share of their real tracked partial possessions spent on each offensive player, times that player’s OBPM (this project’s own Box Plus/Minus reproduction; players under 500 minutes count as replacement level, −2.0). Z-scored per season, and optionally within position. Plotted against the NBA’s own defended-FG% differential (defended FG% minus the shooters’ normal FG%). Descriptive only — no help defense, rebounding or scheme.',
    },
    vegas: {
        group: 'Teams & Markets',
        visual: 'line',
        tagline: 'Real championship odds vs. real win percentage.',
        about: 'market_probability is real, live championship-odds data from multiple real sportsbooks, with each book’s margin removed independently via Shin’s method before averaging. proxy_probability is each team’s real win percentage normalized to sum to 1 — a naive stand-in for "who’s actually good," not a trained championship model. This is a real-data comparison, not a betting recommendation.',
    },
    playoffs: {
        group: 'Teams & Markets',
        visual: 'bracket',
        tagline: 'Regular-season form next to real playoff performance.',
        about: 'Regular-season stats come from the project’s database; playoff stats are live-fetched from the NBA’s own advanced stats for the same season. This shows what actually happened — no regression and no predicted "playoff tax." Playoff sample sizes are often under 20 games, so a small-sample warning shows when relevant.',
    },
    lineups: {
        group: 'Teams & Markets',
        visual: 'network',
        tagline: 'Real 5-man lineups that have actually shared the floor.',
        about: 'Shows real 5-man lineup combinations that have actually shared the floor this season, fetched live from the NBA’s own real lineup data — real minutes, real possessions, real net rating. A minimum shared-minutes cutoff filters out tiny, noisy samples, and that cutoff is shown rather than hidden.',
    },
    replay: {
        group: 'Teams & Markets',
        visual: 'line',
        tagline: 'Any real game, replayed through the win-probability model.',
        about: 'Replays every real play-by-play event from a real game through the same real trained win-probability model used by the Clutch WPA leaderboard. The dashed "what if" line is a clearly-labeled counterfactual: clicking a missed shot shows how win probability would have looked had it gone in.',
    },
    withwithout: {
        group: 'Teams & Markets',
        visual: 'bar',
        tagline: 'Real team record, split by whether a player played.',
        about: 'Shows real team record and real point differential split by whether a player actually played in each real game, live-fetched from the NBA’s own per-game data. This is an association, not causation — other lineup changes in those same games also affect the result. Real sample sizes for both splits are always shown.',
    },
    fatigue: {
        group: 'Teams & Markets',
        visual: 'line',
        tagline: 'Rest, back-to-backs, and travel miles, quantified.',
        about: 'Real rest days, real back-to-backs, and real travel miles (haversine distance between each consecutive game’s arena) for every team game. The rest-vs-win% chart and schedule-difficulty ranking are real historical aggregation, nothing modeled or predicted; the data is a snapshot from whenever it was last computed, not a live feed.',
    },
    referees: {
        group: 'Teams & Markets',
        visual: 'bar',
        tagline: 'Real foul and free-throw rates by official, vs. league average.',
        about: 'For each real NBA official, shows real total fouls called and real free throws attempted in games they worked, compared against the real league average with a 95% confidence interval on the difference. This is a descriptive comparison of real totals — it cannot account for which teams’ games an official was assigned to, and is not a claim about intent or bias. Officials below 25 games worked are flagged as a small sample.',
    },
    prospects: {
        group: 'Prospects',
        visual: 'scatter',
        tagline: 'Real college production, matched to real NBA rookie outcomes.',
        about: 'Uses real D1 college season stats (~105,000 player-seasons, 2014–2025), era-normalized the same way the project normalizes NBA seasons elsewhere. Comps are real players whose college season can be matched to a real NBA rookie season (~55% of NBA rookies since 2015 — international and G-League/draft-and-stash players are a disclosed gap). The projected outcome is a similarity-weighted average of comps’ real rookie numbers, never a trained/predicted model.',
    },
};
