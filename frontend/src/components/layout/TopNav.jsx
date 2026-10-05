import React, { useEffect, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import Icon from '../common/Icon';
import ThemeToggle from '../ui/ThemeToggle';
import CommandPalette from './CommandPalette';
import { NAV_GROUPS, groupForPage } from './navConfig';

function NavItem({ group, isActive, onNavigate }) {
    const [open, setOpen] = useState(false);
    const closeTimer = useRef(null);

    const openNow = () => {
        clearTimeout(closeTimer.current);
        setOpen(true);
    };
    const closeSoon = () => {
        clearTimeout(closeTimer.current);
        closeTimer.current = setTimeout(() => setOpen(false), 120);
    };

    const go = (pageId, hash) => {
        setOpen(false);
        // `hash` is an Analytics tab; App puts it in the URL with the page.
        onNavigate(pageId, hash);
    };

    return (
        <div className="nav-item" onMouseEnter={openNow} onMouseLeave={closeSoon}>
            <button
                type="button"
                className={`nav-item-btn${isActive ? ' nav-item-btn--active' : ''}`}
                onClick={() => go(group.id)}
                aria-expanded={open}
            >
                {group.label}
                {isActive && (
                    <motion.span
                        layoutId="nav-underline"
                        className="nav-underline"
                        transition={{ type: 'spring', stiffness: 260, damping: 30 }}
                    />
                )}
            </button>
            <AnimatePresence>
                {open && group.items.length > 0 && (
                    <motion.div
                        className="nav-dropdown"
                        initial={{ opacity: 0, y: -6 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -6 }}
                        transition={{ duration: 0.16 }}
                        onMouseEnter={openNow}
                        onMouseLeave={closeSoon}
                    >
                        {group.items.map((item) => (
                            <button
                                key={item.label}
                                type="button"
                                className="nav-dropdown-item"
                                onClick={() => go(item.id, item.hash)}
                            >
                                <Icon name={item.icon} size="1.1em" />
                                {item.label}
                            </button>
                        ))}
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}

export default function TopNav({ activePage, onNavigate, onGoToLanding }) {
    const [mobileOpen, setMobileOpen] = useState(false);
    const [paletteOpen, setPaletteOpen] = useState(false);
    const activeGroup = groupForPage(activePage);

    useEffect(() => {
        function onKeyDown(e) {
            const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k';
            if (isCmdK) {
                e.preventDefault();
                setPaletteOpen((v) => !v);
            }
        }
        document.addEventListener('keydown', onKeyDown);
        return () => document.removeEventListener('keydown', onKeyDown);
    }, []);

    const navigate = (pageId, hash) => {
        onNavigate(pageId, hash);
        setMobileOpen(false);
    };

    return (
        <>
            <header className="top-nav">
                <div className="top-nav-inner">
                    <button type="button" className="top-nav-logo" onClick={() => navigate('dashboard')}>
                        <Icon name="sports_basketball" size="1.4em" />
                        <span>NBA Hub</span>
                    </button>

                    <nav className="top-nav-center">
                        {NAV_GROUPS.map((group) => (
                            <NavItem
                                key={group.id}
                                group={group}
                                isActive={activeGroup === group.id}
                                onNavigate={navigate}
                            />
                        ))}
                    </nav>

                    <div className="top-nav-right">
                        {onGoToLanding && (
                            <button
                                type="button"
                                className="nav-icon-btn"
                                onClick={onGoToLanding}
                                aria-label="Back to landing page"
                                title="Back to landing page"
                            >
                                <Icon name="home" size="1.1em" />
                            </button>
                        )}
                        <button type="button" className="nav-search-pill" onClick={() => setPaletteOpen(true)} aria-label="Search">
                            <Icon name="search" size="1em" />
                            <span>Search</span>
                            <kbd>⌘K</kbd>
                        </button>
                        <ThemeToggle />
                        <button
                            type="button"
                            className="nav-hamburger"
                            onClick={() => setMobileOpen(true)}
                            aria-label="Open menu"
                        >
                            <Icon name="menu" />
                        </button>
                    </div>
                </div>
            </header>

            <AnimatePresence>
                {mobileOpen && (
                    <motion.div
                        className="mobile-sheet"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.18 }}
                    >
                        <div className="mobile-sheet-header">
                            <span className="top-nav-logo">
                                <Icon name="sports_basketball" size="1.4em" />
                                <span>NBA Hub</span>
                            </span>
                            <button type="button" className="mobile-sheet-close" onClick={() => setMobileOpen(false)} aria-label="Close menu">
                                <Icon name="close" />
                            </button>
                        </div>
                        <div className="mobile-sheet-body">
                            <ThemeToggle className="mobile-sheet-theme-toggle" />
                            {onGoToLanding && (
                                <button
                                    type="button"
                                    className="mobile-sheet-group-title"
                                    onClick={() => { setMobileOpen(false); onGoToLanding(); }}
                                >
                                    <Icon name="home" />
                                    Back to landing page
                                </button>
                            )}
                            {NAV_GROUPS.map((group) => (
                                <div key={group.id} className="mobile-sheet-group">
                                    <button
                                        type="button"
                                        className={`mobile-sheet-group-title${activePage === group.id ? ' mobile-sheet-group-title--active' : ''}`}
                                        onClick={() => navigate(group.id)}
                                    >
                                        <Icon name={group.icon} />
                                        {group.label}
                                    </button>
                                    {group.items.filter((i) => i.id !== group.id).map((item) => (
                                        <button
                                            key={item.label}
                                            type="button"
                                            className={`mobile-sheet-item${activePage === item.id ? ' mobile-sheet-item--active' : ''}`}
                                            onClick={() => navigate(item.id)}
                                        >
                                            {item.label}
                                        </button>
                                    ))}
                                </div>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} onNavigate={navigate} />
        </>
    );
}
