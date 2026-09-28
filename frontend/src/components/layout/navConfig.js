export const NAV_GROUPS = [
    {
        id: 'dashboard',
        label: 'Today',
        icon: 'today',
        items: [
            { id: 'dashboard', label: 'Dashboard', icon: 'space_dashboard' },
            { id: 'scores', label: 'Live Scores', icon: 'sports_basketball' },
            { id: 'news', label: 'News', icon: 'newspaper' },
            { id: 'standings', label: 'Standings', icon: 'emoji_events' },
        ],
    },
    {
        id: 'players',
        label: 'Players',
        icon: 'person',
        items: [
            { id: 'players', label: 'Player Stats', icon: 'person' },
            { id: 'compare', label: 'Player Comparison', icon: 'compare_arrows' },
            { id: 'leaders', label: 'Stat Leaders', icon: 'leaderboard' },
            { id: 'builder', label: 'Leaderboard Builder', icon: 'tune' },
            { id: 'regression', label: 'Regression Explorer', icon: 'scatter_plot' },
            { id: 'breakouts', label: 'Breakout Detector', icon: 'trending_up' },
            { id: 'stability', label: 'Stat Stability', icon: 'equalizer' },
            { id: 'rapm', label: 'RAPM', icon: 'balance' },
            { id: 'rolefinder', label: 'Role Player Finder', icon: 'person_search' },
            { id: 'era', label: 'Era Translator', icon: 'history' },
            { id: 'aging', label: 'Aging Curves', icon: 'timeline' },
            { id: 'projections', label: 'Projections', icon: 'update' },
            { id: 'statline', label: 'Stat Line Finder', icon: 'manage_search' },
            { id: 'gamefinder', label: 'Game Finder', icon: 'event_note' },
            { id: 'hotstreaks', label: 'Hot Streak Checker', icon: 'local_fire_department' },
            { id: 'splits', label: 'Situational Splits', icon: 'call_split' },
            { id: 'shotcharts', label: 'Shot Charts', icon: 'adjust' },
            { id: 'draft', label: 'Draft Value Guide', icon: 'school' },
            { id: 'rookies', label: 'Rookie Class Tracker', icon: 'eco' },
            { id: 'greats', label: 'Greats of the Game', icon: 'military_tech' },
            { id: 'hof', label: 'Hall of Fame', icon: 'stars' },
        ],
    },
    {
        id: 'teams',
        label: 'Teams',
        icon: 'swords',
        items: [
            { id: 'teams', label: 'Team Comparison', icon: 'swords' },
            { id: 'trade', label: 'Trade Analyzer', icon: 'swap_horiz' },
            { id: 'tradeimpact', label: 'Trade Impact', icon: 'account_balance' },
        ],
    },
    {
        id: 'analytics',
        label: 'Analytics',
        icon: 'insights',
        items: [
            { id: 'analytics', label: 'Models', icon: 'model_training', hash: 'mvp' },
            { id: 'analytics', label: 'Player Analysis', icon: 'query_stats', hash: 'similarity' },
            { id: 'analytics', label: 'Teams & Markets', icon: 'storefront', hash: 'vegas' },
            { id: 'analytics', label: 'College & Draft', icon: 'school', hash: 'prospects' },
            { id: 'methodology', label: 'Methodology', icon: 'fact_check' },
            { id: 'coverage', label: 'Data Coverage', icon: 'table_chart' },
        ],
    },
    {
        id: 'games',
        label: 'Games',
        icon: 'stadium',
        items: [],
    },
    {
        id: 'learn',
        label: 'Learn',
        icon: 'menu_book',
        items: [],
    },
    {
        id: 'watchlist',
        label: 'Watchlist',
        icon: 'star',
        items: [],
    },
    {
        id: 'saved',
        label: 'Saved',
        icon: 'bookmark',
        items: [],
    },
];

export function groupForPage(pageId) {
    if (pageId === 'player') return 'players'; // a player's profile, opened from any name
    const g = NAV_GROUPS.find((group) => group.id === pageId || group.items.some((item) => item.id === pageId));
    return g ? g.id : NAV_GROUPS[0].id;
}
