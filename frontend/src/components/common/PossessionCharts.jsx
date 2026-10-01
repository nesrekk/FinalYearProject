import React, { useRef, useState } from 'react';
import ChartExport from './ChartExport';
import ChartTooltip from './ChartTooltip';
import { useWidth } from '../../utils/rotationFormat';
import '../../styles/possessions.css';

// Charts of the Possession Explorer (pages/PossessionExplorer.jsx).
//
// PppDotChart: one row per item, a dot at its points per possession with
// its 95% interval, an optional reference tick per row (the league's value
// for that start type) and an optional reference line across all rows (the
// league's value for one start type). `better` = 'high' (offence) or 'low'
// (defence) colours a dot whose interval clears the reference.
// TransitionCurve: points per possession after a defensive rebound by the
// second of the first shot, with the transition window shaded.

const f3 = (v) => (v == null ? '—' : v.toFixed(3));
const pct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);

function niceTicks(lo, hi, n = 5) {
    const span = hi - lo;
    const raw = span / n;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) ?? raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toFixed(6)));
    return out;
}

export function PppDotChart({ items, refLine, refLabel, better = 'high', name, ariaLabel, onPick, rowH: rowHIn }) {
    const [boxRef, W] = useWidth(640);
    const svgRef = useRef(null);
    const [hot, setHot] = useState(null);
    const narrow = W < 520;
    const labelW = narrow ? Math.round(Math.min(126, W * 0.36)) : 168;
    const valueW = narrow ? 48 : 64;
    const rowH = rowHIn ?? (narrow ? 22 : 24);
    const top = 22;
    const H = top + items.length * rowH + 6;
    const vals = items.flatMap((d) => [d.lo ?? d.ppp, d.hi ?? d.ppp, d.ref]).concat(refLine ?? []).filter((v) => v != null);
    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    const pad = Math.max(0.01, (hi - lo) * 0.06);
    lo -= pad;
    hi += pad;
    const x0 = labelW;
    const x1 = W - valueW - 6;
    const sx = (v) => x0 + ((v - lo) / (hi - lo)) * (x1 - x0);
    const ticks = niceTicks(lo, hi, narrow ? 4 : 6);
    const verdict = (d) => {
        const ref = d.ref ?? refLine;
        if (ref == null || d.lo == null) return '';
        if (d.lo > ref) return better === 'high' ? 'pc-dot--good' : 'pc-dot--bad';
        if (d.hi < ref) return better === 'high' ? 'pc-dot--bad' : 'pc-dot--good';
        return '';
    };
    const hovered = hot != null ? items[hot] : null;
    const onMove = (e) => {
        const svg = e.currentTarget.ownerSVGElement;
        if (!svg) return;
        const rect = svg.getBoundingClientRect();
        const y = ((e.clientY - rect.top) / rect.height) * H;
        const i = Math.floor((y - top) / rowH);
        setHot(i >= 0 && i < items.length ? i : null);
    };
    const onKey = (e) => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowRight') { e.preventDefault(); setHot((i) => (i == null ? 0 : Math.min(items.length - 1, i + 1))); }
        else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') { e.preventDefault(); setHot((i) => (i == null ? items.length - 1 : Math.max(0, i - 1))); }
        else if (e.key === 'Enter' && hot != null && onPick) onPick(items[hot]);
        else if (e.key === 'Escape') setHot(null);
    };
    return (
        <div className="pc-chart" ref={boxRef}>
            <div className="pc-chart-head">
                <div className="pc-legend" aria-hidden="true">
                    <span className="pc-legend--dot">Points per possession, 95% interval</span>
                    {items.some((d) => d.ref != null) && <span className="pc-legend--tick">League</span>}
                    {refLine != null && <span className="pc-legend--line">{refLabel ?? 'League'}</span>}
                </div>
                <ChartExport svgRef={svgRef} name={name} />
            </div>
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={ariaLabel}>
                    {ticks.map((t) => (
                        <g key={t}>
                            <line className="rx-grid" x1={sx(t)} x2={sx(t)} y1={top - 4} y2={H - 4} />
                            <text className="rx-tick" x={sx(t)} y={12} textAnchor="middle">{t.toFixed(2)}</text>
                        </g>
                    ))}
                    {refLine != null && <line className="pc-refline" x1={sx(refLine)} x2={sx(refLine)} y1={top - 4} y2={H - 4} />}
                    {items.map((d, i) => {
                        const y = top + i * rowH + rowH / 2;
                        return (
                            <g key={d.key} className={`pc-row${d.highlight ? ' pc-row--hl' : ''}${i === hot ? ' pc-row--hot' : ''}`}>
                                <rect className="pc-row-bg" x={0} y={top + i * rowH} width={W} height={rowH - 1} />
                                <text className="pc-label" x={4} y={y} dominantBaseline="middle">{narrow && d.shortLabel ? d.shortLabel : d.label}</text>
                                {d.lo != null && <line className="pc-ci" x1={sx(d.lo)} x2={sx(d.hi)} y1={y} y2={y} />}
                                {d.ref != null && <line className="pc-reftick" x1={sx(d.ref)} x2={sx(d.ref)} y1={y - 7} y2={y + 7} />}
                                <circle className={`pc-dot ${verdict(d)}`} cx={sx(d.ppp)} cy={y} r={d.highlight ? 5 : 4} />
                                <text className="pc-value" x={W - 4} y={y} textAnchor="end" dominantBaseline="middle">{f3(d.ppp)}</text>
                            </g>
                        );
                    })}
                    <rect x={0} y={top} width={W} height={items.length * rowH} className="chart-crosshair-overlay"
                        role="slider" tabIndex={0} aria-label={`${ariaLabel}. Use the arrow keys to step through the rows${onPick ? ', Enter to open one' : ''}`}
                        aria-valuetext={hovered ? `${hovered.label}: ${f3(hovered.ppp)}` : undefined}
                        style={onPick ? { cursor: 'pointer' } : undefined}
                        onPointerMove={onMove} onPointerLeave={() => setHot(null)} onKeyDown={onKey} onBlur={() => setHot(null)}
                        onClick={() => { if (onPick && hot != null) onPick(items[hot]); }} />
                </svg>
                {hovered && (
                    <ChartTooltip x={Math.min(W - 130, Math.max(130, sx(hovered.ppp)))} y={top + hot * rowH}
                        chartWidth={W} chartHeight={H} align={hot < 3 ? 'below' : 'above'}>
                        <div style={{ fontWeight: 600 }}>{hovered.label}</div>
                        <div>{f3(hovered.ppp)} points per possession{hovered.lo != null && ` (${f3(hovered.lo)} to ${f3(hovered.hi)})`}</div>
                        {hovered.poss != null && <div>{hovered.poss.toLocaleString()} possessions{hovered.share != null && `, ${pct(hovered.share)} of all`}</div>}
                        {hovered.ref != null && <div style={{ color: 'var(--text-3)' }}>League {f3(hovered.ref)}</div>}
                        {hovered.note && <div style={{ color: 'var(--text-3)' }}>{hovered.note}</div>}
                    </ChartTooltip>
                )}
            </div>
        </div>
    );
}

export function TransitionCurve({ ppp, n, windowSec, name }) {
    const [boxRef, W] = useWidth(640);
    const svgRef = useRef(null);
    const [hot, setHot] = useState(null);
    const pts = ppp.map((v, i) => ({ s: i, v, n: n?.[i] })).filter((p) => p.v != null && (p.n == null || p.n >= 200));
    const H = 220;
    const m = { l: 44, r: 12, t: 12, b: 34 };
    const xmax = Math.max(...pts.map((p) => p.s)) + 1;
    const vlo = Math.floor(Math.min(...pts.map((p) => p.v)) * 10) / 10;
    const vhi = Math.ceil(Math.max(...pts.map((p) => p.v)) * 10) / 10;
    const sx = (s) => m.l + (s / xmax) * (W - m.l - m.r);
    const sy = (v) => H - m.b - ((v - vlo) / (vhi - vlo)) * (H - m.t - m.b);
    const yt = niceTicks(vlo, vhi, 4);
    const xt = Array.from({ length: Math.floor(xmax / 3) + 1 }, (_, i) => i * 3).filter((s) => s <= xmax);
    const path = pts.map((p, i) => `${i ? 'L' : 'M'}${sx(p.s + 0.5).toFixed(1)},${sy(p.v).toFixed(1)}`).join('');
    const hovered = hot != null ? pts[hot] : null;
    const onMove = (e) => {
        const svg = e.currentTarget.ownerSVGElement;
        if (!svg) return;
        const rect = svg.getBoundingClientRect();
        const x = ((e.clientX - rect.left) / rect.width) * W;
        let best = null;
        pts.forEach((p, i) => { if (best == null || Math.abs(sx(p.s + 0.5) - x) < Math.abs(sx(pts[best].s + 0.5) - x)) best = i; });
        setHot(best);
    };
    const onKey = (e) => {
        if (e.key === 'ArrowRight') { e.preventDefault(); setHot((i) => (i == null ? 0 : Math.min(pts.length - 1, i + 1))); }
        else if (e.key === 'ArrowLeft') { e.preventDefault(); setHot((i) => (i == null ? pts.length - 1 : Math.max(0, i - 1))); }
        else if (e.key === 'Escape') setHot(null);
    };
    return (
        <div className="pc-chart" ref={boxRef}>
            <div className="pc-chart-head">
                <div className="pc-legend" aria-hidden="true">
                    <span className="pc-legend--band">Transition window (first {windowSec} s)</span>
                </div>
                <ChartExport svgRef={svgRef} name={name} />
            </div>
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
                    aria-label={`Points per possession after a defensive rebound by the second of the first shot: ${pts.map((p) => `${p.s} s ${p.v.toFixed(2)}`).join(', ')}`}>
                    <rect className="pc-band" x={sx(0)} y={m.t} width={sx(windowSec) - sx(0)} height={H - m.t - m.b} />
                    {yt.map((t) => (
                        <g key={t}>
                            <line className="rx-grid" x1={m.l} x2={W - m.r} y1={sy(t)} y2={sy(t)} />
                            <text className="rx-tick" x={m.l - 6} y={sy(t)} textAnchor="end" dominantBaseline="middle">{t.toFixed(1)}</text>
                        </g>
                    ))}
                    {xt.map((s) => <text key={s} className="rx-tick" x={sx(s)} y={H - m.b + 16} textAnchor="middle">{s}</text>)}
                    <text className="rx-tick" x={(m.l + W - m.r) / 2} y={H - 4} textAnchor="middle">Seconds from the rebound to the first shot or free throw</text>
                    <path className="pc-curve" d={path} />
                    {pts.map((p, i) => <circle key={p.s} className={`pc-curve-dot${i === hot ? ' pc-curve-dot--hot' : ''}`} cx={sx(p.s + 0.5)} cy={sy(p.v)} r={i === hot ? 4.5 : 3} />)}
                    <rect x={m.l} y={m.t} width={W - m.l - m.r} height={H - m.t - m.b} className="chart-crosshair-overlay"
                        role="slider" tabIndex={0} aria-label="Seconds to the first shot, use the arrow keys to step through them"
                        aria-valuetext={hovered ? `${hovered.s} to ${hovered.s + 1} s: ${hovered.v.toFixed(3)}` : undefined}
                        onPointerMove={onMove} onPointerLeave={() => setHot(null)} onKeyDown={onKey} onBlur={() => setHot(null)} />
                </svg>
                {hovered && (
                    <ChartTooltip x={Math.min(W - 110, Math.max(110, sx(hovered.s + 0.5)))} y={sy(hovered.v)} chartWidth={W} chartHeight={H}>
                        <div style={{ fontWeight: 600 }}>First shot {hovered.s}–{hovered.s + 1} s after the rebound</div>
                        <div>{hovered.v.toFixed(3)} points per possession</div>
                        {hovered.n != null && <div style={{ color: 'var(--text-3)' }}>{hovered.n.toLocaleString()} possessions, 2020-21 to 2025-26</div>}
                    </ChartTooltip>
                )}
            </div>
        </div>
    );
}
