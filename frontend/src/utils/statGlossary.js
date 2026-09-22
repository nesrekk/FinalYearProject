/**
 * Plain-English + formula explanations for every stat/column shown in the
 * Player Stats table. Traditional/box-score fields and the official NBA
 * advanced-stat columns (ts_pct, efg_pct, usg_pct, off_rating, def_rating,
 * net_rating, ast_pct, reb_pct, tov_pct) are pulled directly from nba_api's
 * Advanced measure type — the formulas below describe the NBA's own public
 * methodology, not something this project computes itself.
 *
 * BPM/OBPM/DBPM/VORP are the exception: this project reproduces the public
 * Box Plus/Minus 2.0 formula independently (Basketball-Reference blocks
 * automated access to its own page), so those entries say so explicitly.
 */

export const STAT_GLOSSARY = {
    age: {
        title: 'Age',
        body: "Player's age for that season, as reported by the NBA's stats API.",
    },
    gp: {
        title: 'Games Played',
        body: 'Number of games the player appeared in that season.',
    },
    min: {
        title: 'Minutes',
        body: 'Minutes played per game, season average.',
    },
    pts: {
        title: 'Points',
        body: 'Points scored per game, season average.',
    },
    reb: {
        title: 'Rebounds',
        body: 'Total rebounds (offensive + defensive) per game, season average.',
    },
    oreb: {
        title: 'Offensive Rebounds',
        body: "Rebounds of the player's own team's missed shots, per game, season average.",
    },
    dreb: {
        title: 'Defensive Rebounds',
        body: "Rebounds of the opponent's missed shots, per game, season average.",
    },
    fg3m: {
        title: '3-Pointers Made',
        body: 'Made 3-point field goals per game, season average.',
    },
    ast: {
        title: 'Assists',
        body: 'Assists per game, season average.',
    },
    stl: {
        title: 'Steals',
        body: 'Steals per game, season average.',
    },
    blk: {
        title: 'Blocks',
        body: 'Blocked shots per game, season average.',
    },
    tov: {
        title: 'Turnovers',
        body: 'Turnovers per game, season average.',
    },
    fg_pct: {
        title: 'Field Goal %',
        formula: 'FGM ÷ FGA',
        body: 'Percentage of all field-goal attempts (2s and 3s combined) that were made.',
    },
    fg3_pct: {
        title: '3-Point %',
        formula: '3PM ÷ 3PA',
        body: 'Percentage of 3-point attempts that were made.',
    },
    ft_pct: {
        title: 'Free Throw %',
        formula: 'FTM ÷ FTA',
        body: 'Percentage of free-throw attempts that were made.',
    },
    plus_minus: {
        title: 'Plus/Minus',
        body: 'Raw net point differential accumulated while the player was on the court. Not adjusted for teammates, opponent quality, or pace.',
    },
    ts_pct: {
        title: 'True Shooting %',
        formula: 'PTS ÷ (2 × (FGA + 0.44 × FTA))',
        body: "A scoring-efficiency stat that (unlike FG%) properly credits the extra value of 3-pointers and factors in free-throw efficiency. The NBA's official advanced-stat formula.",
    },
    efg_pct: {
        title: 'Effective FG %',
        formula: '(FGM + 0.5 × 3PM) ÷ FGA',
        body: 'Adjusts regular field-goal percentage to reflect that a made 3-pointer is worth 50% more than a made 2-pointer.',
    },
    usg_pct: {
        title: 'Usage %',
        formula: '≈ 100 × (FGA + 0.44 × FTA + TOV) × (Team MIN / 5) ÷ (MIN × (Team FGA + 0.44 × Team FTA + Team TOV))',
        body: "Estimated percentage of a team's possessions a player \"used\" — via a shot attempt, a trip to the free-throw line, or a turnover — while they were on the floor.",
    },
    off_rating: {
        title: 'Offensive Rating',
        body: "Individual points produced per 100 possessions, using Dean Oliver's play-by-play attribution methodology (the NBA's official individual ORtg).",
    },
    def_rating: {
        title: 'Defensive Rating',
        body: "Estimated points allowed per 100 possessions while the player was on the floor, blending individual defensive stats with team and opponent context (the NBA's official individual DRtg).",
    },
    net_rating: {
        title: 'Net Rating',
        formula: 'Off Rating − Def Rating',
        body: 'Point differential per 100 possessions while the player is on the court.',
    },
    ast_pct: {
        title: 'Assist %',
        body: 'Estimated percentage of teammate field goals a player assisted while they were on the floor.',
    },
    reb_pct: {
        title: 'Rebound %',
        body: 'Estimated percentage of available rebounds (offensive + defensive) a player grabbed while they were on the floor.',
    },
    tov_pct: {
        title: 'Turnover %',
        formula: 'TOV ÷ (FGA + 0.44 × FTA + TOV)',
        body: 'Turnovers per 100 plays used — how often a possession the player used ended in a turnover.',
    },
    bpm: {
        title: 'Box Plus/Minus',
        body: "Estimates a player's total contribution in points per 100 possessions above a league-average player, derived entirely from box-score stats. This project reproduces the public BPM 2.0 formula independently (not Basketball-Reference's own code, which blocks automated access) — rankings are verified sound against real results, but the absolute scale runs somewhat hotter than basketball-reference.com's own numbers.",
    },
    obpm: {
        title: 'Offensive BPM',
        body: 'The offensive half of Box Plus/Minus — points per 100 possessions contributed above league-average on offense only. Same independent reproduction as BPM.',
    },
    dbpm: {
        title: 'Defensive BPM',
        body: 'The defensive half of Box Plus/Minus — points per 100 possessions contributed above league-average on defense only. Same independent reproduction as BPM.',
    },
    impact_score: {
        title: 'Impact Score (Raw)',
        formula: '(TS% × 2.0) + (Net Rtg × 0.5) + (USG% × 0.3) + (AST% × 0.3) + (REB% × 0.2) + (Win% × 1.5) − (DRtg × 0.2)',
        body: "This project's own composite rating — a weighted blend of efficiency, on-court impact, usage, and team success, normalized against the season's mean so different seasons are comparable. Not an official/public stat; treat it as a rough single-number stand-in for \"how good was this season,\" not a substitute for the stat line.",
    },
    vorp: {
        title: 'Value Over Replacement Player',
        formula: 'VORP = (BPM − (−2.0)) × (% of minutes played) × (team games / 82)',
        body: 'Converts BPM into a cumulative, playing-time-adjusted stat: total value contributed over a full season versus a hypothetical "replacement level" player (defined as a fixed −2.0 BPM). Higher minutes and a higher BPM both raise it.',
    },
};
