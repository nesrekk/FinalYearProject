import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchArchetypes, fetchSeasonClusters, fetchPlayerClusterHistory, fetchLeagueEvolution } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import OffensiveStyleSection from './OffensiveStyleSection';
import TableExport from './common/TableExport';
import ChartExport from './common/ChartExport';
import SeasonSelect from './common/SeasonSelect';
import AutocompleteDropdown from './common/AutocompleteDropdown';
import NamesakeNote from './common/NamesakeNote';
import usePlayerSuggestions from '../utils/usePlayerSuggestions';
import { latestCompleteSeason } from '../utils/season';

// Ten distinct hues for the ten roles; fills only (never text), readable on Paper and Ink.
// Ten roles, ten theme series colours (tokens.css --series-N, >= 3:1 on Paper and Ink).
const PALETTE = Array.from({ length: 10 }, (_, i) => `var(--series-${i})`);

const EVO_W = 640, EVO_H = 260, EVO_PAD_L = 42, EVO_PAD_R = 12, EVO_PAD_T = 10, EVO_PAD_B = 22;
const EVO_PLOT_W = EVO_W - EVO_PAD_L - EVO_PAD_R;
const EVO_PLOT_H = EVO_H - EVO_PAD_T - EVO_PAD_B;

function seasonLabel(season) {
    return `${season - 1}-${String(season).slice(-2)}`;
}

function StackedArchetypeChart({ evolution, colorByArchetype }) {
    const svgRef = useRef(null);
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
        <div>
            <ChartExport svgRef={svgRef} name="archetype share of the player pool" />
            <svg ref={svgRef} viewBox={`0 0 ${EVO_W} ${EVO_H}`} style={{ width: '100%', display: 'block' }} role="img" aria-label={`Stacked area chart of each player archetype's share of the qualified player pool from ${seasons[0]} through ${seasons[seasons.length - 1]}`}>
                <rect x="0" y="0" width={EVO_W} height={EVO_H} fill="var(--surface-2)" rx="8" />
                {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                    <React.Fragment key={t}>
                        <line x1={EVO_PAD_L} y1={evoY(t)} x2={EVO_W - EVO_PAD_R} y2={evoY(t)} stroke="var(--hairline)" strokeWidth="1" />
                        <text x={EVO_PAD_L - 6} y={evoY(t) + 3} fill="var(--text-3)" fontSize="9" textAnchor="end">{Math.round(t * 100)}%</text>
                    </React.Fragment>
                ))}
                {layers.map((l) => (
                    <path key={l.archetype} d={l.path} fill={colorByArchetype[l.archetype] || 'var(--text-3)'} fillOpacity={0.85} stroke="var(--surface)" strokeWidth="0.5" />
                ))}
                {seasons.map((s, i) => (
                    i % tickEvery === 0 && (
                        <text key={s} x={evoX(i)} y={EVO_H - 6} fill="var(--text-3)" fontSize="9" textAnchor="middle">{s}</text>
                    )
                ))}
            </svg>
        </div>
    );
}

function TrendMiniChart({ label, data, valueKey, format, color }) {
    const svgRef = useRef(null);
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
            <svg ref={svgRef} viewBox={`0 0 ${w} ${h}`} style={{ width: '100%', display: 'block' }} role="img" aria-label={`Line chart of ${label} from ${data[0].season} to ${data[data.length - 1].season}, ranging from ${format(vMin)} to ${format(vMax)}`}>
                <rect x="0" y="0" width={w} height={h} fill="var(--surface-2)" rx="6" />
                <path d={path} fill="none" stroke={color} strokeWidth="2" />
                <text x={padL} y={h - 3} fill="var(--text-3)" fontSize="8">{data[0].season}</text>
                <text x={w - padR} y={h - 3} fill="var(--text-3)" fontSize="8" textAnchor="end">{data[data.length - 1].season}</text>
                <text x={padL} y={padT + 8} fill="var(--text-2)" fontSize="9" fontWeight="700">{format(vMax)}</text>
                <text x={padL} y={h - padB + 4} fill="var(--text-2)" fontSize="9" fontWeight="700">{format(vMin)}</text>
            </svg>
            <ChartExport svgRef={svgRef} name={label} />
        </div>
    );
}

export default function PlayerArchetypesSection() {
    const scatterRef = useRef(null);
    const [archetypes, setArchetypes] = useState([]);
    const [season, setSeason] = useState(() => latestCompleteSeason());
    const [seasonData, setSeasonData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [hoveredArchetype, setHoveredArchetype] = useState(null);

    const [searchInput, setSearchInput] = useState('');
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

    // Player search for the career-evolution lookup: suggestions carry the NBA id (two players can share a name).
    const sug = usePlayerSuggestions(searchInput, history?.player_name);
    const searchRef = useRef(null);

    async function loadHistory(name, id) {
        setSearchInput(name);
        sug.dismiss();
        setHistoryError('');
        try {
            const data = await fetchPlayerClusterHistory(name, id || undefined);
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
                <InfoTooltip label="How these roles were found" title="Unsupervised K-Means clustering">
                    No labels are fed in. For every player-season since 2009-10 (15+ minutes a game, 20+ games),
                    K-Means groups players by what they do with their minutes: usage, assist, turnover and
                    rebounding rates, steals and blocks per 36, free-throw rate, true shooting, and where their
                    shots come from (rim, paint, mid-range, corner three, above-the-break three). Each stat is
                    compared within its own season, so league-wide changes like the three-point boom aren&apos;t
                    mistaken for role changes. Ten roles is the most the data supports reliably: refitting on
                    different 80% samples reproduces them (stability{' '}
                    {archetypes[0]?.stability_ari != null ? archetypes[0].stability_ari.toFixed(2) : '—'}), while 11 or more
                    roles don&apos;t. Styles are a continuum, not clean boxes (silhouette{' '}
                    {archetypes[0]?.silhouette_score != null ? archetypes[0].silhouette_score.toFixed(2) : '—'}), so players near a
                    border could fit either side. Only the names use basketball knowledge; the grouping is
                    entirely data-driven. The &quot;family&quot; column is the older six-way grouping, which other
                    tools still use.
                </InfoTooltip>
                <SourceBadge source={seasonData?._source} />
            </h2>
            <p className="page-subtitle">
                {archetypes.length || 'Ten'} roles found in the data. Every dot below is one player-season, placed by a 2D
                projection of its style profile, so players who play alike sit near each other regardless of team
                or era. Hover a role to isolate it; click a player&apos;s name in the table to see their whole
                career&apos;s role history.
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
                                {a.description && (
                                    <div className="page-subtitle" style={{ marginTop: 2, fontSize: '0.75rem' }}>
                                        {a.description}
                                    </div>
                                )}
                                <div className="page-subtitle" style={{ marginTop: 2, fontSize: '0.75rem' }}>
                                    e.g. {(a.top_scorers || a.representative_players).slice(0, 3)
                                        .map((p) => `${p.player_name} ${p.season - 1}-${String(p.season).slice(-2)}`)
                                        .filter((v, i, arr) => arr.indexOf(v) === i).join(', ')}
                                </div>
                            </div>
                        </div>
                    ))}
                </div>
            )}

            <div className="input-row" style={{ marginTop: '1.25rem' }}>
                <SeasonSelect value={season} onChange={setSeason} from={2010} to={latestCompleteSeason()} />
            </div>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}

            {!loading && plot && (
                <div className="court-container" style={{ marginTop: '1rem' }}>
                    <ChartExport svgRef={scatterRef} name={`player archetypes ${season}`} />
                    <svg ref={scatterRef} viewBox={`0 0 ${plot.width} ${plot.height}`} className="court-svg" style={{ maxHeight: 420 }} role="img" aria-label={`Scatter plot of a 2D projection of each player's statistical profile for the ${season} season, with dots colored by player archetype cluster`}>
                        <rect x="0" y="0" width={plot.width} height={plot.height} fill="var(--surface-2)" rx="8" />
                        {visiblePlayers.map((p) => (
                            <circle
                                key={p.player_id}
                                cx={plot.scaleX(p.pca_x)}
                                cy={plot.scaleY(p.pca_y)}
                                r={hoveredArchetype ? 4 : 3}
                                fill={colorByArchetype[p.archetype] || 'var(--text-3)'}
                                opacity={0.9}
                            >
                                <title>{p.player_name} ({p.team_abbreviation}) — {p.archetype}: {p.pts} pts, {p.reb} reb, {p.ast} ast</title>
                            </circle>
                        ))}
                    </svg>
                </div>
            )}

            {!loading && players.length > 0 && (
                <>
                    <TableExport />
                    <div className="table-wrapper" style={{ marginTop: '1rem' }}>
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Player</th><th>Team</th><th>Role</th><th>Family</th><th>PTS</th><th>REB</th><th>AST</th>
                                </tr>
                            </thead>
                            <tbody>
                                {visiblePlayers.slice(0, 60).map((p) => (
                                    <tr key={p.player_id}>
                                        <td>
                                            <button
                                                type="button"
                                                onClick={() => loadHistory(p.player_name, p.player_id)}
                                                style={{ background: 'none', border: 'none', color: 'var(--brand-text)', textDecoration: 'underline', cursor: 'pointer', padding: 0, font: 'inherit' }}
                                            >
                                                {p.player_name}
                                            </button>
                                        </td>
                                        <td>{p.team_abbreviation}</td>
                                        <td>
                                            <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: colorByArchetype[p.archetype], marginRight: 6 }} />
                                            {p.archetype}
                                        </td>
                                        <td>{p.family || '—'}</td>
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
                </>
            )}

            <h3 className="section-heading" style={{ marginTop: '1.5rem' }}>Career Role History</h3>
            <div className="input-row" style={{ marginBottom: 0 }}>
                <div style={{ position: 'relative', flex: 1 }}>
                    <input
                        ref={searchRef}
                        type="text"
                        className="input-field"
                        placeholder="Search a player to see how their role changed over their career…"
                        aria-label="Player"
                        value={searchInput}
                        onChange={(e) => setSearchInput(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') loadHistory(searchInput); }}
                    />
                    <AutocompleteDropdown anchorRef={searchRef} items={sug.labels}
                        onPick={(label) => { const p = sug.pick(label); if (p) loadHistory(p.name, p.id); }} />
                </div>
            </div>
            <NamesakeNote name={history?.player_name} id={history?.player_id} onPick={(p) => loadHistory(p.name, p.id)} />

            {historyError && <p className="error-message" style={{ marginTop: '0.75rem' }}>{historyError}</p>}
            {history && (
                <>
                    <TableExport />
                    <div className="table-wrapper" style={{ marginTop: '1rem' }}>
                        <table className="data-table">
                            <thead>
                                <tr><th>Season</th><th>Role</th><th>Family</th><th>PTS</th><th>REB</th><th>AST</th><th>USG%</th></tr>
                            </thead>
                            <tbody>
                                {history.seasons.map((s) => (
                                    <tr key={s.season}>
                                        <td>{s.season - 1}-{String(s.season).slice(-2)}</td>
                                        <td>
                                            <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: colorByArchetype[s.archetype], marginRight: 6 }} />
                                            {s.archetype}
                                        </td>
                                        <td>{s.family || '—'}</td>
                                        <td>{s.pts}</td>
                                        <td>{s.reb}</td>
                                        <td>{s.ast}</td>
                                        <td>{(s.usg_pct * 100).toFixed(1)}%</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}

            <h3 className="section-heading" style={{ marginTop: '1.5rem' }}>
                League Evolution
                <InfoTooltip label="How this works" title="Real historical aggregation, not a model">
                    Role share is the fraction of each season&apos;s qualified player pool in each role found by the
                    same K-Means clustering above — nothing modeled or
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
                        Share of the qualified player pool in each role, {seasonLabel(evolution.seasons[0])} through {seasonLabel(evolution.seasons[evolution.seasons.length - 1])}.
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
                            color="var(--series-4)"
                        />
                        <TrendMiniChart
                            label="True Shooting %"
                            data={evolution.trends}
                            valueKey="ts_pct"
                            format={(v) => `${Math.round(v * 100)}%`}
                            color="var(--series-2)"
                        />
                        <TrendMiniChart
                            label="Pace (real proxy)"
                            data={evolution.trends}
                            valueKey="pace_proxy"
                            format={(v) => v.toFixed(1)}
                            color="var(--series-1)"
                        />
                    </div>
                </>
            )}
        </section>
        <OffensiveStyleSection />
        </>
    );
}
