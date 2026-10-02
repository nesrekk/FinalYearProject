import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchDadIndex } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import PlayerHeadshot from './common/PlayerHeadshot';
import TeamLogo from './common/TeamLogo';
import SourceBadge from './common/SourceBadge';
import AboutModelDrawer from './ui/AboutModelDrawer';
import TableExport from './common/TableExport';
import ChartExport from './common/ChartExport';
import { signed as signedNum } from '../utils/format';

const POS_COLORS = { G: '#38bdf8', F: '#a78bfa', C: '#f59e0b' };
const POS_LABELS = { G: 'Guards', F: 'Forwards', C: 'Centers' };
const QUADRANT_ORDER = ['lockdown', 'struggling', 'hidden', 'targeted'];

function seasonLabel(season) {
    return `${season - 1}-${String(season).slice(-2)}`;
}

// Sign follows the value as shown (no "+0.00" or "-0.00"); this page prints a hyphen minus.
function signed(v, digits = 2) {
    return signedNum(v, digits, '-');
}

// DFG% differential in percentage points (negative = shooters below their normal FG%).
function pp(v) {
    return v == null ? '—' : `${signedNum(v * 100, 1, '-')} pts`;
}

function Scatter({ defenders, zKey, focusId, onHover, onSelect, exportName }) {
    const svgRef = useRef(null);
    const W = 720;
    const H = 470;
    const m = { top: 28, right: 20, bottom: 44, left: 56 };
    const iw = W - m.left - m.right;
    const ih = H - m.top - m.bottom;

    const { xMax, yMax } = useMemo(() => {
        const xs = defenders.map((d) => Math.abs(d[zKey] ?? 0));
        const ys = defenders.map((d) => Math.abs(d.dfg_diff ?? 0));
        return {
            xMax: Math.ceil(Math.max(1, ...xs) * 2) / 2,
            yMax: Math.ceil(Math.max(0.01, ...ys) * 100 + 0.5) / 100,
        };
    }, [defenders, zKey]);

    const x = (v) => m.left + ((v + xMax) / (2 * xMax)) * iw;
    // Inverted: shooters BELOW their normal FG% (better defense) plot HIGHER.
    const y = (dfg) => m.top + ((dfg + yMax) / (2 * yMax)) * ih;

    const xTicks = [];
    for (let t = -Math.floor(xMax); t <= Math.floor(xMax); t += 1) xTicks.push(t);
    const yStep = yMax > 0.08 ? 0.04 : 0.02;
    const yTicks = [];
    for (let t = -Math.floor(yMax / yStep) * yStep; t <= yMax + 1e-9; t += yStep) yTicks.push(Number(t.toFixed(3)));

    const plotted = defenders.filter((d) => d[zKey] != null && d.dfg_diff != null);
    const focus = plotted.find((d) => d.player_id === focusId);

    const corner = (label, cx, cy, anchor) => (
        <text x={cx} y={cy} textAnchor={anchor} fontSize="11" fontWeight="700" fill="var(--text-muted)">{label}</text>
    );

    return (
        <div style={{ width: '100%', overflowX: 'auto' }}>
            <ChartExport svgRef={svgRef} name={exportName} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', minWidth: 520, height: 'auto', display: 'block' }}
                role="img" aria-label="Scatter of DAD z-score against defended FG% differential">
                {xTicks.map((t) => (
                    <g key={`x${t}`}>
                        <line x1={x(t)} x2={x(t)} y1={m.top} y2={m.top + ih} stroke="var(--border)" strokeWidth={t === 0 ? 1.5 : 1} />
                        <text x={x(t)} y={m.top + ih + 16} textAnchor="middle" fontSize="11" fill="var(--text-muted)">{t}</text>
                    </g>
                ))}
                {yTicks.map((t) => (
                    <g key={`y${t}`}>
                        <line x1={m.left} x2={m.left + iw} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={t === 0 ? 1.5 : 1} />
                        <text x={m.left - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--text-muted)">
                            {t > 0 ? '+' : ''}{Math.round(t * 100)}
                        </text>
                    </g>
                ))}
                <text x={m.left + iw / 2} y={H - 6} textAnchor="middle" fontSize="12" fill="var(--text-secondary)">
                    {zKey === 'dad_pos_z' ? 'DAD z-score within position' : 'DAD z-score'} → harder assignments
                </text>
                <text transform={`translate(14 ${m.top + ih / 2}) rotate(-90)`} textAnchor="middle" fontSize="12" fill="var(--text-secondary)">
                    Defended FG% vs. shooters' normal (pts) — better ↑
                </text>

                {corner('Lockdown vs. stars', m.left + iw - 6, m.top + 14, 'end')}
                {corner('Hidden', m.left + 6, m.top + 14, 'start')}
                {corner('Targeted', m.left + 6, m.top + ih - 8, 'start')}
                {corner('Struggling vs. stars', m.left + iw - 6, m.top + ih - 8, 'end')}

                {plotted.map((d) => {
                    const isFocus = d.player_id === focusId;
                    return (
                        <circle key={d.player_id}
                            cx={x(Math.max(-xMax, Math.min(xMax, d[zKey])))}
                            cy={y(Math.max(-yMax, Math.min(yMax, d.dfg_diff)))}
                            r={isFocus ? 7 : 4}
                            fill={POS_COLORS[d.pos_group] || 'var(--text-muted)'}
                            fillOpacity={d.small_dfg_sample ? 0.25 : 0.75}
                            stroke={isFocus ? 'var(--text-primary)' : 'none'}
                            strokeWidth={isFocus ? 2 : 0}
                            style={{ cursor: 'pointer' }}
                            onMouseEnter={() => onHover(d.player_id)}
                            onMouseLeave={() => onHover(null)}
                            onClick={() => onSelect(d.player_id)} />
                    );
                })}
                {focus && (
                    <text x={x(focus[zKey]) + (focus[zKey] > xMax / 2 ? -10 : 10)} y={y(focus.dfg_diff) - 10}
                        textAnchor={focus[zKey] > xMax / 2 ? 'end' : 'start'} fontSize="12" fontWeight="700" fill="var(--text-primary)">
                        {focus.player_name}
                    </text>
                )}
            </svg>
            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', fontSize: '0.75rem', marginTop: '0.25rem' }} className="page-subtitle">
                {Object.entries(POS_LABELS).map(([k, label]) => (
                    <span key={k}><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 5, background: POS_COLORS[k], marginRight: 4 }} />{label}</span>
                ))}
                <span>Faded dot = under 300 real defended shots (DFG% too noisy to read)</span>
            </div>
        </div>
    );
}

function DefenderDetail({ d, zKey, quadrants }) {
    if (!d) return <p className="page-subtitle">Hover or click a dot, or search a defender, to see their real assignments.</p>;
    const q = zKey === 'dad_pos_z' ? d.quadrant_pos : d.quadrant;
    return (
        <div style={{ opacity: d.small_dfg_sample ? 0.75 : 1 }}>
            <div className="entity-row" style={{ gap: '0.75rem', marginBottom: '0.5rem' }}>
                <PlayerHeadshot playerId={d.player_id} playerName={d.player_name} size={44} />
                <div>
                    <div style={{ fontWeight: 700 }}>
                        {d.player_name} <TeamLogo abbreviation={d.team_abbreviation} size={16} style={{ marginLeft: 4 }} />
                        <span className="page-subtitle" style={{ marginLeft: 6 }}>{d.position}</span>
                    </div>
                    <div className="page-subtitle">
                        DAD {signed(d.dad)} · z {signed(d.dad_z)} · within-position z {signed(d.dad_pos_z)} ·{' '}
                        {Math.round(d.total_poss).toLocaleString()} real partial possessions vs. {d.n_assignments} players
                    </div>
                    {q && <div style={{ fontSize: '0.8rem', marginTop: 2 }}>{quadrants[q]}</div>}
                </div>
            </div>
            <div className="page-subtitle" style={{ fontSize: '0.8rem', marginBottom: '0.5rem' }}>
                Defended FG% {(d.d_fg_pct * 100).toFixed(1)}% vs. those shooters' normal {(d.normal_fg_pct * 100).toFixed(1)}%:{' '}
                <strong>{pp(d.dfg_diff)}</strong> ± {(d.dfg_diff_margin95 * 100).toFixed(1)} (95%), n = {d.d_fga} real shots
                {d.small_dfg_sample && ' — small sample'}
                {' · '}{(d.replacement_share * 100).toFixed(1)}% of possessions vs. replacement-level players
            </div>
            <TableExport />
            <table className="data-table">
                <thead>
                    <tr><th>Top assignment</th><th>Share of possessions</th><th>Partial poss.</th><th>OBPM used</th></tr>
                </thead>
                <tbody>
                    {d.top_assignments.map((a) => (
                        <tr key={a.player_id}>
                            <td>
                                <div className="entity-row">
                                    <PlayerHeadshot playerId={a.player_id} playerName={a.player_name} size={22} />
                                    {a.player_name}
                                </div>
                            </td>
                            <td>{(a.share * 100).toFixed(1)}%</td>
                            <td>{a.partial_poss}</td>
                            <td>{signed(a.obpm, 1)}{a.replacement_level && ' (replacement)'}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default function DadIndexSection() {
    const [season, setSeason] = useState(null);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [withinPosition, setWithinPosition] = useState(false);
    const [hoverId, setHoverId] = useState(null);
    const [selectedId, setSelectedId] = useState(null);
    const [search, setSearch] = useState('');
    const [sortDesc, setSortDesc] = useState(true);

    useEffect(() => {
        let active = true;
        Promise.resolve().then(() => {
            if (!active) return;
            setLoading(true);
            fetchDadIndex(season)
                .then((d) => { if (active) { setData(d); setError(''); } })
                .catch((e) => { if (active) setError(e?.response?.data?.detail || 'Could not load the DAD Index.'); })
                .finally(() => { if (active) setLoading(false); });
        });
        return () => { active = false; };
    }, [season]);

    const zKey = withinPosition ? 'dad_pos_z' : 'dad_z';
    const defenders = useMemo(() => data?.defenders || [], [data]);
    const byId = useMemo(() => new Map(defenders.map((d) => [d.player_id, d])), [defenders]);
    const focusId = hoverId ?? (byId.has(selectedId) ? selectedId : null);
    const sorted = useMemo(
        () => [...defenders].filter((d) => d[zKey] != null).sort((a, b) => (sortDesc ? b[zKey] - a[zKey] : a[zKey] - b[zKey])),
        [defenders, zKey, sortDesc]
    );

    function handleSearch(value) {
        setSearch(value);
        const match = defenders.find((d) => d.player_name.toLowerCase() === value.trim().toLowerCase());
        if (match) setSelectedId(match.player_id);
    }

    const v = data?.validation;

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    DAD Index (OBPM-weighted)
                    <InfoTooltip label="How this works" title="Defensive Assignment Difficulty">
                        {data?.methodology || 'Loading methodology…'}
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
                </h3>
                <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                    Raw defensive FG% can mislead: a wing who guards stars every night will allow more than a teammate
                    hidden on a non-shooter. DAD measures how hard each defender's real assignments were.
                </p>
                <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'center', marginTop: '0.75rem' }}>
                    <label className="page-subtitle">
                        Season:{' '}
                        <select value={data?.season ?? ''} onChange={(e) => setSeason(Number(e.target.value))} disabled={!data}>
                            {(data?.seasons_available || []).map((s) => (
                                <option key={s} value={s}>{seasonLabel(s)}</option>
                            ))}
                        </select>
                    </label>
                    <label className="page-subtitle" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <input type="checkbox" checked={withinPosition} onChange={(e) => setWithinPosition(e.target.checked)} />
                        Compare within position (G/F/C)
                        <InfoTooltip label="Why" title="Within-position view">
                            Qualified centers average about +0.65 on the plain DAD z-score, mostly because they guard other
                            starting bigs, who post high OBPMs. The within-position z-score
                            compares each defender only to others at the same listed position that season.
                        </InfoTooltip>
                    </label>
                </div>
                {v && (
                    <p className="page-subtitle" style={{ marginTop: '0.75rem', fontSize: '0.8rem' }}>
                        <strong>Validation:</strong>{' '}
                        {v.year_over_year_r != null
                            ? <>year-over-year stability r = {v.year_over_year_r.toFixed(2)} (n = {v.year_over_year_n} defenders qualified in both seasons) · </>
                            : <>no prior season on file for a year-over-year check · </>}
                        DAD vs. DFG% differential r = {v.dad_vs_dfg_diff_r.toFixed(2)} (descriptive) ·{' '}
                        {v.n_qualified} of {v.n_defenders} real defenders qualified (1,000+ partial possessions) ·{' '}
                        {(v.replacement_possession_share * 100).toFixed(1)}% of real possessions scored at replacement level.
                        {v.note && <span style={{ display: 'block', marginTop: '0.25rem' }}>{v.note}</span>}
                    </p>
                )}
                {error && <p className="error-message">{error}</p>}
            </div>

            {data && (
                <AboutModelDrawer title="Methods, thresholds & quadrants">
                    <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
                        <li><strong>DAD</strong> = Σ (share of the defender's real partial possessions vs. player i × OBPM of player i).</li>
                        <li>
                            <strong>OBPM</strong> is Basketball-Reference&apos;s published Offensive Box Plus/Minus. EPM is
                            proprietary and not used.
                        </li>
                        <li>
                            <strong>Replacement level:</strong> offensive players under {data.thresholds.replacement_min_minutes} real
                            minutes (or with no stored season line) count as OBPM {data.thresholds.replacement_obpm.toFixed(1)}.
                        </li>
                        <li>
                            <strong>Qualified:</strong> {data.thresholds.qualified_min_partial_poss.toLocaleString()}+ real partial possessions.
                            Only matchup pairs of 5+ partial possessions are stored, so shares are over those.
                        </li>
                        <li>
                            <strong>DFG% differential</strong> = real defended FG% − those shooters' real normal FG% (NBA's own
                            LeagueDashPtDefend). Faded under {data.thresholds.reliable_min_defended_fga} real defended shots.
                        </li>
                        {QUADRANT_ORDER.map((q) => <li key={q}>{data.quadrants[q]}.</li>)}
                        <li>
                            Quadrants split at z = 0 (average difficulty among qualified defenders) and a 0-point DFG% differential.
                            They are a descriptive label, not a verdict: DAD ignores help defense, rebounding and scheme.
                        </li>
                    </ul>
                </AboutModelDrawer>
            )}

            {loading && <Loader />}

            {data && !loading && (
                <>
                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <div className="input-row" style={{ marginBottom: '0.75rem' }}>
                            <input
                                type="text"
                                className="input-field"
                                placeholder={`Highlight a qualified defender in ${seasonLabel(data.season)}`}
                                value={search}
                                onChange={(e) => handleSearch(e.target.value)}
                                list="dad-defender-suggestions"
                            />
                            <datalist id="dad-defender-suggestions">
                                {defenders.map((d) => <option key={d.player_id} value={d.player_name} />)}
                            </datalist>
                        </div>
                        <Scatter defenders={defenders} zKey={zKey} focusId={focusId} onHover={setHoverId} onSelect={setSelectedId}
                            exportName={`DAD index ${seasonLabel(data.season)}`} />
                        <div style={{ marginTop: '1rem' }}>
                            <DefenderDetail d={byId.get(focusId)} zKey={zKey} quadrants={data.quadrants} />
                        </div>
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h4 className="section-heading" style={{ marginTop: 0 }}>
                            {sortDesc ? 'Hardest' : 'Easiest'} assignments — {data.defenders.length} qualified defenders
                            <button type="button" className="action-btn" style={{ marginLeft: 'auto', fontSize: '0.75rem' }}
                                onClick={() => setSortDesc((s) => !s)}>
                                Show {sortDesc ? 'easiest' : 'hardest'} first
                            </button>
                        </h4>
                        <TableExport />
                        <div className="table-wrapper" style={{ maxHeight: 460, overflowY: 'auto' }}>
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>#</th>
                                        <th>Defender</th>
                                        <th>{withinPosition ? 'Pos. z' : 'DAD z'}</th>
                                        <th>DAD</th>
                                        <th>DFG% vs. normal</th>
                                        <th>Top assignment</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {sorted.map((d, i) => (
                                        <tr key={d.player_id} onClick={() => setSelectedId(d.player_id)}
                                            style={{ cursor: 'pointer', opacity: d.small_dfg_sample ? 0.55 : 1 }}>
                                            <td>{i + 1}</td>
                                            <td>
                                                <div className="entity-row">
                                                    <PlayerHeadshot playerId={d.player_id} playerName={d.player_name} size={24} />
                                                    {d.player_name}
                                                    <TeamLogo abbreviation={d.team_abbreviation} size={14} style={{ marginLeft: 6 }} />
                                                    <span className="page-subtitle" style={{ marginLeft: 6, fontSize: '0.7rem' }}>{d.position}</span>
                                                </div>
                                            </td>
                                            <td style={{ fontWeight: 700 }}>{signed(d[zKey])}</td>
                                            <td>{signed(d.dad)}</td>
                                            <td>
                                                {pp(d.dfg_diff)}
                                                <span className="page-subtitle" style={{ display: 'block', fontSize: '0.68rem' }}>
                                                    ± {(d.dfg_diff_margin95 * 100).toFixed(1)} · n = {d.d_fga}{d.small_dfg_sample ? ' · small sample' : ''}
                                                </span>
                                            </td>
                                            <td>
                                                {d.top_assignments[0]?.player_name}
                                                <span className="page-subtitle" style={{ display: 'block', fontSize: '0.68rem' }}>
                                                    {d.top_assignments[0] ? `${(d.top_assignments[0].share * 100).toFixed(1)}% of possessions` : ''}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}
