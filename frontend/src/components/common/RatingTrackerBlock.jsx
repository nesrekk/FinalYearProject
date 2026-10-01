import React, { useEffect, useMemo, useRef, useState } from 'react';
import ChartExport from './ChartExport';
import ChartTooltip from './ChartTooltip';
import InfoTooltip from './InfoTooltip';
import TableExport from './TableExport';
import useChartCrosshair from '../../utils/useChartCrosshair';
import '../../styles/tracker.css';

// Rating Tracker (round 6 step 7): a player's RAPM carried across seasons,
// from the `rating_tracker` block of GET /player-profile/{id}
// (player_rating_tracker, both kinds). The chart draws the with-hindsight
// estimate as a line with its 95% band, the as-of-then estimate as dots
// with whiskers, and BPM as a dashed reference; the table has the numbers.
// Reused by nothing else yet; the RAPM page's tracker version has its own
// leaderboard view.

const label = (s) => `${s - 1}-${String(s).slice(-2)}`;
const signed = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}`);
const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const tone = (v) => (v == null || v === 0 ? '' : v > 0 ? 'pp-pos' : 'pp-neg');

function useWidth(initial = 560) {
    const ref = useRef(null);
    const [W, setW] = useState(initial);
    useEffect(() => {
        const el = ref.current;
        if (!el || typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(([entry]) => {
            const w = Math.round(entry.contentRect.width);
            if (w > 0) setW(Math.max(280, w));
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);
    return [ref, W];
}

const M = { l: 44, r: 16, t: 16, b: 36 };

export function RatingTrackerChart({ seasons, name, title }) {
    const [boxRef, W] = useWidth();
    const svgRef = useRef(null);
    const H = W < 480 ? 240 : 280;
    const xs = seasons.map((s) => s.season);
    const vals = [];
    seasons.forEach((s) => {
        if (s.smoothed) vals.push(s.smoothed.ci_low, s.smoothed.ci_high);
        if (s.filtered) vals.push(s.filtered.ci_low, s.filtered.ci_high);
        if (s.bpm != null) vals.push(s.bpm);
    });
    const yMin = Math.min(-2, ...vals.filter((v) => v != null));
    const yMax = Math.max(2, ...vals.filter((v) => v != null));
    const pad = (yMax - yMin) * 0.08;
    const x0 = xs[0];
    const x1 = xs[xs.length - 1];
    const sx = (s) => (x1 === x0 ? (M.l + W - M.r) / 2 : M.l + ((s - x0) / (x1 - x0)) * (W - M.l - M.r));
    const sy = (v) => H - M.b - ((v - (yMin - pad)) / (yMax + pad - (yMin - pad))) * (H - M.t - M.b);
    const sm = seasons.filter((s) => s.smoothed);
    const band = sm.length > 1
        ? `${sm.map((s) => `${sx(s.season).toFixed(1)},${sy(s.smoothed.ci_high).toFixed(1)}`).join(' ')} ${[...sm].reverse().map((s) => `${sx(s.season).toFixed(1)},${sy(s.smoothed.ci_low).toFixed(1)}`).join(' ')}`
        : null;
    const line = sm.map((s) => `${sx(s.season).toFixed(1)},${sy(s.smoothed.rapm).toFixed(1)}`).join(' ');
    const bpmPts = seasons.filter((s) => s.bpm != null);
    const bpmLine = bpmPts.map((s) => `${sx(s.season).toFixed(1)},${sy(s.bpm).toFixed(1)}`).join(' ');
    const points = seasons.map((s) => ({ x: sx(s.season), y: sy(s.filtered?.rapm ?? s.smoothed?.rapm ?? 0), s }));
    const { point: hovered, overlayProps } = useChartCrosshair(points, W);
    const yTicks = [];
    const step = yMax - yMin > 12 ? 4 : yMax - yMin > 6 ? 2 : 1;
    for (let v = Math.ceil((yMin - pad) / step) * step; v <= yMax + pad; v += step) yTicks.push(v);
    const aria = `${title}: ${seasons.length} seasons, with hindsight from ${signed(sm[0]?.smoothed.rapm)} in ${label(xs[0])} to ${signed(sm[sm.length - 1]?.smoothed.rapm)} in ${label(xs[xs.length - 1])}.`;
    return (
        <div className="rx-chart rt-chart" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={name} />
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={aria}>
                    {yTicks.map((t) => (
                        <g key={`y${t}`}>
                            <line className="rx-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                            <text className="rx-tick" x={M.l - 8} y={sy(t)} textAnchor="end" dominantBaseline="middle">{signed(t, 0)}</text>
                        </g>
                    ))}
                    {xs.map((s) => (
                        <text key={`x${s}`} className="rx-tick" x={sx(s)} y={H - M.b + 16} textAnchor="middle">{W < 480 ? `'${String(s).slice(-2)}` : label(s)}</text>
                    ))}
                    <line className="rp-zero" x1={M.l} x2={W - M.r} y1={sy(0)} y2={sy(0)} />
                    <text className="rx-axis" transform={`translate(12 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle">per 100 poss.</text>
                    {band && <polygon className="rt-band" points={band} />}
                    {sm.length > 1 && <polyline className="rt-line" points={line} />}
                    {bpmPts.length > 1 && <polyline className="rt-bpm" points={bpmLine} />}
                    {seasons.map((s) => s.filtered && (
                        <g key={`f${s.season}`}>
                            <line className="rt-whisker" x1={sx(s.season)} x2={sx(s.season)} y1={sy(s.filtered.ci_low)} y2={sy(s.filtered.ci_high)} />
                            <circle className={`rt-dot ${s.filtered.qualified ? '' : 'rt-dot--short'}`} cx={sx(s.season)} cy={sy(s.filtered.rapm)} r={hovered?.s.season === s.season ? 5.5 : 4} />
                        </g>
                    ))}
                    {hovered && <line x1={hovered.x} y1={M.t} x2={hovered.x} y2={H - M.b} className="chart-crosshair-line" />}
                    <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b} className="chart-crosshair-overlay"
                        role="slider" aria-label={`${title}, use arrow keys to step through seasons`}
                        aria-valuetext={hovered ? `${label(hovered.s.season)}: as of then ${signed(hovered.s.filtered?.rapm)}, with hindsight ${signed(hovered.s.smoothed?.rapm)}` : undefined}
                        {...overlayProps} />
                </svg>
                {hovered && (
                    <ChartTooltip x={hovered.x} y={hovered.y} chartWidth={W} chartHeight={H}>
                        <div style={{ fontWeight: 600 }}>{label(hovered.s.season)}{hovered.s.teams ? ` · ${hovered.s.teams}` : ''}</div>
                        {hovered.s.filtered && <div>As of then {signed(hovered.s.filtered.rapm)} ± {num(hovered.s.filtered.rapm_sd)}{hovered.s.filtered.rank ? ` (#${hovered.s.filtered.rank})` : ''}</div>}
                        {hovered.s.smoothed && <div>With hindsight {signed(hovered.s.smoothed.rapm)} ± {num(hovered.s.smoothed.rapm_sd)}</div>}
                        <div style={{ color: 'var(--text-3)' }}>Carried in {signed(hovered.s.filtered?.carried)} · BPM {signed(hovered.s.bpm)}</div>
                    </ChartTooltip>
                )}
            </div>
            <div className="rp-legend rt-legend">
                <span className="rt-legend--band">with hindsight (95% band)</span>
                <span className="rt-legend--dot">as of then (95% whisker)</span>
                <span className="rt-legend--bpm">BPM</span>
            </div>
        </div>
    );
}

function seasonsFromRows(rows) {
    const by = new Map();
    rows.forEach((r) => {
        if (!by.has(r.season)) by.set(r.season, { season: r.season, teams: r.teams, poss: r.poss, bpm: r.bpm });
        by.get(r.season)[r.kind] = r;
    });
    return [...by.values()].sort((a, b) => a.season - b.season);
}

export default function RatingTrackerBlock({ block, coverage, onNavigate, Section }) {
    const seasons = useMemo(() => seasonsFromRows(block.rows), [block.rows]);
    const last = seasons[seasons.length - 1];
    const span = coverage?.from ? `${label(coverage.from)} to ${label(coverage.to)}` : '';
    const fit = block.fit;
    return (
        <Section id="tracker" title="Rating Tracker"
            info={(
                <InfoTooltip label="How the Rating Tracker is computed" title="RAPM that carries across seasons">
                    The same stints as RAPM, but his offence and defence ratings are a hidden state that carries from season to
                    season: between seasons it keeps {fit ? `${Math.round(fit.phi * 100)}%` : 'part'} of itself and gains drift,
                    each season&apos;s games update it, and that season&apos;s BPM is read as a noisy measurement. &quot;As of then&quot;
                    uses nothing after the season (what a forecast could have used); &quot;with hindsight&quot; smooths every later
                    season back through the career. The ± is a posterior standard deviation; &quot;carried in&quot; is what the
                    tracker expected before the season started.
                </InfoTooltip>
            )}
            meta={<>Stints cover {span}; ranks among players with {block.qualified_poss?.toLocaleString()}+ possessions that season
                (as of then). Seasons under the floor are greyed.{' '}
                {onNavigate && <button type="button" className="pp-link" onClick={() => onNavigate('rapm', null, { version: 'tracker', season: last?.season })}>Open the Rating Tracker</button>}</>}>
            <RatingTrackerChart seasons={seasons} name="rating tracker career" title="Rating Tracker" />
            <TableExport />
            <div className="table-wrapper">
                <table className="data-table lb-table pp-table">
                    <thead>
                        <tr><th>Season</th><th>Team</th><th className="lb-num">Poss</th>
                            <th className="lb-num" title="Carried into the season: what the tracker expected before any game">Carried in</th>
                            <th className="lb-num" title="The posterior after that season's games, using nothing later">As of then</th>
                            <th className="lb-num" title="Offence / defence, as of then">O / D</th>
                            <th className="lb-num" title="Every season's games weigh in, later ones included">With hindsight</th>
                            <th className="lb-num">BPM</th></tr>
                    </thead>
                    <tbody>
                        {seasons.map((s) => {
                            const f = s.filtered;
                            const m = s.smoothed;
                            const q = f?.qualified ?? m?.qualified;
                            return (
                                <tr key={s.season} className={q ? undefined : 'sl-short'}
                                    title={q ? undefined : `Under ${block.qualified_poss} possessions: treat as noise`}>
                                    <td>{label(s.season)}</td>
                                    <td>{s.teams}</td>
                                    <td className="lb-num">{s.poss == null ? '—' : Math.round(s.poss).toLocaleString()}</td>
                                    <td className={`lb-num ${tone(f?.carried)}`}>{f ? (f.seasons_seen > 1 ? signed(f.carried) : 'new') : '—'}</td>
                                    <td className={`lb-num lb-stat ${tone(f?.rapm)}`}>{f ? <>{signed(f.rapm)} ± {num(f.rapm_sd)}{f.rank ? ` (#${f.rank})` : ''}</> : '—'}</td>
                                    <td className="lb-num">{f ? `${signed(f.orapm)} / ${signed(f.drapm)}` : '—'}</td>
                                    <td className={`lb-num ${tone(m?.rapm)}`}>{m ? <>{signed(m.rapm)} ± {num(m.rapm_sd)}</> : '—'}</td>
                                    <td className="lb-num">{signed(s.bpm)}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle pp-foot">
                Points per 100 possessions, the same units as RAPM and BPM. Steadier than one-season RAPM because last season is
                part of the estimate{fit ? ` (drift between seasons about ±${num(fit.drift_sd)} per side; a newcomer starts at average ±${num(fit.newcomer_sd)})` : ''};
                the with-hindsight line is the best guess of what he was, not a forecast. Descriptive of those minutes, not adjusted for health or role.
            </p>
        </Section>
    );
}
