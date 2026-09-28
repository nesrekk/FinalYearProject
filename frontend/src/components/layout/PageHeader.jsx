import React from 'react';
import { NAV_GROUPS, groupForPage } from './navConfig';

const POSTER_PAGES = new Set(['dashboard', 'analytics', 'shotcharts', 'games', 'hof']);
// Pages whose own full-bleed hero replaces the standard header.
const HERO_PAGES = new Set(['greats']);

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
    tradeimpact: 'Trade Impact',
    draft: 'Draft Value Guide',
    rookies: 'Rookie Class Tracker',
    hof: 'Hall of Fame',
    greats: 'Greats of the Game',
    games: 'Games',
    learn: 'Learn the Game',
    methodology: 'Methodology',
    builder: 'Leaderboard Builder',
    regression: 'Regression Explorer',
    breakouts: 'Breakout Detector',
    rolefinder: 'Role Player Finder',
    era: 'Era Translator',
    statline: 'Stat Line Finder',
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
    tradeimpact: 'Wins, starting-five spacing and payroll, before and after a 1-for-1 trade',
    draft: 'Career value by draft slot, best-value picks, and draft class browsing',
    rookies: 'This season\'s rookie class, ranked by ROY probability, with historical comps',
    hof: 'Real all-time career leaders, single-season records, and longevity — 1950 to today',
    games: 'Daily player-guessing puzzles and a career-stats streak game, all built on real data',
    learn: 'Basketball for first-time fans, measured from real league data',
    methodology: 'How every model works, how it was checked, and where it falls short',
    builder: 'Rank any player-season by any stat, 1949-50 to today, with your own filters',
    regression: 'Pick two stats and see how they move together, with honest error bars',
    breakouts: 'The biggest season-over-season jumps and drops, and how much usually sticks',
    rolefinder: '"I need a 3-and-D wing": ranked players for a role, with the weights shown in full',
    era: 'Any player-season restated in another season\'s pace and league',
    statline: 'Type a stat line, get the real player-seasons closest to it',
};

function greeting() {
    const hour = new Date().getHours();
    if (hour < 12) return 'Good morning.';
    if (hour < 18) return 'Good afternoon.';
    return 'Good evening.';
}

export default function PageHeader({ activePage }) {
    if (HERO_PAGES.has(activePage)) return null;
    const group = NAV_GROUPS.find((g) => g.id === groupForPage(activePage));
    const poster = POSTER_PAGES.has(activePage);
    const isDashboard = activePage === 'dashboard' || !pageTitles[activePage];
    const eyebrow = isDashboard
        ? new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
        : group && `NBA Hub · ${group.label}`;
    return (
        <header className={`page-header${poster ? ' page-header--poster' : ''}`}>
            {eyebrow && <span className="page-eyebrow">{eyebrow}</span>}
            <h1 className="page-title">{isDashboard ? greeting() : pageTitles[activePage]}</h1>
            <p className="page-subtitle">{pageDescriptions[activePage] || ''}</p>
        </header>
    );
}
