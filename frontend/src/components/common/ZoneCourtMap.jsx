import React from 'react';

// Same court-unit convention as ShotCharts.jsx (NBA shotchartdetail LOC_X/
// LOC_Y, tenths of a foot, hoop-centered at 0,0) — duplicated rather than
// imported since it's a handful of fixed physical constants, not logic
// likely to change, and ShotCharts.jsx doesn't currently export them.
const HOOP_SVG_X = 250;
const HOOP_SVG_Y = 52;
const COURT_SCALE = 0.94;

function toSvgX(rawX) { return HOOP_SVG_X + rawX * COURT_SCALE; }
function toSvgY(rawY) { return HOOP_SVG_Y + rawY * COURT_SCALE; }

function arcPathD(cxRaw, cyRaw, r, theta1Deg, theta2Deg, steps = 48) {
    const parts = [];
    for (let i = 0; i <= steps; i++) {
        const t = theta1Deg + ((theta2Deg - theta1Deg) * i) / steps;
        const rad = (t * Math.PI) / 180;
        const rawX = cxRaw + r * Math.cos(rad);
        const rawY = cyRaw + r * Math.sin(rad);
        parts.push(`${i === 0 ? 'M' : 'L'} ${toSvgX(rawX).toFixed(2)} ${toSvgY(rawY).toFixed(2)}`);
    }
    return parts.join(' ');
}

// Filled region bounded by the 3PT arc, closed down to the baseline —
// approximates "everything inside the arc" for a zone-color wash (not
// pixel-exact at the corner-3 break, fine for a color map, not a shot plot).
const INSIDE_ARC_PATH = `${arcPathD(0, 0, 237.5, 22, 158, 64)} L ${toSvgX(-220).toFixed(2)} ${toSvgY(-47.5).toFixed(2)} L ${toSvgX(220).toFixed(2)} ${toSvgY(-47.5).toFixed(2)} Z`;
const RESTRICTED_AREA_PATH = `${arcPathD(0, 0, 40, 0, 180, 32)} L ${toSvgX(0).toFixed(2)} ${toSvgY(0).toFixed(2)} Z`;
const PAINT_RECT = { x: toSvgX(-80), y: toSvgY(-47.5), width: 160 * COURT_SCALE, height: 190 * COURT_SCALE };
const FULL_RECT = { x: toSvgX(-250), y: toSvgY(-47.5), width: 500 * COURT_SCALE, height: 470 * COURT_SCALE };
const CORNER_LEFT = { x: toSvgX(-250), y: toSvgY(-47.5), width: 30 * COURT_SCALE, height: 140 * COURT_SCALE };
const CORNER_RIGHT = { x: toSvgX(220), y: toSvgY(-47.5), width: 30 * COURT_SCALE, height: 140 * COURT_SCALE };
const THREE_ARC = arcPathD(0, 0, 237.5, 22, 158);
const BACKBOARD = { x1: toSvgX(-30), x2: toSvgX(30), y: toSvgY(-7.5) };
const HOOP = { cx: toSvgX(0), cy: toSvgY(0), r: 7.5 * COURT_SCALE };

// Cold (well below league avg) -> neutral -> hot (well above league avg),
// centered on the ZONE'S OWN league-average so "red" always means "better
// than league at this specific zone," not an absolute FG% threshold that
// would make every zone near the rim look artificially hot.
function heatColor(playerPct, leaguePct) {
    if (playerPct == null || leaguePct == null) return 'rgba(148,163,184,0.18)';
    const diff = playerPct - leaguePct; // -1..+1 range in practice much smaller
    const t = Math.max(-1, Math.min(1, diff / 0.15)); // +-15pp saturates the scale
    if (t >= 0) {
        // neutral slate -> hot amber/red
        const r = Math.round(100 + t * 155);
        const g = Math.round(116 - t * 60);
        const b = Math.round(139 - t * 100);
        return `rgba(${r},${g},${b},0.55)`;
    }
    // neutral slate -> cold blue
    const at = -t;
    const r = Math.round(100 - at * 70);
    const g = Math.round(116 - at * 20);
    const b = Math.round(139 + at * 100);
    return `rgba(${r},${g},${b},0.55)`;
}

export default function ZoneCourtMap({ zones, leagueZones, size = 280 }) {
    const byZone = Object.fromEntries((zones || []).map((z) => [z.zone, z]));
    const leagueByZone = Object.fromEntries((leagueZones || []).map((z) => [z.zone, z]));

    const colorFor = (zoneName) => heatColor(byZone[zoneName]?.fg_pct, leagueByZone[zoneName]?.fg_pct);

    return (
        <svg viewBox="0 0 500 460" style={{ width: '100%', maxWidth: size, height: 'auto', display: 'block' }}>
            <rect x={FULL_RECT.x} y={FULL_RECT.y} width={FULL_RECT.width} height={FULL_RECT.height} fill="#12151a" rx="6" />
            {/* Above the Break 3 wash covers the whole court; everything below layers on top */}
            <rect x={FULL_RECT.x} y={FULL_RECT.y} width={FULL_RECT.width} height={FULL_RECT.height} fill={colorFor('Above the Break 3')} />
            <path d={INSIDE_ARC_PATH} fill={colorFor('Mid-Range')} />
            <rect {...PAINT_RECT} fill={colorFor('In The Paint (Non-RA)')} />
            <path d={RESTRICTED_AREA_PATH} fill={colorFor('Restricted Area')} />
            <rect {...CORNER_LEFT} fill={colorFor('Corner 3')} />
            <rect {...CORNER_RIGHT} fill={colorFor('Corner 3')} />

            {/* Court lines on top for legibility */}
            <path d={THREE_ARC} fill="none" stroke="rgba(255,255,255,0.35)" strokeWidth="1.5" />
            <line x1={CORNER_LEFT.x + CORNER_LEFT.width} y1={toSvgY(-47.5)} x2={CORNER_LEFT.x + CORNER_LEFT.width} y2={toSvgY(92.5)} stroke="rgba(255,255,255,0.35)" strokeWidth="1.5" />
            <line x1={CORNER_RIGHT.x} y1={toSvgY(-47.5)} x2={CORNER_RIGHT.x} y2={toSvgY(92.5)} stroke="rgba(255,255,255,0.35)" strokeWidth="1.5" />
            <rect x={PAINT_RECT.x} y={PAINT_RECT.y} width={PAINT_RECT.width} height={PAINT_RECT.height} fill="none" stroke="rgba(255,255,255,0.35)" strokeWidth="1.5" />
            <line x1={BACKBOARD.x1} y1={BACKBOARD.y} x2={BACKBOARD.x2} y2={BACKBOARD.y} stroke="rgba(255,255,255,0.6)" strokeWidth="2" />
            <circle cx={HOOP.cx} cy={HOOP.cy} r={HOOP.r} fill="none" stroke="#ff7d46" strokeWidth="2" />
        </svg>
    );
}
