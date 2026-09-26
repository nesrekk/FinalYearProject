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
            { id: 'shotcharts', label: 'Shot Charts', icon: 'adjust' },
            { id: 'draft', label: 'Draft Value Guide', icon: 'school' },
            { id: 'rookies', label: 'Rookie Class Tracker', icon: 'eco' },
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
            { id: 'analytics', label: 'Prospects', icon: 'auto_awesome', hash: 'prospects' },
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
];

export function groupForPage(pageId) {
    const g = NAV_GROUPS.find((group) => group.id === pageId || group.items.some((item) => item.id === pageId));
    return g ? g.id : NAV_GROUPS[0].id;
}
