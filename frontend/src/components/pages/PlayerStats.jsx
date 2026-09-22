import React, { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { fetchPlayersTable } from '../../services/api';
import Loader from '../Loader';
import Icon from '../common/Icon';
import TeamLogo from '../common/TeamLogo';
import PlayerHeadshot from '../common/PlayerHeadshot';
import InfoTooltip from '../common/InfoTooltip';
import PlayerDetailModal from '../common/PlayerDetailModal';
import { STAT_GLOSSARY } from '../../utils/statGlossary';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';

const POSITIONS = ['PG', 'SG', 'SF', 'PF', 'C'];

const STAT_TABS = {
    traditional: {
        label: 'Traditional',
        columns: [
            { key: 'pts', label: 'PTS', group: 'traditional' },
            { key: 'reb', label: 'REB', group: 'traditional' },
            { key: 'ast', label: 'AST', group: 'traditional' },
            { key: 'stl', label: 'STL', group: 'traditional' },
            { key: 'blk', label: 'BLK', group: 'traditional' },
            { key: 'tov', label: 'TOV', group: 'traditional' },
            { key: 'fg_pct', label: 'FG%', group: 'traditional', digits: 3 },
            { key: 'fg3_pct', label: '3P%', group: 'traditional', digits: 3 },
            { key: 'ft_pct', label: 'FT%', group: 'traditional', digits: 3 },
            { key: 'plus_minus', label: '+/-', group: 'traditional', signed: true },
        ],
    },
    advanced: {
        label: 'Advanced',
        columns: [
            { key: 'ts_pct', label: 'TS%', group: 'advanced', digits: 3 },
            { key: 'efg_pct', label: 'eFG%', group: 'advanced', digits: 3 },
            { key: 'usg_pct', label: 'USG%', group: 'advanced', digits: 3 },
            { key: 'off_rating', label: 'ORtg', group: 'advanced' },
            { key: 'def_rating', label: 'DRtg', group: 'advanced' },
            { key: 'net_rating', label: 'Net Rtg', group: 'advanced', signed: true },
            { key: 'ast_pct', label: 'AST%', group: 'advanced', digits: 3 },
            { key: 'reb_pct', label: 'REB%', group: 'advanced', digits: 3 },
            { key: 'tov_pct', label: 'TOV%', group: 'advanced', digits: 3 },
        ],
    },
    plus_minus: {
        label: 'Plus-Minus',
        columns: [
            { key: 'bpm', label: 'BPM', group: 'plus_minus', signed: true },
            { key: 'obpm', label: 'OBPM', group: 'plus_minus', signed: true },
            { key: 'dbpm', label: 'DBPM', group: 'plus_minus', signed: true },
            { key: 'vorp', label: 'VORP', group: 'plus_minus', signed: true },
        ],
    },
};

const TOP_LEVEL_LABELS = { age: 'AGE', gp: 'GP', min: 'MIN' };

function fmt(v, digits = 1, signed = false) {
    if (v == null) return '—';
    const s = Number(v).toFixed(digits);
    return signed && v > 0 ? `+${s}` : s;
}

export default function PlayerStats() {
    const [season, setSeason] = useState(2025);
    const [minMinutes, setMinMinutes] = useState(10);
    const [table, setTable] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const [statTab, setStatTab] = useState('traditional');
    const [search, setSearch] = useState('');
    const [activePositions, setActivePositions] = useState(new Set(POSITIONS));
    const [sortKey, setSortKey] = useState('min');
    const [sortGroup, setSortGroup] = useState(null); // null = top-level (min/age/gp)
    const [sortDir, setSortDir] = useState('desc');
    const [selectedPlayer, setSelectedPlayer] = useState(null);

    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);

    async function load() {
        setLoading(true);
        setError('');
        try {
            const data = await fetchPlayersTable(season, minMinutes);
            setTable(data);
        } catch (e) {
            setTable(null);
            setError(e?.response?.data?.detail || 'Failed to load player table.');
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

    function togglePosition(pos) {
        setActivePositions((prev) => {
            const next = new Set(prev);
            if (next.has(pos)) next.delete(pos); else next.add(pos);
            return next;
        });
    }

    function handleSort(col) {
        const key = col.key, group = col.group;
        if (sortKey === key && sortGroup === group) {
            setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
        } else {
            setSortKey(key);
            setSortGroup(group);
            setSortDir('desc');
        }
    }

    const getVal = (r) => (sortGroup ? r[sortGroup]?.[sortKey] : r[sortKey]);

    const rows = useMemo(() => {
        if (!table) return [];
        let list = table.results.filter((r) => activePositions.has(r.position));
        if (search.trim()) {
            const q = search.trim().toLowerCase();
            list = list.filter((r) => r.player_name.toLowerCase().includes(q));
        }
        list = list.slice().sort((a, b) => {
            const av = getVal(a), bv = getVal(b);
            if (av == null && bv == null) return 0;
            if (av == null) return 1;
            if (bv == null) return -1;
            if (typeof av === 'string') {
                return sortDir === 'desc' ? bv.localeCompare(av) : av.localeCompare(bv);
            }
            return sortDir === 'desc' ? bv - av : av - bv;
        });
        return list;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [table, activePositions, search, sortKey, sortGroup, sortDir]);

    const columns = STAT_TABS[statTab].columns;
    const tableVersion = `${statTab}:${sortGroup}.${sortKey}:${sortDir}:${search}:${[...activePositions].join(',')}:${isAdvanced}`;

    // Hero card: whoever's leading the current sort — real data, not staged.
    // Sorting by name/team isn't a "leader" in any meaningful sense, so the
    // hero card only shows for an actual stat column (including MIN/AGE/GP).
    const sortedCol = sortGroup ? columns.find((c) => c.key === sortKey) : null;
    const showHero = Boolean(sortGroup) || sortKey === 'min' || sortKey === 'age' || sortKey === 'gp';
    const leader = showHero ? rows[0] : null;
    const leaderLabel = sortedCol?.label || TOP_LEVEL_LABELS[sortKey] || sortKey.toUpperCase();
    const leaderValue = leader ? fmt(getVal(leader), sortedCol?.digits ?? 1, sortedCol?.signed) : null;

    function isSortedCell(key, group) {
        return sortKey === key && sortGroup === group;
    }

    return (
        <div className="page page-players fade-in">
            <div className="hb-shell">
                <div className="hb-rail">
                    <div className="hb-rail-title">
                        <Icon name="table_view" />
                        Player Stats
                        <InfoTooltip label="How this works" title="Full league table, not a single lookup">
                            Every player for the selected season, sortable and filterable — click any column
                            header to sort by it. "Position" isn't an official roster field (this project has
                            no position data anywhere in its pipeline) — it's estimated from the BPM model's
                            own position-regression (see Impact Rankings → BPM/VORP), rounded to the nearest of
                            5 buckets. Treat it as "plays like a ~PG," not a roster fact.
                        </InfoTooltip>
                    </div>

                    <div className="hb-rail-group">
                        <span className="hb-rail-label">Season</span>
                        <input
                            type="number"
                            value={season}
                            onChange={(e) => setSeason(Number(e.target.value))}
                            min={2010}
                            max={2026}
                        />
                    </div>
                    <div className="hb-rail-group">
                        <span className="hb-rail-label">Search</span>
                        <input
                            type="text"
                            placeholder="Player…"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                        />
                    </div>
                    <div className="hb-rail-group">
                        <span className="hb-rail-label">Min MPG</span>
                        <input
                            type="number"
                            value={minMinutes}
                            onChange={(e) => setMinMinutes(Number(e.target.value))}
                            min={0}
                            max={40}
                        />
                    </div>
                    <button type="button" className="hb-load-btn" onClick={load} disabled={loading}>
                        {loading ? 'Loading…' : 'Load'}
                    </button>

                    <div className="hb-rail-group">
                        <span className="hb-rail-label">Position</span>
                        <div className="hb-rail-list">
                            {POSITIONS.map((pos) => (
                                <button
                                    key={pos}
                                    type="button"
                                    className={`hb-rail-item ${activePositions.has(pos) ? 'hb-rail-item--active' : ''}`}
                                    onClick={() => togglePosition(pos)}
                                >
                                    {pos}
                                </button>
                            ))}
                        </div>
                    </div>

                    <div className="hb-rail-group">
                        <span className="hb-rail-label">View</span>
                        <div className="hb-rail-list">
                            {Object.entries(STAT_TABS).map(([id, cfg]) => (
                                <button
                                    key={id}
                                    type="button"
                                    className={`hb-rail-item ${statTab === id ? 'hb-rail-item--active' : ''}`}
                                    onClick={() => setStatTab(id)}
                                >
                                    {cfg.label}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>

                <div className="hb-main">
                    {error && <p className="error-message">{error}</p>}
                    {loading && <Loader />}

                    {table && !loading && (
                        <>
                            {leader && (
                                <motion.div
                                    key={`hero-${leader.player_id}-${leaderLabel}-${isAdvanced}`}
                                    className="hb-hero"
                                    initial={isAdvanced ? { opacity: 0, y: -8 } : false}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={preset.spring}
                                >
                                    <span className="hb-row-avatar">
                                        <PlayerHeadshot playerId={leader.player_id} playerName={leader.player_name} size={40} />
                                    </span>
                                    <div className="hb-hero-text">
                                        <div className="hb-hero-label">League leader — {leaderLabel}</div>
                                        <div className="hb-hero-name">{leader.player_name}</div>
                                    </div>
                                    <div className="hb-hero-value">{leaderValue}</div>
                                </motion.div>
                            )}

                            <p className="page-subtitle">
                                Showing {rows.length} of {table.count} players (min ≥ {minMinutes} MPG), sorted by {leaderLabel}.
                            </p>

                            <motion.div
                                key={tableVersion}
                                className="hb-table-wrapper table-wrapper"
                                initial={isAdvanced ? { opacity: 0, y: 6 } : false}
                                animate={{ opacity: 1, y: 0 }}
                                transition={preset.tableTransition}
                            >
                                <table className="data-table">
                                    <thead>
                                        <tr>
                                            <th>#</th>
                                            {[
                                                { key: 'player_name', group: null, label: 'Player' },
                                                { key: 'team_abbreviation', group: null, label: 'Team' },
                                                { key: 'age', group: null, label: 'Age' },
                                                { key: 'gp', group: null, label: 'GP' },
                                                { key: 'min', group: null, label: 'MIN' },
                                                ...columns,
                                            ].map((col) => {
                                                const def = STAT_GLOSSARY[col.key];
                                                return (
                                                    <th
                                                        key={`${col.group}.${col.key}`}
                                                        onClick={() => handleSort(col)}
                                                        className="sortable-th"
                                                    >
                                                        {col.label}
                                                        {isSortedCell(col.key, col.group) && (
                                                            <Icon name={sortDir === 'desc' ? 'arrow_drop_down' : 'arrow_drop_up'} size="1em" />
                                                        )}
                                                        {def && (
                                                            <InfoTooltip label={`What is ${def.title}?`} title={def.title}>
                                                                {def.formula && <><code className="stat-formula">{def.formula}</code><br /></>}
                                                                {def.body}
                                                            </InfoTooltip>
                                                        )}
                                                    </th>
                                                );
                                            })}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {rows.map((r, i) => (
                                            <tr
                                                key={r.player_id}
                                                className="clickable-row"
                                                onClick={() => setSelectedPlayer(r)}
                                            >
                                                <td>{i + 1}</td>
                                                <td>
                                                    <span className="entity-row">
                                                        <motion.span
                                                            layoutId={isAdvanced ? `player-avatar-${r.player_id}` : undefined}
                                                            className="hb-row-avatar"
                                                        >
                                                            <PlayerHeadshot playerId={r.player_id} playerName={r.player_name} size={26} />
                                                        </motion.span>
                                                        <span className="entity-row-text">
                                                            <motion.span
                                                                layoutId={isAdvanced ? `player-name-${r.player_id}` : undefined}
                                                                className="entity-row-name"
                                                            >
                                                                {r.player_name}
                                                            </motion.span>
                                                            <span className="entity-row-sub">{r.position}</span>
                                                        </span>
                                                    </span>
                                                </td>
                                                <td className={isSortedCell('team_abbreviation', null) ? 'hb-cell-accent' : ''}>
                                                    <span className="entity-row">
                                                        <TeamLogo abbreviation={r.team_abbreviation} size={18} />
                                                        {r.team_abbreviation}
                                                    </span>
                                                </td>
                                                <td className={isSortedCell('age', null) ? 'hb-cell-accent' : ''}>{r.age ?? '—'}</td>
                                                <td className={isSortedCell('gp', null) ? 'hb-cell-accent' : ''}>{r.gp ?? '—'}</td>
                                                <td className={isSortedCell('min', null) ? 'hb-cell-accent' : ''}>{fmt(r.min, 1)}</td>
                                                {columns.map((col) => (
                                                    <td key={col.key} className={isSortedCell(col.key, col.group) ? 'hb-cell-accent' : ''}>
                                                        {fmt(r[col.group]?.[col.key], col.digits ?? 1, col.signed)}
                                                    </td>
                                                ))}
                                            </tr>
                                        ))}
                                        {rows.length === 0 && (
                                            <tr><td colSpan={7 + columns.length} className="empty-message">No players match these filters.</td></tr>
                                        )}
                                    </tbody>
                                </table>
                            </motion.div>
                        </>
                    )}
                </div>
            </div>
            <AnimatePresence>
                {selectedPlayer && (
                    <PlayerDetailModal
                        key={selectedPlayer.player_id}
                        player={selectedPlayer}
                        onClose={() => setSelectedPlayer(null)}
                    />
                )}
            </AnimatePresence>
        </div>
    );
}
