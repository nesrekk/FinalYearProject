import React, { useEffect, useMemo, useState } from 'react';
import { fetchArchetypes, fetchSeasonClusters, fetchPlayerClusterHistory, fetchLivePlayerSuggestions, fetchLeagueEvolution } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import OffensiveStyleSection from './OffensiveStyleSection';

const PALETTE = ['#38bdf8', '#f87171', '#facc15', '#a78bfa', '#34d399', '#fb923c'];

const EVO_W = 640, EVO_H = 260, EVO_PAD_L = 42, EVO_PAD_R = 12, EVO_PAD_T = 10, EVO_PAD_B = 22;
const EVO_PLOT_W = EVO_W - EVO_PAD_L - EVO_PAD_R;
const EVO_PLOT_H = EVO_H - EVO_PAD_T - EVO_PAD_B;

function seasonLabel(season) {
    return `${season - 1}-${String(season).slice(-2)}`;
}

function StackedArchetypeChart({ evolution, colorByArchetype }) {
    const seasons = evolution?.seasons || [];
    const archetypes = evolution?.archetypes || [];
    if (!seasons.length) return null;

    const evoX = (i) => EVO_PAD_L + (i / (seasons.length - 1 || 1)) * EVO_PLOT_W;
    const evoY = (cum) => EVO_PAD_T + (1 - cum) * EVO_PLOT_H;

    const baseline = seasons.map(() => 0);
    const layers = archetypes.reduce((acc, archetype) => {
        const prevCumulative = acc.length ? acc[acc.length - 1].cumulative : baseline;
        const shares = evolution.archetype_shares[archetype] || [];
        const top = shares.map((s, i) => prevCumulative[i] + (s.share || 0));
        const bottomPath = prevCumulative.map((c, i) => `${i === 0 ? 'M' : 'L'} ${evoX(i).toFixed(1)} ${evoY(c).toFixed(1)}`).join(' ');
        const topPath = top.map((c, i) => `L ${evoX(i).toFixed(1)} ${evoY(c).toFixed(1)}`).reverse().join(' ');
        return [...acc, { archetype, path: `${bottomPath} ${topPath} Z`, cumulative: top }];
    }, []);

    const tickEvery = seasons.length > 10 ? 3 : 1;

    return (
        <svg viewBox={`0 0 ${EVO_W} ${EVO_H}`} style={{ width: '100%', display: 'block' }} role="img" aria-label={`Stacked area chart of each player archetype's share of the qualified player pool from ${seasons[0]} through ${seasons[seasons.length - 1]}`}>
            <rect x="0" y="0" width={EVO_W} height={EVO_H} fill="var(--surface-2)" rx="8" />
            {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                <React.Fragment key={t}>
                    <line x1={EVO_PAD_L} y1={evoY(t)} x2={EVO_W - EVO_PAD_R} y2={evoY(t)} stroke="var(--hairline)" strokeWidth="1" />
                    <text x={EVO_PAD_L - 6} y={evoY(t) + 3} fill="var(--text-3)" fontSize="9" textAnchor="end">{Math.round(t * 100)}%</text>
                </React.Fragment>
            ))}
            {layers.map((l) => (
                <path key={l.archetype} d={l.path} fill={colorByArchetype[l.archetype] || '#94a3b8'} fillOpacity={0.85} stroke="var(--surface)" strokeWidth="0.5" />
            ))}
            {seasons.map((s, i) => (
                i % tickEvery === 0 && (
                    <text key={s} x={evoX(i)} y={EVO_H - 6} fill="var(--text-3)" fontSize="9" textAnchor="middle">{s}</text>
                )
            ))}
        </svg>
    );
}

function TrendMiniChart({ label, data, valueKey, format, color }) {
    const values = data.map((d) => d[valueKey]).filter((v) => v != null);
    if (!values.length) return null;
    const vMin = Math.min(...values), vMax = Math.max(...values);
    const w = 200, h = 110, padL = 4, padR = 4, padT = 8, padB = 16;
    const plotW = w - padL - padR, plotH = h - padT - padB;
    const x = (i) => padL + (i / (data.length - 1 || 1)) * plotW;
    const y = (v) => padT + (1 - (v - vMin) / ((vMax - vMin) || 1)) * plotH;
    const path = data
        .map((d, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(d[valueKey]).toFixed(1)}`)
        .join(' ');

    return (
        <div>
            <div className="page-subtitle" style={{ marginBottom: 4, fontSize: '0.78rem' }}>{label}</div>
            <svg viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', display: 'block' }} role="img" aria-label={`Line chart of ${label} from ${data[0].season} to ${data[data.length - 1].season}, ranging from ${format(vMin)} to ${format(vMax)}`}>
                <rect x="0" y="0" width={w} height={h} fill="var(--surface-2)" rx="6" />
                <path d={path} fill="none" stroke={color} strokeWidth="2" />
                <text x={padL} y={h - 3} fill="var(--text-3)" fontSize="8">{data[0].season}</text>
                <text x={w - padR} y={h - 3} fill="var(--text-3)" fontSize="8" textAnchor="end">{data[data.length - 1].season}</text>
                <text x={padL} y={padT + 8} fill={color} fontSize="9" fontWeight="700">{format(vMax)}</text>
                <text x={padL} y={h - padB + 4} fill={color} fontSize="9" fontWeight="700">{format(vMin)}</text>
            </svg>
        </div>
    );
}

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

    const [evolution, setEvolution] = useState(null);
    const [evolutionError, setEvolutionError] = useState('');

    useEffect(() => {
        let active = true;
        fetchArchetypes()
            .then((data) => { if (active) setArchetypes(data.archetypes || []); })
            .catch(() => { if (active) setArchetypes([]); });
        return () => { active = false; };
    }, []);

    useEffect(() => {
        let active = true;
        fetchLeagueEvolution()
            .then((data) => { if (active) setEvolution(data); })
            .catch((e) => { if (active) setEvolutionError(e?.response?.data?.detail || 'Could not load league evolution.'); });
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
        <>
        <section className="dashboard-card">
            <h2 className="card-title hb-page-title">
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
                <SourceBadge source={seasonData?._source} />
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
                    <svg viewBox={`0 0 ${plot.width} ${plot.height}`} className="court-svg" style={{ maxHeight: 420 }} role="img" aria-label={`Scatter plot of a 2D projection of each player's statistical profile for the ${season} season, with dots colored by player archetype cluster`}>
                        <rect x="0" y="0" width={plot.width} height={plot.height} fill="var(--surface-2)" rx="8" />
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
                            background: 'var(--surface)', border: '2px solid var(--line)', borderRadius: 0, boxShadow: 'var(--shadow-card)',
                            marginTop: 4, maxHeight: 220, overflowY: 'auto', listStyle: 'none', padding: 0,
                        }}>
                            {suggestions.map((name) => (
                                <li key={name}>
                                    <button
                                        type="button"
                                        onClick={() => loadHistory(name)}
                                        style={{ display: 'block', width: '100%', textAlign: 'left', padding: '0.5rem 0.75rem', background: 'transparent', border: 'none', color: 'var(--text)', cursor: 'pointer' }}
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

            <h3 className="section-heading" style={{ marginTop: '1.5rem' }}>
                League Evolution
                <InfoTooltip label="How this works" title="Real historical aggregation, not a model">
                    Archetype share is the real fraction of each season's qualified player pool sorted into
                    each statistical archetype by the same K-Means clustering above — nothing modeled or
                    projected. The three trend charts are minutes-weighted league averages of real per-player
                    stats each season: 3PA rate is 3PA/FGA (shot-selection share, not raw attempts, which pace
                    would confound), TS% is real True Shooting%, and Pace is a real minutes-weighted
                    approximation (season possessions / season minutes * 48) since this project doesn't have
                    the official team-level NBA pace stat historically — disclosed as an approximation.
                </InfoTooltip>
            </h3>
            {evolutionError && <p className="error-message">{evolutionError}</p>}
            {evolution && (
                <>
                    <p className="page-subtitle" style={{ marginTop: '-0.5rem', marginBottom: '0.75rem' }}>
                        Real archetype share of the qualified player pool, {seasonLabel(evolution.seasons[0])} through {seasonLabel(evolution.seasons[evolution.seasons.length - 1])}.
                    </p>
                    <StackedArchetypeChart evolution={evolution} colorByArchetype={colorByArchetype} />
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.75rem 1.25rem', marginTop: '0.6rem' }}>
                        {evolution.archetypes.map((a) => (
                            <div key={a} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                <span style={{ width: 8, height: 8, borderRadius: '50%', background: colorByArchetype[a] }} />
                                <span className="page-subtitle" style={{ margin: 0, fontSize: '0.78rem' }}>{a}</span>
                            </div>
                        ))}
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '1rem', marginTop: '1.25rem' }}>
                        <TrendMiniChart
                            label="3PA Rate (3PA / FGA)"
                            data={evolution.trends}
                            valueKey="three_pt_rate"
                            format={(v) => `${Math.round(v * 100)}%`}
                            color="#facc15"
                        />
                        <TrendMiniChart
                            label="True Shooting %"
                            data={evolution.trends}
                            valueKey="ts_pct"
                            format={(v) => `${Math.round(v * 100)}%`}
                            color="#34d399"
                        />
                        <TrendMiniChart
                            label="Pace (real proxy)"
                            data={evolution.trends}
                            valueKey="pace_proxy"
                            format={(v) => v.toFixed(1)}
                            color="#38bdf8"
                        />
                    </div>
                </>
            )}
        </section>
        <OffensiveStyleSection />
        </>
    );
}
