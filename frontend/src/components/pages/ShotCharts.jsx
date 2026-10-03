import React, { useEffect, useMemo, useState } from 'react';
import { fetchLivePlayerSuggestions, fetchPlayerShots } from '../../services/api';
import Icon from '../common/Icon';
import PlayerName from '../common/PlayerName';
import TableExport from '../common/TableExport';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import ShotCourt, { HeatmapLegend } from '../common/ShotCourt';
import ShotMixHistory from '../common/ShotMixHistory';
import { ShotMakingLeaderboard, ShotMakingModel, ShotMakingPlayer } from '../common/ShotMaking';
import ShotQualityMap from '../common/ShotQualityMap';
import ShotValue from '../common/ShotValue';
import shotTotals from '../../utils/shotTotals';
import { SV_MINS, SV_SORTS } from '../../utils/shotValue';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';

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
  const [viewMode, setViewMode] = useState(() => parseParam.oneOf(params, 'view', ['heatmap', 'shotmaking', 'quality', 'value']) ?? 'dots'); // 'dots' | 'heatmap' | 'shotmaking' | 'quality' | 'value'
  // Shot value tab (round 6 step 8): the leaderboard's season, sort, direction and attempts floor (sv=, svby=, svdir=, svmin=).
  const [svState, setSvState] = useState(() => ({
    season: parseParam.int(params, 'sv', { min: 2021, max: 2100 }),
    sort: parseParam.oneOf(params, 'svby', SV_SORTS) ?? 'sva',
    dir: parseParam.oneOf(params, 'svdir', ['asc', 'desc']) ?? 'desc',
    minFga: SV_MINS.includes(parseParam.int(params, 'svmin')) ? parseParam.int(params, 'svmin') : 200,
  }));
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
  const onValue = viewMode === 'value';
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
    sv: onValue ? svState.season : null,
    svby: onValue && svState.sort !== 'sva' ? svState.sort : null,
    svdir: onValue && svState.dir !== 'desc' ? svState.dir : null,
    svmin: onValue && svState.minFga !== 200 ? svState.minFga : null,
  } : null);

  function handleSeasonChange(newSeason) {
    setSeason(newSeason);
    loadPlayer(resolvedPlayer, newSeason);
  }

  const totals = useMemo(() => shotTotals(shots), [shots]);

  const zoneStats = useMemo(() => computeZoneStats(shots), [shots]);
  const hottestZone = useMemo(() => {
    const eligible = zoneStats.filter((z) => z.attempts >= 40);
    if (!eligible.length) return null;
    return eligible.slice().sort((a, b) => b.fgPct - a.fgPct)[0];
  }, [zoneStats]);

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
          <button
            type="button"
            className={`tab-btn ${viewMode === 'value' ? 'tab-btn--active' : ''}`}
            onClick={() => setViewMode('value')}
          >
            Shot value
          </button>
        </div>
        {viewMode === 'heatmap' && <HeatmapLegend />}
      </div>

      {!onShotMaking && !onQuality && !onValue && (<>
      <ShotCourt shots={shots} viewMode={viewMode} playerName={resolvedPlayer} season={season} />

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

      {onValue && resolvedPlayer && (
        <ShotValue playerId={resolvedPlayerId} playerName={resolvedPlayer} state={svState}
          onChange={(patch) => setSvState((prev) => ({ ...prev, ...patch }))} />
      )}

      {resolvedPlayer && <ShotMixHistory key={resolvedPlayer} playerName={resolvedPlayer} />}
    </div>
  );
}
