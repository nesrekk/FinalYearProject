import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import Icon from '../common/Icon';
import { playerOption, searchPlayers } from '../../utils/playerChoice';
import { openPlayerProfile, openTeamProfile } from '../../utils/useUrlState';
import { TEAM_NAME_TO_ABBR } from '../../utils/teamAssets';
import { NAV_GROUPS } from './navConfig';

const PAGES = NAV_GROUPS.flatMap((group) =>
    group.items.length
        ? group.items.map((item) => ({ kind: 'page', id: item.id, label: item.label, hash: item.hash, icon: item.icon }))
        : [{ kind: 'page', id: group.id, label: group.label, icon: group.icon }]
);

const TEAM_RESULTS = Object.entries(TEAM_NAME_TO_ABBR).map(([name, abbr]) => ({ kind: 'team', name, abbr }));

export default function CommandPalette({ open, onClose, onNavigate }) {
    const [query, setQuery] = useState('');
    const [playerResults, setPlayerResults] = useState([]);
    const [activeIndex, setActiveIndex] = useState(0);
    const inputRef = useRef(null);

    useEffect(() => {
        if (!open) return;
        Promise.resolve().then(() => {
            setQuery('');
            setPlayerResults([]);
            setActiveIndex(0);
            requestAnimationFrame(() => inputRef.current?.focus());
        });
    }, [open]);

    useEffect(() => {
        const q = query.trim();
        if (q.length < 2) {
            Promise.resolve().then(() => setPlayerResults([]));
            return;
        }
        let active = true;
        const timer = setTimeout(() => {
            // With ids and career spans: two players can share a name.
            searchPlayers(q)
                .then((list) => { if (active) setPlayerResults(list.slice(0, 5)); })
                .catch(() => { if (active) setPlayerResults([]); });
        }, 200);
        return () => { active = false; clearTimeout(timer); };
    }, [query]);

    const pageResults = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return PAGES;
        return PAGES.filter((p) => p.label.toLowerCase().includes(q));
    }, [query]);

    const teamResults = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return [];
        return TEAM_RESULTS.filter((t) => t.name.toLowerCase().includes(q) || t.abbr.toLowerCase().includes(q)).slice(0, 5);
    }, [query]);

    const results = useMemo(() => [
        ...pageResults.map((p) => ({ ...p })),
        ...teamResults.map((t) => ({ kind: 'team', label: t.name, abbr: t.abbr })),
        ...playerResults.map((p) => ({ kind: 'player', label: playerOption(p), id: p.id })),
    ], [pageResults, teamResults, playerResults]);

    const select = (result) => {
        if (!result) return;
        if (result.kind === 'page') {
            onNavigate(result.id, result.hash);
        } else if (result.kind === 'team') {
            openTeamProfile(result.abbr);
        } else if (result.kind === 'player') {
            openPlayerProfile(result.id);
        }
        onClose();
    };

    function onKeyDown(e) {
        if (e.key === 'Escape') {
            onClose();
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            setActiveIndex((i) => Math.min(i + 1, results.length - 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActiveIndex((i) => Math.max(i - 1, 0));
        } else if (e.key === 'Enter') {
            e.preventDefault();
            select(results[activeIndex]);
        }
    }

    if (typeof document === 'undefined') return null;

    return createPortal(
        <AnimatePresence>
            {open && (
                <motion.div
                    className="palette-overlay"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.15 }}
                    onClick={onClose}
                >
                    <motion.div
                        className="palette"
                        initial={{ opacity: 0, y: -12, scale: 0.98 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, y: -12, scale: 0.98 }}
                        transition={{ type: 'spring', stiffness: 260, damping: 30 }}
                        onClick={(e) => e.stopPropagation()}
                        onKeyDown={onKeyDown}
                    >
                        <div className="palette-input-row">
                            <Icon name="search" />
                            <input
                                ref={inputRef}
                                type="text"
                                value={query}
                                onChange={(e) => { setQuery(e.target.value); setActiveIndex(0); }}
                                placeholder="Search players, teams, pages, tools…"
                                className="palette-input"
                            />
                            <kbd>Esc</kbd>
                        </div>
                        <div className="palette-results">
                            {results.length === 0 && (
                                <p className="palette-empty">No matches.</p>
                            )}
                            {results.map((r, i) => (
                                <button
                                    type="button"
                                    key={`${r.kind}-${r.label}-${i}`}
                                    className={`palette-result${i === activeIndex ? ' palette-result--active' : ''}`}
                                    onMouseEnter={() => setActiveIndex(i)}
                                    onClick={() => select(r)}
                                >
                                    <Icon name={r.kind === 'player' ? 'person' : r.kind === 'team' ? 'swords' : r.icon || 'chevron_right'} />
                                    <span>{r.label}{r.kind === 'team' ? ` (${r.abbr})` : ''}</span>
                                    <span className="palette-result-kind">{r.kind}</span>
                                </button>
                            ))}
                        </div>
                    </motion.div>
                </motion.div>
            )}
        </AnimatePresence>,
        document.body
    );
}
