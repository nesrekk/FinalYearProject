import React, { useEffect, useMemo, useState } from 'react';
import { fetchArchetypes, fetchSeasonClusters, fetchPlayerClusterHistory, fetchLivePlayerSuggestions } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';

const PALETTE = ['#38bdf8', '#f87171', '#facc15', '#a78bfa', '#34d399', '#fb923c'];

export default function PlayerArchetypesSection() {
    const [archetypes, setArchetypes] = useState([]);
    const [season, setSeason] = useState(2025);
    const [seasonData, setSeasonData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [hoveredArchetype, setHoveredArchetype] = useState(null);

    const [searchInput, setSearchInput] = useState('');
    const [suggestions, setSuggestions] = useState([]);
    const [history, setHistory] = useState(null);
    const [historyError, setHistoryError] = useState('');

    useEffect(() => {
        let active = true;
        fetchArchetypes()
            .then((data) => { if (active) setArchetypes(data.archetypes || []); })
            .catch(() => { if (active) setArchetypes([]); });
        return () => { active = false; };
    }, []);

    useEffect(() => {
        let active = true;
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                const data = await fetchSeasonClusters(season);
                if (active) setSeasonData(data);
            } catch (e) {
                if (!active) return;
                setSeasonData(null);
                setError(e?.response?.data?.detail || 'No cluster results found. Run scripts/cluster_players.py first.');
            } finally {
                if (active) setLoading(false);
            }
        }, 0);
        return () => {
            active = false;
            clearTimeout(timer);
        };
    }, [season]);

    const colorByArchetype = useMemo(() => {
        const map = {};
        archetypes.forEach((a, i) => { map[a.archetype] = PALETTE[i % PALETTE.length]; });
        return map;
    }, [archetypes]);

    // Debounced player search for the career-evolution lookup.
    useEffect(() => {
        const query = searchInput.trim();
        if (query.length < 2 || query.toLowerCase() === (history?.player_name || '').toLowerCase()) {
            setSuggestions([]);
            return;
        }
        let active = true;
        const timer = setTimeout(async () => {
            try {
                const data = await fetchLivePlayerSuggestions(query, 8);
                if (active) setSuggestions(data?.results ?? []);
            } catch {
                if (active) setSuggestions([]);
            }
        }, 200);
        return () => {
            active = false;
            clearTimeout(timer);
        };
    }, [searchInput, history]);

    async function loadHistory(name) {
        setSearchInput(name);
        setSuggestions([]);
        setHistoryError('');
        try {
            const data = await fetchPlayerClusterHistory(name);
            setHistory(data);
        } catch (e) {
            setHistory(null);
            setHistoryError(e?.response?.data?.detail || `No cluster history for "${name}".`);
        }
    }

    const players = useMemo(() => seasonData?.players ?? [], [seasonData]);
    const visiblePlayers = hoveredArchetype ? players.filter((p) => p.archetype === hoveredArchetype) : players;

    const plot = useMemo(() => {
        if (!players.length) return null;
        const xs = players.map((p) => p.pca_x);
        const ys = players.map((p) => p.pca_y);
        const xMin = Math.min(...xs), xMax = Math.max(...xs);
        const yMin = Math.min(...ys), yMax = Math.max(...ys);
        const pad = 30;
        const width = 600, height = 380;
        const scaleX = (x) => pad + ((x - xMin) / (xMax - xMin || 1)) * (width - 2 * pad);
        const scaleY = (y) => (height - pad) - ((y - yMin) / (yMax - yMin || 1)) * (height - 2 * pad);
        return { width, height, scaleX, scaleY };
    }, [players]);

    return (
        <section className="dashboard-card">
            <h2 className="card-title">
                <span className="card-icon"><Icon name="biotech" /></span>
                Player Archetypes
                <InfoTooltip label="How this clustering works" title="Unsupervised K-Means clustering">
                    Unlike MVP/DPOY/ROY (trained to predict a known label) this has no "correct answer" fed
                    in advance. K-Means looks at 11 style stats (scoring, playmaking, rebounding, defense,
                    efficiency) for every player-season and groups players who are statistically similar —
                    the groupings emerge from the data itself, not from box-score categories a human
                    predefined. Only the NAME given to each already-discovered group ("Rim Protector",
                    "Playmaker"...) uses basketball knowledge; the grouping itself is 100% data-driven.
                </InfoTooltip>
            </h2>
            <p className="page-subtitle">
                Every dot below is one player-season, positioned by a 2D projection of their stat profile —
                players who play similarly sit near each other regardless of team or era. Hover a legend
                entry to isolate one archetype; click a dot's player name in the table to see their whole
                career's archetype history.
            </p>

            {archetypes.length > 0 && (
                <div className="stat-cards-row" style={{ marginTop: '1rem' }}>
                    {archetypes.map((a) => (
                        <div
                            key={a.archetype}
                            className="stat-card"
                            style={{ cursor: 'pointer', borderColor: hoveredArchetype === a.archetype ? colorByArchetype[a.archetype] : undefined }}
                            onMouseEnter={() => setHoveredArchetype(a.archetype)}
                            onMouseLeave={() => setHoveredArchetype(null)}
                        >
                            <div style={{ width: 12, height: 12, borderRadius: '50%', background: colorByArchetype[a.archetype], flexShrink: 0 }} />
                            <div>
                                <div className="stat-card-label">{a.archetype}</div>
                                <div className="stat-card-value" style={{ fontSize: '1.1rem' }}>{a.n_player_seasons.toLocaleString()}</div>
                                <div className="page-subtitle" style={{ marginTop: 2, fontSize: '0.75rem' }}>
                                    e.g. {a.representative_players.slice(0, 2).map((p) => p.player_name).join(', ')}
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            <div className="input-row" style={{ marginTop: '1.25rem' }}>
                <input
                    type="number"
                    className="input-field"
                    value={season}
                    onChange={(e) => setSeason(Number(e.target.value))}
                    min={2010}
                    max={2026}
                />
            </div>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}

            {!loading && plot && (
                <div className="court-container" style={{ marginTop: '1rem' }}>
                    <svg viewBox={`0 0 ${plot.width} ${plot.height}`} className="court-svg" style={{ maxHeight: 420 }}>
                        <rect x="0" y="0" width={plot.width} height={plot.height} fill="#1a2332" rx="8" />
                        {visiblePlayers.map((p) => (
                            <circle
                                key={p.player_id}
                                cx={plot.scaleX(p.pca_x)}
                                cy={plot.scaleY(p.pca_y)}
                                r={hoveredArchetype ? 4 : 3}
                                fill={colorByArchetype[p.archetype] || '#94a3b8'}
                                opacity={0.8}
                            >
                                <title>{p.player_name} ({p.team_abbreviation}) — {p.archetype}: {p.pts} pts, {p.reb} reb, {p.ast} ast</title>
                            </circle>
                        ))}
                    </svg>
                </div>
            )}

            {!loading && players.length > 0 && (
                <div className="table-wrapper" style={{ marginTop: '1rem' }}>
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Player</th><th>Team</th><th>Archetype</th><th>PTS</th><th>REB</th><th>AST</th>
                            </tr>
                        </thead>
                        <tbody>
                            {visiblePlayers.slice(0, 60).map((p) => (
                                <tr key={p.player_id}>
                                    <td>
                                        <button
                                            type="button"
                                            onClick={() => loadHistory(p.player_name)}
                                            style={{ background: 'none', border: 'none', color: '#38bdf8', cursor: 'pointer', padding: 0, font: 'inherit' }}
                                        >
                                            {p.player_name}
                                        </button>
                                    </td>
                                    <td>{p.team_abbreviation}</td>
                                    <td>
                                        <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: colorByArchetype[p.archetype], marginRight: 6 }} />
                                        {p.archetype}
                                    </td>
                                    <td>{p.pts}</td>
                                    <td>{p.reb}</td>
                                    <td>{p.ast}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    {visiblePlayers.length > 60 && (
                        <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
                            Showing 60 of {visiblePlayers.length} players.
                        </p>
                    )}
                </div>
            )}

            <h3 className="section-heading" style={{ marginTop: '1.5rem' }}>Career Archetype History</h3>
            <div className="input-row" style={{ marginBottom: 0 }}>
                <div style={{ position: 'relative', flex: 1 }}>
                    <input
                        type="text"
                        className="input-field"
                        placeholder="Search a player to see how their role changed over their career…"
                        value={searchInput}
                        onChange={(e) => setSearchInput(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') loadHistory(searchInput); }}
                    />
                    {suggestions.length > 0 && (
                        <ul className="autocomplete-list" style={{
                            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
                            background: '#1e293b', border: '1px solid #334155', borderRadius: 6,
                            marginTop: 4, maxHeight: 220, overflowY: 'auto', listStyle: 'none', padding: 0,
                        }}>
                            {suggestions.map((name) => (
                                <li key={name}>
                                    <button
                                        type="button"
                                        onClick={() => loadHistory(name)}
                                        style={{ display: 'block', width: '100%', textAlign: 'left', padding: '0.5rem 0.75rem', background: 'transparent', border: 'none', color: '#e2e8f0', cursor: 'pointer' }}
                                    >
                                        {name}
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </div>

            {historyError && <p className="error-message" style={{ marginTop: '0.75rem' }}>{historyError}</p>}
            {history && (
                <div className="table-wrapper" style={{ marginTop: '1rem' }}>
                    <table className="data-table">
                        <thead>
                            <tr><th>Season</th><th>Archetype</th><th>PTS</th><th>REB</th><th>AST</th><th>USG%</th></tr>
                        </thead>
                        <tbody>
                            {history.seasons.map((s) => (
                                <tr key={s.season}>
                                    <td>{s.season - 1}-{String(s.season).slice(-2)}</td>
                                    <td>
                                        <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: colorByArchetype[s.archetype], marginRight: 6 }} />
                                        {s.archetype}
                                    </td>
                                    <td>{s.pts}</td>
                                    <td>{s.reb}</td>
                                    <td>{s.ast}</td>
                                    <td>{(s.usg_pct * 100).toFixed(1)}%</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </section>
    );
}
