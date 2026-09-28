import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchPlayerHistory, fetchTeamHistory, fetchTradeTeams, fetchLivePlayerSuggestions } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import ChartExport from './common/ChartExport';
import ChartTooltip from './common/ChartTooltip';
import useChartCrosshair from '../utils/useChartCrosshair';

const PLAYER_STATS = [
    { key: 'pts', label: 'Points', digits: 1 },
    { key: 'reb', label: 'Rebounds', digits: 1 },
    { key: 'ast', label: 'Assists', digits: 1 },
    { key: 'stl', label: 'Steals', digits: 1 },
    { key: 'blk', label: 'Blocks', digits: 1 },
    { key: 'ts_pct', label: 'True Shooting %', digits: 3, pct: true },
    { key: 'usg_pct', label: 'Usage %', digits: 3, pct: true },
    { key: 'net_rating', label: 'Net Rating', digits: 1 },
    { key: 'min', label: 'Minutes', digits: 1 },
];

const TEAM_STATS = [
    { key: 'net_rating', label: 'Net Rating', digits: 1 },
    { key: 'off_rating', label: 'Offensive Rating', digits: 1 },
    { key: 'def_rating', label: 'Defensive Rating', digits: 1 },
    { key: 'ts_pct', label: 'True Shooting %', digits: 3, pct: true },
    { key: 'win_pct', label: 'Win %', digits: 3, pct: true },
    { key: 'efg_pct', label: 'Four Factors: eFG%', digits: 3, pct: true },
    { key: 'oreb_pct', label: 'Four Factors: OREB%', digits: 3, pct: true },
    { key: 'tov_pct', label: 'Four Factors: TOV%', digits: 3, pct: true },
    { key: 'ftr', label: 'Four Factors: FT Rate', digits: 3, pct: true },
];

const CHART_W = 720, CHART_H = 300, PAD_L = 56, PAD_R = 20, PAD_T = 20, PAD_B = 40;

function seasonLabel(s) {
    return `${s - 1}-${String(s).slice(-2)}`;
}

function LineTrendChart({ points, statDef, color, exportName }) {
    const svgRef = useRef(null);
    const valid = points.filter((p) => p.value != null);

    const values = valid.map((p) => p.value);
    const rawMin = valid.length ? Math.min(...values) : 0;
    const rawMax = valid.length ? Math.max(...values) : 1;
    const span = rawMax - rawMin || 1;
    const yMin = rawMin - span * 0.15;
    const yMax = rawMax + span * 0.15;

    const plotW = CHART_W - PAD_L - PAD_R;
    const plotH = CHART_H - PAD_T - PAD_B;

    const xFor = (i) => PAD_L + (points.length > 1 ? (i / (points.length - 1)) * plotW : plotW / 2);
    const yFor = (v) => PAD_T + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

    const linePath = valid
        .map((p) => `${p === valid[0] ? 'M' : 'L'} ${xFor(p.i).toFixed(1)} ${yFor(p.value).toFixed(1)}`)
        .join(' ');

    const fmt = (v) => statDef.pct ? `${(v * 100).toFixed(1)}%` : v.toFixed(statDef.digits);

    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((t) => yMin + t * (yMax - yMin));
    const peak = valid.length ? valid.reduce((a, b) => (b.value > a.value ? b : a), valid[0]) : null;

    const crosshairPoints = valid.map((p) => ({ ...p, x: xFor(p.i), y: yFor(p.value), isPeak: p.season === peak.season }));
    const { point: hovered, overlayProps } = useChartCrosshair(crosshairPoints, CHART_W);

    if (!valid.length) return <p className="empty-message">No data for this stat.</p>;

    return (
        <div>
            <ChartExport svgRef={svgRef} name={exportName} />
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${CHART_W} ${CHART_H}`} style={{ width: '100%', height: 'auto', display: 'block' }} role="img" aria-label={`Line chart of ${statDef.label} by season, with the peak season marked in gold`}>
                    <rect x="0" y="0" width={CHART_W} height={CHART_H} fill="var(--surface-2)" rx="8" />
                    {yTicks.map((t, idx) => (
                        <React.Fragment key={idx}>
                            <line x1={PAD_L} y1={yFor(t)} x2={CHART_W - PAD_R} y2={yFor(t)} stroke="var(--hairline)" strokeWidth="1" />
                            <text x={PAD_L - 8} y={yFor(t) + 3} fill="var(--text-3)" fontSize="10" textAnchor="end">{fmt(t)}</text>
                        </React.Fragment>
                    ))}
                    {points.map((p, i) => (
                        (i % Math.ceil(points.length / 10) === 0 || i === points.length - 1) && (
                            <text key={p.season} x={xFor(i)} y={CHART_H - PAD_B + 16} fill="var(--text-3)" fontSize="9" textAnchor="middle">
                                {seasonLabel(p.season)}
                            </text>
                        )
                    ))}
                    <path d={linePath} fill="none" stroke={color} strokeWidth="2.5" />
                    {hovered && (
                        <line x1={hovered.x} y1={PAD_T} x2={hovered.x} y2={CHART_H - PAD_B} className="chart-crosshair-line" />
                    )}
                    {valid.map((p) => (
                        <circle
                            key={p.season} cx={xFor(p.i)} cy={yFor(p.value)}
                            r={hovered?.season === p.season ? (p.season === peak.season ? 6 : 5) : (p.season === peak.season ? 4.5 : 3)}
                            fill={p.season === peak.season ? '#facc15' : color}
                            style={{ pointerEvents: 'none' }}
                        />
                    ))}
                    <rect
                        x={PAD_L} y={PAD_T} width={plotW} height={plotH}
                        className="chart-crosshair-overlay" role="slider" aria-label={`${statDef.label} by season, use arrow keys to step through`}
                        aria-valuetext={hovered ? `${seasonLabel(hovered.season)}: ${fmt(hovered.value)}` : undefined}
                        {...overlayProps}
                    />
                </svg>
                {hovered && (
                    <ChartTooltip x={hovered.x} y={hovered.y} chartWidth={CHART_W} chartHeight={CHART_H}>
                        <div style={{ fontWeight: 600 }}>{seasonLabel(hovered.season)}{hovered.isPeak ? ' · peak' : ''}</div>
                        <div>{statDef.label}: {fmt(hovered.value)}</div>
                    </ChartTooltip>
                )}
            </div>
        </div>
    );
}

export default function TrendAnalysisSection() {
    const [mode, setMode] = useState('player'); // 'player' | 'team'

    const [searchInput, setSearchInput] = useState('LeBron James');
    const [suggestions, setSuggestions] = useState([]);
    const [playerName, setPlayerName] = useState('LeBron James');
    const [playerStat, setPlayerStat] = useState('pts');
    const [playerHistory, setPlayerHistory] = useState(null);
    const [playerError, setPlayerError] = useState('');

    const [teams, setTeams] = useState([]);
    const [team, setTeam] = useState('');
    const [teamStat, setTeamStat] = useState('net_rating');
    const [teamHistory, setTeamHistory] = useState(null);
    const [teamError, setTeamError] = useState('');

    const [loading, setLoading] = useState(false);

    useEffect(() => {
        const query = searchInput.trim();
        if (query.length < 2 || query.toLowerCase() === playerName.toLowerCase()) {
            setSuggestions([]);
            return;
        }
        let active = true;
        const timer = setTimeout(async () => {
            try {
                const data = await fetchLivePlayerSuggestions(query, 8);
                if (active) setSuggestions(data?.results ?? []);
            } catch {
                if (active) setSuggestions([]);
            }
        }, 200);
        return () => {
            active = false;
            clearTimeout(timer);
        };
    }, [searchInput, playerName]);

    async function loadPlayer(name) {
        setSearchInput(name);
        setPlayerName(name);
        setSuggestions([]);
        setLoading(true);
        setPlayerError('');
        try {
            const data = await fetchPlayerHistory(name);
            setPlayerHistory(data);
        } catch (e) {
            setPlayerHistory(null);
            setPlayerError(e?.response?.data?.detail || 'No history for this player.');
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { loadPlayer('LeBron James'); }, []);

    useEffect(() => {
        fetchTradeTeams(2024).then((d) => setTeams(d.teams || [])).catch(() => setTeams([]));
    }, []);

    async function loadTeam(abbr) {
        setTeam(abbr);
        setLoading(true);
        setTeamError('');
        try {
            const data = await fetchTeamHistory(abbr);
            setTeamHistory(data);
        } catch (e) {
            setTeamHistory(null);
            setTeamError(e?.response?.data?.detail || 'No history for this team.');
        } finally {
            setLoading(false);
        }
    }

    const playerPoints = useMemo(() => {
        if (!playerHistory) return [];
        return playerHistory.seasons.map((s, i) => ({ i, season: s.season, value: s[playerStat] }));
    }, [playerHistory, playerStat]);

    const teamPoints = useMemo(() => {
        if (!teamHistory) return [];
        return teamHistory.seasons.map((s, i) => ({ i, season: s.season, value: s[teamStat] }));
    }, [teamHistory, teamStat]);

    const playerStatDef = PLAYER_STATS.find((s) => s.key === playerStat);
    const teamStatDef = TEAM_STATS.find((s) => s.key === teamStat);

    return (
        <section className="dashboard-card">
            <h2 className="card-title hb-page-title">
                <span className="card-icon"><Icon name="trending_up" /></span>
                Trend Analysis
                <InfoTooltip label="How this works" title="Career / franchise trajectory">
                    Player trends use every season on record for that player, unfiltered — injury-shortened
                    or rookie seasons included, so the trajectory is honest rather than cherry-picked. Team
                    trends use the same minutes-weighted roster aggregation as Trade Analyzer and the win%
                    model. Note: a team's history here follows one franchise abbreviation — relocations/
                    renames (e.g. NOH to NOP, NJN to BKN) appear as separate entries, not stitched together.
                    <br /><br />
                    The Four Factors options (eFG%/OREB%/TOV%/FT Rate) use that same minutes-weighted-roster
                    methodology, not an official team box score — this DB has no team-level game log, so
                    "team eFG%" here means "this roster's players' own eFG%, weighted by minutes played."
                </InfoTooltip>
                <SourceBadge source={(mode === 'player' ? playerHistory : teamHistory)?._source} />
            </h2>

            <div className="tab-bar" style={{ marginBottom: '1rem' }}>
                <button type="button" className={`tab-btn ${mode === 'player' ? 'tab-btn--active' : ''}`} onClick={() => setMode('player')}>Player</button>
                <button type="button" className={`tab-btn ${mode === 'team' ? 'tab-btn--active' : ''}`} onClick={() => setMode('team')}>Team</button>
            </div>

            {mode === 'player' && (
                <>
                    <div className="input-row">
                        <div style={{ position: 'relative', flex: 1 }}>
                            <input
                                type="text"
                                className="input-field"
                                placeholder="Search a player…"
                                value={searchInput}
                                onChange={(e) => setSearchInput(e.target.value)}
                                onKeyDown={(e) => { if (e.key === 'Enter') loadPlayer(searchInput); }}
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
                                                style={{ display: 'block', width: '100%', textAlign: 'left', padding: '0.5rem 0.75rem', background: 'transparent', border: 'none', color: 'var(--text)', cursor: 'pointer' }}
                                            >
                                                {name}
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                        <select className="input-field" value={playerStat} onChange={(e) => setPlayerStat(e.target.value)}>
                            {PLAYER_STATS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                        </select>
                    </div>

                    {loading && <Loader />}
                    {playerError && <p className="error-message" style={{ marginTop: '0.75rem' }}>{playerError}</p>}
                    {!loading && playerHistory && (
                        <>
                            <p className="page-subtitle" style={{ marginTop: '0.75rem', marginBottom: '0.75rem' }}>
                                {playerHistory.player_name} · {playerHistory.seasons.length} seasons · {playerStatDef.label} · gold dot marks the peak season
                            </p>
                            <LineTrendChart points={playerPoints} statDef={playerStatDef} color="#38bdf8" exportName={`${playerHistory.player_name} ${playerStatDef.label} trend`} />
                        </>
                    )}
                </>
            )}

            {mode === 'team' && (
                <>
                    <div className="input-row">
                        <select className="input-field" value={team} onChange={(e) => loadTeam(e.target.value)}>
                            <option value="">Select a team…</option>
                            {teams.map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                        <select className="input-field" value={teamStat} onChange={(e) => setTeamStat(e.target.value)}>
                            {TEAM_STATS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                        </select>
                    </div>

                    {loading && <Loader />}
                    {teamError && <p className="error-message" style={{ marginTop: '0.75rem' }}>{teamError}</p>}
                    {!loading && teamHistory && (
                        <>
                            <p className="page-subtitle" style={{ marginTop: '0.75rem', marginBottom: '0.75rem' }}>
                                {teamHistory.team} · {teamHistory.seasons.length} seasons · {teamStatDef.label} · gold dot marks the peak season
                            </p>
                            <LineTrendChart points={teamPoints} statDef={teamStatDef} color="#f87171" exportName={`${teamHistory.team} ${teamStatDef.label} trend`} />
                        </>
                    )}
                </>
            )}
        </section>
    );
}
