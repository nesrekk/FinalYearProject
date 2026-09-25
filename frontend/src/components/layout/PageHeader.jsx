import React from 'react';

const pageTitles = {
    dashboard: 'Dashboard',
    scores: 'Live Scores',
    news: 'NBA News',
    standings: 'League Standings',
    teams: 'Team Comparison',
    players: 'Player Stats',
    compare: 'Player Comparison',
    leaders: 'Stat Leaders',
    shotcharts: 'Shot Charts',
    analytics: 'Analytics & Predictions',
    trade: 'Trade Analyzer',
    draft: 'Draft Value Guide',
    rookies: 'Rookie Class Tracker',
    hof: 'Hall of Fame',
    games: 'Games',
};

const pageDescriptions = {
    dashboard: 'Overview of today\'s NBA action',
    scores: 'Real-time game scores and updates',
    news: 'Latest headlines from around the league',
    standings: 'Conference rankings and records',
    teams: 'Head-to-head team stat comparison',
    players: 'Search and explore player statistics',
    compare: 'Head-to-head player statistical profiles',
    leaders: 'Top 10 players by selected stat',
    shotcharts: 'Court-level shooting visualizations',
    analytics: 'Season similarity, MVP prediction, impact rankings',
    trade: 'Simulate a 1-for-1 trade and see the roster impact',
    draft: 'Career value by draft slot, best-value picks, and draft class browsing',
    rookies: 'This season\'s rookie class, ranked by ROY probability, with historical comps',
    hof: 'Real all-time career leaders, single-season records, and longevity — 1950 to today',
    games: 'Daily player-guessing puzzles and a career-stats streak game, all built on real data',
};

export default function PageHeader({ activePage }) {
    return (
        <header className="page-header">
            <h1 className="page-title">{pageTitles[activePage] || 'Dashboard'}</h1>
            <p className="page-subtitle">{pageDescriptions[activePage] || ''}</p>
        </header>
    );
}
