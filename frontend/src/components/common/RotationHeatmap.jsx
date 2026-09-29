import React, { useRef, useState } from 'react';
import ChartExport from './ChartExport';
import ChartTooltip from './ChartTooltip';
import { shortName, useWidth } from '../../utils/rotationFormat';
import '../../styles/rotations.css';

// A team-season's rotation as a heatmap (GET /rotations/team): one row per
// player, one column per minute of regulation, shade = the share of the
// team's games he was on the floor in that minute (or, with measure "own",
// of the games he played). Used by the Rotations page and the team page.

const minuteLabel = (m) => {
    const q = Math.floor(m / 12) + 1;
    const left = 12 - (m % 12);
    return `Q${q}, ${left}:00 to ${left - 1}:00`;
};
const pct = (v) => (v == null ? '—' : `${Math.round(v * 100)}%`);

export default function RotationHeatmap({ data, measure = 'team', maxRows, name }) {
    const [boxRef, W] = useWidth();
    const svgRef = useRef(null);
    const [hot, setHot] = useState(null); // { r, c }
    const narrow = W < 560;
    const players = data.players.filter((p) => p.minutes >= 1);
    const rows = (maxRows ? players.slice(0, maxRows) : players).map((p) => ({
        key: p.player_id, label: p.player_name ?? `#${p.player_id}`, starts: p.starts, games: p.games,
        values: measure === 'own' ? p.share_own : p.share, team: p.share, own: p.share_own,
    }));
    if (data.unidentified_minutes > 0 && measure === 'team') {
        rows.push({ key: 'unknown', label: 'Unidentified', unknown: true, values: data.unidentified, team: data.unidentified });
    }
    const labelW = narrow ? 104 : 168;
    const rightW = narrow ? 30 : 58;
    const top = 26;
    const rowH = narrow ? 16 : 19;
    const plotW = W - labelW - rightW;
    const cellW = plotW / 48;
    const H = top + rows.length * rowH + 22;
    const x = (m) => labelW + m * cellW;
    const y = (r) => top + r * rowH;

    const locate = (e) => {
        const svg = e.currentTarget.ownerSVGElement;
        if (!svg) return null;
        const rect = svg.getBoundingClientRect();
        const cx = ((e.clientX - rect.left) / rect.width) * W;
        const cy = ((e.clientY - rect.top) / rect.height) * H;
        const c = Math.floor((cx - labelW) / cellW);
        const r = Math.floor((cy - top) / rowH);
        if (c < 0 || c > 47 || r < 0 || r >= rows.length) return null;
        return { r, c };
    };
    const onKey = (e) => {
        const move = { ArrowRight: [0, 1], ArrowLeft: [0, -1], ArrowDown: [1, 0], ArrowUp: [-1, 0] }[e.key];
        if (move) {
            e.preventDefault();
            setHot((h) => {
                const cur = h ?? { r: 0, c: 0 };
                return { r: Math.max(0, Math.min(rows.length - 1, cur.r + move[0])), c: Math.max(0, Math.min(47, cur.c + move[1])) };
            });
        } else if (e.key === 'Escape') setHot(null);
    };
    const hr = hot ? rows[hot.r] : null;
    const aria = `Rotation heatmap, ${data.team} ${data.season_label}: ${rows.length} rows, 48 minutes of regulation, shade = share of ${measure === 'own' ? 'his own' : "the team's"} ${data.games_counted} counted games on the floor.`;

    return (
        <div className="rot-chart" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={name || `rotation heatmap ${data.team} ${data.season_label}`} />
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={aria}>
                    {[0, 1, 2, 3].map((q) => (
                        <text key={q} className="rx-tick" x={x(q * 12 + 6)} y={14} textAnchor="middle">Q{q + 1}</text>
                    ))}
                    {!narrow && <text className="rx-tick" x={W - rightW / 2} y={14} textAnchor="middle">GS</text>}
                    {rows.map((row, r) => (
                        <g key={row.key}>
                            <text className={`rot-label ${row.unknown ? 'rot-label--unknown' : ''}`} x={labelW - 8} y={y(r) + rowH / 2}
                                textAnchor="end" dominantBaseline="middle">
                                {shortName(row.label, narrow ? 13 : 22)}
                            </text>
                            {row.values.map((v, c) => (
                                <rect key={c} x={x(c) + 0.5} y={y(r) + 1} width={Math.max(cellW - 1, 0.5)} height={rowH - 2}
                                    className={row.unknown ? 'rot-cell rot-cell--unknown' : 'rot-cell'}
                                    fillOpacity={v == null ? 0 : Math.min(1, 0.04 + v * 0.96)} />
                            ))}
                            {!row.unknown && (
                                <text className="rot-right" x={W - rightW / 2} y={y(r) + rowH / 2} textAnchor="middle" dominantBaseline="middle">
                                    {row.starts}
                                </text>
                            )}
                        </g>
                    ))}
                    {[12, 24, 36].map((m) => (
                        <line key={m} className="rot-quarter" x1={x(m)} x2={x(m)} y1={top - 4} y2={top + rows.length * rowH + 2} />
                    ))}
                    {[0, 6, 12, 18, 24, 30, 36, 42, 48].map((m) => (
                        <text key={`t${m}`} className="rx-tick" x={x(m)} y={H - 6} textAnchor="middle">
                            {m === 48 ? '48' : narrow && m % 12 ? '' : m}
                        </text>
                    ))}
                    {hot && (
                        <rect className="rot-hot" x={x(hot.c)} y={y(hot.r)} width={cellW} height={rowH} />
                    )}
                    <rect x={labelW} y={top} width={plotW} height={rows.length * rowH} className="chart-crosshair-overlay"
                        role="slider" tabIndex={0}
                        aria-label="Rotation heatmap: use the arrow keys to move between players and minutes"
                        aria-valuetext={hr ? `${hr.label}, ${minuteLabel(hot.c)}: ${pct(hr.values[hot.c])}` : undefined}
                        onPointerMove={(e) => setHot(locate(e))} onPointerLeave={() => setHot(null)}
                        onKeyDown={onKey} onFocus={() => setHot((h) => h ?? { r: 0, c: 0 })} onBlur={() => setHot(null)} />
                </svg>
                {hr && (
                    <ChartTooltip x={x(hot.c) + cellW / 2} y={y(hot.r)} chartWidth={W} chartHeight={H}>
                        <div style={{ fontWeight: 600 }}>{hr.label}</div>
                        <div>{minuteLabel(hot.c)} of regulation</div>
                        {hr.unknown ? (
                            <div>{pct(hr.team[hot.c])} of a player-slot a game had no identified player</div>
                        ) : (
                            <>
                                <div>On the floor: {pct(hr.team[hot.c])} of the team&apos;s {data.games_counted} games</div>
                                <div style={{ color: 'var(--text-2)' }}>{pct(hr.own[hot.c])} of his {hr.games} · started {hr.starts}</div>
                            </>
                        )}
                    </ChartTooltip>
                )}
            </div>
            <div className="rot-legend" aria-hidden="true">
                <span>0%</span><span className="rot-legend-bar" /><span>100% of {measure === 'own' ? 'his games' : 'games'}</span>
                <span className="rot-legend-gs">GS = games started</span>
            </div>
        </div>
    );
}
