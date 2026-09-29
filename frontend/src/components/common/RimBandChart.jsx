import React, { useRef, useState } from 'react';
import ChartExport from './ChartExport';
import ChartTooltip from './ChartTooltip';
import { useWidth } from '../../utils/rotationFormat';

// One defender's opponents, by shot distance: attempts per 100 possessions
// with him on the floor (brand bar) and off it (grey bar), FG% and attempts
// beside each pair. Shared by the Rim Deterrence tab and the profile block.
// `row` is one /defense/rim-deterrence player row (or a profile row); `bands`
// is the endpoint's band list; `league` (optional) the season's league bands.

const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
const n = (v) => (v == null ? '—' : Math.round(v).toLocaleString());

export default function RimBandChart({ row, bands, league, name }) {
    const [boxRef, W] = useWidth(560);
    const svgRef = useRef(null);
    const [hot, setHot] = useState(null);
    const shown = bands.filter((b) => b.id !== 'unk' || row.bands.unk.on.fga + row.bands.unk.off.fga > 0);
    const narrow = W < 520;
    const labelW = narrow ? 64 : 84;
    const textW = narrow ? 118 : 280;
    const rowH = narrow ? 40 : 34;
    const top = 8;
    const H = top + shown.length * rowH + 8;
    const barX = labelW;
    const barW = Math.max(60, W - labelW - textW - 8);
    const max = Math.max(1, ...shown.flatMap((b) => [row.bands[b.id].on.per100 ?? 0, row.bands[b.id].off.per100 ?? 0]));
    const sx = (v) => barX + (Math.max(0, v ?? 0) / max) * barW;
    const bh = narrow ? 11 : 10;
    const hovered = hot != null ? shown[hot] : null;
    const onKey = (e) => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowRight') { e.preventDefault(); setHot((i) => (i == null ? 0 : Math.min(shown.length - 1, i + 1))); }
        else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') { e.preventDefault(); setHot((i) => (i == null ? shown.length - 1 : Math.max(0, i - 1))); }
        else if (e.key === 'Escape') setHot(null);
    };
    const onMove = (e) => {
        const svg = e.currentTarget.ownerSVGElement;
        if (!svg) return;
        const rect = svg.getBoundingClientRect();
        const y = ((e.clientY - rect.top) / rect.height) * H;
        const i = Math.floor((y - top) / rowH);
        setHot(i >= 0 && i < shown.length ? i : null);
    };
    const aria = `Opponents' attempts per 100 possessions by distance with ${row.player_name ?? 'him'} on and off the floor: `
        + shown.map((b) => `${b.label} ${row.bands[b.id].on.per100 ?? '—'} on, ${row.bands[b.id].off.per100 ?? '—'} off`).join('; ');
    return (
        <div className="rim-bands" ref={boxRef}>
            <div className="rim-bands-head">
                <div className="rim-legend" aria-hidden="true">
                    <span className="rim-legend--on">On the floor</span>
                    <span className="rim-legend--off">Off</span>
                </div>
                <ChartExport svgRef={svgRef} name={name || `rim deterrence bands ${row.player_name ?? ''}`} />
            </div>
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={aria}>
                    {shown.map((b, i) => {
                        const y0 = top + i * rowH;
                        const on = row.bands[b.id].on;
                        const off = row.bands[b.id].off;
                        return (
                            <g key={b.id} className={i === hot ? 'rim-band rim-band--hot' : 'rim-band'}>
                                <rect className="rim-band-bg" x={0} y={y0} width={W} height={rowH - 2} />
                                <text className="rim-band-label" x={4} y={y0 + rowH / 2} dominantBaseline="middle">{b.label}</text>
                                <rect className="rim-bar rim-bar--on" x={barX} y={y0 + rowH / 2 - bh - 1} width={Math.max(0, sx(on.per100) - barX)} height={bh} rx={2} />
                                <rect className="rim-bar rim-bar--off" x={barX} y={y0 + rowH / 2 + 1} width={Math.max(0, sx(off.per100) - barX)} height={bh} rx={2} />
                                <text className="rim-band-val" x={W - 4} y={y0 + rowH / 2 - (narrow ? 7 : 0)} textAnchor="end" dominantBaseline="middle">
                                    {narrow ? `${(on.per100 ?? 0).toFixed(1)} vs ${(off.per100 ?? 0).toFixed(1)}` : `${(on.per100 ?? 0).toFixed(1)} vs ${(off.per100 ?? 0).toFixed(1)} per 100 · ${pct(on.fg)} vs ${pct(off.fg)}`}
                                </text>
                                {narrow && (
                                    <text className="rim-band-val rim-band-val--sub" x={W - 4} y={y0 + rowH / 2 + 8} textAnchor="end" dominantBaseline="middle">
                                        FG {pct(on.fg)} vs {pct(off.fg)}
                                    </text>
                                )}
                            </g>
                        );
                    })}
                    <rect x={0} y={top} width={W} height={shown.length * rowH} className="chart-crosshair-overlay"
                        role="slider" aria-label="Distance bands, use arrow keys to step through them"
                        aria-valuetext={hovered ? `${hovered.label}: ${row.bands[hovered.id].on.per100} attempts per 100 on, ${row.bands[hovered.id].off.per100} off` : undefined}
                        tabIndex={0} onPointerMove={onMove} onPointerLeave={() => setHot(null)} onKeyDown={onKey} onBlur={() => setHot(null)} />
                </svg>
                {hovered && (() => {
                    const on = row.bands[hovered.id].on;
                    const off = row.bands[hovered.id].off;
                    const lg = league?.bands?.[hovered.id];
                    return (
                        <ChartTooltip x={Math.min(W - 120, Math.max(120, sx(Math.max(on.per100 ?? 0, off.per100 ?? 0))))}
                            y={top + hot * rowH} chartWidth={W} chartHeight={H} align={hot < 2 ? 'below' : 'above'}>
                            <div style={{ fontWeight: 600 }}>{hovered.long} ({hovered.label})</div>
                            <div>On: {n(on.fga)} attempts, {n(on.fgm)} made ({pct(on.fg)}), {(on.per100 ?? 0).toFixed(1)} per 100, {pct(on.share)} of all</div>
                            <div>Off: {n(off.fga)} attempts, {n(off.fgm)} made ({pct(off.fg)}), {(off.per100 ?? 0).toFixed(1)} per 100, {pct(off.share)} of all</div>
                            {lg && <div style={{ color: 'var(--text-3)' }}>League: {lg.per100?.toFixed(1)} per 100, {pct(lg.fg)}</div>}
                        </ChartTooltip>
                    );
                })()}
            </div>
        </div>
    );
}
