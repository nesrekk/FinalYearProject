import React from 'react';

const pageTitles = {
    dashboard: 'Dashboard',
    scores: 'Live Scores',
    news: 'NBA News',
    standings: 'League Standings',
    teams: 'Team Comparison',
    players: 'Player Stats',
    leaders: 'Stat Leaders',
    shotcharts: 'Shot Charts',
    analytics: 'Analytics & Predictions',
    trade: 'Trade Analyzer',
    draft: 'Draft Value Guide',
    rookies: 'Rookie Class Tracker',
};

const pageDescriptions = {
    dashboard: 'Overview of today\'s NBA action',
    scores: 'Real-time game scores and updates',
    news: 'Latest headlines from around the league',
    standings: 'Conference rankings and records',
    teams: 'Head-to-head team stat comparison',
    players: 'Search and explore player statistics',
    leaders: 'Top 10 players by selected stat',
    shotcharts: 'Court-level shooting visualizations',
    analytics: 'Season similarity, MVP prediction, impact rankings',
    trade: 'Simulate a 1-for-1 trade and see the roster impact',
    draft: 'Career value by draft slot, best-value picks, and draft class browsing',
    rookies: 'This season\'s rookie class, ranked by ROY probability, with historical comps',
};

export default function PageHeader({ activePage }) {
    return (
        <header className="page-header">
            <div className="page-header-text">
                <h1 className="page-title">{pageTitles[activePage] || 'Dashboard'}</h1>
                <p className="page-subtitle">{pageDescriptions[activePage] || ''}</p>
            </div>
            <div className="page-header-search">
                <input
                    type="text"
                    placeholder="Search players, teams…"
                    className="search-input"
                />
            </div>
        </header>
    );
}
