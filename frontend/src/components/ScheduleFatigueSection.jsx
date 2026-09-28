import React, { useEffect, useRef, useState } from 'react';
import { fetchRestStudy, fetchScheduleDifficulty } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import TeamLogo from './common/TeamLogo';
import TeamLink from './common/TeamLink';
import TableExport from './common/TableExport';
import ChartExport from './common/ChartExport';
import ChartTooltip from './common/ChartTooltip';

const CHART_W = 480;
const CHART_H = 220;
const PAD_L = 46;
const PAD_R = 16;
const PAD_T = 16;
const PAD_B = 30;
const PLOT_W = CHART_W - PAD_L - PAD_R;
const PLOT_H = CHART_H - PAD_T - PAD_B;

function RestBucketChart({ buckets }) {
    const svgRef = useRef(null);
    const [hovered, setHovered] = useState(null);
    if (!buckets?.length) return null;
    const vals = buckets.map((b) => b.win_pct);
    const vMin = Math.min(...vals, 0.4);
    const vMax = Math.max(...vals, 0.6);
    const x = (i) => PAD_L + (i / (buckets.length - 1 || 1)) * PLOT_W;
    const y = (v) => PAD_T + (1 - (v - vMin) / ((vMax - vMin) || 1)) * PLOT_H;
    const path = buckets.map((b, i) => `${i === 0 ? 'M' : 'L'} ${x(i).toFixed(1)} ${y(b.win_pct).toFixed(1)}`).join(' ');
    const maxN = Math.max(...buckets.map((b) => b.n));

    return (
        <div>
            <ChartExport svgRef={svgRef} name="win percent by days of rest" />
            <div style={{ position: 'relative' }}>
            <svg ref={svgRef} viewBox={`0 0 ${CHART_W} ${CHART_H}`} style={{ width: '100%', display: 'block' }} role="img" aria-label="Line chart of real team win percentage by days of rest before the game, with dot size showing sample size in each rest bucket">
                <rect x="0" y="0" width={CHART_W} height={CHART_H} fill="var(--surface-2)" rx="8" />
                <line x1={PAD_L} y1={y(0.5)} x2={CHART_W - PAD_R} y2={y(0.5)} stroke="var(--hairline)" strokeWidth="1" strokeDasharray="4 3" />
                <path d={path} fill="none" stroke="var(--accent)" strokeWidth="2.5" />
                {buckets.map((b, i) => {
                    const r = 4 + 4 * Math.sqrt(b.n / maxN);
                    return (
                        <circle
                            key={b.rest_days} cx={x(i)} cy={y(b.win_pct)}
                            r={hovered?.rest_days === b.rest_days ? r + 2 : r} fill="var(--accent)"
                            style={{ cursor: 'pointer' }}
                            tabIndex={0}
                            onMouseEnter={() => setHovered({ ...b, x: x(i), y: y(b.win_pct) })}
                            onMouseLeave={() => setHovered((h) => (h?.rest_days === b.rest_days ? null : h))}
                            onFocus={() => setHovered({ ...b, x: x(i), y: y(b.win_pct) })}
                            onBlur={() => setHovered((h) => (h?.rest_days === b.rest_days ? null : h))}
                        />
                    );
                })}
                {buckets.map((b, i) => (
                    <text key={b.rest_days} x={x(i)} y={CHART_H - 8} fill="var(--text-3)" fontSize="9" textAnchor="middle">{b.bucket_label}</text>
                ))}
                <text x="14" y={CHART_H / 2} fill="var(--text-2)" fontSize="10" textAnchor="middle" transform={`rotate(-90 14 ${CHART_H / 2})`}>Win %</text>
            </svg>
            {hovered && (
                <ChartTooltip x={hovered.x} y={hovered.y} chartWidth={CHART_W} chartHeight={CHART_H}>
                    <div style={{ fontWeight: 600 }}>{hovered.bucket_label}</div>
                    <div>{(hovered.win_pct * 100).toFixed(1)}% win rate</div>
                    <div style={{ color: 'var(--text-3)' }}>n = {hovered.n.toLocaleString()} games</div>
                </ChartTooltip>
            )}
            </div>
        </div>
    );
}

export default function ScheduleFatigueSection() {
    const [restStudy, setRestStudy] = useState(null);
    const [restError, setRestError] = useState('');
    const [difficulty, setDifficulty] = useState(null);
    const [difficultyError, setDifficultyError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let active = true;
        Promise.all([
            fetchRestStudy().catch((e) => { throw { kind: 'rest', e }; }),
            fetchScheduleDifficulty().catch((e) => { throw { kind: 'difficulty', e }; }),
        ])
            .then(([rest, diff]) => {
                if (!active) return;
                setRestStudy(rest);
                setDifficulty(diff);
            })
            .catch(() => {
                // Fall back to fetching independently so one failure doesn't blank both.
                if (!active) return;
                fetchRestStudy().then((r) => active && setRestStudy(r)).catch((e) => active && setRestError(e?.response?.data?.detail || 'Could not load the rest study.'));
                fetchScheduleDifficulty().then((d) => active && setDifficulty(d)).catch((e) => active && setDifficultyError(e?.response?.data?.detail || 'Could not load schedule difficulty.'));
            })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, []);

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Schedule Fatigue
                    <InfoTooltip label="How this works" title="Real schedule data, not a model">
                        Real rest days, real back-to-backs, and real travel miles (haversine between each real
                        consecutive game's real arena location) for every real team game, computed by
                        scripts/build_schedule_fatigue.py from the NBA's own real game logs. The rest-vs-win%
                        chart and the schedule-difficulty ranking are both real historical aggregation — nothing
                        modeled or predicted. This data is a snapshot from whenever that script last ran, not a
                        live feed, so very recent real games may not be reflected yet.
                    </InfoTooltip>
                    <SourceBadge source={restStudy?._source || difficulty?._source} />
                </h3>
            </div>

            {loading && <Loader />}

            <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                <h3 className="section-heading" style={{ marginTop: 0 }}>Win % by Real Rest</h3>
                {restError && <p className="error-message">{restError}</p>}
                {restStudy && (
                    <>
                        <p className="page-subtitle" style={{ marginTop: '-0.25rem', marginBottom: '0.75rem' }}>
                            Real win rate by real days of rest before the game, across every real team-game on file.
                            Dot size = real sample size.
                        </p>
                        <RestBucketChart buckets={restStudy.buckets} />
                        <TableExport />
                        <div className="table-wrapper" style={{ marginTop: '1rem' }}>
                            <table className="data-table">
                                <thead>
                                    <tr><th>Rest</th><th>Games (n)</th><th>Win %</th><th>Avg Point Diff</th></tr>
                                </thead>
                                <tbody>
                                    {restStudy.buckets.map((b) => (
                                        <tr key={b.rest_days}>
                                            <td>{b.bucket_label}</td>
                                            <td>{b.n.toLocaleString()}</td>
                                            <td>{(b.win_pct * 100).toFixed(1)}%</td>
                                            <td style={{ color: b.avg_point_diff >= 0 ? 'var(--positive)' : 'var(--negative)' }}>
                                                {b.avg_point_diff >= 0 ? '+' : ''}{b.avg_point_diff}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}
            </div>

            <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Team Schedule Difficulty {difficulty ? `— ${difficulty.season - 1}-${String(difficulty.season).slice(-2)}` : ''}
                </h3>
                {difficultyError && <p className="error-message">{difficultyError}</p>}
                {difficulty && (
                    <>
                        <TableExport />
                        <div className="table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Rank</th><th>Team</th><th>Games</th>
                                        <th>Total Travel Miles</th><th>B2Bs</th><th>4+ Games in 7 Days</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {difficulty.results.map((r) => (
                                        <tr key={r.team_abbreviation}>
                                            <td>{r.rank}</td>
                                            <td>
                                                <TeamLink abbr={r.team_abbreviation} season={difficulty?.season} className="entity-row">
                                                    <TeamLogo abbreviation={r.team_abbreviation} size={20} />
                                                    <span>{r.team_abbreviation}</span>
                                                </TeamLink>
                                            </td>
                                            <td>{r.n_games}</td>
                                            <td>{r.total_travel_miles?.toLocaleString() ?? '—'}</td>
                                            <td>{r.b2b_count}</td>
                                            <td>{r.games_with_4plus_in_7days}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
