import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchLivePlayerSuggestions, fetchPlayerShots } from '../../services/api';
import Icon from '../common/Icon';
import PlayerName from '../common/PlayerName';
import TableExport from '../common/TableExport';
import ChartExport from '../common/ChartExport';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import ShotMixHistory from '../common/ShotMixHistory';
import { ShotMakingLeaderboard, ShotMakingModel, ShotMakingPlayer } from '../common/ShotMaking';
import ShotQualityMap from '../common/ShotQualityMap';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';

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

function zoneForShot(s) {
  const x = Number(s.loc_x || 0);
  const y = Number(s.loc_y || 0);
  const dist = Number(s.shot_distance || 0);
  const is3 = String(s.shot_type || '').includes('3PT');

  // Basic, explainable zones (good enough for demo).
  if (!is3 && dist <= 4) return 'Rim';
  if (!is3 && dist <= 16) return 'Paint';
  if (!is3) return 'Midrange';

  const isCorner = Math.abs(x) > 220 && y < 140;
  if (isCorner) return 'Corner 3';
  return 'Above the Break 3';
}

function computeZoneStats(shots) {
  const byZone = new Map();
  for (const s of shots) {
    const zone = zoneForShot(s);
    const made = Number(s.shot_made_flag) === 1;
    const cur = byZone.get(zone) || { zone, makes: 0, attempts: 0 };
    cur.attempts += 1;
    if (made) cur.makes += 1;
    byZone.set(zone, cur);
  }
  const rows = Array.from(byZone.values()).map((z) => ({
    ...z,
    fgPct: z.attempts ? z.makes / z.attempts : 0,
  }));
  rows.sort((a, b) => b.attempts - a.attempts);
  return rows;
}

export default function ShotCharts() {
  const svgRef = useRef(null);
  // A shared link carries ?player=&season=&view= (utils/useUrlState.js).
  const params = useInitialParams();
  const [searchInput, setSearchInput] = useState(() => parseParam.str(params, 'player') ?? 'Stephen Curry');
  const [suggestions, setSuggestions] = useState([]);
  const [searchingSuggestions, setSearchingSuggestions] = useState(false);

  const [resolvedPlayer, setResolvedPlayer] = useState('');
  const [resolvedPlayerId, setResolvedPlayerId] = useState(null);
  const [shots, setShots] = useState([]);
  const [seasons, setSeasons] = useState([]);
  const [season, setSeason] = useState('');
  const [source, setSource] = useState('');
  const [viewMode, setViewMode] = useState(() => parseParam.oneOf(params, 'view', ['heatmap', 'shotmaking', 'quality']) ?? 'dots'); // 'dots' | 'heatmap' | 'shotmaking' | 'quality'
  // Quality map tab: colour reference, the player's season, an optional second player and cell floor (qm=, qs=, vs=, vss=, qmin=).
  const [quality, setQuality] = useState(() => ({
    mode: parseParam.oneOf(params, 'qm', ['expected', 'league']) ?? 'expected',
    season: parseParam.int(params, 'qs', { min: 1997, max: 2100 }),
    vs: parseParam.str(params, 'vs'),
    vsSeason: parseParam.int(params, 'vss', { min: 1997, max: 2100 }),
    minShots: [1, 2, 3, 5, 10].includes(parseParam.int(params, 'qmin')) ? parseParam.int(params, 'qmin') : 2,
  }));
  // Shot-making tab: the leaderboard's season, ranking and order (rank=, by=, order= in the link).
  const [rank, setRank] = useState(() => ({
    season: parseParam.int(params, 'rank', { min: 1997, max: 2100 }),
    sort: parseParam.oneOf(params, 'by', ['shot_making', 'quality', 'pts_above', 'efg']) ?? 'shot_making',
    order: parseParam.oneOf(params, 'order', ['asc', 'desc']) ?? 'desc',
  }));

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Debounced live suggestions, same pattern as PlayerStats.jsx.
  useEffect(() => {
    const query = searchInput.trim();
    if (query.length < 2 || query.toLowerCase() === resolvedPlayer.toLowerCase()) {
      setSuggestions([]);
      return;
    }
    let active = true;
    const timer = setTimeout(async () => {
      setSearchingSuggestions(true);
      try {
        const data = await fetchLivePlayerSuggestions(query, 8);
        if (active) setSuggestions(data?.results ?? []);
      } catch {
        if (active) setSuggestions([]);
      } finally {
        if (active) setSearchingSuggestions(false);
      }
    }, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [searchInput, resolvedPlayer]);

  async function loadPlayer(name, seasonOverride) {
    const target = (name || '').trim();
    if (!target) return;
    setLoading(true);
    setError('');
    try {
      const data = await fetchPlayerShots(target, seasonOverride || undefined);
      setResolvedPlayer(data.player_name);
      setResolvedPlayerId(data.player_id ?? null);
      setSeasons(data.seasons || []);
      setSeason(data.season || '');
      setShots(data.shots || []);
      setSource(data.source || '');
      setSearchInput(data.player_name);
      setSuggestions([]);
    } catch (e) {
      const detail = e?.response?.data?.detail;
      setError(detail || e?.message || 'Failed to load shot data.');
      setShots([]);
    } finally {
      setLoading(false);
    }
  }

  // Load the linked player and season, or Curry by default (already cached — instant).
  useEffect(() => {
    const linkedSeason = params.get('season');
    loadPlayer(parseParam.str(params, 'player') ?? 'Stephen Curry',
      /^\d{4}-\d{2}$/.test(linkedSeason ?? '') ? linkedSeason : undefined);
  }, [params]);

  // Nothing is written until a player has loaded, so a slow first load
  // doesn't wipe the link it came from.
  const onShotMaking = viewMode === 'shotmaking';
  const onQuality = viewMode === 'quality';
  useUrlSync(resolvedPlayer ? {
    player: resolvedPlayer, season, view: viewMode === 'dots' ? null : viewMode,
    rank: onShotMaking ? rank.season : null,
    by: onShotMaking && rank.sort !== 'shot_making' ? rank.sort : null,
    order: onShotMaking && rank.order !== 'desc' ? rank.order : null,
    qm: onQuality && quality.mode !== 'expected' ? quality.mode : null,
    qs: onQuality ? quality.season : null,
    vs: onQuality ? quality.vs : null,
    vss: onQuality && quality.vs ? quality.vsSeason : null,
    qmin: onQuality && quality.minShots !== 2 ? quality.minShots : null,
  } : null);

  function handleSeasonChange(newSeason) {
    setSeason(newSeason);
    loadPlayer(resolvedPlayer, newSeason);
  }

  const totals = useMemo(() => {
    const att = shots.length;
    const makes = shots.reduce((acc, s) => acc + (Number(s.shot_made_flag) === 1 ? 1 : 0), 0);
    const threes = shots.filter((s) => String(s.shot_type || '').includes('3PT'));
    const tAtt = threes.length;
    const tMake = threes.reduce((acc, s) => acc + (Number(s.shot_made_flag) === 1 ? 1 : 0), 0);
    return {
      attempts: att,
      makes,
      fgPct: att ? (makes / att) : 0,
      threeAtt: tAtt,
      threeMake: tMake,
      threePct: tAtt ? (tMake / tAtt) : 0,
    };
  }, [shots]);

  const zoneStats = useMemo(() => computeZoneStats(shots), [shots]);
  const hottestZone = useMemo(() => {
    const eligible = zoneStats.filter((z) => z.attempts >= 40);
    if (!eligible.length) return null;
    return eligible.slice().sort((a, b) => b.fgPct - a.fgPct)[0];
  }, [zoneStats]);

  const heatmapCells = useMemo(() => binShotsIntoGrid(shots), [shots]);

  return (
    <div className="page page-shotcharts fade-in">
      <div className="dashboard-card" style={{ marginBottom: '1rem' }}>
        <h2 className="card-title hb-page-title">
          <span className="card-icon"><Icon name="adjust" /></span>
          Shot Chart
          <CopyLinkButton />
          <SaveViewButton pageId="shotcharts" />
        </h2>

        <div className="input-row" style={{ marginBottom: 0 }}>
          <div style={{ position: 'relative', flex: 1 }}>
            <input
              type="text"
              className="input-field"
              placeholder="Search any player (e.g. LeBron, Jokic, Curry)…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') loadPlayer(searchInput);
              }}
            />
            {suggestions.length > 0 && (
              <ul className="autocomplete-list" style={{
                position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
                background: 'var(--surface)', border: '2px solid var(--line)', borderRadius: 0, boxShadow: 'var(--shadow-card)',
                marginTop: 4, maxHeight: 220, overflowY: 'auto', listStyle: 'none', padding: 0,
              }}>
                {suggestions.map((name) => (
                  <li key={name}>
                    <button
                      type="button"
                      onClick={() => loadPlayer(name)}
                      style={{
                        display: 'block', width: '100%', textAlign: 'left', padding: '0.5rem 0.75rem',
                        background: 'transparent', border: 'none', color: 'var(--text)', cursor: 'pointer',
                      }}
                    >
                      {name}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <button
            type="button"
            className="btn-primary"
            onClick={() => loadPlayer(searchInput)}
            disabled={loading}
          >
            {loading ? 'Loading…' : 'Search'}
          </button>

          <select
            className="input-field"
            value={season}
            onChange={(e) => handleSeasonChange(e.target.value)}
            disabled={loading || !seasons.length}
          >
            {!seasons.length && <option value="">No seasons loaded</option>}
            {seasons.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>

        <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>
          {resolvedPlayer ? (
            <PlayerName playerId={resolvedPlayerId} name={resolvedPlayer} size={40}>
              <span className="text-eyebrow" style={{ display: 'block', marginTop: 2 }}>
                {shots.length.toLocaleString()} shots · {seasons.length} seasons{source === 'live' ? ' · fetched live just now' : ''}
              </span>
            </PlayerName>
          ) : 'Search a player to load their shot chart'}
        </p>

        {loading && (
          <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>
            Loading shot data… first-time searches for a player we haven't cached yet can take
            a couple of minutes — we deliberately rate-limit requests to NBA's stats API so we
            don't get blocked. Already-searched players load instantly.
          </p>
        )}
        {error && <p className="error-message" style={{ marginTop: '0.75rem' }}>{error}</p>}
        {searchingSuggestions && <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>Searching…</p>}
      </div>

      <div className="dashboard-card" style={{ marginBottom: '1rem' }}>
        <div className="tab-bar" style={{ marginBottom: 0 }}>
          <button
            type="button"
            className={`tab-btn ${viewMode === 'dots' ? 'tab-btn--active' : ''}`}
            onClick={() => setViewMode('dots')}
          >
            Shot Dots
          </button>
          <button
            type="button"
            className={`tab-btn ${viewMode === 'heatmap' ? 'tab-btn--active' : ''}`}
            onClick={() => setViewMode('heatmap')}
          >
            Heat Map
          </button>
          <button
            type="button"
            className={`tab-btn ${viewMode === 'shotmaking' ? 'tab-btn--active' : ''}`}
            onClick={() => setViewMode('shotmaking')}
          >
            Shot-making
          </button>
          <button
            type="button"
            className={`tab-btn ${viewMode === 'quality' ? 'tab-btn--active' : ''}`}
            onClick={() => setViewMode('quality')}
          >
            Quality map
          </button>
        </div>
        {viewMode === 'heatmap' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: '0.75rem' }}>
            <span className="page-subtitle">Cold (low FG%)</span>
            <div style={{
              width: 140, height: 10, borderRadius: 5,
              background: 'linear-gradient(90deg, rgb(37,99,235), rgb(100,116,139), rgb(239,68,68))',
            }} />
            <span className="page-subtitle">Hot (high FG%)</span>
            <span className="page-subtitle" style={{ marginLeft: '1rem' }}>· opacity = shot volume · zones with &lt;{HEATMAP_MIN_ATTEMPTS} attempts hidden</span>
          </div>
        )}
      </div>

      {!onShotMaking && !onQuality && (<>
      <div className="court-container">
        <ChartExport svgRef={svgRef} name={`${resolvedPlayer || 'player'} shot chart ${season || ''}`} />
        <svg ref={svgRef} viewBox="0 0 500 470" className="court-svg" role="img" aria-label={viewMode === 'heatmap'
          ? `Half-court heat map of ${resolvedPlayer || 'the selected player'}'s real field goal percentage by court zone for the ${season || 'selected'} season, colored from cold (low FG%) to hot (high FG%) with opacity showing shot volume`
          : `Half-court shot chart of every real shot ${resolvedPlayer || 'the selected player'} attempted in the ${season || 'selected'} season, plotted at its real court location and marked made or missed`}>
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
                fill={made ? '#00e5ff' : '#f87171'}
                opacity={made ? 0.85 : 0.45}
              />
            );
          })}
        </svg>
      </div>

      <div className="dashboard-card" style={{ marginTop: '1rem' }}>
        <h3 className="section-heading" style={{ marginTop: 0 }}>Summary</h3>
        <div className="stat-cards-row" style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
          <div className="stat-card">
            <div className="stat-card-label">Shots</div>
            <div className="stat-card-value">{totals.attempts}</div>
          </div>
          <div className="stat-card">
            <div className="stat-card-label">FG%</div>
            <div className="stat-card-value">{(totals.fgPct * 100).toFixed(1)}%</div>
          </div>
          <div className="stat-card">
            <div className="stat-card-label">3P%</div>
            <div className="stat-card-value">{(totals.threePct * 100).toFixed(1)}%</div>
          </div>
          <div className="stat-card">
            <div className="stat-card-label">Hot zone</div>
            <div className="stat-card-value">{hottestZone ? hottestZone.zone : '—'}</div>
          </div>
        </div>

        <TableExport />
        <div className="table-wrapper" style={{ marginTop: '1rem' }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Zone</th>
                <th>FG%</th>
                <th>Makes</th>
                <th>Attempts</th>
              </tr>
            </thead>
            <tbody>
              {zoneStats.map((z) => (
                <tr key={z.zone}>
                  <td>{z.zone}</td>
                  <td className="text-accent">{(z.fgPct * 100).toFixed(1)}%</td>
                  <td>{z.makes}</td>
                  <td>{z.attempts}</td>
                </tr>
              ))}
              {!zoneStats.length && (
                <tr><td colSpan={4} className="empty-message">No shots loaded for this season.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>
          Showing up to 5,000 shots for performance.
        </p>
      </div>

      </>)}

      {onQuality && resolvedPlayer && (
        <ShotQualityMap playerName={resolvedPlayer} state={quality}
          onChange={(patch) => setQuality((prev) => ({ ...prev, ...patch }))} />
      )}

      {onShotMaking && resolvedPlayer && (
        <>
          <ShotMakingPlayer key={resolvedPlayer} playerName={resolvedPlayer} />
          <ShotMakingLeaderboard season={rank.season} sort={rank.sort} order={rank.order}
            onChange={(patch) => setRank((prev) => ({ ...prev, ...patch }))} />
          <ShotMakingModel />
        </>
      )}

      {resolvedPlayer && <ShotMixHistory key={resolvedPlayer} playerName={resolvedPlayer} />}
    </div>
  );
}
