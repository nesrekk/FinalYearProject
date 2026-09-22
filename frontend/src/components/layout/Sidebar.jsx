import React, { useState } from 'react';
import Icon from '../common/Icon';

const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: 'space_dashboard' },
    { id: 'scores', label: 'Live Scores', icon: 'sports_basketball' },
    { id: 'news', label: 'News', icon: 'newspaper' },
    { id: 'standings', label: 'Standings', icon: 'emoji_events' },
    { id: 'teams', label: 'Teams', icon: 'swords' },
    { id: 'players', label: 'Players', icon: 'person' },
    { id: 'leaders', label: 'Stat Leaders', icon: 'leaderboard' },
    { id: 'shotcharts', label: 'Shot Charts', icon: 'adjust' },
    { id: 'analytics', label: 'Analytics', icon: 'insights' },
    { id: 'trade', label: 'Trade Analyzer', icon: 'swap_horiz' },
    { id: 'draft', label: 'Draft Value Guide', icon: 'school' },
    { id: 'rookies', label: 'Rookie Class Tracker', icon: 'eco' },
];

export default function Sidebar({ activePage, onNavigate }) {
    const [collapsed, setCollapsed] = useState(false);

    return (
        <>
            {/* Mobile toggle */}
            <button
                className="sidebar-toggle"
                onClick={() => setCollapsed((c) => !c)}
                aria-label="Toggle navigation"
            >
                <Icon name={collapsed ? 'menu' : 'close'} />
            </button>

            <aside className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''}`}>
                <div className="sidebar-brand">
                    <span className="sidebar-logo"><Icon name="sports_basketball" /></span>
                    {!collapsed && <span className="sidebar-title">NBA Hub</span>}
                </div>

                <nav className="sidebar-nav">
                    {navItems.map((item) => (
                        <button
                            key={item.id}
                            className={`sidebar-link ${activePage === item.id ? 'sidebar-link--active' : ''}`}
                            onClick={() => {
                                onNavigate(item.id);
                                // Auto-close on mobile
                                if (window.innerWidth < 768) setCollapsed(true);
                            }}
                            title={item.label}
                        >
                            <span className="sidebar-link-icon"><Icon name={item.icon} /></span>
                            {!collapsed && <span className="sidebar-link-label">{item.label}</span>}
                        </button>
                    ))}
                </nav>

                <div className="sidebar-footer">
                    {!collapsed && <p className="sidebar-version">v1.0 — NBA Analytics</p>}
                </div>
            </aside>
        </>
    );
}
