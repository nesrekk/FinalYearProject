import React, { useMemo, useRef } from 'react';
import ChartExport from './ChartExport';

// The half-court shot chart (dots or a zone heat map) of the Shot Charts page,
// shared with the Workbench's shot chart block. `shots` are rows of
// GET /shots/player/{name} (NBA shotchartdetail units: tenths of a foot,
// hoop at (0, 0)). Up to 5,000 dots are drawn.

function clamp(n, lo, hi) {
  return Math.max(lo, Math.min(hi, n));
}

// ─── Court geometry ─────────────────────────────────────────────
// NBA shotchartdetail LOC_X/LOC_Y are in tenths of a foot, hoop-centered at
// (0, 0), Y increasing away from the hoop toward half-court. These are the
// standard real-world court dimensions in that same unit (the numbers widely
// used by NBA shot-chart tooling, e.g. a 23.75ft/237.5-unit three-point arc
// radius, 22ft/220-unit corner threes, etc.) so every line we draw and every
// shot dot we plot go through the exact same transform — a shot beyond the
// real arc will always render outside the drawn arc.
const HOOP_SVG_X = 250;
const HOOP_SVG_Y = 52;
const COURT_SCALE = 0.94; // uniform on both axes so circles stay circles

function toSvgX(rawX) {
  return HOOP_SVG_X + rawX * COURT_SCALE;
}
function toSvgY(rawY) {
  return HOOP_SVG_Y + rawY * COURT_SCALE;
}

// Samples a circular arc (center + radius in raw court units, angles in
// degrees measured the usual math way) into an SVG polyline path — avoids
// fiddly SVG arc-command flags while staying exact since it reuses the same
// toSvgX/toSvgY transform as the shot dots.
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

function rectSvg(xRawMin, yRawMin, xRawMax, yRawMax) {
  return {
    x: toSvgX(xRawMin),
    y: toSvgY(yRawMin),
    width: (xRawMax - xRawMin) * COURT_SCALE,
    height: (yRawMax - yRawMin) * COURT_SCALE,
  };
}

const COURT = {
  outerBox: rectSvg(-250, -47.5, 250, 422.5), // half-court boundary
  paintOuter: rectSvg(-80, -47.5, 80, 142.5),
  paintInner: rectSvg(-60, -47.5, 60, 142.5),
  hoop: { cx: toSvgX(0), cy: toSvgY(0), r: 7.5 * COURT_SCALE },
  backboard: {
    x1: toSvgX(-30), x2: toSvgX(30), y: toSvgY(-7.5),
  },
  restrictedArea: arcPathD(0, 0, 40, 0, 180),
  freeThrowTop: arcPathD(0, 142.5, 60, 0, 180),
  freeThrowBottom: arcPathD(0, 142.5, 60, 180, 360),
  threePointArc: arcPathD(0, 0, 237.5, 22, 158),
  corner3Left: { x1: toSvgX(-220), y1: toSvgY(-47.5), x2: toSvgX(-220), y2: toSvgY(92.5) },
  corner3Right: { x1: toSvgX(220), y1: toSvgY(-47.5), x2: toSvgX(220), y2: toSvgY(92.5) },
  halfCourtOuter: arcPathD(0, 422.5, 60, 180, 360),
  halfCourtInner: arcPathD(0, 422.5, 20, 180, 360),
};

// ─── Zone-density heatmap ───────────────────────────────────────
// Bins shots into a fixed grid (in the same raw court units as everything
// else) rather than plotting each one — color encodes FG% (cold blue ->
// neutral gray around league-average -> hot red), opacity encodes volume,
// so both "where do they shoot from" and "how well" read at a glance.
const HEATMAP_CELL_SIZE = 25; // 2.5 ft
const HEATMAP_X_MIN = -250, HEATMAP_X_MAX = 250;
const HEATMAP_Y_MIN = -47.5, HEATMAP_Y_MAX = 400; // cuts off rare full-court heaves
const HEATMAP_MIN_ATTEMPTS = 3; // don't color a cell off one lucky/unlucky shot

function fgPctColor(pct) {
  const stops = [
    { p: 0.25, c: [37, 99, 235] },   // cold — well below average efficiency
    { p: 0.45, c: [100, 116, 139] }, // neutral — roughly league-average FG%
    { p: 0.70, c: [239, 68, 68] },   // hot — elite efficiency
  ];
  const clamped = Math.max(stops[0].p, Math.min(stops[stops.length - 1].p, pct));
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i], b = stops[i + 1];
    if (clamped >= a.p && clamped <= b.p) {
      const t = (clamped - a.p) / (b.p - a.p);
      const rgb = a.c.map((v, idx) => Math.round(v + t * (b.c[idx] - v)));
      return `rgb(${rgb.join(',')})`;
    }
  }
  return `rgb(${stops[stops.length - 1].c.join(',')})`;
}

function binShotsIntoGrid(shots) {
  const cols = Math.ceil((HEATMAP_X_MAX - HEATMAP_X_MIN) / HEATMAP_CELL_SIZE);
  const rows = Math.ceil((HEATMAP_Y_MAX - HEATMAP_Y_MIN) / HEATMAP_CELL_SIZE);
  const cells = new Map();

  for (const s of shots) {
    const x = Number(s.loc_x || 0);
    const y = Number(s.loc_y || 0);
    if (x < HEATMAP_X_MIN || x > HEATMAP_X_MAX || y < HEATMAP_Y_MIN || y > HEATMAP_Y_MAX) continue;
    const col = Math.min(cols - 1, Math.floor((x - HEATMAP_X_MIN) / HEATMAP_CELL_SIZE));
    const row = Math.min(rows - 1, Math.floor((y - HEATMAP_Y_MIN) / HEATMAP_CELL_SIZE));
    const key = `${col}_${row}`;
    const cur = cells.get(key) || { col, row, attempts: 0, makes: 0 };
    cur.attempts += 1;
    if (Number(s.shot_made_flag) === 1) cur.makes += 1;
    cells.set(key, cur);
  }

  const list = Array.from(cells.values())
    .filter((c) => c.attempts >= HEATMAP_MIN_ATTEMPTS)
    .map((c) => ({
      ...c,
      fgPct: c.makes / c.attempts,
      xRawMin: HEATMAP_X_MIN + c.col * HEATMAP_CELL_SIZE,
      yRawMin: HEATMAP_Y_MIN + c.row * HEATMAP_CELL_SIZE,
    }));

  const maxAttempts = list.reduce((m, c) => Math.max(m, c.attempts), 1);
  return list.map((c) => ({ ...c, volumeRatio: c.attempts / maxAttempts }));
}

// The heat map's colour key (cold = low FG%, hot = high; opacity = volume).
export function HeatmapLegend() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: '0.75rem' }}>
      <span className="page-subtitle">Cold (low FG%)</span>
      <div style={{
        width: 140, height: 10, borderRadius: 5,
        background: 'linear-gradient(90deg, rgb(37,99,235), rgb(100,116,139), rgb(239,68,68))',
      }} />
      <span className="page-subtitle">Hot (high FG%)</span>
      <span className="page-subtitle" style={{ marginLeft: '1rem' }}>· opacity = shot volume · zones with &lt;{HEATMAP_MIN_ATTEMPTS} attempts hidden</span>
    </div>
  );
}

export default function ShotCourt({ shots, viewMode, playerName, season }) {
  const svgRef = useRef(null);
  const heatmapCells = useMemo(() => (viewMode === 'heatmap' ? binShotsIntoGrid(shots) : []), [shots, viewMode]);
  return (
    <div className="court-container">
      <ChartExport svgRef={svgRef} name={`${playerName || 'player'} shot chart ${season || ''}`} />
      <svg ref={svgRef} viewBox="0 0 500 470" className="court-svg" role="img" aria-label={viewMode === 'heatmap'
        ? `Half-court heat map of ${playerName || 'the selected player'}'s real field goal percentage by court zone for the ${season || 'selected'} season, colored from cold (low FG%) to hot (high FG%) with opacity showing shot volume`
        : `Half-court shot chart of every real shot ${playerName || 'the selected player'} attempted in the ${season || 'selected'} season, plotted at its real court location and marked made or missed`}>
        <rect x="0" y="0" width="500" height="470" fill="var(--surface-2)" rx="8" />

        {viewMode === 'heatmap' && heatmapCells.map((c) => {
          const svgCell = rectSvg(c.xRawMin, c.yRawMin, c.xRawMin + HEATMAP_CELL_SIZE, c.yRawMin + HEATMAP_CELL_SIZE);
          return (
            <rect
              key={`${c.col}_${c.row}`}
              x={svgCell.x}
              y={svgCell.y}
              width={svgCell.width}
              height={svgCell.height}
              fill={fgPctColor(c.fgPct)}
              opacity={0.25 + 0.6 * c.volumeRatio}
            >
              <title>{c.attempts} attempts, {c.makes} makes, {(c.fgPct * 100).toFixed(1)}% FG</title>
            </rect>
          );
        })}

        <rect {...COURT.outerBox} fill="none" stroke="var(--hairline)" strokeWidth="2" rx="4" />
        <rect {...COURT.paintOuter} fill="rgba(56,189,248,0.04)" stroke="var(--hairline)" strokeWidth="1.5" />
        <rect {...COURT.paintInner} fill="none" stroke="var(--hairline)" strokeWidth="1" />
        <path d={COURT.freeThrowTop} fill="none" stroke="var(--hairline)" strokeWidth="1.5" />
        <path d={COURT.freeThrowBottom} fill="none" stroke="var(--hairline)" strokeWidth="1" strokeDasharray="4 3" />
        <path d={COURT.restrictedArea} fill="none" stroke="var(--hairline)" strokeWidth="1" strokeDasharray="4 3" />
        <circle cx={COURT.hoop.cx} cy={COURT.hoop.cy} r={COURT.hoop.r} fill="none" stroke="var(--accent)" strokeWidth="2" />
        <line x1={COURT.backboard.x1} y1={COURT.backboard.y} x2={COURT.backboard.x2} y2={COURT.backboard.y} stroke="var(--text-3)" strokeWidth="3" />
        <line {...COURT.corner3Left} stroke="var(--hairline)" strokeWidth="1.5" />
        <line {...COURT.corner3Right} stroke="var(--hairline)" strokeWidth="1.5" />
        <path d={COURT.threePointArc} fill="none" stroke="var(--hairline)" strokeWidth="1.5" />
        <path d={COURT.halfCourtOuter} fill="none" stroke="var(--hairline)" strokeWidth="1" strokeDasharray="6 4" />
        <path d={COURT.halfCourtInner} fill="none" stroke="var(--hairline)" strokeWidth="1" strokeDasharray="6 4" />

        {/* Shots — same toSvgX/toSvgY transform as every court line above,
            so a shot beyond the real arc always renders outside it. */}
        {viewMode === 'dots' && shots.slice(0, 5000).map((s, idx) => {
          const cx = clamp(toSvgX(Number(s.loc_x || 0)), 8, 492);
          const cy = clamp(toSvgY(Number(s.loc_y || 0)), 8, 462);
          const made = Number(s.shot_made_flag) === 1;
          return (
            <circle
              key={`${s.game_id}-${idx}`}
              cx={cx}
              cy={cy}
              r={2.2}
              fill={made ? 'var(--series-5)' : 'var(--series-8)'}
              opacity={made ? 0.85 : 0.65} /* misses fainter, still >= 3:1 on the court (R8-050) */
            />
          );
        })}
      </svg>
    </div>
  );
}
