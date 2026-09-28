import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchPlayerGameLog } from '../../services/api';
import Loader from '../Loader';
import HotStreakCard from './HotStreakCard';
import InfoTooltip from './InfoTooltip';
import TableExport from './TableExport';
import TeamLogo from './TeamLogo';
import { openPage } from '../../utils/useUrlState';
import '../../styles/gamelog.css';

// Game log on the player profile: every regular-season game he played in a
// season (GET /games/player-log/{id}, player_game_lines, 2020-21 on), with a
// rolling average of one stat. Rolling shooting % are ratios of the window's
// totals, not averages of per-game percentages.

const label = (s) => `${s - 1}-${String(s).slice(-2)}`;
const pct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const day = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
const SHORT_MIN = 10; // rows under this many minutes are greyed

// key -> [label, per-game value, window value from summed rows, format]
const sum = (rows, k) => rows.reduce((a, r) => a + r[k], 0);
const ratio = (a, b) => (b ? a / b : null);
const CHART_STATS = {
    pts: ['Points', (r) => r.pts, (w) => sum(w, 'pts') / w.length, 'num'],
    reb: ['Rebounds', (r) => r.reb, (w) => sum(w, 'reb') / w.length, 'num'],
    ast: ['Assists', (r) => r.ast, (w) => sum(w, 'ast') / w.length, 'num'],
    stl: ['Steals', (r) => r.stl, (w) => sum(w, 'stl') / w.length, 'num'],
    blk: ['Blocks', (r) => r.blk, (w) => sum(w, 'blk') / w.length, 'num'],
    tov: ['Turnovers', (r) => r.tov, (w) => sum(w, 'tov') / w.length, 'num'],
    fg3m: ['Threes made', (r) => r.fg3m, (w) => sum(w, 'fg3m') / w.length, 'num'],
    fta: ['Free throw attempts', (r) => r.fta, (w) => sum(w, 'fta') / w.length, 'num'],
    min: ['Minutes', (r) => r.min, (w) => sum(w, 'min') / w.length, 'num'],
    ts_pct: ['True shooting %', (r) => r.ts_pct,
        (w) => ratio(sum(w, 'pts'), 2 * (sum(w, 'fga') + 0.44 * sum(w, 'fta'))), 'pct'],
    fg3_pct: ['3P%', (r) => (r.fg3a ? r.fg3m / r.fg3a : null), (w) => ratio(sum(w, 'fg3m'), sum(w, 'fg3a')), 'pct'],
};
const WINDOWS = [5, 10, 20];

// 1, 2, 2.5 or 5 times a power of ten, at least `raw`.
function niceStep(raw) {
    const p = 10 ** Math.floor(Math.log10(raw));
    return [1, 2, 2.5, 5, 10].map((m) => m * p).find((v) => v >= raw);
}

function RollingChart({ rows, stat, win, seasonValue }) {
    const boxRef = useRef(null);
    const [W, setW] = useState(720);
    const H = W < 520 ? 240 : 280;
    const M = { t: 16, r: 16, b: 34, l: 44 };
    useEffect(() => {
        const el = boxRef.current;
        if (!el || typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(([entry]) => {
            const w = Math.round(entry.contentRect.width);
            if (w > 0) setW(Math.max(280, w));
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

    const [name, one, many, format] = CHART_STATS[stat];
    const points = rows.map((r, i) => ({ i, v: one(r), r }));
    const rolling = rows.map((_, i) => (i + 1 >= win ? many(rows.slice(i + 1 - win, i + 1)) : null));
    const values = [...points.map((p) => p.v), ...rolling, seasonValue].filter((v) => v != null);
    // A true-shooting game can pass 100% (a 4-for-4 night with free throws); cap the axis at 150%.
    const top = format === 'pct' ? Math.min(1.5, Math.max(0.8, ...values)) : Math.max(1, ...values);
    const step = niceStep(top / 4);
    const yMax = Math.ceil(top / step - 1e-9) * step;
    const ticks = Array.from({ length: Math.round(yMax / step) + 1 }, (_, k) => k * step);
    const n = rows.length;
    const sx = (i) => M.l + (n <= 1 ? 0.5 : i / (n - 1)) * (W - M.l - M.r);
    const sy = (v) => H - M.b - (Math.max(0, Math.min(yMax, v)) / yMax) * (H - M.t - M.b);
    const fmt = (v) => (v == null ? '—' : format === 'pct' ? pct(v) : v.toFixed(1));
    const line = rolling.map((v, i) => (v == null ? null : `${sx(i).toFixed(1)},${sy(v).toFixed(1)}`)).filter(Boolean).join(' ');
    // Date labels at least ~64 px apart, always including the last game.
    const xTicks = [];
    for (let i = 0; i < n - 1; i += 1) {
        const prev = xTicks[xTicks.length - 1];
        if ((prev === undefined || sx(i) - sx(prev) >= 64) && sx(n - 1) - sx(i) >= 64) xTicks.push(i);
    }
    if (n) xTicks.push(n - 1);
    const summary = `${name}, game by game, with a ${win}-game rolling average across ${n} games.`;

    return (
        <div className="rx-chart gl-chart" ref={boxRef}>
            <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary}>
                {ticks.map((t) => (
                    <g key={t}>
                        <line className="rx-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                        <text className="rx-tick" x={M.l - 6} y={sy(t)} textAnchor="end" dominantBaseline="middle">
                            {format === 'pct' ? `${Math.round(t * 100)}%` : +t.toFixed(2)}
                        </text>
                    </g>
                ))}
                {xTicks.map((i) => (
                    <text key={rows[i].date} className="rx-tick" x={sx(i)} y={H - M.b + 16}
                        textAnchor={i === n - 1 && n > 1 ? 'end' : i === 0 ? 'start' : 'middle'}>{day(rows[i].date)}</text>
                ))}
                {seasonValue != null && (
                    <line className="gl-avg" x1={M.l} x2={W - M.r} y1={sy(seasonValue)} y2={sy(seasonValue)} />
                )}
                <g className="gl-dots">
                    {points.map((p) => p.v != null && (
                        <circle key={p.r.date} cx={sx(p.i)} cy={sy(p.v)} r={3}>
                            <title>{`${day(p.r.date)} ${p.r.home ? 'vs' : '@'} ${p.r.opponent}: ${fmt(p.v)} in ${p.r.min} min`}</title>
                        </circle>
                    ))}
                </g>
                {line && <polyline className="gl-roll" points={line} />}
            </svg>
            <p className="ss-legend">
                Dots: each game. Line: average of the last {win} games
                {format === 'pct' ? ' (made over attempted across those games)' : ''}, from game {win} on. Dashed: season average
                ({fmt(seasonValue)}).
            </p>
        </div>
    );
}

export default function GameLogBlock({ playerId, seasons, nbaGp = {} }) {
    const have = seasons.map((s) => s.season);
    const [season, setSeason] = useState(have[have.length - 1]);
    const [loaded, setLoaded] = useState({});
    const [error, setError] = useState('');
    const [stat, setStat] = useState('pts');
    const [win, setWin] = useState(5);
    const data = loaded[season];

    useEffect(() => {
        if (loaded[season]) return undefined;
        let active = true;
        fetchPlayerGameLog(playerId, season)
            .then((d) => { if (active) { setError(''); setLoaded((m) => ({ ...m, [season]: d })); } })
            .catch((e) => { if (active) setError(e.response?.data?.detail || 'Could not load that season.'); });
        return () => { active = false; };
    }, [playerId, season, loaded]);

    const seasonValue = useMemo(() => (data ? CHART_STATS[stat][2](data.rows) : null), [data, stat]);
    const gamesBySeason = Object.fromEntries(seasons.map((s) => [s.season, s.games]));
    const gp = data?.nba_gp ?? nbaGp[season];

    return (
        <section id="pp-gamelog" className="dashboard-card pp-section">
            <h2 className="card-title pp-section-title">
                Game log
                <InfoTooltip label="Where the game log comes from" title="Rebuilt from play-by-play">
                    {data ? `${data.notes.coverage} ${data.notes.accuracy}` : 'Rebuilt from ESPN play-by-play, 2020-21 on.'}
                </InfoTooltip>
            </h2>
            <p className="page-subtitle pp-meta">
                Every regular-season game he played, rebuilt from ESPN play-by-play (2020-21 on; there is no game-level
                data before that). Opponent, result and rest from the schedule. Rows under {SHORT_MIN} minutes are greyed.
            </p>
            <div className="pp-row gl-controls">
                <label className="pp-select">
                    <span>Season</span>
                    <select className="input-field" value={season} onChange={(e) => { setError(''); setSeason(Number(e.target.value)); }}>
                        {[...have].reverse().map((s) => <option key={s} value={s}>{label(s)} ({gamesBySeason[s]} games)</option>)}
                    </select>
                </label>
                <label className="pp-select">
                    <span>Chart</span>
                    <select className="input-field" value={stat} onChange={(e) => setStat(e.target.value)}>
                        {Object.entries(CHART_STATS).map(([k, [name]]) => <option key={k} value={k}>{name}</option>)}
                    </select>
                </label>
                <div className="tab-bar gl-windows" role="group" aria-label="Rolling window">
                    {WINDOWS.map((w) => (
                        <button key={w} type="button" aria-pressed={win === w}
                            className={`tab-btn ${win === w ? 'tab-btn--active' : ''}`} onClick={() => setWin(w)}>
                            {w}-game
                        </button>
                    ))}
                </div>
                <button type="button" className="pp-link gl-find"
                    onClick={() => openPage('gamefinder', { player: playerId, from: season, to: season })}>
                    Search these games in Game Finder
                </button>
            </div>
            {error && !data && <p className="error-message">{error}</p>}
            {!data && !error && <Loader />}
            {data && (
                <>
                    <p className="gl-summary">
                        <strong>{data.games}</strong> games · {data.averages.pts?.toFixed(1)} pts · {data.averages.reb?.toFixed(1)} reb ·{' '}
                        {data.averages.ast?.toFixed(1)} ast · {pct(data.averages.ts_pct)} TS · {data.averages.min?.toFixed(1)} min
                        {gp != null && gp !== data.games && (
                            <span className="pp-warn gl-gap">
                                NBA.com lists {gp} games for him this season; the play-by-play has {data.games}
                                {data.cup_final_games > 0 ? ` (plus ${data.cup_final_games} NBA Cup final, left out: it doesn't count in season stats)` : ''}.
                            </span>
                        )}
                    </p>
                    {data.rows.length > 1 && <RollingChart rows={data.rows} stat={stat} win={win} seasonValue={seasonValue} />}
                    <HotStreakCard key={season} playerId={playerId} season={season} stat={stat} win={win} dates={data.rows.map((r) => r.date)} />
                    <TableExport name={`${data.player_name} game log ${label(season)}`} />
                    <div className="table-wrapper pp-scroll">
                        <table className="data-table lb-table pp-table gl-table">
                            <thead>
                                <tr>
                                    <th>Date</th><th>Opp</th><th>Result</th><th className="lb-num">MIN</th>
                                    <th className="lb-num">PTS</th><th className="lb-num">REB</th><th className="lb-num">AST</th>
                                    <th className="lb-num">STL</th><th className="lb-num">BLK</th><th className="lb-num">TOV</th>
                                    <th className="lb-num">FG</th><th className="lb-num">3P</th><th className="lb-num">FT</th>
                                    <th className="lb-num">TS%</th><th>Rest</th>
                                </tr>
                            </thead>
                            <tbody>
                                {[...data.rows].reverse().map((r) => (
                                    <tr key={r.date} className={r.min < SHORT_MIN ? 'pp-small' : ''}>
                                        <td>{day(r.date)}</td>
                                        <td>
                                            <span className="gl-opp">
                                                {r.home ? 'vs' : '@'} <TeamLogo abbreviation={r.opponent} size={18} /> {r.opponent}
                                            </span>
                                        </td>
                                        <td className={r.win ? 'pp-pos' : 'pp-neg'}>{r.win ? 'W' : 'L'} {r.margin > 0 ? '+' : r.margin < 0 ? '−' : ''}{Math.abs(r.margin)}</td>
                                        <td className="lb-num">{r.min.toFixed(1)}</td>
                                        <td className="lb-num lb-stat">{r.pts}</td>
                                        <td className="lb-num">{r.reb}</td>
                                        <td className="lb-num">{r.ast}</td>
                                        <td className="lb-num">{r.stl}</td>
                                        <td className="lb-num">{r.blk}</td>
                                        <td className="lb-num">{r.tov}</td>
                                        <td className="lb-num">{r.fgm}-{r.fga}</td>
                                        <td className="lb-num">{r.fg3m}-{r.fg3a}</td>
                                        <td className="lb-num">{r.ftm}-{r.fta}</td>
                                        <td className="lb-num">{pct(r.ts_pct)}</td>
                                        <td>{r.b2b ? <span className="pp-tag">B2B</span> : r.rest_days == null ? 'Opener' : `${r.rest_days}d`}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="page-subtitle pp-foot">
                        Newest first. Result and margin are his team&apos;s. Rest = days off since his team&apos;s previous game
                        (B2B = second night of a back-to-back, no day off). {data.notes.left_out} Games he sat out have no row.
                    </p>
                </>
            )}
        </section>
    );
}
