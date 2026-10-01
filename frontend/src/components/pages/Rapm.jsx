import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchRapm, fetchRapmOptions } from '../../services/api';
import Loader from '../Loader';
import ChartExport from '../common/ChartExport';
import ChartTooltip from '../common/ChartTooltip';
import CopyLinkButton from '../common/CopyLinkButton';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import useChartCrosshair from '../../utils/useChartCrosshair';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/rapm.css';
import '../../styles/tracker.css';

// RAPM (?page=rapm, GET /rapm): regularized adjusted plus-minus for every
// player from the play-by-play stints, in three versions (one season, a
// three-season window, one season shrunk toward BPM), with bootstrap error
// bars, the cross-validation curve that picked the shrinkage, and the
// held-out tests against BPM, on/off and "everyone average". A fourth
// version, the Rating Tracker (round 6 step 7), carries ratings across
// seasons: posterior sds instead of bootstrap errors, a drift profile
// instead of the shrinkage curve, "as of then" or "with hindsight" (kind).
// Link: version, season, kind, team, min (possessions), sort, dir.

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const signed = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}`);
const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const int = (v) => (v == null ? '—' : Math.round(v).toLocaleString());
const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
const tone = (v) => (v == null || v === 0 ? '' : v > 0 ? 'oo-pos' : 'oo-neg');
const CI_RANGE = 8; // points per 100 possessions from zero to the bar's edge

const COLUMNS = {
    player_name: { label: 'Player', text: true },
    teams: { label: 'Team', text: true, title: 'Teams in the window, most possessions first' },
    games: { label: 'GP', title: 'Games with a tracked stint' },
    minutes: { label: 'Min', title: 'Minutes in tracked stints' },
    poss: { label: 'Poss', title: 'Possessions, offence and defence averaged' },
    orapm: { label: 'ORAPM', title: 'Points per 100 his team scores more with him on the floor, the other nine held constant' },
    drapm: { label: 'DRAPM', title: 'Points per 100 the opponent scores less, the other nine held constant' },
    rapm: { label: 'RAPM', title: 'ORAPM + DRAPM' },
    bpm: { label: 'BPM', title: 'Basketball-Reference BPM for the same season (minutes-weighted over a window)' },
    carried: { label: 'Carried in', title: 'What the tracker expected before the season started: last season\'s rating times the carry-over (a newcomer starts at average)' },
};
const KINDS = [['filtered', 'As of then'], ['smoothed', 'With hindsight']];

function formFromParams(p, versions) {
    return {
        version: parseParam.oneOf(p, 'version', versions) ?? 'single',
        season: parseParam.int(p, 'season', { min: 2000, max: 2100 }),
        kind: parseParam.oneOf(p, 'kind', KINDS.map((k) => k[0])) ?? 'filtered',
        team: parseParam.str(p, 'team')?.toUpperCase() ?? null,
        minPoss: parseParam.num(p, 'min', { min: 0, max: 20000 }),
        sort: parseParam.oneOf(p, 'sort', Object.keys(COLUMNS)) ?? 'rapm',
        dir: parseParam.oneOf(p, 'dir', ['asc', 'desc']) ?? 'desc',
    };
}

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

// A small range bar: zero line in the middle, the 95% interval as a whisker,
// the estimate as a dot (filled when the interval clears zero).
function CiBar({ est, lo, hi, excludes }) {
    if (est == null) return null;
    const w = 110;
    const h = 16;
    const mid = w / 2;
    const x = (v) => mid + Math.max(-1, Math.min(1, v / CI_RANGE)) * (mid - 5);
    const color = est > 0 ? 'var(--positive)' : est < 0 ? 'var(--negative)' : 'var(--text-3)';
    return (
        <svg className="rp-ci" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" data-export-skip>
            <line x1={mid} x2={mid} y1={2} y2={h - 2} stroke="var(--line)" strokeWidth={1} opacity={0.5} />
            {lo != null && (
                <line x1={x(lo)} x2={x(hi)} y1={h / 2} y2={h / 2} stroke={excludes ? color : 'var(--text-3)'}
                    strokeWidth={2} strokeLinecap="round" />
            )}
            <circle cx={x(est)} cy={h / 2} r={3.5} fill={excludes ? color : 'var(--surface)'} stroke={color} strokeWidth={1.5} />
        </svg>
    );
}

function SortHeader({ colKey, sort, dir, onSort, className }) {
    const c = COLUMNS[colKey];
    const active = sort === colKey;
    return (
        <th className={className} aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'} title={c.title}>
            <button type="button" className={`rp-sort ${active ? 'rp-sort--active' : ''}`} onClick={() => onSort(colKey)}>
                {c.label}{active && <span aria-hidden="true">{dir === 'asc' ? ' ▲' : ' ▼'}</span>}
            </button>
        </th>
    );
}

function sortRows(rows, sort, dir) {
    const text = COLUMNS[sort]?.text;
    const sign = dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
        const va = a[sort];
        const vb = b[sort];
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        return text ? sign * String(va).localeCompare(String(vb)) : sign * (va - vb);
    });
}

// ─── RAPM against BPM: one dot per qualified player ───────────────────
const M = { l: 48, r: 16, t: 16, b: 40 };

function ScatterChart({ rows, season, versionLabel, corr }) {
    const [boxRef, W] = useWidth();
    const svgRef = useRef(null);
    const H = W < 480 ? 280 : 340;
    const pts = rows.filter((r) => r.bpm != null && r.qualified);
    const xs = pts.map((p) => p.bpm);
    const ys = pts.map((p) => p.rapm);
    const xMin = Math.min(-4, ...xs);
    const xMax = Math.max(4, ...xs);
    const yMin = Math.min(-3, ...ys);
    const yMax = Math.max(3, ...ys);
    const sx = (v) => M.l + ((v - xMin) / (xMax - xMin)) * (W - M.l - M.r);
    const sy = (v) => H - M.b - ((v - yMin) / (yMax - yMin)) * (H - M.t - M.b);
    const dots = pts.map((p) => ({ x: sx(p.bpm), y: sy(p.rapm), p }));
    const [hot, setHot] = useState(null);
    const onMove = (e) => {
        const svg = e.currentTarget.ownerSVGElement;
        if (!svg || !dots.length) return;
        const rect = svg.getBoundingClientRect();
        const cx = ((e.clientX - rect.left) / rect.width) * W;
        const cy = ((e.clientY - rect.top) / rect.height) * H;
        let best = null;
        let bestD = Infinity;
        dots.forEach((d, i) => {
            const dd = (d.x - cx) ** 2 + (d.y - cy) ** 2;
            if (dd < bestD) { bestD = dd; best = i; }
        });
        setHot(bestD < 30 ** 2 ? best : null);
    };
    const onKey = (e) => {
        if (!dots.length) return;
        if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { e.preventDefault(); setHot((i) => (i == null ? 0 : Math.min(dots.length - 1, i + 1))); }
        else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { e.preventDefault(); setHot((i) => (i == null ? dots.length - 1 : Math.max(0, i - 1))); }
        else if (e.key === 'Escape') setHot(null);
    };
    const hovered = hot != null ? dots[hot] : null;
    const xTicks = [];
    for (let v = Math.ceil(xMin / 2) * 2; v <= xMax; v += 2) xTicks.push(v);
    const yTicks = [];
    for (let v = Math.ceil(yMin); v <= yMax; v += 1) yTicks.push(v);
    const aria = `${versionLabel} RAPM against BPM, ${seasonLabel(season)}: ${pts.length} qualified players, correlation ${corr == null ? 'not computed' : corr.toFixed(2)}.`;
    return (
        <div className="rx-chart rp-panel" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={`rapm vs bpm ${seasonLabel(season)}`} />
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={aria}>
                    {yTicks.map((t) => (
                        <g key={`y${t}`}>
                            <line className="rx-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                            <text className="rx-tick" x={M.l - 8} y={sy(t)} textAnchor="end" dominantBaseline="middle">{signed(t, 0)}</text>
                        </g>
                    ))}
                    {xTicks.map((t) => (
                        <text key={`x${t}`} className="rx-tick" x={sx(t)} y={H - M.b + 16} textAnchor="middle">{signed(t, 0)}</text>
                    ))}
                    <line className="rp-zero" x1={sx(0)} x2={sx(0)} y1={M.t} y2={H - M.b} />
                    <line className="rp-zero" x1={M.l} x2={W - M.r} y1={sy(0)} y2={sy(0)} />
                    <text className="rx-axis" x={(M.l + W - M.r) / 2} y={H - 6} textAnchor="middle">BPM (Basketball-Reference)</text>
                    <text className="rx-axis" transform={`translate(12 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle">RAPM</text>
                    {dots.map((d, i) => (
                        <circle key={d.p.player_id} cx={d.x} cy={d.y} r={i === hot ? 6 : 3.5}
                            className={`rp-dot ${d.p.rapm > 0 ? 'rp-dot--pos' : 'rp-dot--neg'} ${i === hot ? 'rp-dot--hot' : ''}`}
                            style={{ pointerEvents: 'none' }} />
                    ))}
                    <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b} className="chart-crosshair-overlay"
                        role="slider" aria-label="RAPM against BPM, use arrow keys to step through players"
                        aria-valuetext={hovered ? `${hovered.p.player_name}: RAPM ${signed(hovered.p.rapm)}, BPM ${signed(hovered.p.bpm)}` : undefined}
                        tabIndex={0} onPointerMove={onMove} onPointerLeave={() => setHot(null)} onKeyDown={onKey}
                        onBlur={() => setHot(null)} />
                </svg>
                {hovered && (
                    <ChartTooltip x={hovered.x} y={hovered.y} chartWidth={W} chartHeight={H}>
                        <div style={{ fontWeight: 600 }}>{hovered.p.player_name}</div>
                        <div>RAPM {signed(hovered.p.rapm)} ± {num(hovered.p.rapm_se)} (O {signed(hovered.p.orapm)}, D {signed(hovered.p.drapm)})</div>
                        <div style={{ color: 'var(--text-3)' }}>BPM {signed(hovered.p.bpm)} · {int(hovered.p.poss)} poss</div>
                    </ChartTooltip>
                )}
            </div>
        </div>
    );
}

// ─── The cross-validation curve that picked the shrinkage ─────────────
function LambdaChart({ curve, fit, season }) {
    const [boxRef, W] = useWidth();
    const svgRef = useRef(null);
    const H = W < 480 ? 260 : 300;
    const scales = [...new Set(curve.map((c) => c.prior_scale))];
    const lambdas = [...new Set(curve.map((c) => c.lambda))].sort((a, b) => a - b);
    const lx = (l) => Math.log10(l);
    const xMin = lx(lambdas[0]);
    const xMax = lx(lambdas[lambdas.length - 1]);
    const vals = curve.map((c) => c.cv_rmse).concat(fit.cv_rmse_zero != null ? [fit.cv_rmse_zero] : []);
    const yMin = Math.min(...vals);
    const yMax = Math.max(...vals);
    const pad = (yMax - yMin) * 0.12 || 0.05;
    const sx = (l) => M.l + 8 + ((lx(l) - xMin) / (xMax - xMin)) * (W - M.l - M.r - 16);
    const sy = (v) => H - M.b - ((v - (yMin - pad)) / (yMax + 2 * pad - yMin)) * (H - M.t - M.b);
    const series = scales.map((s) => ({ scale: s, pts: lambdas.map((l) => curve.find((c) => c.lambda === l && c.prior_scale === s)).filter(Boolean) }));
    const crosshairPoints = lambdas.map((l) => {
        const at = curve.filter((c) => c.lambda === l);
        const best = at.reduce((a, b) => (a == null || b.cv_rmse < a.cv_rmse ? b : a), null);
        return { x: sx(l), y: sy(best.cv_rmse), lambda: l, at };
    });
    const { point: hovered, overlayProps } = useChartCrosshair(crosshairPoints, W);
    const yTicks = [yMin, (yMin + yMax) / 2, yMax];
    const lineClass = (i) => (i === 0 ? 'rp-line' : i === 1 ? 'rp-line rp-line--alt' : 'rp-line rp-line--alt2');
    const aria = `Cross-validated error against shrinkage, ${seasonLabel(season)}: lowest at lambda ${fit.lambda}${fit.prior_scale != null ? ` with prior scale ${fit.prior_scale}` : ''}; the zero model scores ${num(fit.cv_rmse_zero, 2)}.`;
    return (
        <div className="rx-chart rp-panel" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={`rapm shrinkage curve ${seasonLabel(season)}`} />
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={aria}>
                    {yTicks.map((t) => (
                        <g key={`y${t}`}>
                            <line className="rx-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                            <text className="rx-tick" x={M.l - 8} y={sy(t)} textAnchor="end" dominantBaseline="middle">{t.toFixed(2)}</text>
                        </g>
                    ))}
                    {lambdas.filter((_, i) => W >= 480 || i % 2 === 0).map((l) => (
                        <text key={`x${l}`} className="rx-tick" x={sx(l)} y={H - M.b + 16} textAnchor="middle">{l >= 1000 ? `${l / 1000}k` : l}</text>
                    ))}
                    <text className="rx-axis" x={(M.l + W - M.r) / 2} y={H - 6} textAnchor="middle">Shrinkage λ (log scale)</text>
                    <text className="rx-axis" transform={`translate(12 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle">Held-out error</text>
                    {fit.cv_rmse_zero != null && (
                        <g>
                            <line className="rp-zero" x1={M.l} x2={W - M.r} y1={sy(fit.cv_rmse_zero)} y2={sy(fit.cv_rmse_zero)} />
                            <text className="rp-mark" x={W - M.r - 4} y={sy(fit.cv_rmse_zero) - 6} textAnchor="end">everyone average</text>
                        </g>
                    )}
                    {series.map((s, i) => (
                        <polyline key={String(s.scale)} className={lineClass(i)}
                            points={s.pts.map((c) => `${sx(c.lambda).toFixed(1)},${sy(c.cv_rmse).toFixed(1)}`).join(' ')} />
                    ))}
                    {hovered && <line x1={hovered.x} y1={M.t} x2={hovered.x} y2={H - M.b} className="chart-crosshair-line" />}
                    <circle className="rp-chosen" cx={sx(fit.lambda)} cy={sy(fit.cv_rmse)} r={5.5} />
                    <text className="rp-mark" x={sx(fit.lambda)} y={sy(fit.cv_rmse) - 10} textAnchor="middle">chosen: {fit.lambda}</text>
                    <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b} className="chart-crosshair-overlay"
                        role="slider" aria-label="Shrinkage curve, use arrow keys to step through"
                        aria-valuetext={hovered ? `lambda ${hovered.lambda}: ${hovered.at.map((c) => c.cv_rmse.toFixed(3)).join(', ')}` : undefined}
                        {...overlayProps} />
                </svg>
                {hovered && (
                    <ChartTooltip x={hovered.x} y={hovered.y} chartWidth={W} chartHeight={H}>
                        <div style={{ fontWeight: 600 }}>λ = {hovered.lambda.toLocaleString()}</div>
                        {hovered.at.map((c) => (
                            <div key={String(c.prior_scale)}>{c.prior_scale != null ? `prior × ${c.prior_scale}: ` : ''}{c.cv_rmse.toFixed(3)}</div>
                        ))}
                    </ChartTooltip>
                )}
            </div>
            {scales.length > 1 && (
                <div className="rp-legend">
                    {scales.map((s, i) => <span key={String(s)} className={i === 1 ? 'rp-legend--alt' : i === 2 ? 'rp-legend--alt2' : ''}>prior × {s}</span>)}
                </div>
            )}
        </div>
    );
}

// ─── The tracker's drift profile: tune next-season RMSE (the criterion) and
// the likelihood along the drift grid, the chosen drift marked ────────────────
function DriftChart({ curve, fit, season }) {
    const [boxRef, W] = useWidth();
    const svgRef = useRef(null);
    const H = W < 480 ? 260 : 300;
    const pts = [...curve].sort((a, b) => a.lambda_q - b.lambda_q);
    const lx = (l) => Math.log10(l);
    const xMin = lx(pts[0].lambda_q);
    const xMax = lx(pts[pts.length - 1].lambda_q);
    const rmse = pts.map((c) => c.next_rmse);
    const rMin = Math.min(...rmse);
    const rMax = Math.max(...rmse);
    const rPad = (rMax - rMin) * 0.15 || 0.01;
    const llMin = Math.min(...pts.map((c) => c.neg2ll));
    const dll = pts.map((c) => c.neg2ll - llMin);
    const dMax = Math.max(...dll, 1);
    const ML = { l: 52, r: 52, t: 16, b: 40 };
    const sx = (l) => ML.l + 8 + ((lx(l) - xMin) / (xMax - xMin)) * (W - ML.l - ML.r - 16);
    const sy = (v) => H - ML.b - ((v - (rMin - rPad)) / (rMax + rPad - (rMin - rPad))) * (H - ML.t - ML.b);
    const sy2 = (v) => H - ML.b - (v / (dMax * 1.15)) * (H - ML.t - ML.b);
    const chosen = pts.find((c) => c.chosen) ?? pts.reduce((a, b) => (b.next_rmse < a.next_rmse ? b : a));
    const llBest = pts.reduce((a, b) => (b.neg2ll < a.neg2ll ? b : a));
    const crosshairPoints = pts.map((c, i) => ({ x: sx(c.lambda_q), y: sy(c.next_rmse), c, dll: dll[i] }));
    const { point: hovered, overlayProps } = useChartCrosshair(crosshairPoints, W);
    const yTicks = [rMin, (rMin + rMax) / 2, rMax];
    const y2Ticks = [0, dMax / 2, dMax];
    const aria = `Drift profile, hyperparameters chosen on ${fit.estimated_on}: next-season RMSE is lowest at drift λ ${Math.round(chosen.lambda_q).toLocaleString()} (${chosen.next_rmse.toFixed(3)}); the likelihood prefers ${Math.round(llBest.lambda_q).toLocaleString()}.`;
    return (
        <div className="rx-chart rp-panel" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={`rating tracker drift profile ${seasonLabel(season)}`} />
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={aria}>
                    {yTicks.map((t) => (
                        <g key={`y${t}`}>
                            <line className="rx-grid" x1={ML.l} x2={W - ML.r} y1={sy(t)} y2={sy(t)} />
                            <text className="rx-tick" x={ML.l - 8} y={sy(t)} textAnchor="end" dominantBaseline="middle">{t.toFixed(2)}</text>
                        </g>
                    ))}
                    {y2Ticks.map((t) => (
                        <text key={`y2${t}`} className="rx-tick" x={W - ML.r + 8} y={sy2(t)} textAnchor="start" dominantBaseline="middle">{t >= 1000 ? `+${(t / 1000).toFixed(1)}k` : `+${Math.round(t)}`}</text>
                    ))}
                    {pts.filter((_, i) => W >= 480 || i % 2 === 0).map((c) => (
                        <text key={`x${c.lambda_q}`} className="rx-tick" x={sx(c.lambda_q)} y={H - ML.b + 16} textAnchor="middle">{c.lambda_q >= 1000 ? `${Math.round(c.lambda_q / 1000)}k` : Math.round(c.lambda_q)}</text>
                    ))}
                    <text className="rx-axis" x={(ML.l + W - ML.r) / 2} y={H - 6} textAnchor="middle">Drift λ (log scale; higher = ratings change less between seasons)</text>
                    <text className="rx-axis" transform={`translate(12 ${(ML.t + H - ML.b) / 2}) rotate(-90)`} textAnchor="middle">Next-season RMSE (tune)</text>
                    <text className="rx-axis" transform={`translate(${W - 10} ${(ML.t + H - ML.b) / 2}) rotate(90)`} textAnchor="middle">−2 log likelihood above its minimum</text>
                    <polyline className="rt-curve-line--alt" points={pts.map((c, i) => `${sx(c.lambda_q).toFixed(1)},${sy2(dll[i]).toFixed(1)}`).join(' ')} />
                    <polyline className="rt-curve-line" points={pts.map((c) => `${sx(c.lambda_q).toFixed(1)},${sy(c.next_rmse).toFixed(1)}`).join(' ')} />
                    {hovered && <line x1={hovered.x} y1={ML.t} x2={hovered.x} y2={H - ML.b} className="chart-crosshair-line" />}
                    <circle className="rp-chosen" cx={sx(chosen.lambda_q)} cy={sy(chosen.next_rmse)} r={5.5} />
                    <text className="rp-mark" x={sx(chosen.lambda_q)} y={sy(chosen.next_rmse) - 10} textAnchor="middle">chosen: {Math.round(chosen.lambda_q).toLocaleString()}</text>
                    <rect x={ML.l} y={ML.t} width={W - ML.l - ML.r} height={H - ML.t - ML.b} className="chart-crosshair-overlay"
                        role="slider" aria-label="Drift profile, use arrow keys to step through"
                        aria-valuetext={hovered ? `drift λ ${Math.round(hovered.c.lambda_q)}: RMSE ${hovered.c.next_rmse.toFixed(3)}, likelihood +${Math.round(hovered.dll)}` : undefined}
                        {...overlayProps} />
                </svg>
                {hovered && (
                    <ChartTooltip x={hovered.x} y={hovered.y} chartWidth={W} chartHeight={H}>
                        <div style={{ fontWeight: 600 }}>drift λ = {Math.round(hovered.c.lambda_q).toLocaleString()}</div>
                        <div>next-season RMSE {hovered.c.next_rmse.toFixed(3)}</div>
                        <div style={{ color: 'var(--text-3) ' }}>−2 log likelihood +{Math.round(hovered.dll).toLocaleString()} over its minimum</div>
                    </ChartTooltip>
                )}
            </div>
            <div className="rp-legend">
                <span>next-season RMSE on the tune pairs (the criterion)</span>
                <span className="rp-legend--alt">likelihood of the tune seasons&apos; stints (stored, not used)</span>
            </div>
        </div>
    );
}

function TrackerParams({ fit }) {
    const pct = (v) => `${Math.round(v * 100)}%`;
    const k = (v) => Math.round(v).toLocaleString();
    const items = [
        ['Carry-over φ', fit.phi.toFixed(2), `${pct(fit.phi)} of last season's rating carries into the next before any game`],
        ['Drift between seasons', `± ${num(fit.drift_sd, 2)}`, `points per 100 per side a rating may move between seasons (λ ${k(fit.lambda_q)})`],
        ['Newcomer spread', `± ${num(fit.newcomer_sd, 2)}`, `a first season starts at average with this spread (λ ${k(fit.lambda0)}: the ordinary ridge)`],
        ['BPM weight', `${k(fit.lambda_b)} poss.`, `a season's BPM counts like this many possessions of stint evidence (± ${num(fit.bpm_sd, 2)} per side)`],
        ['BPM scale', fit.prior_scale.toFixed(2), 'the measurement is this times OBPM / DBPM'],
    ];
    return (
        <div className="rt-params">
            {items.map(([label, value, note]) => (
                <div className="rt-param" key={label}>
                    <span className="rt-param-label">{label}</span>
                    <span className="rt-param-value">{value}</span>
                    <span className="rt-param-note">{note}</span>
                </div>
            ))}
        </div>
    );
}

function ValidationTable({ rows, title, note, name }) {
    if (!rows?.length) return null;
    const rated = rows.filter((r) => r.game_rmse != null);
    const best = rated.length ? Math.min(...rated.map((r) => r.game_rmse)) : null;
    const hasCoverage = rows.some((r) => r.coverage != null);
    return (
        <div className="rp-panel">
            <h3 className="rp-panel-title">{title}</h3>
            <TableExport name={name} />
            <div className="table-wrapper">
                <table className="data-table lb-table rp-val-table">
                    <thead>
                        <tr>
                            <th>Model</th>
                            <th className="lb-num" title="Root-mean-square error of each game's predicted home margin, in points">Game margin RMSE</th>
                            <th className="lb-num" title="Correlation of predicted and actual game margins">r</th>
                            <th className="lb-num" title="Possession-weighted RMSE of stint points per 100 possessions">Stint RMSE</th>
                            {hasCoverage && <th className="lb-num" title="Share of the players on the floor (possession-weighted) who had a rating from the earlier season; in brackets, the share of possessions where all ten did">Rated</th>}
                            {rows.some((r) => r.scale_fit != null) && <th className="lb-num" title="The multiplier on the ratings that would have fitted best (1 = the rating's size was right)">Best scale</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.model} className={`${r.game_rmse === best ? 'rp-val--best' : ''} ${r.model.startsWith('rapm') ? 'rp-val--rapm' : ''}`}>
                                <td>{r.model_label}</td>
                                <td className="lb-num">{num(r.game_rmse, 2)}</td>
                                <td className="lb-num">{num(r.game_corr, 3)}</td>
                                <td className="lb-num">{num(r.stint_rmse, 2)}</td>
                                {hasCoverage && <td className="lb-num">{r.coverage == null ? '—' : `${pct(r.coverage)} (${pct(r.coverage_all10)})`}</td>}
                                {rows.some((x) => x.scale_fit != null) && <td className="lb-num">{num(r.scale_fit, 2)}</td>}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            {note && <p className="rp-panel-note">{note}</p>}
        </div>
    );
}

export default function Rapm() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [result, setResult] = useState(null); // { key, data } | { key, error }

    useEffect(() => {
        fetchRapmOptions()
            .then((o) => {
                setOptions(o);
                setForm(formFromParams(params, o.versions.map((v) => v.id)));
            })
            .catch(() => setOptionsError('RAPM couldn\'t load. Is the impact API (port 8002) running, and has scripts/build_rapm.py been run?'));
    }, [params]);

    const reqKey = form ? JSON.stringify([form.version, form.season, form.team, form.minPoss, form.version === 'tracker' ? form.kind : null]) : null;
    useEffect(() => {
        if (!form) return undefined;
        let active = true;
        fetchRapm({
            version: form.version, season: form.season ?? undefined, team: form.team ?? undefined,
            min_poss: form.minPoss ?? undefined, kind: form.version === 'tracker' ? form.kind : undefined,
        })
            .then((d) => { if (active) setResult({ key: reqKey, data: d }); })
            .catch((e) => { if (active) setResult({ key: reqKey, error: e.response?.data?.detail || 'The leaderboard couldn\'t load.' }); });
        return () => { active = false; };
    }, [form, reqKey]);
    const loading = result?.key !== reqKey;
    const data = result?.data ?? null;
    const error = result?.error ?? '';

    const shownSeason = form?.season ?? data?.season ?? null;
    useUrlSync(form && {
        version: form.version === 'single' ? null : form.version, season: shownSeason, team: form.team,
        kind: form.version === 'tracker' && form.kind !== 'filtered' ? form.kind : null,
        min: form.minPoss, sort: form.sort === 'rapm' ? null : form.sort, dir: form.dir === 'desc' ? null : form.dir,
    });

    const rows = useMemo(() => (data && form ? sortRows(data.players, form.sort, form.dir) : []), [data, form]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;
    if (!data && loading) return <Loader />;
    if (!data) return <section className="dashboard-card"><p className="error-message">{error}</p></section>;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const onSort = (key) => setForm((f) => (
        f.sort === key ? { ...f, dir: f.dir === 'asc' ? 'desc' : 'asc' } : { ...f, sort: key, dir: COLUMNS[key].text ? 'asc' : 'desc' }
    ));
    const version = options.versions.find((v) => v.id === data.version);
    const seasons = [...version.seasons].reverse();
    const season = data.season;
    const fit = data.fit;
    const floor = data.min_poss;
    const val = data.validation;
    const isPrior = data.version === 'prior';
    const isMulti = data.version === 'multi';
    const isTracker = data.version === 'tracker';
    const tr = isTracker ? data.tracker : null;
    const tfit = tr?.fit;
    const windowLabel = isMulti ? `${seasonLabel(fit.seasons_from)} to ${seasonLabel(fit.seasons_to)}` : seasonLabel(season);
    const nextRows = val.next_season_from_this.length ? val.next_season_from_this : val.next_season;
    const nextTitle = val.next_season_from_this.length
        ? `Predicting ${seasonLabel(season + 1)} from ${seasonLabel(season)} ratings`
        : val.next_season.length ? `Predicting ${seasonLabel(season)} from ${seasonLabel(season - 1)} ratings` : null;
    const y2y = val.year_to_year;
    const y2yText = (m) => { const r = y2y.find((x) => x.model === m); return r ? `${num(r.corr, 2)} (${r.players} players)` : null; };
    const cols = ['player_name', 'teams', 'games', 'minutes', 'poss', 'orapm', 'drapm', 'rapm'];
    const kindLabel = KINDS.find((k) => k[0] === (tr?.kind ?? 'filtered'))[1].toLowerCase();
    const trackerTests = isTracker ? [...val.next_season_from_this, ...val.next_season] : [];
    const trBest = (rows) => {
        const t = rows.find((r) => r.model === 'rapm_tracker');
        if (!t) return null;
        const others = rows.filter((r) => r.model !== 'rapm_tracker' && r.game_rmse != null);
        const best = others.reduce((a, b) => (a == null || b.game_rmse < a.game_rmse ? b : a), null);
        return best ? { t, best } : null;
    };

    return (
        <section className="dashboard-card lb-card oo-card">
            <h2 className="card-title hb-page-title">
                RAPM: what each player adds with the other nine held constant
                <InfoTooltip label="How RAPM is computed" title="Under the hood">{data.method}</InfoTooltip>
                <SourceBadge source={data._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="rapm" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                On/Off credits a player with everything his lineups did. RAPM is the regression that On/Off isn&apos;t:
                every tracked five-man stint since 2020-21, each player&apos;s offence and defence estimated with the other nine
                players on the floor held constant, shrunk toward average so small samples don&apos;t explode. Error bars from
                resampling games, the shrinkage picked by cross-validation, and a real test of whether it predicts games better
                than BPM. It usually doesn&apos;t by much: that is a known result, and it is on the page.
            </p>

            <div className="tab-bar lb-modes" role="tablist" aria-label="Version" style={{ marginTop: '0.75rem' }}>
                {options.versions.map((v) => (
                    <button key={v.id} type="button" role="tab" aria-selected={form.version === v.id}
                        className={`tab-btn ${form.version === v.id ? 'tab-btn--active' : ''}`}
                        onClick={() => set({ version: v.id, season: v.seasons.includes(form.season) ? form.season : null })}>
                        {v.label}
                    </button>
                ))}
            </div>
            <p className="rp-version-blurb">{version.blurb}</p>

            <div className="lb-controls">
                {isTracker && (
                    <label>
                        <span>Estimate</span>
                        <div className="tab-bar lb-modes rt-kind" role="tablist" aria-label="Kind of estimate">
                            {KINDS.map(([id, lab]) => (
                                <button key={id} type="button" role="tab" aria-selected={form.kind === id}
                                    className={`tab-btn ${form.kind === id ? 'tab-btn--active' : ''}`} onClick={() => set({ kind: id })}>{lab}</button>
                            ))}
                        </div>
                    </label>
                )}
                <label>
                    <span>{isMulti ? 'Window ending' : 'Season'}</span>
                    <select className="input-field" value={season} onChange={(e) => set({ season: Number(e.target.value) })}>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>Team</span>
                    <select className="input-field" value={form.team ?? ''} onChange={(e) => set({ team: e.target.value || null })}>
                        <option value="">All teams</option>
                        {data.teams.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                </label>
                <label>
                    <span>Min. possessions</span>
                    <input className="input-field" type="number" min={0} max={20000} step={250} value={form.minPoss ?? data.default_min_poss}
                        onChange={(e) => set({ minPoss: e.target.value === '' ? 0 : Number(e.target.value) })} />
                </label>
            </div>

            {error && <p className="error-message">{error}</p>}

            <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                {isTracker ? (
                    <>
                        <p className="rx-verdict">
                            <strong>{windowLabel}, {kindLabel}: {fit.players} players in {int(fit.stints)} tracked stints of {int(fit.games)} games; {data.noise.qualified}
                                {data.team ? ` league-wide` : ''} clear {int(floor)} possessions; {tr.n_carried} carried a rating in from an earlier season and {tr.n_newcomers} started new.</strong>{' '}
                            {tr.kind === 'filtered'
                                ? 'As of then: each rating is the posterior after this season\'s games, using nothing later, so it is what a forecast made at the end of the season could have used.'
                                : 'With hindsight: every later season\'s games weigh in through the smoother, so this is the best guess of what he was, not what could have been known at the time.'}{' '}
                            The five hyperparameters were chosen on {tfit.estimated_on} by next-season game prediction (pooled RMSE {num(tfit.tune_rmse, 3)} over {int(tfit.tune_games)} games, the same criterion as the other versions&apos; λ under the paper&apos;s protocol) and held fixed, so {seasonLabel(tfit.seasons_to - 1)} and {seasonLabel(tfit.seasons_to)} are out of sample.
                            The home side scores {signed(fit.home_edge_per_100)} per 100 possessions more than an identical away side.{' '}
                            {data.noise.ci_excludes_zero} of the {data.noise.qualified} qualified intervals clear zero (about {data.noise.expected_by_chance} would by chance).
                            {data.noise.corr_with_bpm != null && <> Correlation with BPM among them: r = {num(data.noise.corr_with_bpm, 2)}, higher than one-season RAPM&apos;s because BPM is one of the tracker&apos;s inputs.</>}
                            {' '}{fit.players_with_prior} of {fit.players} players had a BPM to measure against.
                            {tfit.ml_estimate && <> The state-space model&apos;s own maximum-likelihood estimate (drift λ {Math.round(tfit.ml_estimate.par.lambda_q).toLocaleString()}, BPM weight {Math.round(tfit.ml_estimate.par.lambda_b).toLocaleString()}, φ {tfit.ml_estimate.par.phi.toFixed(2)}) is stored but not used: it scores a season&apos;s stints against that season&apos;s BPM, which already contains the season&apos;s point differential, and predicts the next season worse (RMSE {num(tfit.ml_estimate.tune_rmse, 3)}).</>}
                        </p>
                        <TrackerParams fit={tfit} />
                    </>
                ) : (
                <p className="rx-verdict">
                    <strong>{windowLabel}: {fit.players} players in {int(fit.stints)} tracked stints of {int(fit.games)} games; {data.noise.qualified}
                        {data.team ? ` league-wide` : ''} clear {int(floor)} possessions.</strong>{' '}
                    Shrinkage λ = {fit.lambda.toLocaleString()}{isPrior && fit.prior_scale != null ? ` and prior scale ${fit.prior_scale}` : ''} was chosen by
                    {' '}{fit.cv_folds}-fold cross-validation grouped by game: held-out stint error {num(fit.cv_rmse, 2)} against {num(fit.cv_rmse_zero, 2)} with
                    everyone set to average, a small gain because single stints are mostly noise. The home side scores{' '}
                    {signed(fit.home_edge_per_100)} per 100 possessions more than an identical away side.{' '}
                    {data.noise.ci_excludes_zero} of the {data.noise.qualified} qualified intervals clear zero (about {data.noise.expected_by_chance} would by chance).
                    {data.noise.corr_with_bpm != null && <> Correlation with BPM among them: r = {num(data.noise.corr_with_bpm, 2)}, positive and well under 1.</>}
                    {isPrior && fit.players_with_prior != null && <> {fit.players_with_prior} of {fit.players} players had a BPM to shrink toward; the rest shrink toward zero.</>}
                </p>
                )}

                <div className="rp-grid">
                    <div>
                        <h3 className="rp-panel-title">{isTracker ? 'Tracker' : 'RAPM'} against BPM, qualified players</h3>
                        <ScatterChart rows={data.players} season={season} versionLabel={version.label} corr={data.noise.corr_with_bpm} />
                    </div>
                    <div>
                        <h3 className="rp-panel-title">{isTracker ? 'How much drift the data want' : 'The shrinkage curve'}</h3>
                        {isTracker ? <DriftChart curve={tr.curve} fit={tfit} season={season} /> : <LambdaChart curve={data.lambda_curve} fit={fit} season={season} />}
                    </div>
                </div>

                <div className="rp-grid">
                    <ValidationTable rows={val.held_out_games} title={`Held-out games within ${seasonLabel(season)}`}
                        name={`rapm validation held-out ${seasonLabel(season)}`}
                        note={isTracker
                            ? 'Each fifth of the season\'s games predicted by a fit on the other four; the tracker\'s row refits each fold from the prior the filter brought into the season (earlier seasons and this season\'s BPM, the same for every fold). The other rows are the RAPM page\'s own, on the same folds. BPM here is the full-season published value, which saw the held-out games.'
                            : "Each fifth of the season's games predicted by a fit on the other four. BPM here is the full-season published value with one scale fitted on the training games (it saw the held-out games; RAPM didn't); on/off is recomputed from the training games only. The RAPM rows use the λ chosen on these same folds, so they are very slightly flattered."} />
                    {nextTitle && (
                        <ValidationTable rows={nextRows} title={nextTitle} name={`rapm validation next season ${seasonLabel(season)}`}
                            note={isTracker
                                ? `Ratings used as published (scale 1), only the intercept and home term refitted on the later season; every version rescored here on the same rows. The tracker's "as of then" ratings only: the with-hindsight kind has seen the later season and is not scored. A player the tracker has seen who missed the earlier season keeps his carried rating, which the one-season versions cannot do ("Rated" shows the difference). Year-to-year correlation among players qualified in both seasons${y2y.length ? `: tracker ${y2yText('rapm_tracker') ?? '—'}, with hindsight ${y2yText('rapm_tracker_smoothed') ?? '—'}, BPM-prior RAPM ${y2yText('rapm_prior') ?? '—'}, one-season RAPM ${y2yText('rapm_single') ?? '—'}, BPM ${y2yText('bpm') ?? '—'}` : ' is not on file for this season'}; the tracker's is higher by construction (last season is part of this season's estimate), a smoothness, not evidence.`
                                : `Ratings used as published (scale 1), only the intercept and home term refitted on the later season. Players without a rating (rookies, newcomers) count as average for every model; "Rated" is the share of players on the floor who had one, and in brackets the share of possessions where all ten did. Year-to-year correlation among players qualified in both seasons${y2y.length ? `: RAPM ${y2yText('rapm_single') ?? '—'}, BPM ${y2yText('bpm') ?? '—'}, on/off ${y2yText('onoff') ?? '—'}` : ' is not on file for this season'}.`} />
                    )}
                </div>
                {isTracker && trBest(trackerTests) && (
                    <p className="rp-panel-note">
                        {(() => { const { t, best } = trBest(trackerTests); const d = t.game_rmse - best.game_rmse; return (
                            <>On the next-season test above the tracker&apos;s game-margin RMSE is {num(t.game_rmse, 2)} against {num(best.game_rmse, 2)} for the best other model ({best.model_label}): {Math.abs(d) < 0.005 ? 'a tie' : d < 0 ? `${num(-d, 2)} better` : `${num(d, 2)} worse`}. Whether such gaps are outside noise is the paper&apos;s question (paired bootstrap over games under its protocol); the Methodology card quotes the answer.</>
                        ); })()}
                    </p>
                )}

                <TableExport name={`rapm ${data.version} ${data.team ?? 'league'} ${seasonLabel(season)}`} />
                <div className="table-wrapper">
                    <table className="data-table lb-table oo-table">
                        <thead>
                            <tr>
                                <th title="Rank among qualified players, league-wide">#</th>
                                {cols.map((k) => (
                                    <SortHeader key={k} colKey={k} sort={form.sort} dir={form.dir} onSort={onSort}
                                        className={COLUMNS[k].text ? undefined : `lb-num ${k === 'rapm' ? 'lb-stat' : ''}`} />
                                ))}
                                <th className="lb-num" title={isTracker ? 'Posterior 95% interval (±1.96 sd) for the rating' : 'Bootstrap 95% interval for RAPM, resampling games'}>95% interval</th>
                                {isPrior && <th className="lb-num" title="The value he was shrunk toward: scaled OBPM + DBPM">Prior</th>}
                                {isTracker && <SortHeader colKey="carried" sort={form.sort} dir={form.dir} onSort={onSort} className="lb-num" />}
                                <SortHeader colKey="bpm" sort={form.sort} dir={form.dir} onSort={onSort} className="lb-num" />
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r) => {
                                const short = !r.qualified;
                                const flag = short ? `Under ${int(floor)} possessions: treat as noise` : null;
                                return (
                                    <tr key={r.player_id} className={short ? 'sl-short' : undefined} title={flag ?? undefined}>
                                        <td>{r.rapm_rank ?? '—'}</td>
                                        <td>
                                            <PlayerName playerId={r.player_id} name={r.player_name ?? `#${r.player_id}`} size={24} />
                                            {flag && <span className="rp-flag" aria-label={flag} title={flag} data-export-as=" (small sample)">†</span>}
                                        </td>
                                        <td className="rp-team">
                                            {r.team_list.map((t, i) => (
                                                <React.Fragment key={t}>{i > 0 && '/'}<TeamLink abbr={t} season={isMulti ? fit.seasons_to : season} /></React.Fragment>
                                            ))}
                                        </td>
                                        <td className="lb-num">{r.games}</td>
                                        <td className="lb-num">{int(r.minutes)}</td>
                                        <td className="lb-num">{int(r.poss)}</td>
                                        <td className={`lb-num ${tone(r.orapm)}`} title={`± ${num(r.orapm_se)}`}>{signed(r.orapm)}</td>
                                        <td className={`lb-num ${tone(r.drapm)}`} title={`± ${num(r.drapm_se)}`}>{signed(r.drapm)}</td>
                                        <td className={`lb-num lb-stat ${tone(r.rapm)}`}>{signed(r.rapm)}</td>
                                        <td className="lb-num rp-ci-cell" title={`95% interval ${signed(r.rapm_ci_low)} to ${signed(r.rapm_ci_high)}${r.ci_excludes_zero ? ' (clear of zero)' : ' (includes zero: within noise)'}`}>
                                            <CiBar est={r.rapm} lo={r.rapm_ci_low} hi={r.rapm_ci_high} excludes={r.ci_excludes_zero} />
                                            <span className="rp-ci-text">{signed(r.rapm_ci_low)} to {signed(r.rapm_ci_high)}</span>
                                        </td>
                                        {isPrior && <td className="lb-num">{r.prior_o == null ? '—' : signed(r.prior_o + r.prior_d)}</td>}
                                        {isTracker && <td className={`lb-num ${r.seasons_seen > 1 ? tone(r.carried) : ''}`} title={r.seasons_seen > 1 ? `Carried in: offence ${signed(r.carried_o)}, defence ${signed(r.carried_d)}; his ${r.seasons_seen}th season on file` : 'First season on file: starts at average'}>{r.seasons_seen > 1 ? signed(r.carried) : 'new'}</td>}
                                        <td className="lb-num">{signed(r.bpm)}</td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
                {rows.length === 0 && <p className="empty-message">No player here. Lower the possessions floor or pick another team.</p>}
                <p className="page-subtitle lb-summary rp-foot">
                    {isTracker ? (
                        <>Units are points per 100 possessions, the same as BPM. A player&apos;s number is his effect with the other nine on the
                        floor held constant, carried from season to season and measured against his BPM; the ± is a posterior standard
                        deviation (the stint noise, estimated from the stints, over the evidence on him), not a bootstrap, and a 95% interval
                        that includes zero is within noise. &quot;Carried in&quot; is the rating he brought into the season. † marks a row under
                        the possessions floor. Stints from games whose play-by-play didn&apos;t reconcile, and stints with an unidentified
                        player, are left out. {tr.kind === 'smoothed' ? 'With hindsight: not a forecast.' : 'As of then: a season with few minutes leans on what he carried in and on his BPM.'} No ageing, no health, no role.</>
                    ) : (
                        <>Units are points per 100 possessions, the same as BPM. A player&apos;s number is his effect with the other nine on the
                        floor held constant, shrunk toward zero by λ, so stars sit lower than their on/off and everyone&apos;s spread is
                        compressed (that is the point: it is what the data can support). † marks a row under the possessions floor.
                        Stints from games whose play-by-play didn&apos;t reconcile, and stints with an unidentified player, are left out;
                        the On/Off and Pair Chemistry pages list them. Descriptive of {windowLabel} only: no ageing, no health, no role.</>
                    )}
                </p>
            </div>
        </section>
    );
}
