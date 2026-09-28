import React, { useEffect, useRef, useState } from 'react';
import { fetchStatStability } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import ChartExport from '../common/ChartExport';
import ChartTooltip from '../common/ChartTooltip';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import useChartCrosshair from '../../utils/useChartCrosshair';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/stability.css';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
// Sample sizes: whole numbers once they're big, one decimal below 100.
const fmtN = (v) => (v == null ? '—' : v >= 100 || Number.isInteger(v) ? Math.round(v).toLocaleString() : v.toFixed(1));
const fmtR = (v) => (v == null ? '—' : v.toFixed(2));
const VARIANTS = { catalogue: 'split_half', per_minute: 'per_minute' };
const TYPICAL_NOTE = '50+ games'; // build_stat_stability.py TYPICAL_GAMES

// ─── Reliability curve (hand-built SVG, like the rest of the app's charts) ──
const M = { l: 48, r: 16, t: 14, b: 46 };

function niceTicks(max, count) {
    const step0 = max / count;
    const mag = 10 ** Math.floor(Math.log10(step0));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) ?? 10 * mag;
    const ticks = [];
    for (let v = 0; v <= max + step * 1e-9; v += step) ticks.push(Number(v.toFixed(10)));
    return ticks;
}

function ReliabilityCurve({ est, label }) {
    const boxRef = useRef(null);
    const svgRef = useRef(null);
    const [W, setW] = useState(720);
    const H = W < 520 ? 300 : 360;
    useEffect(() => {
        const el = boxRef.current;
        if (!el || typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(([entry]) => {
            const w = Math.round(entry.contentRect.width);
            if (w > 0) setW(Math.max(280, w));
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

    const m = est.stable_n;
    const xMax = Math.max(...est.curve.map((p) => p.n_half), m * 1.6, Math.min(est.typical_n ?? 0, m * 6)) * 1.05;
    const sx = (v) => M.l + (v / xMax) * (W - M.l - M.r);
    const sy = (v) => H - M.b - Math.max(0, Math.min(1, v)) * (H - M.t - M.b);
    const fitted = Array.from({ length: 81 }, (_, i) => {
        const n = (xMax * i) / 80;
        return `${sx(n).toFixed(1)},${sy(n / (n + m)).toFixed(1)}`;
    }).join(' ');
    const typicalShown = est.typical_n != null && est.typical_n <= xMax;
    const summary = `${label}: reliability against sample size in ${est.unit_label}. Half signal at ${fmtN(m)} ${est.unit_label}.`;

    const crosshairPoints = est.curve.map((p) => ({ x: sx(p.n_half), y: sy(p.r), p }));
    const { point: hovered, overlayProps } = useChartCrosshair(crosshairPoints, W);

    return (
        <div className="rx-chart ss-chart" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={`${label} reliability curve`} />
            <div style={{ position: 'relative' }}>
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary}>
                {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                    <g key={`y${t}`}>
                        <line className="rx-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                        <text className="rx-tick" x={M.l - 8} y={sy(t)} textAnchor="end" dominantBaseline="middle">{t.toFixed(2)}</text>
                    </g>
                ))}
                {niceTicks(xMax, W < 520 ? 4 : 6).map((t) => (
                    <text key={`x${t}`} className="rx-tick" x={sx(t)} y={H - M.b + 16} textAnchor="middle">{fmtN(t)}</text>
                ))}
                <text className="rx-axis" x={(M.l + W - M.r) / 2} y={H - 8} textAnchor="middle">Sample ({est.unit_label})</text>
                <text className="rx-axis" transform={`translate(12 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle">Reliability</text>
                <line className="ss-half" x1={M.l} x2={W - M.r} y1={sy(0.5)} y2={sy(0.5)} />
                <line className="ss-half" x1={sx(m)} x2={sx(m)} y1={sy(0)} y2={sy(0.5)} />
                <text className="ss-mark" x={sx(m) + 6} y={sy(0.5) - 8}>Half signal: {fmtN(m)}</text>
                {typicalShown && (
                    <g>
                        <line className="ss-typical" x1={sx(est.typical_n)} x2={sx(est.typical_n)} y1={sy(0)} y2={sy(1)} />
                        <text className="ss-mark" x={sx(est.typical_n) - 6} y={sy(1) + 12} textAnchor="end">Typical season</text>
                    </g>
                )}
                {hovered && <line x1={hovered.x} y1={M.t} x2={hovered.x} y2={H - M.b} className="chart-crosshair-line" />}
                <polyline className="rx-fit ss-fit" points={fitted} />
                <g className="ss-points">
                    {est.curve.map((p) => (
                        <circle key={p.n} cx={sx(p.n_half)} cy={sy(p.r)} r={hovered?.p === p ? (p.pool >= 100 ? 7 : 5.5) : (p.pool >= 100 ? 5 : 3.5)}
                            className={p.pool >= 100 ? '' : 'ss-thin'} style={{ pointerEvents: 'none' }} />
                    ))}
                </g>
                <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b}
                    className="chart-crosshair-overlay" role="slider" aria-label={`${label} reliability curve, use arrow keys to step through`}
                    aria-valuetext={hovered ? `${fmtN(hovered.p.n_half)} ${est.unit_label} per half: r = ${hovered.p.r.toFixed(2)}` : undefined}
                    {...overlayProps} />
            </svg>
            {hovered && (
                <ChartTooltip x={hovered.x} y={hovered.y} chartWidth={W} chartHeight={H}>
                    <div style={{ fontWeight: 600 }}>{fmtN(hovered.p.n_half)} {est.unit_label} per half</div>
                    <div>r = {hovered.p.r.toFixed(2)}</div>
                    <div style={{ color: 'var(--text-3)' }}>{hovered.p.pool.toLocaleString()} player-seasons</div>
                </ChartTooltip>
            )}
            </div>
        </div>
    );
}

function Estimate({ est, missing = 'season totals only' }) {
    if (!est) return <span className="ss-none">{missing}</span>;
    return <>{fmtN(est.stable_n)} <span className="ss-unit">{est.unit_label}</span></>;
}

export default function StatStability() {
    const params = useInitialParams();
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [pick, setPick] = useState(() => ({
        stat: parseParam.str(params, 'stat'),
        variant: parseParam.oneOf(params, 'v', Object.keys(VARIANTS)) ?? 'catalogue',
    }));

    useEffect(() => {
        fetchStatStability()
            .then(setData)
            .catch((err) => setError(err.response?.data?.detail
                || 'Stat stability couldn\'t load. Is the impact API (port 8002) running?'));
    }, []);

    const withSplit = data ? data.stats.filter((s) => s.split_half) : [];
    const selected = data
        ? (withSplit.find((s) => s.key === pick.stat) ?? withSplit.find((s) => s.key === 'fg3_pct') ?? withSplit[0])
        : null;
    const variant = selected?.[VARIANTS[pick.variant]] ? pick.variant : 'catalogue';
    const est = selected?.[VARIANTS[variant]];
    useUrlSync(selected && { stat: selected.key, v: variant === 'catalogue' ? null : variant });

    if (error) return <section className="dashboard-card"><p className="error-message">{error}</p></section>;
    if (!data) return <Loader />;

    const byKey = Object.fromEntries(data.stats.map((s) => [s.key, s]));
    const three = byKey.fg3_pct?.split_half;
    const ft = byKey.ft_pct?.split_half;
    const reb = byKey.reb_pct?.split_half;
    const groups = [...new Set(data.stats.map((s) => s.group))];
    const choose = (key) => setPick((p) => ({ ...p, stat: key }));

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                When does a stat mean something?
                <InfoTooltip label="How stat stability is measured" title="Under the hood">
                    {data.method} Per-game data: play-by-play for every regular-season game from{' '}
                    {seasonLabel(byKey.pts.split_half.season_from)} to {seasonLabel(byKey.pts.split_half.season_to)},
                    rebuilt into player lines that match NBA.com&apos;s season totals within 1.5%; FG%, 3P% and eFG% use
                    every shot from {seasonLabel(byKey.fg_pct.split_half.season_from)} on. Built {data.built_on}.
                </InfoTooltip>
                <SourceBadge source={data._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="stability" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                How big a sample each stat needs before a player&apos;s number says more about him than about luck.
                The sample where it&apos;s half and half is the number to remember.
            </p>

            {three && ft && reb && (
                <p className="rx-verdict">
                    <strong>3-point % is mostly noise for a season.</strong> It takes about{' '}
                    <strong>{fmtN(three.stable_n)} attempts</strong> (95% range {fmtN(three.ci[0])} to {fmtN(three.ci[1])})
                    before a player&apos;s 3P% is half skill; a typical rotation player&apos;s season ({fmtN(three.typical_n)}{' '}
                    attempts) gets to {fmtR(three.typical_reliability)}. Free-throw % gets there in{' '}
                    <strong>{fmtN(ft.stable_n)} attempts</strong> and rebound % in <strong>{fmtN(reb.stable_n)} rebound
                    chances</strong>. Published estimates for 3P% run from 242 attempts{' '}
                    (<a className="ss-link" href="https://kmedved.com/2020/08/06/nba-stabilization-rates-and-the-padding-approach/" target="_blank" rel="noreferrer">Medvedovsky, 2020</a>)
                    to 750 (Blackport, Nylon Calculus, 2014, as cited by{' '}
                    <a className="ss-link" href="https://www.theringer.com/2019/11/22/nba/chicago-bulls-brooklyn-nets-new-orleans-pelicans" target="_blank" rel="noreferrer">The Ringer</a>);
                    each used a different method and pool of players.
                </p>
            )}

            <div className="ss-picked">
                <div className="ss-picked-head">
                    <h3 className="ss-picked-title">{selected.label}</h3>
                    {selected.per_minute && (
                        <div className="tab-bar lb-modes" role="tablist" aria-label="Per game or per minute">
                            {[['catalogue', 'Per game'], ['per_minute', 'Per minute']].map(([id, lab]) => (
                                <button key={id} type="button" role="tab" aria-selected={variant === id}
                                    className={`tab-btn ${variant === id ? 'tab-btn--active' : ''}`}
                                    onClick={() => setPick((p) => ({ ...p, variant: id }))}>
                                    {lab}
                                </button>
                            ))}
                        </div>
                    )}
                    <label className="ss-select">
                        <span>Stat</span>
                        <select className="input-field" value={selected.key} onChange={(e) => choose(e.target.value)}>
                            {groups.map((g) => (
                                <optgroup key={g} label={g}>
                                    {withSplit.filter((s) => s.group === g).map((s) => (
                                        <option key={s.key} value={s.key}>{s.label}</option>
                                    ))}
                                </optgroup>
                            ))}
                        </select>
                    </label>
                </div>
                <p className="ss-picked-line">
                    Half signal at <strong>{fmtN(est.stable_n)} {est.unit_label}</strong> (95% range {fmtN(est.ci[0])}
                    {' '}to {fmtN(est.ci[1])}).
                    {est.typical_reliability != null && (
                        <> A typical rotation player&apos;s season ({TYPICAL_NOTE}: {fmtN(est.typical_n)} {est.unit_label})
                            {' '}reaches <strong>{fmtR(est.typical_reliability)}</strong>.</>
                    )}
                    {est.per_target_range[0] != null && est.per_target_range[1] > est.per_target_range[0] * 1.5 && (
                        <span className="lb-note"> Estimated from each sample size alone it ranges from{' '}
                            {fmtN(est.per_target_range[0])} to {fmtN(est.per_target_range[1])}: the pool at big samples
                            is only high-volume players, who are more alike.</span>
                    )}
                    {variant === 'catalogue' && selected.per_minute && (
                        <span className="lb-note"> Per-game numbers settle within a game or two mostly because minutes
                            and role barely change; the per-minute view measures the skill itself.</span>
                    )}
                    {['off_rating', 'def_rating', 'net_rating', 'plus_minus'].includes(selected.key) && (
                        <span className="lb-note"> On-court ratings are team results while he plays: much of what
                            stays stable is his teammates and coach, not him.</span>
                    )}
                </p>
                <ReliabilityCurve est={est} label={selected.label} />
                <p className="ss-legend">
                    Each dot: player-seasons whose odd and even games both reached that sample, and how well the two
                    halves agree (larger dots: 100+ player-seasons, used in the fit). Line: reliability = n / (n +{' '}
                    {fmtN(est.stable_n)}). Data: {est.source === 'player_shots' ? 'shot charts' : 'play-by-play'},{' '}
                    {seasonLabel(est.season_from)} to {seasonLabel(est.season_to)}.
                </p>
            </div>

            <TableExport name="stat stability" />
            <div className="table-wrapper">
                <table className="data-table lb-table ss-table">
                    <thead>
                        <tr>
                            <th>Stat</th>
                            <th className="lb-num">Half signal at</th>
                            <th className="lb-num">95% range</th>
                            <th className="lb-num">Typical season</th>
                            <th className="lb-num">Per minute: half signal at</th>
                            <th className="lb-num">Year to year r</th>
                            <th className="lb-num">Pairs</th>
                        </tr>
                    </thead>
                    <tbody>
                        {groups.map((g) => (
                            <React.Fragment key={g}>
                                <tr className="ss-group"><th colSpan={7} scope="colgroup">{g}</th></tr>
                                {data.stats.filter((s) => s.group === g).map((s) => {
                                    const sh = s.split_half;
                                    const on = s.key === selected.key;
                                    return (
                                        <tr key={s.key} className={on ? 'ss-on' : undefined}>
                                            <td>
                                                {sh ? (
                                                    <button type="button" className="ss-pick" aria-pressed={on}
                                                        onClick={() => choose(s.key)}>{s.label}</button>
                                                ) : s.label}
                                            </td>
                                            <td className="lb-num"><Estimate est={sh} /></td>
                                            <td className="lb-num">{sh ? `${fmtN(sh.ci[0])}–${fmtN(sh.ci[1])}` : '—'}</td>
                                            <td className="lb-num">
                                                {sh?.typical_reliability != null ? (
                                                    <span className={sh.typical_reliability < data.reliable_at ? 'ss-low' : undefined}>
                                                        {fmtR(sh.typical_reliability)}
                                                    </span>
                                                ) : '—'}
                                            </td>
                                            <td className="lb-num"><Estimate est={s.per_minute} missing="—" /></td>
                                            <td className="lb-num">{fmtR(s.year_to_year?.r)}</td>
                                            <td className="lb-num">{s.year_to_year ? s.year_to_year.pairs.toLocaleString() : '—'}</td>
                                        </tr>
                                    );
                                })}
                            </React.Fragment>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="ss-legend">
                Typical season = reliability of the median season among players with {TYPICAL_NOTE}. Year to year r = the
                correlation between a player&apos;s value in one season and the next ({byKey.pts.year_to_year?.floors}),
                centred by season. It mixes noise with real change (age, role, team), so it runs lower than split-half
                reliability, and it&apos;s the only measure for BPM, VORP and impact score, which exist only as season
                totals. Per-game and play-by-play estimates come from {seasonLabel(byKey.pts.split_half.season_from)}{' '}
                to {seasonLabel(byKey.pts.split_half.season_to)}; applying them to other eras assumes a similar spread of
                players. The Leaderboard Builder and Breakout Detector grey out numbers under {data.reliable_at} reliability.
            </p>
        </section>
    );
}
