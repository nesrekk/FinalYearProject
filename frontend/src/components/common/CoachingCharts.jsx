import React, { useRef, useState } from 'react';
import ChartExport from './ChartExport';
import ChartTooltip from './ChartTooltip';
import { useWidth } from '../../utils/rotationFormat';
import '../../styles/possessions.css';

// Chart of the Coaching Decisions page (pages/CoachingDecisions.jsx).
//
// IntervalChart: one row per item, a dot at its value with its 95% interval
// when it has one, and an optional reference line across all rows (zero for
// an effect, the league's rate for a success rate). `tone` on an item
// ('good' | 'bad') colours its dot; `fmt` formats values and ticks. Styles
// are the Possession Explorer's (pc-* in possessions.css).

function niceTicks(lo, hi, n = 5) {
    const span = hi - lo;
    const raw = span / n;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) ?? raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Number(v.toFixed(6)));
    return out;
}

export function IntervalChart({ items, refLine, refLabel, fmt, tickFmt, legend, name, ariaLabel, onPick, rowH: rowHIn }) {
    const [boxRef, W] = useWidth(640);
    const svgRef = useRef(null);
    const [hot, setHot] = useState(null);
    const narrow = W < 520;
    const labelW = narrow ? Math.round(Math.min(130, W * 0.38)) : 190;
    const valueW = narrow ? 56 : 72;
    const rowH = rowHIn ?? (narrow ? 22 : 24);
    const top = 22;
    const H = top + items.length * rowH + 6;
    const vals = items.flatMap((d) => [d.lo ?? d.value, d.hi ?? d.value]).concat(refLine ?? []).filter((v) => v != null && Number.isFinite(v));
    let lo = vals.length ? Math.min(...vals) : 0;
    let hi = vals.length ? Math.max(...vals) : 1;
    if (hi - lo < 1e-9) { lo -= 0.5; hi += 0.5; }
    const pad = (hi - lo) * 0.08;
    lo -= pad;
    hi += pad;
    const x0 = labelW;
    const x1 = W - valueW - 6;
    const sx = (v) => x0 + ((v - lo) / (hi - lo)) * (x1 - x0);
    const ticks = niceTicks(lo, hi, narrow ? 4 : 6);
    const tf = tickFmt ?? fmt;
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
                    <span className="pc-legend--dot">{legend}</span>
                    {refLine != null && <span className="pc-legend--line">{refLabel}</span>}
                </div>
                <ChartExport svgRef={svgRef} name={name} />
            </div>
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={ariaLabel}>
                    {ticks.map((t) => (
                        <g key={t}>
                            <line className="rx-grid" x1={sx(t)} x2={sx(t)} y1={top - 4} y2={H - 4} />
                            <text className="rx-tick" x={sx(t)} y={12} textAnchor="middle">{tf(t)}</text>
                        </g>
                    ))}
                    {refLine != null && <line className="pc-refline" x1={sx(refLine)} x2={sx(refLine)} y1={top - 4} y2={H - 4} />}
                    {items.map((d, i) => {
                        const y = top + i * rowH + rowH / 2;
                        return (
                            <g key={d.key} className={`pc-row${d.highlight ? ' pc-row--hl' : ''}${i === hot ? ' pc-row--hot' : ''}`}>
                                <rect className="pc-row-bg" x={0} y={top + i * rowH} width={W} height={rowH - 1} />
                                <text className="pc-label" x={4} y={y} dominantBaseline="middle">{narrow && d.shortLabel ? d.shortLabel : d.label}</text>
                                {d.lo != null && d.hi != null && <line className="pc-ci" x1={sx(d.lo)} x2={sx(d.hi)} y1={y} y2={y} />}
                                {d.value != null && (
                                    <circle className={`pc-dot${d.tone === 'good' ? ' pc-dot--good' : d.tone === 'bad' ? ' pc-dot--bad' : ''}`}
                                        cx={sx(d.value)} cy={y} r={d.highlight ? 5 : 4} />
                                )}
                                <text className="pc-value" x={W - 4} y={y} textAnchor="end" dominantBaseline="middle">{d.value == null ? '—' : fmt(d.value)}</text>
                            </g>
                        );
                    })}
                    <rect x={0} y={top} width={W} height={items.length * rowH} className="chart-crosshair-overlay"
                        role="slider" tabIndex={0} aria-label={`${ariaLabel}. Use the arrow keys to step through the rows${onPick ? ', Enter to open one' : ''}`}
                        aria-valuetext={hovered ? `${hovered.label}: ${hovered.value == null ? 'no value' : fmt(hovered.value)}` : undefined}
                        style={onPick ? { cursor: 'pointer' } : undefined}
                        onPointerMove={onMove} onPointerLeave={() => setHot(null)} onKeyDown={onKey} onBlur={() => setHot(null)}
                        onClick={() => { if (onPick && hot != null) onPick(items[hot]); }} />
                </svg>
                {hovered && (
                    <ChartTooltip x={Math.min(W - 130, Math.max(130, hovered.value == null ? W / 2 : sx(hovered.value)))} y={top + hot * rowH}
                        chartWidth={W} chartHeight={H} align={hot < 3 ? 'below' : 'above'}>
                        <div style={{ fontWeight: 600 }}>{hovered.label}</div>
                        <div>{hovered.value == null ? 'No value' : fmt(hovered.value)}{hovered.lo != null && hovered.hi != null && ` (95% interval ${fmt(hovered.lo)} to ${fmt(hovered.hi)})`}</div>
                        {hovered.note && <div style={{ color: 'var(--text-3)' }}>{hovered.note}</div>}
                    </ChartTooltip>
                )}
            </div>
        </div>
    );
}

export default IntervalChart;
