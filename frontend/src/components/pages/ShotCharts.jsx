import React, { useEffect, useMemo, useState } from 'react';
import { fetchPlayerShots } from '../../services/api';
import Icon from '../common/Icon';
import PlayerName from '../common/PlayerName';
import TableExport from '../common/TableExport';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import ShotCourt, { HeatmapLegend } from '../common/ShotCourt';
import ShotMixHistory from '../common/ShotMixHistory';
import { ShotMakingLeaderboard, ShotMakingModel, ShotMakingPlayer } from '../common/ShotMaking';
import ShotQualityMap from '../common/ShotQualityMap';
import ShotValue from '../common/ShotValue';
import shotTotals, { SHOT_GAMES } from '../../utils/shotTotals';
import { namesakes, playerSpan, searchPlayers } from '../../utils/playerChoice';
import { SV_MINS, SV_SORTS } from '../../utils/shotValue';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import { LiveSeasonTag } from '../common/LiveSeasonNote';

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
  // A shared link carries ?player=&pid=&season=&games=&view= (utils/useUrlState.js).
  // pid (NBA id) picks the player; player (the name) keeps the link readable and,
  // alone, still works: it opens the latest career of that name.
  const params = useInitialParams();
  const [searchInput, setSearchInput] = useState(() => parseParam.str(params, 'player') ?? 'Stephen Curry');
  const [suggestions, setSuggestions] = useState([]); // [{ id, name, from, to, team }]
  const [searchingSuggestions, setSearchingSuggestions] = useState(false);

  const [resolvedPlayer, setResolvedPlayer] = useState('');
  const [resolvedPlayerId, setResolvedPlayerId] = useState(null);
  const [shots, setShots] = useState([]);
  const [seasons, setSeasons] = useState([]);
  const [season, setSeason] = useState('');
  const [source, setSource] = useState('');
  const [badge, setBadge] = useState(null); // the shots route's _source (R8-014)
  // Which games' shots to draw and count (player_shots mixes in playoffs and play-in).
  const [games, setGames] = useState(() => parseParam.oneOf(params, 'games', ['playoffs', 'all']) ?? 'regular');
  // Other players with the loaded player's name: { for: id, list }.
  const [others, setOthers] = useState({ for: null, list: [] });
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
    vsId: parseParam.int(params, 'vsid', { min: 1 }),
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

  // Debounced suggestions with each player's id and career span.
  useEffect(() => {
    const query = searchInput.trim();
    if (query.length < 2 || query.toLowerCase() === resolvedPlayer.toLowerCase()) {
      setSuggestions([]);
      setSearchingSuggestions(false);
      return;
    }
    let active = true;
    const timer = setTimeout(async () => {
      setSearchingSuggestions(true);
      try {
        const data = await searchPlayers(query);
        if (active) setSuggestions(data.slice(0, 8));
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

  // By id when known; a typed name takes the latest player of exactly that
  // name (case and accents ignored), else the server's name lookup.
  async function loadPlayer(name, seasonOverride, id) {
    const target = (name || '').trim() || (id ? String(id) : '');
    if (!target) return;
    setLoading(true);
    setError('');
    try {
      let pid = id;
      if (!pid) {
        try {
          pid = (await namesakes(target))[0]?.id;
        } catch {
          pid = undefined;
        }
      }
      const data = await fetchPlayerShots(target, seasonOverride || undefined, pid);
      setResolvedPlayer(data.player_name);
      setResolvedPlayerId(data.player_id ?? null);
      setSeasons(data.seasons || []);
      setSeason(data.season || '');
      setShots(data.shots || []);
      setSource(data.source || '');
      setBadge(data._source || null);
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
    const linkedId = parseParam.int(params, 'pid', { min: 1 });
    loadPlayer(parseParam.str(params, 'player') ?? (linkedId ? '' : 'Stephen Curry'),
      /^\d{4}-\d{2}$/.test(linkedSeason ?? '') ? linkedSeason : undefined, linkedId);
  }, [params]);

  // Say so when another player has this name (searching the name picks the latest one).
  useEffect(() => {
    if (!resolvedPlayerId) return undefined;
    let active = true;
    namesakes(resolvedPlayer)
      .then((list) => { if (active) setOthers({ for: resolvedPlayerId, list: list.filter((p) => p.id !== resolvedPlayerId) }); })
      .catch(() => { if (active) setOthers({ for: resolvedPlayerId, list: [] }); });
    return () => { active = false; };
  }, [resolvedPlayer, resolvedPlayerId]);
  const otherNamesakes = others.for === resolvedPlayerId ? others.list : [];

  // Nothing is written until a player has loaded, so a slow first load
  // doesn't wipe the link it came from.
  const onShotMaking = viewMode === 'shotmaking';
  const onQuality = viewMode === 'quality';
  const onValue = viewMode === 'value';
  useUrlSync(resolvedPlayer ? {
    player: resolvedPlayer, pid: resolvedPlayerId, season, view: viewMode === 'dots' ? null : viewMode,
    games: !onShotMaking && !onQuality && !onValue && games !== 'regular' ? games : null,
    rank: onShotMaking ? rank.season : null,
    by: onShotMaking && rank.sort !== 'shot_making' ? rank.sort : null,
    order: onShotMaking && rank.order !== 'desc' ? rank.order : null,
    qm: onQuality && quality.mode !== 'expected' ? quality.mode : null,
    qs: onQuality ? quality.season : null,
    vs: onQuality ? quality.vs : null,
    vsid: onQuality && quality.vs ? quality.vsId : null,
    vss: onQuality && quality.vs ? quality.vsSeason : null,
    qmin: onQuality && quality.minShots !== 2 ? quality.minShots : null,
    sv: onValue ? svState.season : null,
    svby: onValue && svState.sort !== 'sva' ? svState.sort : null,
    svdir: onValue && svState.dir !== 'desc' ? svState.dir : null,
    svmin: onValue && svState.minFga !== 200 ? svState.minFga : null,
  } : null);

  function handleSeasonChange(newSeason) {
    setSeason(newSeason);
    loadPlayer(resolvedPlayer, newSeason, resolvedPlayerId);
  }

  // The season's shots in the chosen games; everything below counts only these.
  const shown = useMemo(() => shots.filter((s) => SHOT_GAMES[games][1](String(s.game_id))), [shots, games]);
  const leftOut = shots.length - shown.length;
  const gamesText = { regular: 'regular-season', playoffs: 'playoff and play-in', all: '' }[games];

  const totals = useMemo(() => shotTotals(shown), [shown]);

  const zoneStats = useMemo(() => computeZoneStats(shown), [shown]);
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
          {!onShotMaking && !onQuality && !onValue && <SourceBadge source={badge} />}
          <CopyLinkButton />
          <SaveViewButton pageId="shotcharts" />
        </h2>

        <div className="input-row" style={{ marginBottom: 0 }}>
          <div style={{ position: 'relative', flex: '1 1 260px' }}>
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
                {suggestions.map((p) => (
                  <li key={p.id}>
                    <button
                      type="button"
                      onClick={() => loadPlayer(p.name, undefined, p.id)}
                      style={{
                        display: 'block', width: '100%', textAlign: 'left', padding: '0.5rem 0.75rem',
                        background: 'transparent', border: 'none', color: 'var(--text)', cursor: 'pointer',
                      }}
                    >
                      {p.name}
                      <span className="text-eyebrow" style={{ marginLeft: '0.5rem' }}>{playerSpan(p)}{p.team ? ` · ${p.team}` : ''}</span>
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
            aria-label="Season"
            style={{ flex: '0 1 180px' }}
            value={season}
            onChange={(e) => handleSeasonChange(e.target.value)}
            disabled={loading || !seasons.length}
          >
            {!seasons.length && <option value="">No seasons loaded</option>}
            {seasons.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <LiveSeasonTag season={season ? Number(String(season).slice(0, 4)) + 1 : null} />

          {!onShotMaking && !onQuality && !onValue && (
            <select
              className="input-field"
              aria-label="Games"
              style={{ flex: '0 1 200px' }}
              value={games}
              onChange={(e) => setGames(e.target.value)}
            >
              {Object.entries(SHOT_GAMES).map(([k, [text]]) => (
                <option key={k} value={k}>{text}</option>
              ))}
            </select>
          )}
        </div>

        <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>
          {resolvedPlayer ? (
            <PlayerName playerId={resolvedPlayerId} name={resolvedPlayer} size={40}>
              <span className="text-eyebrow" style={{ display: 'block', marginTop: 2 }}>
                {onShotMaking || onQuality || onValue
                  ? `${seasons.length} seasons of shots on file`
                  : `${shown.length.toLocaleString()} ${gamesText ? `${gamesText} ` : ''}shots in ${season}${leftOut > 0 ? ` · ${leftOut.toLocaleString()} other shots this season not counted` : ''} · ${seasons.length} seasons`}
                {source === 'live' ? ' · fetched live just now' : ''}
              </span>
            </PlayerName>
          ) : 'Search a player to load their shot chart'}
        </p>
        {resolvedPlayer && otherNamesakes.length > 0 && (
          <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
            Another player is also called {resolvedPlayer}:{' '}
            {otherNamesakes.map((p, i) => (
              <React.Fragment key={p.id}>
                {i > 0 && ', '}
                <button type="button" className="pp-link" onClick={() => loadPlayer(p.name, undefined, p.id)}>
                  the one of {playerSpan(p)}
                </button>
              </React.Fragment>
            ))}
          </p>
        )}

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
      <ShotCourt shots={shown} viewMode={viewMode} playerName={resolvedPlayer} season={season} />

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
                <tr><td colSpan={4} className="empty-message">{shots.length ? `No ${gamesText} shots on file for ${season}.` : 'No shots loaded for this season.'}</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>
          {SHOT_GAMES[games][0]} only{games === 'regular' ? ' (like the quality map, shot-making and shot mix)' : ''}; choose the games
          beside the season. Shot Dots draws up to 5,000 shots.
        </p>
      </div>

      </>)}

      {onQuality && resolvedPlayer && (
        <ShotQualityMap playerName={resolvedPlayer} playerId={resolvedPlayerId} state={quality}
          onChange={(patch) => setQuality((prev) => ({ ...prev, ...patch }))} />
      )}

      {onShotMaking && resolvedPlayer && (
        <>
          <ShotMakingPlayer key={resolvedPlayerId ?? resolvedPlayer} playerName={resolvedPlayer} playerId={resolvedPlayerId} />
          <ShotMakingLeaderboard season={rank.season} sort={rank.sort} order={rank.order}
            onChange={(patch) => setRank((prev) => ({ ...prev, ...patch }))} />
          <ShotMakingModel />
        </>
      )}

      {onValue && resolvedPlayer && (
        <ShotValue playerId={resolvedPlayerId} playerName={resolvedPlayer} state={svState}
          onChange={(patch) => setSvState((prev) => ({ ...prev, ...patch }))} />
      )}

      {resolvedPlayer && <ShotMixHistory key={resolvedPlayerId ?? resolvedPlayer} playerName={resolvedPlayer} playerId={resolvedPlayerId} />}
    </div>
  );
}
