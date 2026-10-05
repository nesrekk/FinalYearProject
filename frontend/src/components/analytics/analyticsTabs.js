// The Analytics page's tools, in their four groups (?page=analytics#<id>). Kept in its own small module so
// the shell (SaveViewButton's titles) can name a tool without loading the Analytics page (round 8, R8-058).
export const TAB_GROUPS = [
    {
        name: 'Models',
        tabs: [
            { id: 'mvp', label: 'Awards Race', icon: 'emoji_events' },
            { id: 'impact', label: 'Impact Rankings', icon: 'bolt' },
            { id: 'validation', label: 'Model Validation', icon: 'science' },
            { id: 'ledger', label: 'Prediction Ledger', icon: 'timeline' },
        ],
    },
    {
        name: 'Player Analysis',
        tabs: [
            { id: 'similarity', label: 'Season Similarity', icon: 'bar_chart' },
            { id: 'archetypes', label: 'Player Archetypes', icon: 'biotech' },
            { id: 'radar', label: 'Radar Compare', icon: 'radar' },
            { id: 'trends', label: 'Trend Analysis', icon: 'trending_up' },
            { id: 'trajectory', label: 'Career Trajectory', icon: 'timeline' },
            { id: 'helio', label: 'Heliocentricity', icon: 'wb_sunny' },
            { id: 'wpa', label: 'Clutch WPA', icon: 'timer' },
            { id: 'matchups', label: 'Matchup Finder', icon: 'swords' },
            { id: 'garbage', label: 'Garbage-Time Deflator', icon: 'delete_sweep' },
            { id: 'dad', label: 'DAD Index', icon: 'shield' },
            { id: 'rim', label: 'Rim Deterrence', icon: 'block' },
        ],
    },
    {
        name: 'Teams & Markets',
        tabs: [
            { id: 'vegas', label: 'Vegas Scanner', icon: 'currency_exchange' },
            { id: 'playoffs', label: 'Playoff Forecaster', icon: 'military_tech' },
            { id: 'lineups', label: 'Lineup Chemistry', icon: 'diversity_3' },
            { id: 'pairs', label: 'Pair Chemistry', icon: 'grid_on' },
            { id: 'onoff', label: 'On/Off', icon: 'swap_horiz' },
            { id: 'luck', label: 'Luck & Schedule', icon: 'casino' },
            { id: 'spacing', label: 'Spacing Lab', icon: 'open_with' },
            { id: 'contracts', label: 'Contract Value', icon: 'payments' },
            { id: 'replay', label: 'Game Replay', icon: 'movie' },
            { id: 'withwithout', label: 'With/Without a Star', icon: 'person_off' },
            { id: 'fatigue', label: 'Schedule Fatigue', icon: 'flight' },
            { id: 'referees', label: 'Referee Tendencies', icon: 'sports_score' },
        ],
    },
    {
        name: 'College & Draft',
        tabs: [
            { id: 'prospects', label: 'Draft Prospects', icon: 'school' },
            { id: 'pipeline', label: 'College → NBA', icon: 'route' },
            { id: 'madness', label: 'March Madness', icon: 'account_tree' },
        ],
    },
];

export const ANALYTICS_TABS = TAB_GROUPS.flatMap((g) => g.tabs);
