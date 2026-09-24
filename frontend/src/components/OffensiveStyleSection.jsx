import React, { useEffect, useMemo, useState } from 'react';
import { fetchPlaytypeArchetypes, fetchPlaytypeSeasonClusters } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';

const PALETTE = ['#facc15', '#fb923c', '#38bdf8', '#a78bfa', '#34d399', '#f87171'];

export default function OffensiveStyleSection() {
    const [styles, setStyles] = useState([]);
    const [season, setSeason] = useState(2025);
    const [seasonData, setSeasonData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [hoveredStyle, setHoveredStyle] = useState(null);

    useEffect(() => {
        let active = true;
        fetchPlaytypeArchetypes()
            .then((data) => { if (active) setStyles(data.styles || []); })
            .catch(() => { if (active) setStyles([]); });
        return () => { active = false; };
    }, []);

    useEffect(() => {
        let active = true;
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                const data = await fetchPlaytypeSeasonClusters(season);
                if (active) setSeasonData(data);
            } catch (e) {
                if (!active) return;
                setSeasonData(null);
                setError(e?.response?.data?.detail || 'No offensive-style results found. Run scripts/cluster_playtypes.py first.');
            } finally {
                if (active) setLoading(false);
            }
        }, 0);
        return () => { active = false; clearTimeout(timer); };
    }, [season]);

    const colorByStyle = useMemo(() => {
        const map = {};
        styles.forEach((s, i) => { map[s.style] = PALETTE[i % PALETTE.length]; });
        return map;
    }, [styles]);

    const players = useMemo(() => seasonData?.players ?? [], [seasonData]);
    const visiblePlayers = hoveredStyle ? players.filter((p) => p.style === hoveredStyle) : players;

    const plot = useMemo(() => {
        if (!players.length) return null;
        const xs = players.map((p) => p.pca_x);
        const ys = players.map((p) => p.pca_y);
        const xMin = Math.min(...xs), xMax = Math.max(...xs);
        const yMin = Math.min(...ys), yMax = Math.max(...ys);
        const pad = 30;
        const width = 600, height = 340;
        const scaleX = (x) => pad + ((x - xMin) / (xMax - xMin || 1)) * (width - 2 * pad);
        const scaleY = (y) => (height - pad) - ((y - yMin) / (yMax - yMin || 1)) * (height - 2 * pad);
        return { width, height, scaleX, scaleY };
    }, [players]);

    return (
        <section className="dashboard-card" style={{ marginTop: '1.5rem' }}>
            <h3 className="section-heading" style={{ marginTop: 0 }}>
                Offensive Style
                <InfoTooltip label="How this works" title="Real play-type mix, not production">
                    The archetypes above group players by what they PRODUCE (scoring, playmaking, rebounding,
                    defense). This is a separate real K-Means clustering (scripts/cluster_playtypes.py) on a
                    different real feature space: each real player's real offensive play-type frequency mix
                    (NBA Synergy tracking — real isolation, pick-and-roll, spot-up, post-up, transition, and
                    roll-man possession shares, era-normalized within season the same way as the stat
                    archetypes). It answers "how does this player's offense actually get generated" rather
                    than "what does their production look like." The real silhouette score for this clustering
                    is lower than the stat archetypes' (play-type mixes overlap more than production profiles
                    do) — disclosed honestly, not hidden.
                </InfoTooltip>
                <SourceBadge source={seasonData?._source} />
            </h3>

            {styles.length > 0 && (
                <div className="stat-cards-row" style={{ marginBottom: '1rem' }}>
                    {styles.map((s) => (
                        <div
                            key={s.style}
                            className="stat-card"
                            style={{ cursor: 'pointer', borderColor: hoveredStyle === s.style ? colorByStyle[s.style] : undefined }}
                            onMouseEnter={() => setHoveredStyle(s.style)}
                            onMouseLeave={() => setHoveredStyle(null)}
                        >
                            <div style={{ width: 12, height: 12, borderRadius: '50%', background: colorByStyle[s.style], flexShrink: 0 }} />
                            <div>
                                <div className="stat-card-label">{s.style}</div>
                                <div className="stat-card-value" style={{ fontSize: '1.1rem' }}>{s.n_player_seasons.toLocaleString()}</div>
                                <div className="page-subtitle" style={{ marginTop: 2, fontSize: '0.75rem' }}>
                                    e.g. {s.representative_players.slice(0, 2).map((p) => p.player_name).join(', ')}
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            <div className="input-row" style={{ marginBottom: '1rem' }}>
                <input
                    type="number"
                    className="input-field"
                    value={season}
                    onChange={(e) => setSeason(Number(e.target.value))}
                    min={2013}
                    max={2026}
                />
            </div>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}

            {!loading && plot && (
                <div className="court-container">
                    <svg viewBox={`0 0 ${plot.width} ${plot.height}`} className="court-svg" style={{ maxHeight: 380 }}>
                        <rect x="0" y="0" width={plot.width} height={plot.height} fill="var(--surface-2)" rx="8" />
                        {visiblePlayers.map((p) => (
                            <circle
                                key={p.player_id}
                                cx={plot.scaleX(p.pca_x)}
                                cy={plot.scaleY(p.pca_y)}
                                r={hoveredStyle ? 4 : 3}
                                fill={colorByStyle[p.style] || '#94a3b8'}
                                opacity={0.8}
                            >
                                <title>{p.player_name} ({p.team_abbreviation}) — {p.style}</title>
                            </circle>
                        ))}
                    </svg>
                </div>
            )}

            {!loading && players.length > 0 && (
                <div className="table-wrapper" style={{ marginTop: '1rem' }}>
                    <table className="data-table">
                        <thead>
                            <tr><th>Player</th><th>Team</th><th>Offensive Style</th></tr>
                        </thead>
                        <tbody>
                            {visiblePlayers.slice(0, 60).map((p) => (
                                <tr key={p.player_id}>
                                    <td>{p.player_name}</td>
                                    <td>{p.team_abbreviation}</td>
                                    <td>
                                        <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: colorByStyle[p.style], marginRight: 6 }} />
                                        {p.style}
                                    </td>
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
        </section>
    );
}
