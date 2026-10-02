import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchShotValue, fetchShotValueOptions, fetchShotValuePlayer } from '../../services/api';
import ChartExport from './ChartExport';
import InfoTooltip from './InfoTooltip';
import PlayerName from './PlayerName';
import SourceBadge from './SourceBadge';
import TableExport from './TableExport';
import TeamLink from './TeamLink';
import Loader from '../Loader';
import { SV_MINS } from '../../utils/shotValue';
import '../../styles/shotvalue.css';

// Shot Value Added (GET /shots/shot-value*, routers/shot_value.py, scripts/build_shot_value.py; round 6
// step 8): the Shot Charts "Shot value" tab. Every attempt from 2020-21 on is priced before its game, as an
// average shooter would make it and as this shooter would given his record; a season's shooting points
// split into skill (aware minus blind) and beyond (made minus aware). Three pieces:
//   ShotValuePlayer       the selected player's seasons and his skill track by kind of attempt
//   ShotValueLeaderboard  one season's players, sortable (season, sort, dir, min FGA in the link)
//   ShotValueModel        does the shooter's skill price attempts better on seasons it never saw, how much the
//                         parts repeat, and the skill model's settings in plain words
// Hand-built SVG like the app's other charts.

const label = (s) => `${s - 1}-${String(s).slice(-2)}`;
const signed = (v, d = 1) => {
    if (v == null) return '—';
    const r = Number(v.toFixed(d));          // sign of the shown value, so −0.04 reads 0.0, not −0.0
    return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toFixed(d)}`;
};
const int = (v) => (v == null ? '—' : Math.round(v).toLocaleString());
const pctOf = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const tone = (v) => (v == null || Math.abs(v) < 1e-9 ? '' : v > 0 ? 'sv-pos' : 'sv-neg');
const CLASS_SHORT = { rim: 'Rim', mid: 'Other 2s', three: '3s', ft: 'FT' };

export function ShotValueInfo() {
    return (
        <InfoTooltip label="About Shot Value Added" title="Shot Value Added">
            Every attempt since 2020-21 is priced before its game twice. As an average shooter would make it: a location model
            fitted only on earlier seasons, moved by the league&apos;s level so far that season (free throws at last season&apos;s
            league rate). As this shooter would make it: the same, plus his own skill as of the day before, a number for each
            kind of attempt (at the rim, other twos, threes, free throws) that carries across seasons and moves with every game.
            Skill points are the difference: what his record said his shooting adds over an average shooter taking the same
            shots. Beyond is what he scored past that this season, luck included. Shot Value Added is the skill part.
        </InfoTooltip>
    );
}

function useWidth(initial = 720) {
    const ref = useRef(null);
    const [W, setW] = useState(initial);
    useEffect(() => {
        const el = ref.current;
        if (!el || typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(([entry]) => {
            const w = Math.round(entry.contentRect.width);
            if (w > 0) setW(Math.max(280, w));
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);
    return [ref, W];
}

// ── Skill track: one small panel per kind of attempt, carried-in skill per season with its 95% band ──
const TM = { l: 40, r: 8, t: 22, b: 24 };

function SkillTrack({ track, classes, name }) {
    const [boxRef, W] = useWidth();
    const svgRef = useRef(null);
    const cols = W < 560 ? 2 : 4;
    const pw = W / cols;
    const ph = 150;
    const rows = Math.ceil(classes.length / cols);
    const H = ph * rows;
    const seasons = [...new Set(track.map((t) => t.season))].sort();
    const summary = `${name}'s shooting skill carried into each season, in percentage points over an average shooter on the same attempts: `
        + classes.map((c) => {
            const last = track.filter((t) => t.cls === c.id).slice(-1)[0];
            return `${c.label} ${last ? signed(last.pre, 1) : '—'}`;
        }).join(', ') + ` (${seasons.length ? label(seasons[seasons.length - 1]) : ''}).`;
    return (
        <div className="sv-track" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={`${name} shooting skill track`} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary}>
                {classes.map((c, k) => {
                    const ox = (k % cols) * pw;
                    const oy = Math.floor(k / cols) * ph;
                    const pts = track.filter((t) => t.cls === c.id);
                    const vals = pts.flatMap((t) => [t.pre_lo, t.pre_hi, t.post]).filter((v) => v != null);
                    const lim = Math.max(2, ...vals.map((v) => Math.abs(v))) * 1.1;
                    const n = seasons.length;
                    const sx = (s) => ox + TM.l + ((seasons.indexOf(s) + 0.5) / n) * (pw - TM.l - TM.r);
                    const sy = (v) => oy + TM.t + (1 - (v + lim) / (2 * lim)) * (ph - TM.t - TM.b);
                    const step = lim > 12 ? 10 : lim > 6 ? 5 : lim > 3 ? 2 : 1;
                    const ticks = [];
                    for (let t = -Math.floor(lim / step) * step; t <= lim + 1e-9; t += step) ticks.push(t);
                    const line = pts.filter((t) => t.pre != null).map((t, i) => `${i ? 'L' : 'M'}${sx(t.season).toFixed(1)},${sy(t.pre).toFixed(1)}`).join('');
                    const band = pts.filter((t) => t.pre_lo != null);
                    const area = band.length
                        ? `M${band.map((t) => `${sx(t.season).toFixed(1)},${sy(t.pre_hi).toFixed(1)}`).join('L')}`
                          + `L${[...band].reverse().map((t) => `${sx(t.season).toFixed(1)},${sy(t.pre_lo).toFixed(1)}`).join('L')}Z`
                        : '';
                    return (
                        <g key={c.id}>
                            <text className="sv-panel-title" x={ox + TM.l} y={oy + 13}>{c.label}</text>
                            {ticks.map((t) => (
                                <g key={t}>
                                    <line className={t === 0 ? 'sv-zero' : 'sv-grid'} x1={ox + TM.l} x2={ox + pw - TM.r} y1={sy(t)} y2={sy(t)} />
                                    <text className="sv-tick" x={ox + TM.l - 5} y={sy(t)} textAnchor="end" dominantBaseline="middle">{signed(t, 0)}</text>
                                </g>
                            ))}
                            {area && <path className="sv-band" d={area} />}
                            {line && <path className="sv-line" d={line} />}
                            {pts.map((t) => (
                                <g key={t.season}>
                                    {t.pre != null && <circle className="sv-dot" cx={sx(t.season)} cy={sy(t.pre)} r={3}>
                                        <title>{`${c.label}, ${label(t.season)}: carried in ${signed(t.pre)} (95% ${signed(t.pre_lo)} to ${signed(t.pre_hi)}), after the season ${signed(t.post)}; ${int(t.att)} attempts`}</title>
                                    </circle>}
                                    {t.post != null && t.att > 0 && <circle className="sv-dot-post" cx={sx(t.season) + 4} cy={sy(t.post)} r={2.5} />}
                                </g>
                            ))}
                            {seasons.map((s, i) => ((pw - TM.l - TM.r) / n >= 26 || (n - 1 - i) % 2 === 0 ? (   // narrow: every other, latest kept
                                <text key={s} className="sv-tick" x={sx(s)} y={oy + ph - 8} textAnchor="middle">{`'${String(s).slice(-2)}`}</text>
                            ) : null))}
                        </g>
                    );
                })}
            </svg>
            <p className="sv-legend">
                <span className="sv-key sv-key--line" /> carried into the season (95% band) <span className="sv-key sv-key--post" /> after the season;
                percentage points over an average shooter on the same attempt, at that season&apos;s league rate.
            </p>
        </div>
    );
}

// ── The selected player's seasons ─────────────────────────────────────────
export function ShotValuePlayer({ playerId, playerName }) {
    // keyed by player in the parent, so a new player remounts with empty state
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    useEffect(() => {
        if (!playerId) return undefined;
        let live = true;
        fetchShotValuePlayer(playerId)
            .then((d) => { if (live) setData(d); })
            .catch((e) => { if (live) setError(e?.response?.data?.detail || 'Could not load his shot value.'); });
        return () => { live = false; };
    }, [playerId]);
    if (!playerId) return null;
    if (error) {
        return (
            <div className="dashboard-card sv-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>{playerName}: Shot Value Added</h3>
                <p className="page-subtitle">Not on file: {error}</p>
            </div>
        );
    }
    if (!data) return <div className="dashboard-card sv-card"><Loader /></div>;
    const rows = data.seasons;
    return (
        <div className="dashboard-card sv-card">
            <div className="sv-head">
                <h3 className="section-heading" style={{ marginTop: 0 }}>{data.player_name}: Shot Value Added <ShotValueInfo /></h3>
                <SourceBadge source={data._source} />
            </div>
            <SkillTrack track={data.track} classes={data.classes} name={data.player_name} />
            <TableExport name={`${data.player_name} shot value by season`} />
            <div className="table-wrapper">
                <table className="data-table lb-table sv-table">
                    <thead>
                        <tr>
                            <th>Season</th><th>Team</th>
                            <th className="lb-num" title="Field-goal attempts on the shot chart">FGA</th>
                            <th className="lb-num" title="Points from field goals">FG pts</th>
                            <th className="lb-num" title="What an average shooter would have scored on these shots, at the league's level so far">Avg shooter</th>
                            <th className="lb-num" title="What his record said his shooting adds: shooter-aware minus average-shooter expected points">Skill</th>
                            <th className="lb-num" title="FG points scored beyond the shooter-aware expectation this season (luck included)">Beyond</th>
                            <th className="lb-num" title="FG points above an average shooter on the same shots (skill + beyond)">Total</th>
                            <th className="lb-num" title="The same skill points for free throws">FT skill</th>
                            <th className="lb-num" title="Shot Value Added: field-goal and free-throw skill points">SVA</th>
                            <th className="lb-num" title="Shot Value Added per 100 shots (FGA + 0.44 FTA)">per 100</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.season} className={r.qualified ? '' : 'sv-row--small'}>
                                <td>{label(r.season)}</td>
                                <td>{r.team_abbreviation ? <TeamLink abbr={r.team_abbreviation} season={r.season} /> : '—'}</td>
                                <td className="lb-num">{int(r.fga)}</td>
                                <td className="lb-num">{int(r.pts)}</td>
                                <td className="lb-num">{int(r.x_blind)}</td>
                                <td className={`lb-num ${tone(r.skill_pts)}`}>{signed(r.skill_pts, 0)}</td>
                                <td className={`lb-num ${tone(r.above_pts)}`}>{signed(r.above_pts, 0)}</td>
                                <td className={`lb-num ${tone(r.total_pts)}`}>{signed(r.total_pts, 0)}</td>
                                <td className={`lb-num ${tone(r.ft_skill_pts)}`}>{signed(r.ft_skill_pts, 0)}</td>
                                <td className={`lb-num sv-strong ${tone(r.sva)}`}>{signed(r.sva, 0)}</td>
                                <td className={`lb-num ${tone(r.sva_per100)}`}>{signed(r.sva_per100, 1)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle sv-note">Seasons under 200 FGA are greyed. {data.not_on_file}</p>
        </div>
    );
}

// ── One season's players ──────────────────────────────────────────────────
const LB_COLS = [
    ['fga', 'FGA', 'Field-goal attempts on the shot chart', (r) => int(r.fga)],
    ['pts', 'FG pts', 'Points from field goals', (r) => int(r.pts)],
    ['skill_pts', 'Skill', 'FG points his record said his shooting adds over an average shooter on the same shots', (r) => signed(r.skill_pts, 0), 'skill_pts'],
    ['beyond', 'Beyond', 'FG and FT points scored beyond the shooter-aware expectation this season (luck included)', (r) => signed(r.beyond, 0), 'beyond'],
    ['total_pts', 'FG total', 'FG points above an average shooter on the same shots (skill + beyond)', (r) => signed(r.total_pts, 0), 'total_pts'],
    ['ft_skill_pts', 'FT skill', 'Free-throw points his record said he adds over a league-average free-throw shooter', (r) => signed(r.ft_skill_pts, 0), 'ft_skill_pts'],
    ['sva', 'SVA', 'Shot Value Added: FG + FT skill points', (r) => signed(r.sva, 0), 'sva'],
    ['sva_per100', 'per 100', 'Shot Value Added per 100 shots (FGA + 0.44 FTA)', (r) => signed(r.sva_per100, 1), 'sva_per100'],
    ['pre_rim', 'Rim', 'Skill at the rim carried into the season, percentage points over an average shooter', (r) => signed(r.pre_rim, 1), 'pre_rim'],
    ['pre_mid', 'Other 2s', 'Skill on other twos carried into the season, percentage points', (r) => signed(r.pre_mid, 1), 'pre_mid'],
    ['pre_three', '3s', 'Skill on threes carried into the season, percentage points', (r) => signed(r.pre_three, 1), 'pre_three'],
    ['pre_ft', 'FT', 'Free-throw skill carried into the season, percentage points over the league rate', (r) => signed(r.pre_ft, 1), 'pre_ft'],
];

export function ShotValueLeaderboard({ season, sort, dir, minFga, onChange }) {
    const [result, setResult] = useState({ key: null, data: null, error: '' });
    const key = `${season ?? ''}|${minFga}`;
    useEffect(() => {
        let live = true;
        fetchShotValue({ season: season ?? undefined, min_fga: minFga })
            .then((d) => { if (live) setResult({ key, data: d, error: '' }); })
            .catch((e) => { if (live) setResult({ key, data: null, error: e?.response?.data?.detail || 'Could not load the season.' }); });
        return () => { live = false; };
    }, [key, season, minFga]);
    // keep showing the last table while the next one loads (no flash), but never a stale error
    const data = result.data;
    const error = result.key === key ? result.error : '';
    const sorted = useMemo(() => {
        if (!data) return [];
        const s = dir === 'asc' ? 1 : -1;
        return [...data.players].sort((a, b) => {
            const va = a[sort];
            const vb = b[sort];
            if (va == null && vb == null) return 0;
            if (va == null) return 1;
            if (vb == null) return -1;
            return s * (va - vb);
        });
    }, [data, sort, dir]);
    if (error) return <div className="dashboard-card sv-card"><p className="page-subtitle">Not on file: {error}</p></div>;
    if (!data) return <div className="dashboard-card sv-card"><Loader /></div>;
    const lg = data.league;
    const onSort = (k) => onChange(k === sort ? { dir: dir === 'asc' ? 'desc' : 'asc' } : { sort: k, dir: 'desc' });
    const yty = Object.fromEntries((data.year_to_year || []).map((y) => [y.part, y]));
    return (
        <div className="dashboard-card sv-card">
            <div className="sv-head">
                <h3 className="section-heading" style={{ marginTop: 0 }}>Shot Value Added, {label(data.season)}</h3>
                <SourceBadge source={data._source} />
            </div>
            <div className="sv-controls">
                <label className="sv-select">
                    <span>Season</span>
                    <select className="input-field" value={data.season} onChange={(e) => onChange({ season: Number(e.target.value) })}>
                        {[...data.seasons].reverse().map((s) => <option key={s} value={s}>{label(s)}</option>)}
                    </select>
                </label>
                <label className="sv-select">
                    <span>Min. FGA</span>
                    <select className="input-field" value={minFga} onChange={(e) => onChange({ minFga: Number(e.target.value) })}>
                        {SV_MINS.map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                </label>
            </div>
            <p className="page-subtitle sv-note">
                {sorted.length} players with {minFga}+ FGA. The league as a whole scored {signed(lg.above_per100_fga, 2)} FG points per 100 FGA
                beyond its own forecast and {signed(lg.ft_above_per100_fta, 2)} per 100 FTA on free throws{Math.abs(lg.above_per100_fga ?? 0) >= 0.5
                    ? ': the league level moved during the season and the running estimate lags it, so read a player\'s beyond against that.' : '.'}
                {yty.skill_pts && <> From {yty.skill_pts.pair}, skill per FGA repeated with r = {yty.skill_pts.r.toFixed(2)} and beyond per FGA with r = {yty.above_pts?.r.toFixed(2)} ({yty.skill_pts.n} players with 200+ FGA in both).</>}
            </p>
            <TableExport name={`shot value added ${label(data.season)}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table sv-table">
                    <thead>
                        <tr>
                            <th>#</th><th>Player</th><th>Team</th>
                            {LB_COLS.map(([k, lab, title]) => (
                                <th key={k} className="lb-num" title={title} aria-sort={sort === k ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
                                    <button type="button" className={`sv-sort ${sort === k ? 'sv-sort--active' : ''}`} onClick={() => onSort(k)}>
                                        {lab}{sort === k && <span aria-hidden="true">{dir === 'asc' ? ' ▲' : ' ▼'}</span>}
                                    </button>
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {sorted.map((r, i) => (
                            <tr key={r.player_id}>
                                <td>{i + 1}</td>
                                <td><PlayerName name={r.player_name} playerId={r.player_id} /></td>
                                <td>{r.team_abbreviation ? <TeamLink abbr={r.team_abbreviation} season={r.season} /> : '—'}</td>
                                {LB_COLS.map(([k, , , fmt, toned]) => (
                                    <td key={k} className={`lb-num ${toned ? tone(r[toned]) : ''} ${k === 'sva' ? 'sv-strong' : ''}`}>{fmt(r)}</td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle sv-note">{data.not_on_file}</p>
        </div>
    );
}

// ── Does it work: out-of-sample pricing, how much the parts repeat, the settings ──
const PRICE_LABEL = {
    blind: 'Location model alone',
    lf: 'Average shooter (+ league level so far)',
    pre: 'Shooter skill frozen at the season start',
    sa: 'Shooter skill as of the day before',
    xfg: 'Round 5 model (cross-fitted over all seasons)',
};

export function ShotValueModel() {
    const [data, setData] = useState(null);
    const [scope, setScope] = useState('fg');
    useEffect(() => {
        let live = true;
        fetchShotValueOptions().then((d) => { if (live) setData(d); }).catch(() => {});
        return () => { live = false; };
    }, []);
    if (!data) return null;
    // per season, then the tune span and all six (the validate and test scopes are single seasons, already listed)
    const val = data.validation.filter((r) => r.cls === scope && r.scope !== 'validate' && r.scope !== 'test');
    const scopes = [...new Set(val.map((r) => r.scope))];
    const prices = ['lf', 'pre', 'sa', 'blind', 'xfg'].filter((p) => val.some((r) => r.price === p));
    const get = (sc, p) => val.find((r) => r.scope === sc && r.price === p);
    const pairs = [...new Set(data.year_to_year.map((y) => y.pair))];
    const yty = (pair, part) => data.year_to_year.find((y) => y.pair === pair && y.part === part);
    return (
        <div className="dashboard-card sv-card sv-model">
            <h3 className="section-heading" style={{ marginTop: 0 }}>Does the shooter&apos;s record price shots better?</h3>
            <p className="page-subtitle">{data.method}</p>
            <div className="tab-bar lb-modes" role="tablist" aria-label="Kind of attempt">
                {[['fg', 'All field goals'], ['rim', 'Rim'], ['mid', 'Other 2s'], ['three', '3s'], ['ft', 'Free throws']].map(([id, lab]) => (
                    <button key={id} type="button" role="tab" aria-selected={scope === id} className={`tab-btn ${scope === id ? 'tab-btn--active' : ''}`}
                        onClick={() => setScope(id)}>{lab}</button>
                ))}
            </div>
            <TableExport name={`shot value pricing test ${scope}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table sv-table">
                    <thead>
                        <tr>
                            <th>Seasons</th>
                            <th className="lb-num">Attempts</th>
                            {prices.map((p) => <th key={p} className="lb-num" title={PRICE_LABEL[p]}>{PRICE_LABEL[p]}</th>)}
                            <th className="lb-num" title="Log loss with the shooter's skill minus the average shooter's, per 1,000 attempts, with a 95% interval resampling games">Skill gain (95%)</th>
                        </tr>
                    </thead>
                    <tbody>
                        {scopes.map((sc) => {
                            const sa = get(sc, 'sa');
                            const base = get(sc, 'lf');
                            return (
                                <tr key={sc} className={sc === 'tune' || sc === 'all' ? 'sv-row--pooled' : ''}>
                                    <td>{sc === 'tune' ? '2020-21 to 2023-24' : sc === 'all' ? 'All six' : sc}</td>
                                    <td className="lb-num">{int(base?.n)}</td>
                                    {prices.map((p) => <td key={p} className="lb-num">{get(sc, p)?.log_loss?.toFixed(4) ?? '—'}</td>)}
                                    <td className={`lb-num ${sa && sa.ci_hi < 0 ? 'sv-pos' : ''}`}>
                                        {sa ? `${signed(1000 * sa.d_log_loss_vs_lf)} (${signed(1000 * sa.ci_lo)} to ${signed(1000 * sa.ci_hi)})` : '—'}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle sv-note">
                Log loss per attempt (lower is better). Every season here is out of sample for the skill model: its settings were
                estimated on 2010-11 to 2019-20, and the location model that prices a season was fitted on earlier seasons only.
                The bold rows pool the paper&apos;s tune seasons and all six (nothing here was tuned on any of them).
            </p>
            <h4 className="sv-subhead">How much each part repeats the next season</h4>
            <div className="table-wrapper">
                <table className="data-table lb-table sv-table">
                    <thead>
                        <tr>
                            <th>Seasons</th>
                            <th className="lb-num" title="Players with 200+ FGA in both seasons">Players</th>
                            <th className="lb-num" title="Skill points per FGA">Skill</th>
                            <th className="lb-num" title="Beyond points per FGA">Beyond</th>
                            <th className="lb-num" title="Total above an average shooter per FGA">Total</th>
                            <th className="lb-num" title="Free-throw skill points per FTA (50+ FTA both seasons)">FT skill</th>
                            <th className="lb-num" title="Free-throw beyond points per FTA">FT beyond</th>
                        </tr>
                    </thead>
                    <tbody>
                        {pairs.map((p) => (
                            <tr key={p}>
                                <td>{p}</td>
                                <td className="lb-num">{yty(p, 'skill_pts')?.n ?? '—'}</td>
                                {['skill_pts', 'above_pts', 'total_pts', 'ft_skill_pts', 'ft_above_pts'].map((k) => (
                                    <td key={k} className="lb-num">{yty(p, k)?.r?.toFixed(2) ?? '—'}</td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle sv-note">
                Correlation of a player&apos;s per-attempt value in one season with the next. Skill is built from his record, so part of its
                persistence is by construction; the useful reading is that the beyond part, which the shooter-aware RAPM drops as luck,
                barely repeats.
            </p>
            <h4 className="sv-subhead">The skill model&apos;s settings (estimated on {data.params[0]?.estimated_on})</h4>
            <div className="table-wrapper">
                <table className="data-table lb-table sv-table">
                    <thead>
                        <tr>
                            <th>Attempt</th>
                            <th className="lb-num" title="League make rate the percentage points below are measured at">League rate</th>
                            <th className="lb-num" title="A debut season starts here on average (percentage points over an average shooter)">Rookie start</th>
                            <th className="lb-num" title="Spread of debut skill (one standard deviation, percentage points)">Rookie spread</th>
                            <th className="lb-num" title="Share of last season's skill carried into the next">Carry-over</th>
                            <th className="lb-num" title="How far skill may move between seasons (one standard deviation, percentage points)">Season drift</th>
                            <th className="lb-num" title="Spread of skill among established players (one standard deviation, percentage points)">Veteran spread</th>
                            <th className="lb-num">Attempts used</th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.params.map((p) => (
                            <tr key={p.cls}>
                                <td>{p.label}</td>
                                <td className="lb-num">{pctOf(p.base_rate)}</td>
                                <td className="lb-num">{signed(p.rookie_mean_pp, 1)}</td>
                                <td className="lb-num">±{p.rookie_sd_pp?.toFixed(1)}</td>
                                <td className="lb-num">{p.carry?.toFixed(2)}</td>
                                <td className="lb-num">±{p.drift_sd_pp?.toFixed(1)}</td>
                                <td className="lb-num">±{p.veteran_sd_pp?.toFixed(1)}</td>
                                <td className="lb-num">{int(p.attempts)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle sv-note">
                Empirical Bayes: the four numbers per kind of attempt maximise the likelihood of every attempt of 2010-11 to 2019-20,
                priced by the 2020-21 location models, from three starting points (the Methodology card lists them).
            </p>
        </div>
    );
}

export default function ShotValue({ playerId, playerName, state, onChange }) {
    return (
        <>
            <ShotValuePlayer key={playerId} playerId={playerId} playerName={playerName} />
            <ShotValueLeaderboard season={state.season} sort={state.sort} dir={state.dir} minFga={state.minFga} onChange={onChange} />
            <ShotValueModel />
        </>
    );
}
