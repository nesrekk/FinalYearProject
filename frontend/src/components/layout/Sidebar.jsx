import React, { useState } from 'react';

const navItems = [
    { id: 'dashboard', label: 'Dashboard', icon: '🏠' },
    { id: 'scores', label: 'Live Scores', icon: '🏀' },
    { id: 'news', label: 'News', icon: '📰' },
    { id: 'standings', label: 'Standings', icon: '🏆' },
    { id: 'teams', label: 'Teams', icon: '⚔️' },
    { id: 'players', label: 'Players', icon: '👤' },
    { id: 'leaders', label: 'Stat Leaders', icon: '📈' },
    { id: 'shotcharts', label: 'Shot Charts', icon: '🎯' },
    { id: 'analytics', label: 'Analytics', icon: '📊' },
    { id: 'trade', label: 'Trade Analyzer', icon: '🔄' },
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
                {collapsed ? '☰' : '✕'}
            </button>

            <aside className={`sidebar ${collapsed ? 'sidebar--collapsed' : ''}`}>
                <div className="sidebar-brand">
                    <span className="sidebar-logo">🏀</span>
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
                            <span className="sidebar-link-icon">{item.icon}</span>
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
