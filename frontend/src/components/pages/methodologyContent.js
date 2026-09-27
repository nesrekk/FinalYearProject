// Content for the Methodology page. Every number here was checked against
// the live endpoint or the build script's own output on the date given;
// the award models and the win-probability model are fetched live instead.
// When a model changes, change its entry in the same commit.

export const CHECKED_ON = '2026-09-27';

export const PRINCIPLES = [
    ['Real data only', 'Every number comes from a stored table or a live source; nothing is filled in with placeholders.'],
    ['Held-out checks', 'Predictive models are scored on seasons or games they never trained on.'],
    ['Null results stay in', 'When a model finds nothing (College → NBA Pipeline) or doesn\'t beat a simple baseline (March Madness champions), the page says so.'],
    ['Samples are shown', 'Small samples are flagged or greyed out rather than ranked beside reliable ones.'],
    ['Association is not cause', 'Descriptive studies say what went together, not why.'],
];

// kind: 'model' (predicts), 'index' (a rating built from a formula),
// 'similarity', 'study' (describes what happened).
export const SECTIONS = [
    {
        id: 'predictive',
        title: 'Predictive models',
        blurb: 'Models that output a probability. Each is scored on data it never saw.',
        items: [
            {
                id: 'awards',
                name: 'Award models (MVP, DPOY, ROY, All-NBA)',
                open: { page: 'analytics', hash: 'mvp', label: 'Awards Race' },
                answers: 'Who is on course to win each award this season?',
                method: 'One logistic regression per award on season stats, standardised; random forest and gradient boosting are backtested alongside for comparison. The models are trained with balanced class weights, so their raw outputs run far too high (several players at 99%+ at once). The app shows a calibrated chance instead: for MVP, DPOY and ROY the scores are turned into chances that add up to 100% across the field; for All-NBA (15 selections) they are Platt-scaled to add up to about 15.',
                checked: 'Leave-one-season-out: each past season is predicted by a model trained without it, then compared with the real winner (live table below; Model Validation has the ROC curves). The calibration is fitted on those held-out scores and checked in a nested loop, so no season is scored by a calibration that saw it (live table below).',
                limits: [
                    'The calibration rests on 14 to 16 seasons, so the percentages are approximate; the MVP favourite averaged a 55% chance and won 7 of 15.',
                    'The Prediction Ledger keeps the raw probabilities that were logged before 2026-09-27.',
                    'Voter narrative and the league\'s 65-game eligibility rule are not modelled; DPOY and All-NBA only apply a minutes floor to who counts as a candidate.',
                    'ROY candidates are players in their first NBA season per Basketball-Reference. Until 2026-09-27 the first season in this database was used, which let in 240 players with missing earlier stints (53 in 2025-26); the ROY model was retrained on the corrected pool.',
                ],
                live: 'awards',
            },
            {
                id: 'wp',
                name: 'Win probability (Clutch WPA, Game Replay, Garbage-Time, Guess the Game)',
                open: { page: 'analytics', hash: 'wpa', label: 'Clutch WPA' },
                answers: 'Given the score margin and the time left, how likely is the home team to win?',
                method: 'Logistic regression with isotonic calibration on three inputs (seconds left, home score margin, and margin divided by the square root of time left), trained on play-by-play from 7,232 games (2020-21 to 2025-26). WPA per play = win probability after it minus before it, credited to the player who made it.',
                checked: 'Held out by game, not by play, so a game\'s later plays can\'t leak into training. Brier score, log loss and a reliability curve on the held-out games (live numbers below).',
                limits: [
                    'Possession, timeouts and fouls to give are not inputs, so in a close finish the model can\'t tell who has the ball.',
                    'Covers 2020-21 onward only (the play-by-play on file).',
                    '1.4% of play-by-play events couldn\'t be matched to a player id by name and carry no player credit.',
                    'Clutch-time predictions sit near 50/50, so their scores aren\'t directly comparable with the all-events scores.',
                ],
                live: 'wp',
            },
            {
                id: 'madness',
                name: 'March Madness model',
                open: { page: 'analytics', hash: 'madness', label: 'March Madness' },
                answers: 'Pre-tournament odds for every team in the NCAA bracket.',
                method: 'Opponent- and venue-adjusted scoring margin (ridge least squares on every D1 game before the tournament), logistic regression on the difference between two teams, 20,000 simulations of the real bracket.',
                checked: 'Five input sets compared by leave-one-season-out log loss on 2013-2025 (801 games); adjusted margin alone won (0.557). 2026 was held back entirely and run once: Michigan won; the model had them 3rd (18.9%).',
                limits: [
                    'Better calibrated than seeds game by game, but not better at finding the champion: the champion was its favourite in 2 of 12 backtest seasons, and a 1 seed in 9.',
                    'Torvik\'s end-of-season ratings are never used as inputs: they include the tournament.',
                ],
            },
        ],
    },
    {
        id: 'indexes',
        title: 'Ratings and indexes',
        blurb: 'Formulas that turn data into one number. Each says what it leaves out.',
        items: [
            {
                id: 'bpm',
                name: 'BPM and VORP',
                open: { page: 'analytics', hash: 'impact', label: 'Impact Rankings' },
                answers: 'How much a player adds per 100 possessions (BPM), and in total over a replacement player (VORP).',
                method: 'Basketball-Reference\'s published values, loaded for every season; 2009-10 onward is linked to NBA ids through the project\'s id map (7,273 of 7,279 rows; the 6 unlinked are left empty).',
                checked: 'Games played agree within one game on every linked row, an independent check of the id link.',
                limits: [
                    'Until 2026-09-27 the app used its own BPM reproduction, which ran hot (2025-26 SGA at 22.0 vs the published 11.7). It is kept only as an input to Pair Synergy, whose trained model was fitted on it.',
                    'Box-score based: defence away from the ball is only partly captured.',
                ],
            },
            {
                id: 'dad',
                name: 'DAD Index (Defensive Assignment Difficulty)',
                open: { page: 'analytics', hash: 'dad', label: 'DAD Index' },
                answers: 'How hard were the players each defender actually guarded?',
                method: 'Share of the defender\'s tracked possessions against each opponent × that opponent\'s offensive BPM (Basketball-Reference). Opponents under 500 minutes count as replacement level (−2.0). Z-scored per season, with a within-position version.',
                checked: 'Year-over-year stability r = 0.70 for 243 defenders qualified in both 2024-25 and 2025-26.',
                limits: [
                    'Describes assignments, not a full defensive rating: no help defence, rebounding or scheme.',
                    'Matchup pairs under 5 partial possessions were dropped when the data was fetched.',
                    'Qualified defenders only (1,000+ partial possessions).',
                ],
            },
            {
                id: 'gravity',
                name: 'Gravity Index and Spacing Lab',
                open: { page: 'analytics', hash: 'spacing', label: 'Spacing Lab' },
                answers: 'How much does a shooter pull the defence, and do well-spaced lineups score more?',
                method: 'A proxy, not tracking gravity: 3PA per 100 possessions, catch-and-shoot 3P% and the share of threes taken with a defender within 6 ft, each z-scored within the season (500+ minutes) and summed. Lineup spacing = the five players\' sum.',
                checked: 'Offensive rating of 4,250 lineups (100+ possessions, 2013-14 to 2025-26) regressed on spacing plus the five players\' summed OBPM, season fixed effects, errors clustered by team-season: +0.074 ORtg per spacing point (95% CI 0.014 to 0.134, p = 0.016).',
                limits: [
                    'The effect is small: spacing adds 0.001 to R² once lineup quality is in the model.',
                    'An association across lineups, not a forecast for a specific five.',
                ],
            },
            {
                id: 'contracts',
                name: 'Contract Value',
                open: { page: 'analytics', hash: 'contracts', label: 'Contract Value' },
                answers: 'Is a player worth their salary?',
                method: 'WAR = published VORP × 2.7, rescaled per season so positive WAR adds up to the league\'s real wins above replacement (factor 0.77 to 0.80). Fair value = WAR × that season\'s cost per win + the league minimum.',
                checked: 'Cost per win is built from real salaries, real team records and the real minimum salary for each season: $2.08M per win in 2005-06 up to $6.11M in 2024-25.',
                limits: [
                    'Salary data is third-party (Kaggle uploads built from HoopsHype and Basketball-Reference) and imperfect.',
                    'Covers 2005-06 to 2016-17, 2018-19, 2019-20 and 2024-25 only.',
                    'Only players who played are priced.',
                ],
            },
            {
                id: 'garbage',
                name: 'Garbage-Time Deflator',
                open: { page: 'analytics', hash: 'garbage', label: 'Garbage-Time Deflator' },
                answers: 'How much of a player\'s scoring came when the game was already decided?',
                method: 'Every play gets a leverage index from the win-probability model (average play = 1.0). Garbage time = win probability above 99% either way, or a margin of 18+ in the second half or overtime.',
                checked: 'Buckets are applied in a fixed order and their shares are shown; the "stat-padding" badge is only given at the 90th percentile or above among 40+ game players.',
                limits: [
                    'ESPN play-by-play only, 2020-21 to 2025-26.',
                    'Thresholds are choices, stated on the page, not facts.',
                ],
            },
            {
                id: 'helio',
                name: 'Heliocentricity Index',
                open: { page: 'analytics', hash: 'helio', label: 'Heliocentricity' },
                answers: 'Whose team\'s offence runs through them most?',
                method: 'The average of four percentile ranks within the season: time-of-possession share, touches, usage and assist percentage.',
                checked: 'Descriptive only; the weights are equal on purpose and disclosed.',
                limits: ['Not predictive and not a measure of value: a high score can be good or bad for a team.'],
            },
        ],
    },
    {
        id: 'similarity',
        title: 'Similarity and clustering',
        blurb: 'Grouping and matching players. Every stat is compared within its own season first, so eras compare fairly.',
        items: [
            {
                id: 'similarity',
                name: 'Season Similarity',
                open: { page: 'analytics', hash: 'similarity', label: 'Season Similarity' },
                answers: 'Which player-seasons look most like this one?',
                method: 'Eight numbers per season (points, true shooting, usage, net rating, assist %, rebound % z-scored within the season, plus age and minutes), standardised, compared by cosine similarity.',
                checked: 'The live view was checked against the stored top 10 for all 7,279 seasons: identical scores and lists. A smoke test re-checks one season on every run.',
                limits: [
                    '2009-10 onward only: usage and net rating aren\'t on file before that.',
                    'Cosine compares shape, so a smaller version of the same profile can still score high; the stat line is shown next to each match for that reason.',
                ],
            },
            {
                id: 'roles',
                name: 'Player Roles',
                open: { page: 'analytics', hash: 'archetypes', label: 'Player Archetypes' },
                answers: 'What kind of player is this, judged by what they do with their minutes?',
                method: 'K-Means on rate stats and shot-location shares, z-scored within each season: 5,386 player-seasons 2010-2026 (15+ minutes, 20+ games, 100+ located shots).',
                checked: 'Ten roles is the most whose grouping survives refitting on 80% subsamples (adjusted Rand 0.83; 11 or more drop to 0.52-0.64).',
                limits: ['Separation is low at every K (silhouette 0.10): playing styles are a continuum, and many players sit between two roles.'],
            },
            {
                id: 'offstyle',
                name: 'Offensive Style',
                open: { page: 'analytics', hash: 'archetypes', label: 'Player Archetypes' },
                answers: 'How does a player\'s offence get generated?',
                method: 'K-Means on play-type shares (isolation, pick-and-roll, spot-up, post-up, transition, roll man), era-normalised.',
                checked: 'Silhouette 0.21, disclosed as weaker separation than the stat-based groups.',
                limits: ['Play-type mixes overlap a lot; treat the style as a tendency.'],
            },
            {
                id: 'prospects',
                name: 'Draft Prospect Comp Finder',
                open: { page: 'analytics', hash: 'prospects', label: 'Draft Prospects' },
                answers: 'Which past college players does a prospect resemble, and how did they do in the NBA?',
                method: 'Around 105,000 D1 player-seasons, era-normalised, matched by similarity; optional combine measurements.',
                checked: 'NBA outcomes shown only where a real name match exists.',
                limits: ['About 55% of NBA rookies since 2015 have a US college record; international and G League players never appear.'],
            },
        ],
    },
    {
        id: 'studies',
        title: 'Descriptive studies',
        blurb: 'What happened, measured carefully. None of these claims a cause.',
        items: [
            {
                id: 'pipeline',
                name: 'College → NBA Pipeline',
                open: { page: 'analytics', hash: 'pipeline', label: 'College Pipeline' },
                answers: 'Do players from stronger college teams outperform their draft slot?',
                method: 'Win Shares in the first four NBA seasons compared with a draft-slot expectation fitted on 598 picks (2013-2022).',
                checked: 'Bootstrap intervals on every group; 478 college players from classes old enough to judge.',
                limits: ['A null result: +0.38 Win Shares per 10 points of team margin (95% range −0.30 to +1.04, r = 0.05). Once you know the pick, college team strength barely matters.'],
            },
            {
                id: 'draft',
                name: 'Draft Value',
                open: { page: 'draft', label: 'Draft Value Guide' },
                answers: 'What is a draft slot usually worth?',
                method: 'Basketball-Reference Win Shares in each pick\'s first five NBA seasons, draft classes 1980-2021.',
                checked: 'Slot averages 23.6 / 14.4 / 8.4 / 3.9 / 1.9 Win Shares for picks 1-5 / 6-14 / 15-30 / 31-45 / 46-60; career totals spot-checked against Basketball-Reference.',
                limits: ['Win Shares lean on team results, especially on defence; five seasons misses late bloomers.'],
            },
            {
                id: 'referees',
                name: 'Referee Tendencies',
                open: { page: 'analytics', hash: 'referees', label: 'Referee Tendencies' },
                answers: 'Do games an official works produce more fouls, free throws or pace than usual?',
                method: 'Each official\'s games compared with the league average for the same seasons, with a 95% confidence interval on the difference.',
                checked: 'Officials with under 25 games are flagged as a small sample, not ranked.',
                limits: ['Crew assignment, opponents and era are unmeasured; coverage grows as the resumable fetch is re-run.'],
            },
            {
                id: 'matchups',
                name: 'Matchup Finder',
                open: { page: 'analytics', hash: 'matchups', label: 'Matchup Finder' },
                answers: 'Which defenders hold a scorer down, and who torches whom?',
                method: 'NBA player-vs-player matchup tracking, 2017-18 to 2025-26 (571,608 rows).',
                checked: 'Pairs under 20 partial possessions are greyed out and marked small sample.',
                limits: ['One defended shot is 0% or 100%: small pairs are noise even when shown.'],
            },
            {
                id: 'lineups',
                name: 'Lineup Chemistry and With vs. Without',
                open: { page: 'analytics', hash: 'lineups', label: 'Lineup Chemistry' },
                answers: 'How did real five-man units, and teams with and without a star, actually do?',
                method: 'Real lineup ratings with a stated minutes cutoff; team results split by whether a player played.',
                checked: 'The 2023-24 76ers with and without Embiid (79.5% vs 37.2% wins) reproduce that season\'s known story.',
                limits: ['Other absences and opponents aren\'t controlled for; small lineups are noisy.'],
            },
            {
                id: 'scouting',
                name: 'Scouting Report',
                open: { page: 'compare', label: 'Player Comparison' },
                answers: 'Where is a player significantly better or worse than the league?',
                method: 'Shot-zone FG% and play-type points per possession tested against the rest of the league that season, and high-leverage shooting against the player\'s own, at p < 0.05. Qualified: 1,500+ minutes.',
                checked: 'The card always shows how many splits would clear the bar by chance (about 1 in 20 tested).',
                limits: ['No pick-and-roll coverage or drive-direction data exists here; shot-context splits cover threes only.'],
            },
        ],
    },
];

// Problems found and not yet fixed. Remove an entry in the commit that fixes it.
export const OPEN_ISSUES = [
    {
        title: 'Some models can\'t be retrained right now',
        body: 'stats.nba.com has been unreachable from the build machine since 2026-09-26, so Pair Synergy still uses the old in-house defensive BPM it was trained on.',
    },
];
