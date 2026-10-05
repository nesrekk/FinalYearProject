import React, { useEffect, useRef, useState } from 'react';
import { fetchAgingCurves, fetchAgingPlayer, fetchEraPlayers } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import ChartExport from '../common/ChartExport';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import AutocompleteDropdown from '../common/AutocompleteDropdown';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/stability.css';
import '../../styles/aging.css';
import { signed } from '../../utils/format';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const ERAS = [
    ['all', 'Every season'],
    ['three_point', 'Three-point era'],
    ['modern', '2009-10 on'],
];
const ERA_IDS = ERAS.map(([id]) => id);
const isPct = (kind) => kind === 'pct' || kind === 'rate';
// A level or a value in the stat's own units.
const fmtVal = (kind, v) => (v == null ? '—' : isPct(kind) ? `${(v * 100).toFixed(1)}%` : v.toFixed(kind === 'per36' ? 2 : 1));
// A difference: percentage points for percentages.
const fmtDiff = (kind, v, unit = true) => {
    if (v == null || Number.isNaN(v)) return '—';
    const x = isPct(kind) ? v * 100 : v;
    const d = kind === 'per36' || isPct(kind) ? (Math.abs(x) < 10 ? 2 : 1) : 1;
    return signed(x, d) + (unit && isPct(kind) ? ' pts' : '');
};
const pctTxt = (v) => (v == null ? '—' : `${Math.round(v * 100)}%`);

const EXAMPLES = [
    { label: 'BPM · LeBron James', stat: 'bpm', era: 'all', player: 2544 },
    { label: 'Points per 36 · Dirk Nowitzki', stat: 'pts', era: 'all', player: 1717 },
    { label: 'Blocks per 36 · Tim Duncan', stat: 'blk', era: 'all', player: 1495 },
    { label: '3-point attempts · Stephen Curry', stat: 'fg3a', era: 'modern', player: 201939 },
    { label: 'Free-throw % · every era', stat: 'ft_pct', era: 'all', player: null },
];

// ─── The curve (hand-built SVG, like the app's other charts) ──────────
const M = { l: 52, r: 16, t: 18, b: 40 };
const BARS_H = 50;

function useWidth() {
    const ref = useRef(null);
    const [W, setW] = useState(720);
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

function niceStep(span, count) {
    const raw = span / count;
    const mag = 10 ** Math.floor(Math.log10(raw));
    return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
}

function AgingChart({ data, player }) {
    const [boxRef, W] = useWidth();
    const svgRef = useRef(null);
    const narrow = W < 520;
    const H = narrow ? 300 : 360;
    const s = data.summary;
    const kind = s.kind;
    const pts = data.points;
    const anchor = pts[0].level - pts[0].change_vs_ref; // level at the reference age
    const band = pts.map((p) => ({ age: p.age, lo: anchor + p.ci_lo, hi: anchor + p.ci_hi }));
    const playerPts = (player?.seasons ?? []).filter((x) => x.vs_league != null && x.age != null);
    const others = data.eras.filter((e) => e.era !== data.era);

    const ages = [...pts.map((p) => p.age), ...playerPts.map((x) => x.age)];
    const a0 = Math.min(...ages);
    const a1 = Math.max(...ages);
    const ys = [
        ...band.flatMap((b) => [b.lo, b.hi]),
        ...others.flatMap((e) => e.points.map((p) => p.level)),
        ...playerPts.map((x) => x.vs_league),
        ...(player?.path ?? []).map((p) => p.level),
        0,
    ];
    let y0 = Math.min(...ys);
    let y1 = Math.max(...ys);
    const pad = (y1 - y0) * 0.08 || 1;
    y0 -= pad;
    y1 += pad;
    const step = niceStep(y1 - y0, narrow ? 4 : 6);
    const yTicks = [];
    for (let t = Math.ceil(y0 / step) * step; t <= y1 + 1e-9; t += step) yTicks.push(Number(t.toFixed(10)));

    const sx = (a) => M.l + ((a - a0) / Math.max(1, a1 - a0)) * (W - M.l - M.r);
    const sy = (v) => H - M.b - ((v - y0) / (y1 - y0)) * (H - M.t - M.b);
    const line = (arr, key = 'level') => arr.map((p, i) => `${i ? 'L' : 'M'}${sx(p.age).toFixed(1)},${sy(p[key]).toFixed(1)}`).join('');
    const area = `${band.map((b, i) => `${i ? 'L' : 'M'}${sx(b.age).toFixed(1)},${sy(b.hi).toFixed(1)}`).join('')}`
        + `${band.slice().reverse().map((b) => `L${sx(b.age).toFixed(1)},${sy(b.lo).toFixed(1)}`).join('')}Z`;
    const peak = pts.find((p) => p.age === s.peak_age);
    const ageStep = narrow ? 4 : 2;
    const xTicks = [];
    for (let a = Math.ceil(a0 / ageStep) * ageStep; a <= a1; a += ageStep) xTicks.push(a);
    const maxPairs = Math.max(...pts.map((p) => p.pairs ?? 0));
    const bw = Math.max(3, ((W - M.l - M.r) / Math.max(1, a1 - a0 + 1)) * 0.7);
    const yUnit = isPct(kind) ? 'percentage points' : kind === 'per36' ? 'per 36 minutes' : kind === 'per_game' ? 'minutes a game' : 'points per 100 possessions';
    const aria = `${s.label} by age against that season's league average, ${s.era_label}: highest at ${s.peak_age}`
        + (player ? `, with ${player.player.player_name}'s seasons.` : '.');

    return (
        <div className="rx-chart ag-chart" ref={boxRef}>
            <ChartExport svgRef={svgRef} name={`${s.label} aging curve ${s.era_label}`} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={aria}>
                {yTicks.map((t) => (
                    <g key={`y${t}`}>
                        <line className={t === 0 ? 'ag-zero' : 'rx-grid'} x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                        <text className="rx-tick" x={M.l - 8} y={sy(t)} textAnchor="end" dominantBaseline="middle">
                            {fmtDiff(kind, t, false)}
                        </text>
                    </g>
                ))}
                {xTicks.map((a) => (
                    <text key={`x${a}`} className="rx-tick" x={sx(a)} y={H - M.b + 16} textAnchor="middle">{a}</text>
                ))}
                <text className="rx-axis" x={(M.l + W - M.r) / 2} y={H - 6} textAnchor="middle">Age (on February 1 of the season)</text>
                <text className="ag-zero-label" x={W - M.r - 4} y={sy(0) - 6} textAnchor="end">League average</text>
                <path className="ag-band" d={area} />
                {others.map((e) => (
                    <path key={e.era} className={`ag-era ag-era--${e.era}`} d={line(e.points)}>
                        <title>{`${e.era_label}: highest at ${e.peak_age}`}</title>
                    </path>
                ))}
                <path className="ag-curve" d={line(pts)} />
                {pts.filter((p) => p.thin || p.pairs == null).map((p) => (
                    <circle key={`t${p.age}`} className="ag-thin" cx={sx(p.age)} cy={sy(p.level)} r={3} />
                ))}
                {peak && (
                    <g className="ag-peak">
                        <circle cx={sx(peak.age)} cy={sy(peak.level)} r={5.5} />
                        <text x={sx(peak.age)} y={sy(peak.level) - 11} textAnchor="middle">
                            {s.higher_is_better ? 'Peak' : 'Lowest'} {peak.age}
                        </text>
                    </g>
                )}
                {player?.path && <path className="ag-path" d={line(player.path)} />}
                {playerPts.length > 1 && <path className="ag-player-line" d={line(playerPts, 'vs_league')} />}
                {playerPts.map((x) => (
                    <circle key={x.season} className={x.qualified ? 'ag-player' : 'ag-player ag-player--short'}
                        cx={sx(x.age)} cy={sy(x.vs_league)} r={x.qualified ? 4.5 : 3.5}>
                        <title>{`${seasonLabel(x.season)}, age ${x.age}: ${fmtVal(kind, x.value)} (${fmtDiff(kind, x.vs_league)} vs. league)${x.note ? `, not used: ${x.note}` : ''}`}</title>
                    </circle>
                ))}
                {pts.map((p) => (
                    <circle key={`h${p.age}`} className="ag-hit" cx={sx(p.age)} cy={sy(p.level)} r={7}>
                        <title>{`Age ${p.age}: ${fmtDiff(kind, p.level)} vs. league average; ${fmtDiff(kind, p.change_vs_ref)} vs. age ${s.ref_age} (95% range ${fmtDiff(kind, p.ci_lo)} to ${fmtDiff(kind, p.ci_hi)})${p.pairs != null ? `; ${p.pairs} players to the next age` : ''}`}</title>
                    </circle>
                ))}
            </svg>
            <svg className="ag-bars" viewBox={`0 0 ${W} ${BARS_H}`} role="img"
                aria-label={`Sample by age: players with a qualified season at that age and the next, most at age ${pts.reduce((m, p) => ((p.pairs ?? 0) > (m.pairs ?? 0) ? p : m), pts[0]).age}.`}>
                {pts.filter((p) => p.pairs != null).map((p) => {
                    const h = (p.pairs / maxPairs) * (BARS_H - 8);
                    return (
                        <rect key={p.age} className={p.thin ? 'ag-bar ag-bar--thin' : 'ag-bar'}
                            x={sx(p.age) - bw / 2} y={BARS_H - 2 - h} width={bw} height={Math.max(1, h)}>
                            <title>{`Age ${p.age} to ${p.age + 1}: ${p.pairs} players${p.thin ? ` (under ${data.thin_pairs}: thin)` : ''}`}</title>
                        </rect>
                    );
                })}
                <text className="rx-tick" x={M.l - 8} y={12} textAnchor="end">{maxPairs.toLocaleString()}</text>
            </svg>
            <p className="ag-key">
                <span className="ag-key-item"><span className="ag-swatch ag-swatch--curve" />Typical player, {s.era_label.toLowerCase()} (band: 95% range, relative to age {s.ref_age})</span>
                {others.map((e) => (
                    <span key={e.era} className="ag-key-item"><span className={`ag-swatch ag-swatch--${e.era}`} />{e.era_label}</span>
                ))}
                {player && (
                    <>
                        <span className="ag-key-item"><span className="ag-swatch ag-swatch--player" />{player.player.player_name} (hollow: not used, too few minutes or attempts)</span>
                        {player.path && <span className="ag-key-item"><span className="ag-swatch ag-swatch--path" />Typical curve at his level</span>}
                    </>
                )}
                <span className="ag-key-item">Bars: players aging from each age to the next (lighter: under {data.thin_pairs}).</span>
                <span className="ag-key-item">Y axis: difference from that season&apos;s league average, {yUnit}.</span>
            </p>
        </div>
    );
}

// ─── Player search (local database, every player since 1949-50) ───────
function PlayerSearch({ onPick }) {
    const inputRef = useRef(null);
    const [q, setQ] = useState('');
    const [hits, setHits] = useState([]);

    useEffect(() => {
        const query = q.trim();
        if (query.length < 2) return undefined;
        let live = true;
        const timer = setTimeout(() => {
            fetchEraPlayers(query)
                .then((d) => { if (live) setHits(d.results); })
                .catch(() => { if (live) setHits([]); });
        }, 200);
        return () => { live = false; clearTimeout(timer); };
    }, [q]);

    const shown = q.trim().length >= 2 ? hits : [];
    const label = (p) => `${p.player_name} (${seasonLabel(p.from)}${p.to !== p.from ? ` to ${seasonLabel(p.to)}` : ''})`;
    const labels = shown.map(label);
    return (
        <label className="era-search">
            <span>Overlay a player</span>
            <input ref={inputRef} className="input-field" type="search" value={q} placeholder="Search any player"
                autoComplete="off" onChange={(e) => setQ(e.target.value)} />
            <AutocompleteDropdown anchorRef={inputRef} items={labels}
                onPick={(l) => {
                    const p = shown[labels.indexOf(l)];
                    if (p) onPick(p);
                    setQ('');
                    setHits([]);
                }} />
        </label>
    );
}

export default function AgingCurves() {
    const params = useInitialParams();
    const [form, setForm] = useState(() => ({
        stat: parseParam.str(params, 'stat') ?? 'bpm',
        era: parseParam.oneOf(params, 'era', ERA_IDS) ?? 'all',
        player: parseParam.int(params, 'player', { min: 1 }),
    }));
    const curveKey = `${form.stat}|${form.era}`;
    const [curve, setCurve] = useState(null);
    const data = curve?.data ?? null;
    const curveError = curve?.key === curveKey ? curve.error : '';
    const loading = curve?.key !== curveKey;
    const playerKey = form.player ? `${form.player}|${curveKey}` : null;
    const [playerRes, setPlayerRes] = useState(null);
    const player = playerKey && playerRes?.key === playerKey ? playerRes.data : null;
    const playerError = playerKey && playerRes?.key === playerKey ? playerRes.error : '';

    useUrlSync({ stat: form.stat, era: form.era === 'all' ? null : form.era, player: form.player });

    useEffect(() => {
        let live = true;
        const [stat, era] = curveKey.split('|');
        fetchAgingCurves({ stat, era })
            .then((d) => { if (live) setCurve({ key: curveKey, data: d, error: '' }); })
            .catch((err) => {
                if (!live) return;
                // A stat or era from an old link that no longer exists: back to the default.
                if ([400, 404].includes(err.response?.status) && curveKey !== 'bpm|all') {
                    setForm((f) => ({ ...f, stat: 'bpm', era: 'all' }));
                    return;
                }
                setCurve((c) => ({
                    key: curveKey, data: c?.data ?? null,
                    error: err.response?.data?.detail || 'Aging curves couldn\'t load. Is the impact API (port 8002) running?',
                }));
            });
        return () => { live = false; };
    }, [curveKey]);

    useEffect(() => {
        if (!playerKey) return undefined;
        let live = true;
        const [pid, stat, era] = playerKey.split('|');
        fetchAgingPlayer({ player_id: Number(pid), stat, era })
            .then((d) => { if (live) setPlayerRes({ key: playerKey, data: d, error: '' }); })
            .catch((err) => {
                if (live) setPlayerRes({ key: playerKey, data: null, error: err.response?.data?.detail || 'That player\'s seasons couldn\'t load.' });
            });
        return () => { live = false; };
    }, [playerKey]);

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));

    if (!data) {
        return curveError
            ? <section className="dashboard-card"><p className="error-message">{curveError}</p></section>
            : <Loader />;
    }

    const s = data.summary;
    const kind = s.kind;
    const byAge = Object.fromEntries(data.points.map((p) => [p.age, p]));
    const groups = [...new Set(data.stats.map((x) => x.group))];
    const statInfo = data.stats.find((x) => x.key === data.stat);
    const at = (a) => byAge[a]?.change_vs_ref;
    const later = [31, 34].filter((a) => byAge[a]);
    const survivor = byAge[34] ?? byAge[33];
    const hib = s.higher_is_better;
    const peakRange = s.peak_lo === s.peak_hi ? `${s.peak_lo}` : `${s.peak_lo}–${s.peak_hi}`;
    const shown = player && player.player.player_id === form.player ? player : null;

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                Aging curves
                <InfoTooltip label="How the aging curves are built" title="Under the hood">
                    {data.method} Built {s.built_on}.
                </InfoTooltip>
                <SourceBadge source={data._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="aging" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                How a typical NBA player&apos;s game changes from one birthday to the next: every player who played two
                seasons in a row, compared with himself, added up by age.
            </p>

            <div className="lb-presets" aria-label="Examples">
                {EXAMPLES.map((ex) => (
                    <button key={ex.label} type="button"
                        aria-pressed={form.stat === ex.stat && form.era === ex.era && form.player === ex.player}
                        onClick={() => setForm({ stat: ex.stat, era: ex.era, player: ex.player })}>
                        {ex.label}
                    </button>
                ))}
            </div>

            <div className="lb-controls ag-controls">
                <label>
                    <span>Stat</span>
                    <select className="input-field" value={form.stat} onChange={(e) => set({ stat: e.target.value })}>
                        {groups.map((g) => (
                            <optgroup key={g} label={g}>
                                {data.stats.filter((x) => x.group === g).map((x) => (
                                    <option key={x.key} value={x.key}>{x.label}</option>
                                ))}
                            </optgroup>
                        ))}
                    </select>
                </label>
                <PlayerSearch onPick={(p) => set({ player: p.player_id })} />
                {form.player && (
                    <div className="ag-clear">
                        <button type="button" className="ag-clear-btn" onClick={() => set({ player: null })}>
                            Remove {shown ? shown.player.player_name : 'player'}
                        </button>
                    </div>
                )}
            </div>
            <div className="tab-bar lb-modes ag-eras" role="tablist" aria-label="Which seasons">
                {ERAS.map(([id, lab]) => {
                    const has = statInfo?.peaks[id];
                    return (
                        <button key={id} type="button" role="tab" aria-selected={form.era === id} disabled={!has}
                            className={`tab-btn ${form.era === id ? 'tab-btn--active' : ''}`}
                            onClick={() => set({ era: id })}>
                            {lab}
                        </button>
                    );
                })}
            </div>

            {curveError && <p className="error-message">{curveError}</p>}
            <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                <p className="rx-verdict">
                    <strong>{s.label}</strong>, {seasonLabel(s.season_from)} to {seasonLabel(s.season_to)}:{' '}
                    a typical player is at his {hib ? 'highest' : 'lowest'} at <strong>{s.peak_age}</strong>{' '}
                    (95% range {peakRange}).
                    {later.length > 0 && (
                        <> Compared with age {s.ref_age}, he&apos;s{' '}
                            {later.map((a, i) => (
                                <React.Fragment key={a}>
                                    {i > 0 && ' and '}<strong>{fmtDiff(kind, at(a))}</strong> at {a}
                                </React.Fragment>
                            ))}
                            {byAge[22] && <>, and {fmtDiff(kind, at(22))} at 22</>}.</>
                    )}
                    {' '}From {s.pairs.toLocaleString()} year-to-year pairs by {s.players.toLocaleString()} players.
                </p>
                {survivor?.returned_share != null && (
                    <p className="era-note">
                        <strong>Survivor bias:</strong> of {survivor.seasons_with_next.toLocaleString()} qualified players
                        aged {survivor.age}, {pctTxt(1 - survivor.returned_share)} never had another qualified season
                        {survivor.leaver_gap != null && (
                            <>; that year they were {fmtDiff(kind, Math.abs(survivor.leaver_gap)).replace('+', '')}{' '}
                                {survivor.leaver_gap < 0 ? 'below' : 'above'} the players who came back</>
                        )}. Their next season isn&apos;t in the curve, so the drop at older ages is if anything too gentle.
                    </p>
                )}

                <AgingChart data={data} player={shown} />

                {form.player && (
                    <div className="ag-player-block">
                        {playerError && <p className="error-message">{playerError}</p>}
                        {!shown && !playerError && <Loader />}
                        {shown && (
                            <>
                                <h3 className="ag-h3">
                                    <PlayerName playerId={shown.player.player_id} name={shown.player.player_name} />: {s.label} by age
                                </h3>
                                <p className="ag-small">
                                    {shown.qualified_seasons} of {shown.seasons.length} seasons qualify
                                    ({s.min_minutes}+ minutes{s.min_attempts ? `, ${s.min_attempts}+ attempts` : ''}
                                    {s.season_from > 1950 ? `, ${seasonLabel(s.season_from)} on` : ''}).
                                    {shown.offset != null && (
                                        <> Across them he ran <strong>{fmtDiff(kind, shown.offset)}</strong> against the
                                            typical curve at the same ages; the dashed line is that curve moved to his level
                                            (how a typical player at his level ages, not a forecast).</>
                                    )}
                                </p>
                                {shown.qualified_seasons === 0 && shown.seasons.length > 0 && (
                                    <p className="era-note era-note--warn">None of his seasons can be placed on this curve.
                                        {' '}Try a stat recorded in his era (points, rebounds or assists per 36 go back to 1951-52).</p>
                                )}
                                {shown.notes.map((n) => <p key={n} className="era-note era-note--warn">{n}</p>)}
                                <TableExport name={`aging ${shown.player.player_name} ${s.label}`} />
                                <div className="table-wrapper">
                                    <table className="data-table lb-table ag-table">
                                        <thead>
                                            <tr>
                                                <th>Season</th>
                                                <th>Team</th>
                                                <th className="lb-num">Age</th>
                                                <th className="lb-num">Games</th>
                                                <th className="lb-num">Minutes</th>
                                                <th className="lb-num lb-stat">{s.label}</th>
                                                <th className="lb-num">League</th>
                                                <th className="lb-num">vs. league</th>
                                                <th className="lb-num">Typical curve</th>
                                                <th>Not used because</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {shown.seasons.map((x) => {
                                                const c = byAge[x.age];
                                                return (
                                                    <tr key={x.season} className={x.qualified ? undefined : 'sl-short'}>
                                                        <td>{seasonLabel(x.season)}</td>
                                                        <td>{x.team}</td>
                                                        <td className="lb-num">{x.age ?? '—'}</td>
                                                        <td className="lb-num">{x.gp}</td>
                                                        <td className="lb-num">{x.minutes != null ? x.minutes.toLocaleString() : '—'}</td>
                                                        <td className="lb-num lb-stat">{fmtVal(kind, x.value)}</td>
                                                        <td className="lb-num">{fmtVal(kind, x.league_average)}</td>
                                                        <td className="lb-num">{fmtDiff(kind, x.vs_league)}</td>
                                                        <td className="lb-num">{c && shown.offset != null ? fmtDiff(kind, c.level + shown.offset) : '—'}</td>
                                                        <td className="era-row-note">{x.note ?? ''}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            </>
                        )}
                    </div>
                )}

                <h3 className="ag-h3">By age: {s.label}, {s.era_label.toLowerCase()}</h3>
                <TableExport name={`aging curve ${s.label} ${s.era_label}`} />
                <div className="table-wrapper">
                    <table className="data-table lb-table ag-table">
                        <thead>
                            <tr>
                                <th className="lb-num">Age</th>
                                <th className="lb-num lb-stat">vs. league average</th>
                                <th className="lb-num">vs. age {s.ref_age}</th>
                                <th className="lb-num">95% range</th>
                                <th className="lb-num">Change to next age</th>
                                <th className="lb-num">Players to next age</th>
                                <th className="lb-num">Qualified at this age</th>
                                <th className="lb-num">Had another qualified season</th>
                                <th className="lb-num">Those who didn&apos;t, vs. those who did</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.points.map((p) => (
                                <tr key={p.age} className={p.thin ? 'sl-short' : undefined}>
                                    <td className="lb-num">{p.age}{p.age === s.peak_age ? ' ★' : ''}</td>
                                    <td className="lb-num lb-stat">{fmtDiff(kind, p.level)}</td>
                                    <td className="lb-num">{p.age === s.ref_age ? 'reference' : fmtDiff(kind, p.change_vs_ref)}</td>
                                    <td className="lb-num">{p.age === s.ref_age ? '—' : `${fmtDiff(kind, p.ci_lo, false)} to ${fmtDiff(kind, p.ci_hi, false)}`}</td>
                                    <td className="lb-num">{fmtDiff(kind, p.delta_next)}</td>
                                    <td className="lb-num">{p.pairs != null ? p.pairs.toLocaleString() : '—'}</td>
                                    <td className="lb-num">{p.seasons_at_age.toLocaleString()}</td>
                                    <td className="lb-num">{pctTxt(p.returned_share)}</td>
                                    <td className="lb-num">{p.leaver_gap != null ? fmtDiff(kind, p.leaver_gap) : '—'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <p className="ss-legend ag-small">
                    ★ {hib ? 'peak' : 'lowest point'}. Greyed: under {data.thin_pairs} players aging to the next year. The last
                    age has no change of its own (the curve ends there). &ldquo;Had another qualified season&rdquo; counts only
                    seasons before {seasonLabel(s.latest_season)}, which has no next season yet; the last column needs 10+ players on
                    each side.
                </p>

                <h3 className="ag-h3">Peak age for every stat</h3>
                <TableExport name="aging curves peak ages" />
                <div className="table-wrapper">
                    <table className="data-table lb-table ag-table">
                        <thead>
                            <tr>
                                <th>Stat</th>
                                {ERAS.map(([id, lab]) => <th key={id} className="lb-num">{lab}</th>)}
                                <th className="lb-num">Pairs (every season)</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.stats.map((x) => (
                                <tr key={x.key} className={x.key === data.stat ? 'ag-on' : undefined}>
                                    <td>
                                        <button type="button" className="ss-pick" aria-pressed={x.key === data.stat}
                                            onClick={() => set({ stat: x.key })}>{x.label}</button>
                                        {!x.higher_is_better && <span className="ag-small"> (lowest)</span>}
                                    </td>
                                    {ERA_IDS.map((e) => {
                                        const pk = x.peaks[e];
                                        return (
                                            <td key={e} className="lb-num">
                                                {pk ? <>{pk.age} <span className="ag-range">({pk.lo === pk.hi ? pk.lo : `${pk.lo}–${pk.hi}`})</span></> : '—'}
                                            </td>
                                        );
                                    })}
                                    <td className="lb-num">{x.peaks.all ? x.peaks.all.pairs.toLocaleString() : '—'}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <p className="ss-legend ag-small">
                    Age where the typical player is highest (lowest for turnovers and turnover %, marked), with its 95% range
                    from the bootstrap. Athletic stats (blocks, offensive rebounds, rebounds, steals) top out early; skill and
                    role stats (assists, free-throw %, three-point volume) late.
                </p>

                <div className="era-method">
                    <h3>What the curve can&apos;t tell you</h3>
                    {data.caveats.map((c) => <p key={c}>{c}</p>)}
                </div>
            </div>
        </section>
    );
}
