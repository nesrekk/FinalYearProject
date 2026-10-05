import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchContractValue } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import PlayerHeadshot from './common/PlayerHeadshot';
import TeamLogo from './common/TeamLogo';
import SourceBadge from './common/SourceBadge';
import AboutModelDrawer from './ui/AboutModelDrawer';
import TableExport from './common/TableExport';
import ChartExport from './common/ChartExport';
import { withSign } from '../utils/format';

function seasonLabel(season) {
    return `${season - 1}-${String(season).slice(-2)}`;
}

function money(v, digits = 1) {
    if (v == null) return '—';
    const sign = v < 0 ? '−' : '';
    const a = Math.abs(v);
    if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(digits)}M`;
    return `${sign}$${Math.round(a / 1e3).toLocaleString()}K`;
}

// Sign follows the amount as shown, so a surplus that rounds to $0K carries none.
function signedMoney(v) {
    return v == null ? '—' : withSign(v, money(Math.abs(v)));
}

function Scatter({ points, costPerWin, minSalary, minMinutes, exportName }) {
    const svgRef = useRef(null);
    const [hover, setHover] = useState(null);
    const W = 720;
    const H = 440;
    const m = { top: 20, right: 20, bottom: 44, left: 64 };
    const iw = W - m.left - m.right;
    const ih = H - m.top - m.bottom;
    const { xMin, xMax, yMax } = useMemo(() => {
        const wars = points.map((p) => p.war);
        return {
            xMin: Math.floor(Math.min(0, ...wars)),
            xMax: Math.ceil(Math.max(...wars)),
            yMax: Math.ceil(Math.max(...points.map((p) => p.salary), ...points.map((p) => p.fair_value)) / 10e6) * 10e6,
        };
    }, [points]);
    const x = (v) => m.left + ((v - xMin) / (xMax - xMin || 1)) * iw;
    const y = (v) => m.top + ih - (v / yMax) * ih;
    const xTicks = [];
    for (let t = Math.ceil(xMin / 5) * 5; t <= xMax; t += 5) xTicks.push(t);
    const yTicks = [];
    const yStep = yMax > 100e6 ? 25e6 : 10e6;
    for (let t = 0; t <= yMax; t += yStep) yTicks.push(t);
    const fairAt = (war) => Math.max(war, 0) * costPerWin + minSalary;
    const lineEnd = Math.min(xMax, (yMax - minSalary) / costPerWin);
    const hovered = points.find((p) => p.player_id === hover);

    return (
        <div style={{ width: '100%', overflowX: 'auto' }}>
            <ChartExport svgRef={svgRef} name={exportName} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', minWidth: 520, height: 'auto', display: 'block' }}
                role="img" aria-label="Scatter of real salary against WAR with the fair-value line">
                {yTicks.map((t) => (
                    <g key={`y${t}`}>
                        <line x1={m.left} x2={m.left + iw} y1={y(t)} y2={y(t)} stroke="var(--border)" />
                        <text x={m.left - 8} y={y(t) + 4} textAnchor="end" fontSize="11" fill="var(--text-muted)">{money(t, 0)}</text>
                    </g>
                ))}
                {xTicks.map((t) => (
                    <g key={`x${t}`}>
                        <line x1={x(t)} x2={x(t)} y1={m.top} y2={m.top + ih} stroke="var(--border)" strokeWidth={t === 0 ? 1.5 : 1} />
                        <text x={x(t)} y={m.top + ih + 16} textAnchor="middle" fontSize="11" fill="var(--text-muted)">{t}</text>
                    </g>
                ))}
                <text x={m.left + iw / 2} y={H - 6} textAnchor="middle" fontSize="12" fill="var(--text-secondary)">WAR (calibrated wins above replacement)</text>
                <text transform={`translate(14 ${m.top + ih / 2}) rotate(-90)`} textAnchor="middle" fontSize="12" fill="var(--text-secondary)">Real salary</text>

                <polyline fill="none" stroke="var(--series-4)" strokeWidth={2} strokeDasharray="6 4"
                    points={`${x(xMin)},${y(fairAt(xMin))} ${x(0)},${y(fairAt(0))} ${x(lineEnd)},${y(fairAt(lineEnd))}`} />
                <text x={x(lineEnd) - 4} y={y(fairAt(lineEnd)) + 14} textAnchor="end" fontSize="11" fill="var(--streak)">fair value</text>

                {points.map((p) => (
                    <circle key={p.player_id} cx={x(p.war)} cy={y(p.salary)} r={p.player_id === hover ? 6 : 3.5}
                        fill={p.surplus >= 0 ? 'var(--positive)' : 'var(--negative)'}
                        fillOpacity={p.minutes >= minMinutes ? 0.75 : 0.25}
                        stroke={p.player_id === hover ? 'var(--text-primary)' : 'none'}
                        onMouseEnter={() => setHover(p.player_id)} onMouseLeave={() => setHover(null)} />
                ))}
            </svg>
            <p className="page-subtitle" style={{ fontSize: '0.78rem', minHeight: '1.3em' }}>
                {hovered
                    ? <><strong>{hovered.player_name}</strong> ({hovered.team_abbreviation}): {money(hovered.salary)} salary, {hovered.war.toFixed(1)} WAR,
                        fair value {money(hovered.fair_value)}, surplus {signedMoney(hovered.surplus)} · {Math.round(hovered.minutes).toLocaleString()} min</>
                    : <>Green = paid below fair value, red = above. Points below the dashed line are bargains. Faded = under {minMinutes} real minutes.</>}
            </p>
        </div>
    );
}

function ValueTable({ title, tooltip, rows }) {
    return (
        <div className="dashboard-card" style={{ flex: '1 1 100%', minWidth: 0 }}>
            <h4 className="section-heading" style={{ marginTop: 0 }}>
                {title}
                <InfoTooltip label="What this ranks" title={title}>{tooltip}</InfoTooltip>
            </h4>
            <TableExport />
            <div className="table-wrapper">
                <table className="data-table">
                    <thead>
                        <tr><th>#</th><th>Player</th><th>Salary</th><th>WAR</th><th>Fair value</th><th>Surplus</th><th>Minutes</th></tr>
                    </thead>
                    <tbody>
                        {rows.map((r, i) => (
                            <tr key={r.player_id}>
                                <td>{i + 1}</td>
                                <td>
                                    <div className="entity-row">
                                        <PlayerHeadshot playerId={r.player_id} playerName={r.player_name} size={24} />
                                        {r.player_name}
                                        <TeamLogo abbreviation={r.team_abbreviation} size={14} style={{ marginLeft: 6 }} />
                                    </div>
                                    {r.rookie_scale_years && (
                                        <span className="page-subtitle" style={{ display: 'block', fontSize: '0.68rem' }}>
                                            rookie-scale years (#{r.draft_pick} pick, {r.draft_year} draft)
                                        </span>
                                    )}
                                </td>
                                <td>{money(r.salary)}</td>
                                <td>
                                    {r.war?.toFixed(1)}
                                    <span className="page-subtitle" style={{ display: 'block', fontSize: '0.68rem' }}>VORP {r.vorp?.toFixed(1)}</span>
                                </td>
                                <td>{money(r.fair_value)}</td>
                                <td style={{ fontWeight: 700, color: r.surplus >= 0 ? 'var(--positive)' : 'var(--negative)' }}>{signedMoney(r.surplus)}</td>
                                <td>{Math.round(r.minutes).toLocaleString()}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {rows.length === 0 && <p className="empty-message">No players meet this list's threshold this season.</p>}
            </div>
        </div>
    );
}

export default function ContractValueSection() {
    const [season, setSeason] = useState(null);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        Promise.resolve().then(() => {
            if (!active) return;
            setLoading(true);
            fetchContractValue(season)
                .then((d) => { if (active) { setData(d); setError(''); } })
                .catch((e) => { if (active) setError(e?.response?.data?.detail || 'Could not load contract values.'); })
                .finally(() => { if (active) setLoading(false); });
        });
        return () => { active = false; };
    }, [season]);

    const s = data?.summary;

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Contract Value
                    <InfoTooltip label="How this works" title="Real on-court value in dollars vs. real salary">
                        {data?.methodology || 'Loading methodology…'}
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
                </h3>
                <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                    What each player's real production was worth at that season's real price of a win, against what he was
                    really paid — the bargains and the cap liabilities.
                </p>
                <label className="page-subtitle" style={{ display: 'inline-block', marginTop: '0.5rem' }}>
                    Season:{' '}
                    <select value={data?.season ?? ''} onChange={(e) => setSeason(Number(e.target.value))} disabled={!data}>
                        {(data?.seasons_available || []).map((x) => <option key={x} value={x}>{seasonLabel(x)}</option>)}
                    </select>
                </label>
                {s && (
                    <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', alignItems: 'baseline', marginTop: '1rem' }}>
                        <div>
                            <div className="page-subtitle" style={{ fontSize: '0.75rem' }}>Real cost per win, {seasonLabel(s.season)}</div>
                            <div style={{ fontSize: '2rem', fontWeight: 800 }}>{money(s.cost_per_win, 2)}</div>
                        </div>
                        <p className="page-subtitle" style={{ fontSize: '0.8rem', margin: 0, flex: '1 1 320px' }}>
                            = ({money(s.oncourt_payroll)} real salaries of {s.n_paid_players} players who played − {s.n_paid_players} ×{' '}
                            {money(s.min_salary_0yr, 3)} league minimum) ÷ ({Math.round(s.wins).toLocaleString()} real wins −{' '}
                            {Math.round(s.replacement_wins).toLocaleString()} a .200 team would win). Salaries matched for{' '}
                            {(s.minutes_coverage * 100).toFixed(1)}% of the season's real minutes.
                        </p>
                    </div>
                )}
                {error && <p className="error-message">{error}</p>}
            </div>

            {s && (
                <AboutModelDrawer title="Methods, sources & caveats">
                    <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
                        <li><strong>Surplus</strong> = fair value − real salary; <strong>fair value</strong> = max(WAR, 0) × cost per win + league minimum.</li>
                        <li>
                            <strong>WAR</strong> = VORP × 2.7 (Basketball-Reference's conversion) on Basketball-Reference's published VORP,
                            then scaled by {s.war_scale_k.toFixed(3)} this season: raw positive WAR summed to {s.war_to_real_wins_ratio.toFixed(2)}×
                            the real {Math.round(s.wins_above_replacement)} league wins above replacement, so it's rescaled to match them.
                        </li>
                        <li><strong>League minimum</strong> ({money(s.min_salary_0yr, 3)}, 0 years of service): {s.min_salary_source}.</li>
                        <li>
                            <strong>Salaries:</strong> two public Kaggle datasets built from HoopsHype and Basketball-Reference (no license stated),
                            checked against real contracts. Seasons 2020-21 to 2023-24 are left out (that data is inflation-adjusted, not what
                            players were paid){data.seasons_excluded_low_coverage.length > 0 && (
                                <>, and {data.seasons_excluded_low_coverage.map(seasonLabel).join(', ')} because too few players' salaries are on file</>
                            )}. Some individual rows are wrong upstream and can't all be caught.
                        </li>
                        <li>
                            Only players who actually played are priced, because the salary data repeats stale rows for retired players —
                            so cost per win is the price of on-court production, not total team spending (dead money and injured players aren't in it).
                        </li>
                        <li>
                            Cap liabilities list only players paid {money(data.thresholds.liability_min_salary, 0)}+; bargains need{' '}
                            {data.thresholds.bargain_min_minutes}+ real minutes. "Rookie-scale years" = a real first-round pick in his first four
                            seasons (real draft data); no minimum-contract label is inferred.
                        </li>
                    </ul>
                </AboutModelDrawer>
            )}

            {loading && <Loader />}

            {data && !loading && (
                <>
                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h4 className="section-heading" style={{ marginTop: 0 }}>Salary vs. WAR — {seasonLabel(data.season)}</h4>
                        <Scatter points={data.points} costPerWin={s.cost_per_win} minSalary={s.min_salary_0yr}
                            minMinutes={data.thresholds.bargain_min_minutes} exportName={`salary vs WAR ${seasonLabel(data.season)}`} />
                    </div>
                    <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginTop: '1rem' }}>
                        <ValueTable title="Top 10 bargains"
                            tooltip={`Largest real surplus (fair value − real salary) among players with ${data.thresholds.bargain_min_minutes}+ real minutes.`}
                            rows={data.bargains} />
                        <ValueTable title={`Top 10 cap liabilities (paid ${money(data.thresholds.liability_min_salary, 0)}+)`}
                            tooltip="Most negative real surplus among players paid at least $25M that season."
                            rows={data.liabilities} />
                    </div>
                </>
            )}
        </div>
    );
}
