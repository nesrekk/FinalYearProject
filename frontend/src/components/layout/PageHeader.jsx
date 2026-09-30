import React from 'react';
import { NAV_GROUPS, groupForPage } from './navConfig';

const POSTER_PAGES = new Set(['dashboard', 'analytics', 'shotcharts', 'games', 'hof']);
// Pages whose own hero replaces the standard header.
const HERO_PAGES = new Set(['greats', 'player', 'team']);

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
    stability: 'Stat Stability',
    rolefinder: 'Role Player Finder',
    era: 'Era Translator',
    aging: 'Aging Curves',
    projections: 'Projections',
    statline: 'Stat Line Finder',
    gamefinder: 'Game Finder',
    plays: 'Play Finder',
    hotstreaks: 'Hot Streak Checker',
    rapm: 'RAPM',
    rotations: 'Rotations',
    assists: 'Assist Network',
    simulator: 'Season Simulator',
    ledger: 'Forecast Ledger',
    bestgames: 'Best Games & Upsets',
    splits: 'Situational Splits',
    watchlist: 'Watchlist',
    coverage: 'Data Coverage',
    saved: 'Saved Analyses',
    report: 'Report Builder',
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
    stability: 'How big a sample each stat needs before it says more about the player than about luck',
    rolefinder: '"I need a 3-and-D wing": ranked players for a role, with the weights shown in full',
    era: 'Any player-season restated in another season\'s pace and league',
    aging: 'How a typical player\'s game changes from one age to the next, and any career against it',
    projections: 'Next season\'s stat line for every current player, from a simple baseline that was backtested on 26 seasons',
    statline: 'Type a stat line, get the real player-seasons closest to it',
    gamefinder: 'Every player-game since 2020-21: filter by any stat line, or find the longest streaks',
    plays: 'Every play since 2020-21: find any shot, assist, block or turnover by player, clock, score and distance, and replay it',
    hotstreaks: 'Who\'s hot or cold over their last few games, and how much of it is likely to last',
    rapm: 'What each player adds per 100 possessions once the other nine on the floor are held constant, with error bars and a real test against BPM',
    rotations: 'Who was on the floor at every minute: season heatmaps, starting and closing fives, and any game as a rotation chart',
    assists: 'Who sets up whom: every assisted basket since 2020-21 as a team network, with each player\'s top targets and feeders',
    simulator: 'Playoff, play-in and seed odds from any date of any season since 2010-11: 10,000 simulated seasons on a pre-game model tested on 19,118 games',
    ledger: 'The 2026-27 predictions, locked with a SHA-256 before opening night: win totals, playoff and title odds and every game, from two models',
    bestgames: 'The most exciting games since 2020-21 and the biggest upsets since 2010-11, scored from win-probability swings and held-out pre-game odds',
    splits: 'Home or away, back-to-back or rested, long trip or short, strong or weak opponent: each player against the average',
    watchlist: 'Your starred players, side by side',
    coverage: 'Every table this app reads: live row counts, season span, source and known gaps',
    saved: 'Every tool view you\'ve saved, exactly as you left it',
    report: 'Collect charts and tables from any page, add your own notes, and print or save the result as a PDF',
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
