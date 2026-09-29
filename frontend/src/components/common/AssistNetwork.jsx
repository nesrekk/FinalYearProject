import React, { useId, useMemo, useRef, useState } from 'react';
import ChartExport from './ChartExport';
import ChartTooltip from './ChartTooltip';
import { pickNetwork } from '../../utils/assistNetwork';
import { useWidth } from '../../utils/rotationFormat';

// A team-season's assist network (GET /assists/team): its top players by
// minutes around a circle, one curved arrow per passer -> scorer pair
// (width = assists; a -> b and b -> a bend to opposite sides), node size =
// the player's assists. Hover or focus a node to light up his passes
// (brand) and the passes to him (dark); hover or tap a line for its counts.
// Nodes are buttons: Enter/click calls onSelect(player_id). Shared by the
// Assist Network page and the team page's block; the pair table next to it
// is the text version.

const lastName = (name) => {
    if (!name) return '?';
    const parts = name.split(' ').filter((p) => !/^(jr\.?|sr\.?|ii|iii|iv)$/i.test(p));
    return parts.length > 1 ? parts.slice(1).join(' ') : parts[0];
};
const pctOf = (a, b) => (b ? `${Math.round((100 * a) / b)}%` : '—');

export default function AssistNetwork({ players, edges, nPlayers = 10, minEdge = 1, selected, onSelect, name }) {
    const [boxRef, W] = useWidth(720);
    const svgRef = useRef(null);
    const uid = useId().replace(/[^a-zA-Z0-9]/g, '');
    const [hot, setHot] = useState(null); // { node: id } | { edge: key }
    const { nodes, lines } = useMemo(() => pickNetwork(players, edges, nPlayers, minEdge), [players, edges, nPlayers, minEdge]);

    const narrow = W < 560;
    const labelRoom = narrow ? 58 : 110;
    const H = narrow ? Math.round(Math.min(W * 1.05, W - 2 * labelRoom + 76)) : Math.min(600, Math.round(W * 0.68));
    const R = Math.max(80, Math.min(W / 2 - labelRoom, H / 2 - (narrow ? 30 : 36)));
    const cx = W / 2;
    const cy = H / 2;
    const maxAst = Math.max(1, ...nodes.map((p) => p.ast));
    const maxEdge = Math.max(1, ...lines.map((e) => e.ast));
    const pos = {};
    nodes.forEach((p, i) => {
        const a = -Math.PI / 2 + (2 * Math.PI * i) / nodes.length;
        const r = (narrow ? 5 : 6) + (narrow ? 9 : 13) * Math.sqrt(p.ast / maxAst);
        pos[p.player_id] = { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a), a, r, p };
    });
    const key = (e) => `${e.passer_id}-${e.scorer_id}`;
    const focusNode = hot?.node ?? (hot?.edge ? null : selected ?? null);
    const hotEdge = hot?.edge ? lines.find((e) => key(e) === hot.edge) : null;

    const geometry = (e) => {
        const s = pos[e.passer_id];
        const t = pos[e.scorer_id];
        const dx = t.x - s.x;
        const dy = t.y - s.y;
        const d = Math.hypot(dx, dy) || 1;
        // Bend to the right of the direction of travel, so a->b and b->a separate.
        const bend = 0.16 * d;
        const qx = (s.x + t.x) / 2 + (dy / d) * bend;
        const qy = (s.y + t.y) / 2 - (dx / d) * bend;
        const unit = (fx, fy, tx, ty) => { const l = Math.hypot(tx - fx, ty - fy) || 1; return [(tx - fx) / l, (ty - fy) / l]; };
        const [ux0, uy0] = unit(s.x, s.y, qx, qy);
        const [ux1, uy1] = unit(t.x, t.y, qx, qy);
        const x0 = s.x + ux0 * (s.r + 1);
        const y0 = s.y + uy0 * (s.r + 1);
        const x1 = t.x + ux1 * (t.r + 3);
        const y1 = t.y + uy1 * (t.r + 3);
        return { d: `M${x0.toFixed(1)},${y0.toFixed(1)} Q${qx.toFixed(1)},${qy.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`, qx, qy };
    };

    const edgeClass = (e) => {
        if (hotEdge) return key(e) === key(hotEdge) ? 'an-edge an-edge--out' : 'an-edge an-edge--dim';
        if (focusNode == null) return 'an-edge';
        if (e.passer_id === focusNode) return 'an-edge an-edge--out';
        if (e.scorer_id === focusNode) return 'an-edge an-edge--in';
        return 'an-edge an-edge--dim';
    };
    const marker = (cls) => (cls.includes('--in') ? `url(#${uid}-in)` : cls.includes('--dim') ? `url(#${uid}-dim)` : `url(#${uid}-out)`);
    // Draw the lit lines last so they sit on top.
    const order = [...lines].sort((a, b) => {
        const rank = (e) => (edgeClass(e).includes('--dim') ? 0 : edgeClass(e) === 'an-edge' ? 1 : 2);
        return rank(a) - rank(b) || a.ast - b.ast;
    });

    const tipNode = hot?.node != null ? pos[hot.node] : null;
    const nodeTip = (() => {
        if (!tipNode) return null;
        const p = tipNode.p;
        const outs = edges.filter((e) => e.passer_id === p.player_id);
        const ins = edges.filter((e) => e.scorer_id === p.player_id);
        return (
            <>
                <div style={{ fontWeight: 600 }}>{p.player_name}</div>
                <div>{p.ast.toLocaleString()} assists, {p.ast_pts.toLocaleString()} points on them · {Math.round(p.minutes).toLocaleString()} min</div>
                <div>{p.received.toLocaleString()} of his {p.fgm.toLocaleString()} baskets assisted by a named teammate</div>
                {outs[0] && <div>Feeds most: {outs[0].scorer_name} ({outs[0].ast})</div>}
                {ins[0] && <div>Fed most by: {ins[0].passer_name} ({ins[0].ast})</div>}
                {onSelect && <div style={{ color: 'var(--text-3)' }}>Click for all his passes</div>}
            </>
        );
    })();
    const edgeTip = hotEdge ? (
        <>
            <div style={{ fontWeight: 600 }}>{hotEdge.passer_name} → {hotEdge.scorer_name}</div>
            <div>{hotEdge.ast} assists in {hotEdge.games} games, {hotEdge.pts} points</div>
            <div>{hotEdge.kinds.rim} layups/dunks · {hotEdge.kinds.floater} floaters/hooks · {hotEdge.kinds.jumper} 2-pt jumpers · {hotEdge.kinds.three} threes</div>
            <div style={{ color: 'var(--text-3)' }}>
                {pctOf(hotEdge.ast, pos[hotEdge.passer_id]?.p.ast)} of {lastName(hotEdge.passer_name)}&apos;s assists
            </div>
        </>
    ) : null;
    const tipAt = tipNode ? { x: tipNode.x, y: tipNode.y, below: tipNode.y < H / 2 }
        : hotEdge ? (() => { const g = geometry(hotEdge); return { x: g.qx, y: g.qy, below: g.qy < H / 2 }; })() : null;

    const top = lines.slice().sort((a, b) => b.ast - a.ast).slice(0, 3);
    const aria = `Assist network of ${nodes.length} players. Biggest connections: `
        + top.map((e) => `${e.passer_name} to ${e.scorer_name}, ${e.ast} assists`).join('; ') + '.';

    return (
        <div className="an-wrap" ref={boxRef}>
            <div className="an-head">
                <div className="an-legend" aria-hidden="true">
                    <span className="an-legend--out">{focusNode != null ? 'His passes' : 'Passer → scorer'}</span>
                    {focusNode != null && <span className="an-legend--in">Passes to him</span>}
                    <span className="an-legend--size">Circle size = assists</span>
                </div>
                <ChartExport svgRef={svgRef} name={name || 'assist network'} />
            </div>
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" role="group" aria-label={aria} className="an-svg"
                    onPointerLeave={() => setHot(null)}>
                    <defs>
                        {[['out', 'an-arrow--out'], ['in', 'an-arrow--in'], ['dim', 'an-arrow--dim']].map(([k, cls]) => (
                            <marker key={k} id={`${uid}-${k}`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8"
                                markerUnits="userSpaceOnUse" orient="auto-start-reverse">
                                <path d="M0,0 L10,5 L0,10 z" className={`an-arrow ${cls}`} />
                            </marker>
                        ))}
                    </defs>
                    <rect x={0} y={0} width={W} height={H} className="an-bg" onPointerDown={() => setHot(null)} />
                    {order.map((e) => {
                        const g = geometry(e);
                        const cls = edgeClass(e);
                        const w = 1 + (narrow ? 6 : 9) * (e.ast / maxEdge);
                        return (
                            <g key={key(e)}>
                                <path d={g.d} className={cls} strokeWidth={w} markerEnd={marker(cls)} />
                                <path d={g.d} className="an-hit" strokeWidth={Math.max(10, w + 6)}
                                    onPointerEnter={() => setHot({ edge: key(e) })} onPointerDown={() => setHot({ edge: key(e) })} />
                            </g>
                        );
                    })}
                    {nodes.map((p) => {
                        const n = pos[p.player_id];
                        const c = Math.cos(n.a);
                        const s = Math.sin(n.a);
                        const lx = n.x + c * (n.r + 6);
                        const ly = n.y + s * (n.r + 6);
                        const anchor = c > 0.25 ? 'start' : c < -0.25 ? 'end' : 'middle';
                        const base = s > 0.6 ? 'hanging' : s < -0.6 ? 'auto' : 'middle';
                        const lit = focusNode === p.player_id;
                        const dim = focusNode != null && !lit && !lines.some((e) => (e.passer_id === focusNode && e.scorer_id === p.player_id)
                            || (e.scorer_id === focusNode && e.passer_id === p.player_id));
                        return (
                            <g key={p.player_id} className={`an-node${lit ? ' an-node--lit' : ''}${dim ? ' an-node--dim' : ''}`}
                                role={onSelect ? 'button' : undefined} tabIndex={0}
                                aria-label={`${p.player_name}: ${p.ast} assists, ${p.received} baskets assisted by teammates`}
                                aria-pressed={onSelect ? selected === p.player_id : undefined}
                                onPointerEnter={() => setHot({ node: p.player_id })} onFocus={() => setHot({ node: p.player_id })}
                                onBlur={() => setHot(null)}
                                onClick={() => onSelect?.(selected === p.player_id ? null : p.player_id)}
                                onKeyDown={(ev) => {
                                    if ((ev.key === 'Enter' || ev.key === ' ') && onSelect) { ev.preventDefault(); onSelect(selected === p.player_id ? null : p.player_id); }
                                    if (ev.key === 'Escape') setHot(null);
                                }}>
                                <circle cx={n.x} cy={n.y} r={n.r + 8} className="an-node-hit" />
                                <circle cx={n.x} cy={n.y} r={n.r} className="an-node-dot" />
                                <text x={lx} y={ly} textAnchor={anchor} dominantBaseline={base} className="an-label">
                                    {lastName(p.player_name)}
                                </text>
                                {!narrow && (
                                    <text x={lx} y={ly + (base === 'hanging' ? 14 : base === 'auto' ? -14 : 13)} textAnchor={anchor}
                                        dominantBaseline={base} className="an-label an-label--sub">
                                        {p.ast} ast
                                    </text>
                                )}
                            </g>
                        );
                    })}
                </svg>
                {tipAt && (nodeTip || edgeTip) && (
                    <ChartTooltip x={Math.min(W - 130, Math.max(130, tipAt.x))} y={tipAt.y} chartWidth={W} chartHeight={H}
                        align={tipAt.below ? 'below' : 'above'}>
                        {nodeTip || edgeTip}
                    </ChartTooltip>
                )}
            </div>
        </div>
    );
}
