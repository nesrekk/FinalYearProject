import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchRimDeterrence } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import TableExport from './common/TableExport';
import CopyLinkButton from './common/CopyLinkButton';
import SaveViewButton from './common/SaveViewButton';
import PlayerName from './common/PlayerName';
import TeamLink from './common/TeamLink';
import ChartExport from './common/ChartExport';
import ChartTooltip from './common/ChartTooltip';
import RimBandChart from './common/RimBandChart';
import { bySign, signed } from '../utils/format';
import { currentPageParam, openPage, parseParam, pushPage, useInitialParams, useUrlSync } from '../utils/useUrlState';
import { useWidth } from '../utils/rotationFormat';
import '../styles/rim.css';
import { LiveSeasonTag } from './common/LiveSeasonNote';

// Analytics › Player Analysis › Rim Deterrence (#rim). Opponents' shots at
// the rim with each defender on the floor vs. off, from every tracked
// play-by-play stint (GET /defense/rim-deterrence). On/off, not adjusted.

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const int = (v) => (v == null ? '—' : Math.round(v).toLocaleString());
const pct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const pts = (v) => (v == null ? '—' : `${signed(v * 100)} pts`);
// A drop is good for the defence: green when opponents did less with him on.
// Colour follows the value as shown (one decimal, after `scale`: 100 for FG% points), so 0.0 isn't tinted.
const tone = (v, scale = 1) => bySign(v * scale, 1, 'oo-neg', 'oo-pos');

const VIEWS = [
    { id: 'league', label: 'League' },
    { id: 'team', label: 'One team' },
];
const URL_KEYS = ['season', 'team', 'view', 'pos', 'min', 'sort', 'dir', 'sel'];

const HEAD = {
    rim_fga100: { label: 'Rim attempts per 100', short: 'Att/100', fmt: (v) => signed(v), range: 8 },
    rim_fg: { label: 'Rim FG%', short: 'FG%', fmt: pts, range: 0.12 },
    rim_pts100: { label: 'Rim points per 100', short: 'Pts/100', fmt: (v) => signed(v), range: 14 },
};

const COLUMNS = {
    player_name: { label: 'Player', text: true },
    team_abbreviation: { label: 'Team', text: true },
    games: { label: 'GP', title: 'Games with tracked minutes on the floor' },
    minutes_on: { label: 'Min on', title: 'Tracked minutes on the floor' },
    blk36: { label: 'Blk/36', title: 'His own blocks per 36 minutes (play-by-play lines)' },
    rim_fga100_diff: { label: 'Att/100 Δ', title: 'Opponents\' rim attempts per 100 possessions, on minus off' },
    rim_fg_diff: { label: 'FG% Δ', title: 'Opponents\' rim FG%, on minus off' },
    rim_pts100_diff: { label: 'Pts/100 Δ', title: 'Opponents\' rim points per 100 possessions, on minus off: both effects together' },
};

function formFromParams(p) {
    return {
        season: parseParam.int(p, 'season', { min: 2000, max: 2100 }),
        team: parseParam.str(p, 'team')?.toUpperCase() ?? null,
        view: parseParam.oneOf(p, 'view', VIEWS.map((v) => v.id)) ?? 'league',
        position: parseParam.oneOf(p, 'pos', ['all', 'bigs']) ?? 'all',
        minMinutes: parseParam.num(p, 'min', { min: 0, max: 3000 }) ?? 1000,
        sort: parseParam.oneOf(p, 'sort', Object.keys(COLUMNS)) ?? 'rim_pts100_diff',
        dir: parseParam.oneOf(p, 'dir', ['asc', 'desc']) ?? 'asc',
        sel: parseParam.str(p, 'sel'),
    };
}

function sortRows(rows, sort, dir) {
    const text = COLUMNS[sort]?.text;
    const sign = dir === 'asc' ? 1 : -1;
    // Rows under the minutes floor (team view) go after the qualified ones: their extremes are mostly noise.
    return [...rows].sort((a, b) => {
        if (a.qualified !== b.qualified) return a.qualified ? -1 : 1;
        const va = a[sort];
        const vb = b[sort];
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        return text ? sign * String(va).localeCompare(String(vb)) : sign * (va - vb);
    });
}

const rowKey = (r) => `${r.player_id}-${r.team_abbreviation}`;

// Zero in the middle, the 95% interval as a whisker, the estimate as a dot
// (filled when the interval clears zero). Green left of zero: a drop.
function CiBar({ est, lo, hi, clear, range }) {
    if (est == null) return null;
    const w = 96;
    const h = 16;
    const mid = w / 2;
    const x = (v) => mid + Math.max(-1, Math.min(1, v / range)) * (mid - 5);
    const color = est < 0 ? 'var(--positive)' : est > 0 ? 'var(--negative)' : 'var(--text-3)';
    return (
        <svg className="oo-ci" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" data-export-skip>
            <line x1={mid} x2={mid} y1={2} y2={h - 2} stroke="var(--line)" strokeWidth={1} opacity={0.5} />
            {lo != null && (
                <line x1={x(lo)} x2={x(hi)} y1={h / 2} y2={h / 2} stroke={clear ? color : 'var(--text-3)'} strokeWidth={2} strokeLinecap="round" />
            )}
            <circle cx={x(est)} cy={h / 2} r={3.5} fill={clear ? color : 'var(--surface)'} stroke={color} strokeWidth={1.5} />
        </svg>
    );
}

function DiffCell({ r, k, bar }) {
    const h = HEAD[k];
    const est = r[`${k}_diff`];
    const lo = r[`${k}_lo`];
    const hi = r[`${k}_hi`];
    const clear = r[`${k}_clear`];
    const interval = lo == null ? 'no interval' : `95% interval ${h.fmt(lo)} to ${h.fmt(hi)}${clear ? ' (clear of zero)' : ' (includes zero: within noise)'}`;
    return (
        <td className={`lb-num oo-ci-cell ${k === 'rim_pts100' ? 'lb-stat' : ''}`}
            title={`${h.label}: on ${k === 'rim_fg' ? pct(r[`${k}_on`]) : num(r[`${k}_on`])}, off ${k === 'rim_fg' ? pct(r[`${k}_off`]) : num(r[`${k}_off`])}; ${interval}`}>
            {bar && <CiBar est={est} lo={lo} hi={hi} clear={clear} range={h.range} />}
            <span className={tone(est, k === 'rim_fg' ? 100 : 1)}>{h.fmt(est)}</span>
            {lo != null && <span className="oo-ci-text rim-ci-text">{h.fmt(lo)} to {h.fmt(hi)}</span>}
        </td>
    );
}

// ─── Scatter: fewer attempts (x) against worse finishing (y) ──────────
const M = { l: 52, r: 14, t: 14, b: 42 };

function DeterrenceScatter({ rows, season, selected, onSelect }) {
    const [boxRef, W] = useWidth(640);
    const svgRef = useRef(null);
    const H = W < 480 ? 300 : 360;
    const players = rows.filter((r) => r.qualified && r.rim_fga100_diff != null && r.rim_fg_diff != null);
    const xs = players.map((p) => p.rim_fga100_diff);
    const ys = players.map((p) => p.rim_fg_diff * 100);
    const xMax = Math.max(4, ...xs.map(Math.abs)) * 1.05;
    const yMax = Math.max(6, ...ys.map(Math.abs)) * 1.05;
    const sx = (v) => M.l + ((v + xMax) / (2 * xMax)) * (W - M.l - M.r);
    const sy = (v) => M.t + ((yMax - v) / (2 * yMax)) * (H - M.t - M.b);
    const maxMin = Math.max(1, ...players.map((p) => p.minutes_on));
    const dots = players.map((p) => ({ x: sx(p.rim_fga100_diff), y: sy(p.rim_fg_diff * 100), r: 2.5 + 3.5 * Math.sqrt(p.minutes_on / maxMin), p }))
        .sort((a, b) => a.p.rim_fga100_diff - b.p.rim_fga100_diff);
    const [hot, setHot] = useState(null);
    const onMove = (e) => {
        const svg = e.currentTarget.ownerSVGElement;
        if (!svg || !dots.length) return;
        const rect = svg.getBoundingClientRect();
        const cx = ((e.clientX - rect.left) / rect.width) * W;
        const cy = ((e.clientY - rect.top) / rect.height) * H;
        let best = null;
        let bestD = Infinity;
        dots.forEach((d, i) => {
            const dd = (d.x - cx) ** 2 + (d.y - cy) ** 2;
            if (dd < bestD) { bestD = dd; best = i; }
        });
        setHot(bestD < 30 ** 2 ? best : null);
    };
    const onKey = (e) => {
        if (!dots.length) return;
        if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { e.preventDefault(); setHot((i) => (i == null ? 0 : Math.min(dots.length - 1, i + 1))); }
        else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { e.preventDefault(); setHot((i) => (i == null ? dots.length - 1 : Math.max(0, i - 1))); }
        else if (e.key === 'Enter' && hot != null) onSelect(dots[hot].p);
        else if (e.key === 'Escape') setHot(null);
    };
    const hovered = hot != null ? dots[hot] : null;
    const step = xMax > 8 ? 4 : 2;
    const xTicks = [];
    for (let v = -Math.floor(xMax / step) * step; v <= xMax; v += step) xTicks.push(v);
    const ystep = yMax > 16 ? 8 : 4;
    const yTicks = [];
    for (let v = -Math.floor(yMax / ystep) * ystep; v <= yMax; v += ystep) yTicks.push(v);
    const aria = `Rim deterrence, ${seasonLabel(season)}: ${players.length} players over the minutes floor. Horizontal: opponents' rim attempts per 100 possessions, on minus off; vertical: their rim FG%, on minus off. Lower left is fewer attempts and worse finishing with him on the floor.`;
    return (
        <div className="rx-chart rim-scatter" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={`rim deterrence ${seasonLabel(season)}`} />
            <div style={{ position: 'relative' }}>
                <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={aria}>
                    <rect className="rim-quad" x={M.l} y={sy(0)} width={sx(0) - M.l} height={H - M.b - sy(0)} />
                    {yTicks.map((t) => (
                        <g key={`y${t}`}>
                            <line className="rx-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                            <text className="rx-tick" x={M.l - 8} y={sy(t)} textAnchor="end" dominantBaseline="middle">{signed(t, 0)}</text>
                        </g>
                    ))}
                    {xTicks.map((t) => (
                        <text key={`x${t}`} className="rx-tick" x={sx(t)} y={H - M.b + 16} textAnchor="middle">{signed(t, 0)}</text>
                    ))}
                    <line className="rim-zero" x1={sx(0)} x2={sx(0)} y1={M.t} y2={H - M.b} />
                    <line className="rim-zero" x1={M.l} x2={W - M.r} y1={sy(0)} y2={sy(0)} />
                    <text className="rim-quad-label" x={M.l + 6} y={H - M.b - 8}>Fewer tries, worse finishing</text>
                    <text className="rim-quad-label" x={W - M.r - 6} y={M.t + 12} textAnchor="end">More tries, better finishing</text>
                    <text className="rx-axis" x={(M.l + W - M.r) / 2} y={H - 6} textAnchor="middle">Opponents&apos; rim attempts per 100, on − off</text>
                    <text className="rx-axis" transform={`translate(12 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle">Rim FG%, on − off (pts)</text>
                    {dots.map((d, i) => {
                        const sel = selected && rowKey(d.p) === selected;
                        return (
                            <circle key={rowKey(d.p)} cx={d.x} cy={d.y} r={i === hot || sel ? d.r + 2 : d.r}
                                className={`rim-dot ${d.p.rim_pts100_diff < 0 ? 'rim-dot--good' : 'rim-dot--bad'} ${d.p.big ? 'rim-dot--big' : ''} ${i === hot || sel ? 'rim-dot--hot' : ''}`}
                                style={{ pointerEvents: 'none' }} />
                        );
                    })}
                    <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b} className="chart-crosshair-overlay"
                        role="slider" aria-label="Players, use arrow keys to step through them and Enter to open one"
                        aria-valuetext={hovered ? `${hovered.p.player_name}: rim attempts ${signed(hovered.p.rim_fga100_diff)} per 100, rim FG% ${pts(hovered.p.rim_fg_diff)}` : undefined}
                        tabIndex={0} onPointerMove={onMove} onPointerLeave={() => setHot(null)} onKeyDown={onKey}
                        onBlur={() => setHot(null)} onClick={() => hovered && onSelect(hovered.p)}
                        style={{ cursor: hovered ? 'pointer' : 'default' }} />
                </svg>
                {hovered && (
                    <ChartTooltip x={hovered.x} y={hovered.y} chartWidth={W} chartHeight={H} align={hovered.y < H / 3 ? 'below' : 'above'}>
                        <div style={{ fontWeight: 600 }}>{hovered.p.player_name} · {hovered.p.team_abbreviation}</div>
                        <div>Rim attempts/100 {num(hovered.p.rim_fga100_on)} on, {num(hovered.p.rim_fga100_off)} off ({signed(hovered.p.rim_fga100_diff)})</div>
                        <div>Rim FG% {pct(hovered.p.rim_fg_on)} on, {pct(hovered.p.rim_fg_off)} off ({pts(hovered.p.rim_fg_diff)})</div>
                        <div style={{ color: 'var(--text-3)' }}>{int(hovered.p.minutes_on)} min on · {int(hovered.p.rim_fga_on)} rim attempts faced · click to open</div>
                    </ChartTooltip>
                )}
            </div>
            <div className="rim-legend rim-legend--dots" aria-hidden="true">
                <span className="rim-legend--good">Rim points down with him on</span>
                <span className="rim-legend--bad">Up</span>
                <span className="rim-legend--big">Ringed: listed center</span>
                <span>Dot size: minutes</span>
            </div>
        </div>
    );
}

function PlayerDetail({ r, data, season }) {
    const facts = [
        ['Rim attempts per 100', num(r.rim_fga100_on), num(r.rim_fga100_off), r.rim_fga100_diff, 'rim_fga100'],
        ['Rim FG%', pct(r.rim_fg_on), pct(r.rim_fg_off), r.rim_fg_diff, 'rim_fg'],
        ['Rim points per 100', num(r.rim_pts100_on), num(r.rim_pts100_off), r.rim_pts100_diff, 'rim_pts100'],
        ['Share of their shots at the rim', pct(r.rim_share_on), pct(r.rim_share_off), r.rim_share_diff, null],
        ['Their FG% on all shots', pct(r.opp_fg_on), pct(r.opp_fg_off), r.opp_fg_diff, null],
    ];
    return (
        <div className="rim-detail">
            <h3 className="rim-detail-title">
                <PlayerName playerId={r.player_id} name={r.player_name ?? `#${r.player_id}`} size={28} />
                <span className="rim-detail-team"><TeamLink abbr={r.team_abbreviation} season={season} /> {seasonLabel(season)}</span>
            </h3>
            <p className="page-subtitle rim-detail-n">
                {int(r.minutes_on)} tracked minutes on the floor over {r.games} games, {int(r.minutes_off)} off; opponents took{' '}
                {int(r.rim_fga_on)} rim attempts with him on and {int(r.rim_fga_off)} with him off.{' '}
                {r.blk} blocks of his own ({num(r.blk36, 2)} per 36).
                {!r.qualified && ` Under ${int(data.min_minutes)} minutes: treat as noise.`}
                {r.few_off_minutes && ` Only ${int(r.minutes_off)} minutes without him, so the off side is thin.`}
            </p>
            <div className="rim-detail-grid">
                <div>
                    <div className="table-wrapper">
                        <table className="data-table lb-table rim-facts">
                            <thead><tr><th>Opponents&apos;</th><th className="lb-num">On</th><th className="lb-num">Off</th><th className="lb-num">On − Off</th><th className="lb-num">95% interval</th></tr></thead>
                            <tbody>
                                {facts.map(([what, on, off, d, k]) => (
                                    <tr key={what}>
                                        <td>{what}</td>
                                        <td className="lb-num">{on}</td>
                                        <td className="lb-num">{off}</td>
                                        <td className={`lb-num ${tone(d, k === 'rim_fg' || k == null ? 100 : 1)}`}>{k === 'rim_fg' || k == null ? pts(d) : signed(d)}</td>
                                        <td className="lb-num rim-facts-ci">{k && r[`${k}_lo`] != null ? `${signed(r[`${k}_lo`] * (k === 'rim_fg' ? 100 : 1))} to ${signed(r[`${k}_hi`] * (k === 'rim_fg' ? 100 : 1))}` : '—'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    {(r.rim_pts100_rank || r.rim_fga100_rank) && (
                        <p className="page-subtitle rim-detail-rank">
                            League rank among {data.n_ranked} players with {int(data.min_minutes)}+ minutes (1 = biggest drop):
                            rim attempts #{r.rim_fga100_rank ?? '—'}, rim FG% #{r.rim_fg_rank ?? '—'}, rim points #{r.rim_pts100_rank ?? '—'}.
                        </p>
                    )}
                </div>
                <div>
                    <p className="rim-panel-title">Every distance: opponents&apos; attempts per 100, on vs. off</p>
                    <RimBandChart row={r} bands={data.bands} league={data.league} name={`rim deterrence ${r.player_name} ${seasonLabel(season)}`} />
                </div>
            </div>
        </div>
    );
}

export default function RimDeterrenceSection() {
    const params = useInitialParams();
    const [form, setForm] = useState(() => formFromParams(params));
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const shownSeason = form.season ?? data?.season ?? null;
    const shownTeam = form.view === 'team' ? (form.team ?? data?.team ?? null) : null;
    useUrlSync({
        season: shownSeason, team: shownTeam, view: form.view === 'league' ? null : form.view,
        pos: form.position === 'all' ? null : form.position,
        min: form.minMinutes === 1000 ? null : (form.minMinutes === '' ? 0 : form.minMinutes),
        sort: form.sort === 'rim_pts100_diff' ? null : form.sort, dir: form.dir === 'asc' ? null : form.dir,
        sel: form.sel,
    });

    // Inside the Analytics page: drop this tool's inputs from the link when the tab closes.
    const [page] = useState(currentPageParam);
    useEffect(() => () => {
        const url = new URL(window.location.href);
        if (url.searchParams.get('page') !== page) return;
        URL_KEYS.forEach((k) => url.searchParams.delete(k));
        window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
    }, [page]);

    useEffect(() => {
        let retry = false;
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                const res = await fetchRimDeterrence({
                    season: form.season ?? undefined,
                    team: form.view === 'team' ? (form.team ?? undefined) : undefined,
                    min_minutes: form.minMinutes === '' ? 0 : form.minMinutes,
                    position: form.position,
                });
                if (form.view === 'team' && res.team == null) {
                    // The team view needs a team: take the first and load it.
                    retry = true;
                    setForm((f) => ({ ...f, team: res.teams[0] }));
                    return;
                }
                setData(res);
            } catch (e) {
                setError(e?.response?.data?.detail || 'Could not load rim deterrence data. Is the impact API (port 8002) running?');
            } finally {
                if (!retry) setLoading(false);
            }
        }, 250);
        return () => clearTimeout(timer);
    }, [form.season, form.team, form.view, form.minMinutes, form.position]);

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const onSort = (key) => setForm((f) => (
        f.sort === key ? { ...f, dir: f.dir === 'asc' ? 'desc' : 'asc' } : { ...f, sort: key, dir: COLUMNS[key].text ? 'asc' : (key.endsWith('_diff') ? 'asc' : 'desc') }
    ));

    const rows = useMemo(() => (data ? sortRows(data.players, form.sort, form.dir) : []), [data, form.sort, form.dir]);
    const selected = useMemo(() => {
        if (!rows.length) return null;
        return rows.find((r) => rowKey(r) === form.sel) ?? rows.find((r) => r.qualified) ?? rows[0];
    }, [rows, form.sel]);
    const detailRef = useRef(null);
    const pick = (r) => {
        set({ sel: rowKey(r) });
        detailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    };

    if (!data && loading) return <Loader />;
    if (!data) return <section className="dashboard-card"><p className="error-message">{error}</p></section>;

    const season = data.season;
    const seasons = [...data.seasons_available].reverse();
    const isTeam = form.view === 'team';
    const lg = data.league;
    const st = data.stability;
    const floor = data.min_minutes;
    const src = lg.sources;
    const twos = src.coords + src.text + src.rule + src.unknown;
    const cols = ['player_name', ...(isTeam ? [] : ['team_abbreviation']), 'games', 'minutes_on', 'blk36'];

    return (
        <section className="dashboard-card lb-card oo-card rim-card">
            <h2 className="card-title hb-page-title">
                Rim deterrence: opponents at the rim with him on the floor, and without
                <InfoTooltip label="How rim deterrence is computed" title="Under the hood">
                    {data.method}
                </InfoTooltip>
                <SourceBadge source={data._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="analytics" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Every shot of every regular-season game since 2020-21, placed in the five-man stint it happened in, with its
                distance from the NBA&apos;s shot chart. Two things a rim protector can do: keep opponents from trying at the
                rim, and make them miss when they do. This is on/off: it also moves with who plays beside him and who his
                backup is, so read it with the interval.
            </p>

            <div className="tab-bar lb-modes" role="tablist" aria-label="View" style={{ marginTop: '0.75rem' }}>
                {VIEWS.map((v) => (
                    <button key={v.id} type="button" role="tab" aria-selected={form.view === v.id}
                        className={`tab-btn ${form.view === v.id ? 'tab-btn--active' : ''}`}
                        onClick={() => set({ view: v.id, team: v.id === 'team' ? (selected?.team_abbreviation ?? form.team ?? data?.teams?.[0] ?? null) : form.team })}>
                        {v.label}
                    </button>
                ))}
            </div>

            <div className="lb-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={season} onChange={(e) => set({ season: Number(e.target.value), sel: null })}>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                    <LiveSeasonTag season={season} />
                </label>
                {isTeam && (
                    <label>
                        <span>Team</span>
                        <select className="input-field" value={data.team ?? ''} onChange={(e) => set({ team: e.target.value, sel: null })}>
                            {data.teams.map((tm) => <option key={tm} value={tm}>{tm}</option>)}
                        </select>
                    </label>
                )}
                <label>
                    <span>Players</span>
                    <select className="input-field" value={form.position} onChange={(e) => set({ position: e.target.value })}>
                        {Object.entries(data.positions).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                </label>
                <label>
                    <span>Min. minutes on</span>
                    <input className="input-field" type="number" min={0} max={3000} step={50} value={form.minMinutes}
                        onChange={(e) => set({ minMinutes: e.target.value === '' ? '' : Number(e.target.value) })} />
                </label>
            </div>

            {error && <p className="error-message">{error}</p>}

            <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                <p className="rx-verdict">
                    <strong>{seasonLabel(season)}: the league took {num(lg.bands.rim.per100)} rim attempts per 100 possessions and made{' '}
                        {pct(lg.bands.rim.fg)}</strong> ({int(lg.bands.rim.fga)} attempts in tracked stints).{' '}
                    Of {data.noise.rim_fga100.qualified} {isTeam ? `${data.team} ` : ''}players shown with {int(floor)}+ minutes,{' '}
                    {data.noise.rim_fga100.ci_excludes_zero} have a rim-attempts gap whose interval clears zero,{' '}
                    {data.noise.rim_fg.ci_excludes_zero} a rim FG% gap and {data.noise.rim_pts100.ci_excludes_zero} a rim-points gap;
                    about {data.noise.rim_fga100.expected_by_chance} each would by chance.
                    {st && <> Year to year (same player, {int(st.min_minutes)}+ minutes both seasons, {st.pairs} pairs) the attempts gap
                        correlates {st.rim_fga100.toFixed(2)} with itself, the FG% gap only {st.rim_fg.toFixed(2)}: keeping
                        opponents away from the rim is more repeatable than what they shoot once there.</>}
                </p>

                {rows.length > 0 && (
                    <DeterrenceScatter rows={rows} season={season} selected={selected ? rowKey(selected) : null} onSelect={pick} />
                )}

                <div ref={detailRef}>
                    {selected && <PlayerDetail r={selected} data={data} season={season} />}
                </div>

                <TableExport name={`rim deterrence ${isTeam ? data.team : 'league'} ${form.position === 'bigs' ? 'centers ' : ''}${seasonLabel(season)}`} />
                <div className="table-wrapper">
                    <table className="data-table lb-table oo-table rim-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                {[...cols, 'rim_fga100_diff', 'rim_fg_diff', 'rim_pts100_diff'].map((k) => {
                                    const c = COLUMNS[k];
                                    const active = form.sort === k;
                                    return (
                                        <th key={k} className={c.text ? undefined : `lb-num ${k === 'rim_pts100_diff' ? 'lb-stat' : ''}`}
                                            aria-sort={active ? (form.dir === 'asc' ? 'ascending' : 'descending') : 'none'} title={c.title}>
                                            <button type="button" className={`oo-sort ${active ? 'oo-sort--active' : ''}`} onClick={() => onSort(k)}>
                                                {c.label}{active && <span aria-hidden="true">{form.dir === 'asc' ? ' ▲' : ' ▼'}</span>}
                                            </button>
                                        </th>
                                    );
                                })}
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r, i) => {
                                const short = !r.qualified;
                                const flags = [
                                    short && `Under ${int(floor)} minutes on the floor: treat as noise`,
                                    r.few_off_minutes && `Only ${int(r.minutes_off)} minutes without him: the off side is a small sample`,
                                ].filter(Boolean);
                                const isSel = selected && rowKey(r) === rowKey(selected);
                                return (
                                    <tr key={rowKey(r)} className={`${short ? 'sl-short' : ''} ${isSel ? 'rim-row--sel' : ''}`}
                                        title={flags.join('. ') || undefined}>
                                        <td>{i + 1}</td>
                                        <td>
                                            <PlayerName playerId={r.player_id} name={r.player_name ?? `#${r.player_id}`} size={24} />
                                            {flags.length > 0 && <span className="oo-flag" aria-label={flags.join('. ')} title={flags.join('. ')} data-export-as={short ? ' (small sample)' : ' (few off minutes)'}>†</span>}
                                            <button type="button" className="rim-open" onClick={() => pick(r)} aria-pressed={!!isSel}
                                                aria-label={`Show ${r.player_name}'s shot breakdown`} data-export-skip>
                                                {isSel ? 'Shown' : 'Detail'}
                                            </button>
                                        </td>
                                        {!isTeam && <td className="oo-team"><TeamLink abbr={r.team_abbreviation} season={season} /></td>}
                                        <td className="lb-num">{r.games}</td>
                                        <td className="lb-num">{int(r.minutes_on)}</td>
                                        <td className="lb-num">{num(r.blk36, 2)}</td>
                                        <DiffCell r={r} k="rim_fga100" bar />
                                        <DiffCell r={r} k="rim_fg" />
                                        <DiffCell r={r} k="rim_pts100" bar />
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
                {rows.length === 0 && <p className="empty-message">No player clears the minutes floor. Lower it to see more.</p>}
                <p className="page-subtitle lb-summary oo-foot">
                    Rim = under 4 feet from the hoop, about the restricted area. Δ = with him on the floor minus without him, in the
                    games he played; green means opponents did less with him on. Rates are per 100 possessions (FGA + 0.44 FTA −
                    OREB + TOV, both sides averaged, the On/Off convention). Rim points = 2 × rim makes: fewer tries and worse
                    finishing together (fouls and and-ones not counted). † marks a row under the minutes floor or with few
                    minutes without him. Where distances come from, {seasonLabel(season)}: {pct(src.coords / twos)} of twos from
                    the NBA shot chart&apos;s coordinates, {int(src.text)} from ESPN&apos;s &ldquo;N-foot&rdquo; text,{' '}
                    {int(src.rule)} layups/dunks/tips with no distance counted as rim (the same rule is right for{' '}
                    {pct(lg.rule.share_right, 0)} of the {int(lg.rule.checked)} such shots the shot chart can check), {int(src.unknown)} left
                    unknown. Not adjusted for teammates or opponents: see{' '}
                    <button type="button" className="oo-link" onClick={() => openPage('rapm', { season })}>RAPM</button> for that, and{' '}
                    <button type="button" className="oo-link" onClick={() => pushPage('analytics', 'onoff', { season })}>On/Off</button> for the
                    whole defensive rating.
                </p>
            </div>
        </section>
    );
}
