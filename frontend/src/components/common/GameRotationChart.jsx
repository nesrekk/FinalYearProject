import React, { useMemo, useRef } from 'react';
import ChartExport from './ChartExport';
import ChartTooltip from './ChartTooltip';
import useChartCrosshair from '../../utils/useChartCrosshair';
import { shortName, useWidth } from '../../utils/rotationFormat';
import '../../styles/rotations.css';

// One game's rotation (GET /rotations/game/{id}): both teams' players as
// rows, the game clock across, a bar wherever each was on the floor, and the
// score margin (from `team`'s side) underneath. Hover or arrow keys step
// through the stints: who was on, the score, the time.

function clockLabel(t) {
    const mmss = (left) => {
        const r = Math.round(left);
        return `${Math.floor(r / 60)}:${String(r % 60).padStart(2, '0')}`;
    };
    if (t < 2880) {
        const q = Math.floor(Math.min(t, 2879.9) / 720) + 1;
        return `Q${q} ${mmss(720 * q - t)}`;
    }
    const ot = Math.floor(Math.min(t - 2880, 299.9 + 300 * 20) / 300) + 1;
    return `OT${ot > 1 ? ot : ''} ${mmss(2880 + 300 * ot - t)}`;
}

const signed = (v) => (v > 0 ? `+${v}` : v < 0 ? `−${Math.abs(v)}` : '0');

export default function GameRotationChart({ game, team }) {
    const [boxRef, W] = useWidth();
    const svgRef = useRef(null);
    const narrow = W < 560;
    const mine = game.home_team === team ? 'home' : 'away';
    const theirs = mine === 'home' ? 'away' : 'home';
    const sides = [mine, theirs];

    const labelW = narrow ? 100 : 160;
    const rightW = narrow ? 34 : 50;
    const plotW = W - labelW - rightW;
    const rowH = narrow ? 15 : 18;
    const headH = 20;
    const top = 22;
    const x = (t) => labelW + (t / game.length) * plotW;

    const blocks = [];
    let yCursor = top;
    sides.forEach((side) => {
        const s = game[side];
        const rows = s.players.map((p) => ({ key: p.player_id, label: p.player_name ?? `#${p.player_id}`, starter: p.starter,
            minutes: p.minutes, stretches: p.stretches }));
        if (s.unidentified.length) {
            rows.push({ key: 'unknown', label: 'Unidentified', unknown: true,
                minutes: s.unidentified_minutes, stretches: s.unidentified.map((u) => [u.from, u.to]) });
        }
        blocks.push({ side, team: s.team, y: yCursor, rows });
        yCursor += headH + rows.length * rowH + 8;
    });
    const marginTop = yCursor + 8;
    const marginH = narrow ? 70 : 90;
    const H = marginTop + marginH + 24;

    const series = game.margin.map((m) => ({ t: m.t, v: mine === 'home' ? m.home - m.away : m.away - m.home }));
    const maxAbs = Math.max(5, ...series.map((m) => Math.abs(m.v)));
    const my = (v) => marginTop + marginH / 2 - (v / maxAbs) * (marginH / 2 - 4);

    const points = useMemo(() => game.stints.map((s) => ({ x: labelW + (((s.from + s.to) / 2) / game.length) * plotW, s })),
        [game, labelW, plotW]);
    const { point: hovered, overlayProps } = useChartCrosshair(points, W);
    const name = (id) => game.names[String(id)] ?? `#${id}`;
    const onFloor = hovered ? new Set([...hovered.s.home_ids, ...hovered.s.away_ids]) : null;

    const periodLines = game.period_starts.slice(1);
    const periodMid = game.period_starts.map((p, i) => {
        const end = game.period_starts[i + 1] ?? game.length;
        return { x: x((p + end) / 2), label: i < 4 ? `Q${i + 1}` : `OT${i > 4 ? i - 3 : ''}` };
    });
    const aria = `Rotation chart, ${game.away_team} at ${game.home_team}, ${game.date}: bars show when each player was on the floor; the margin below is from ${team}'s side.`;

    return (
        <div className="rot-chart" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={`rotation ${game.away_team} at ${game.home_team} ${game.date}`} />
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={aria}>
                    {periodMid.map((p) => <text key={p.label} className="rx-tick" x={p.x} y={14} textAnchor="middle">{p.label}</text>)}
                    <text className="rx-tick" x={W - rightW / 2} y={14} textAnchor="middle">Min</text>
                    {blocks.map((b) => (
                        <g key={b.side}>
                            <text className={`rot-team ${b.side === mine ? 'rot-team--mine' : ''}`} x={labelW - 8} y={b.y + headH / 2 + 2}
                                textAnchor="end" dominantBaseline="middle">{b.team}</text>
                            <line className="rot-sep" x1={labelW} x2={W - rightW} y1={b.y + headH - 2} y2={b.y + headH - 2} />
                            {b.rows.map((r, i) => {
                                const ry = b.y + headH + i * rowH;
                                const on = onFloor && !r.unknown && onFloor.has(r.key);
                                return (
                                    <g key={r.key}>
                                        <text className={`rot-label ${r.starter ? 'rot-label--starter' : ''} ${r.unknown ? 'rot-label--unknown' : ''} ${on ? 'rot-label--on' : ''}`}
                                            x={labelW - 8} y={ry + rowH / 2} textAnchor="end" dominantBaseline="middle">
                                            {shortName(r.label, narrow ? 13 : 22)}
                                        </text>
                                        <line className="rot-row-line" x1={labelW} x2={W - rightW} y1={ry + rowH / 2} y2={ry + rowH / 2} />
                                        {r.stretches.map(([a, z], k) => (
                                            <rect key={`s${k}`} x={x(a)} y={ry + 2} width={Math.max(x(z) - x(a), 0.8)} height={rowH - 4} rx={2}
                                                className={r.unknown ? 'rot-bar rot-bar--unknown' : b.side === mine ? 'rot-bar rot-bar--mine' : 'rot-bar'} />
                                        ))}
                                        <text className="rot-right" x={W - rightW / 2} y={ry + rowH / 2} textAnchor="middle" dominantBaseline="middle">
                                            {Math.round(r.minutes)}
                                        </text>
                                    </g>
                                );
                            })}
                        </g>
                    ))}
                    {periodLines.map((p) => (
                        <line key={p} className="rot-quarter" x1={x(p)} x2={x(p)} y1={top} y2={marginTop + marginH} />
                    ))}
                    <text className="rot-team" x={labelW - 8} y={marginTop + marginH / 2} textAnchor="end" dominantBaseline="middle">
                        {team} margin
                    </text>
                    <line className="rp-zero" x1={labelW} x2={W - rightW} y1={my(0)} y2={my(0)} />
                    {series.slice(0, -1).map((m, i) => {
                        const next = series[i + 1];
                        if (m.v === 0) return null;
                        return (
                            <rect key={`m${i}`} x={x(m.t)} width={Math.max(x(next.t) - x(m.t), 0.5)}
                                y={Math.min(my(0), my(m.v))} height={Math.abs(my(m.v) - my(0))}
                                className={m.v > 0 ? 'rot-margin rot-margin--pos' : 'rot-margin rot-margin--neg'} />
                        );
                    })}
                    <text className="rx-tick" x={labelW + 4} y={marginTop + 8} dominantBaseline="middle">+{maxAbs}</text>
                    <text className="rx-tick" x={labelW + 4} y={marginTop + marginH - 6} dominantBaseline="middle">−{maxAbs}</text>
                    <text className="rot-right" x={W - rightW / 2} y={my(series[series.length - 1]?.v ?? 0)} textAnchor="middle" dominantBaseline="middle">
                        {signed(series[series.length - 1]?.v ?? 0)}
                    </text>
                    {[0, 720, 1440, 2160, 2880].filter((t) => t <= game.length).map((t) => (
                        <text key={t} className="rx-tick" x={x(t)} y={H - 6} textAnchor="middle">{t / 60}′</text>
                    ))}
                    {hovered && (
                        <>
                            <line className="chart-crosshair-line" x1={x(hovered.s.from)} x2={x(hovered.s.from)} y1={top} y2={marginTop + marginH} />
                            <line className="chart-crosshair-line" x1={x(hovered.s.to)} x2={x(hovered.s.to)} y1={top} y2={marginTop + marginH} />
                        </>
                    )}
                    <rect x={labelW} y={top} width={plotW} height={marginTop + marginH - top} className="chart-crosshair-overlay"
                        role="slider" aria-label="Rotation chart: use the arrow keys to step through the stints"
                        aria-valuetext={hovered ? `${clockLabel(hovered.s.from)} to ${clockLabel(hovered.s.to)}` : undefined}
                        {...overlayProps} />
                </svg>
                {hovered && (() => {
                    const s = hovered.s;
                    const mineIds = mine === 'home' ? s.home_ids : s.away_ids;
                    const theirIds = mine === 'home' ? s.away_ids : s.home_ids;
                    const scoreMine = mine === 'home' ? s.home_score : s.away_score;
                    const scoreTheirs = mine === 'home' ? s.away_score : s.home_score;
                    const ptsMine = mine === 'home' ? s.home_pts : s.away_pts;
                    const ptsTheirs = mine === 'home' ? s.away_pts : s.home_pts;
                    return (
                        <ChartTooltip x={hovered.x} y={top + 10} chartWidth={W} chartHeight={H} align="below">
                            <div style={{ fontWeight: 600 }}>{clockLabel(s.from)} to {clockLabel(s.to)}</div>
                            <div>Score at the start: {team} {scoreMine}-{scoreTheirs}; this stint {ptsMine}-{ptsTheirs}</div>
                            <div className="rot-tip-five"><strong>{team}</strong> {mineIds.map(name).join(', ')}{mineIds.length < 5 ? ' + unidentified' : ''}</div>
                            <div className="rot-tip-five" style={{ color: 'var(--text-2)' }}>
                                <strong>{game[theirs].team}</strong> {theirIds.map(name).join(', ')}{theirIds.length < 5 ? ' + unidentified' : ''}
                            </div>
                        </ChartTooltip>
                    );
                })()}
            </div>
        </div>
    );
}
