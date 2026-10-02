import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchProjections } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import ChartExport from '../common/ChartExport';
import ChartTooltip from '../common/ChartTooltip';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import useChartCrosshair from '../../utils/useChartCrosshair';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import { signed } from '../../utils/format';
import '../../styles/stability.css';
import '../../styles/projections.css';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const minus = (t) => t.replace('-', '−');
const fmtVal = (format, v) => {
    if (v == null || Number.isNaN(v)) return '—';
    if (format === 'pct') return `${(v * 100).toFixed(1)}%`;
    if (format === 'signed1') return signed(v, 1);
    return v.toFixed(1);
};
const fmtDiff = (format, v) => {
    if (v == null || Number.isNaN(v)) return '—';
    const x = format === 'pct' ? v * 100 : v;
    const txt = x.toFixed(1);
    const zero = Number(txt) === 0; // no sign on a rounded zero
    return minus(`${!zero && x > 0 ? '+' : ''}${zero ? '0.0' : txt}`) + (format === 'pct' ? ' pts' : '');
};
const fmtErr = (format, v, d) => (v == null ? '—' : format === 'pct' ? (v * 100).toFixed(d ?? 1) : v.toFixed(d ?? 2));
const pctTxt = (v) => (v == null ? '—' : `${Math.round(v * 100)}%`);
const SHOW = 50;

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

// ─── Backtest: mean absolute error of each method, one bar each ────────
function ErrorBars({ bt, format, label }) {
    const [ref, W] = useWidth();
    const svgRef = useRef(null);
    const methods = [
        ['Projection', bt.mae, true],
        ['Same as last season', bt.mae_last],
        ['Weighted average, no regression or age', bt.mae_average],
        ['Regressed, no age step', bt.mae_no_age],
        ['League average', bt.mae_league],
    ];
    const max = Math.max(...methods.map((m) => m[1]));
    const rowH = 30;
    const labelW = W < 520 ? 150 : 250;
    const H = rowH * methods.length + 8;
    const sx = (v) => labelW + (v / max) * (W - labelW - 70);
    return (
        <div className="rx-chart pj-bars" ref={ref}>
            <ChartExport svgRef={svgRef} name={`${label} backtest error by method`} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
                aria-label={`${label}: average miss of each method in the backtest. Projection ${fmtErr(format, bt.mae)}, same as last season ${fmtErr(format, bt.mae_last)}, league average ${fmtErr(format, bt.mae_league)}.`}>
                {methods.map(([name, v, main], i) => (
                    <g key={name} transform={`translate(0 ${i * rowH + 4})`}>
                        <text className="pj-bar-label" x={labelW - 10} y={rowH / 2} textAnchor="end" dominantBaseline="middle">{name}</text>
                        <rect className={main ? 'pj-bar pj-bar--proj' : 'pj-bar'} x={labelW} y={6} width={Math.max(2, sx(v) - labelW)} height={rowH - 12} rx={2} />
                        <text className="pj-bar-value" x={sx(v) + 6} y={rowH / 2} dominantBaseline="middle">{fmtErr(format, v)}</text>
                    </g>
                ))}
            </svg>
        </div>
    );
}

// ─── Backtest by season: projection vs. the two naive baselines ──────────
function SeasonLines({ rows, format, label }) {
    const [ref, W] = useWidth();
    const svgRef = useRef(null);
    const M = { l: 46, r: 14, t: 12, b: 32 };
    const H = W < 520 ? 200 : 240;
    const s0 = rows[0].season;
    const s1 = rows[rows.length - 1].season;
    const ys = rows.flatMap((r) => [r.mae, r.mae_last, r.mae_league]);
    const y1 = Math.max(...ys) * 1.05;
    const sx = (s) => M.l + ((s - s0) / Math.max(1, s1 - s0)) * (W - M.l - M.r);
    const sy = (v) => H - M.b - (v / y1) * (H - M.t - M.b);
    const path = (key) => rows.map((r, i) => `${i ? 'L' : 'M'}${sx(r.season).toFixed(1)},${sy(r[key]).toFixed(1)}`).join('');
    const ticks = rows.map((r) => r.season).filter((s) => s % (W < 520 ? 10 : 5) === 0);
    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * y1);
    const crosshairPoints = rows.map((r) => ({ x: sx(r.season), y: sy(r.mae), r }));
    const { point: hovered, overlayProps } = useChartCrosshair(crosshairPoints, W);
    return (
        <div className="rx-chart" ref={ref}>
            <ChartExport svgRef={svgRef} name={`${label} backtest error by season`} />
            <div style={{ position: 'relative' }}>
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
                aria-label={`${label}: average miss by target season, ${seasonLabel(s0)} to ${seasonLabel(s1)}, projection against same-as-last-season and league average.`}>
                {yTicks.map((t) => (
                    <g key={t}>
                        <line className="rx-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                        <text className="rx-tick" x={M.l - 6} y={sy(t)} textAnchor="end" dominantBaseline="middle">{fmtErr(format, t, 1)}</text>
                    </g>
                ))}
                {ticks.map((s) => <text key={s} className="rx-tick" x={sx(s)} y={H - M.b + 16} textAnchor="middle">{s}</text>)}
                <text className="rx-axis" x={(M.l + W - M.r) / 2} y={H - 4} textAnchor="middle">Target season (year it ended)</text>
                {hovered && <line x1={hovered.x} y1={M.t} x2={hovered.x} y2={H - M.b} className="chart-crosshair-line" />}
                <path className="pj-season-line pj-season-line--league" d={path('mae_league')} />
                <path className="pj-season-line pj-season-line--last" d={path('mae_last')} />
                <path className="pj-season-line" d={path('mae')} />
                <rect x={M.l} y={M.t} width={W - M.l - M.r} height={H - M.t - M.b}
                    className="chart-crosshair-overlay" role="slider" aria-label={`${label} backtest error by season, use arrow keys to step through`}
                    aria-valuetext={hovered ? `${hovered.r.season}: projection ${fmtErr(format, hovered.r.mae, 1)}, same as last season ${fmtErr(format, hovered.r.mae_last, 1)}, league average ${fmtErr(format, hovered.r.mae_league, 1)}` : undefined}
                    {...overlayProps} />
            </svg>
            {hovered && (
                <ChartTooltip x={hovered.x} y={hovered.y} chartWidth={W} chartHeight={H}>
                    <div style={{ fontWeight: 600 }}>{hovered.r.season}</div>
                    <div>Projection: {fmtErr(format, hovered.r.mae, 1)}</div>
                    <div style={{ color: 'var(--text-3)' }}>Same as last: {fmtErr(format, hovered.r.mae_last, 1)}</div>
                    <div style={{ color: 'var(--text-3)' }}>League average: {fmtErr(format, hovered.r.mae_league, 1)}</div>
                </ChartTooltip>
            )}
            </div>
            <p className="pj-key">
                <span className="pj-key-item"><span className="pj-swatch" />Projection</span>
                <span className="pj-key-item"><span className="pj-swatch pj-swatch--last" />Same as last season</span>
                <span className="pj-key-item"><span className="pj-swatch pj-swatch--league" />League average</span>
                <span className="pj-key-item">Y: mean absolute error{format === 'pct' ? ', percentage points' : ''}.</span>
            </p>
        </div>
    );
}

export default function Projections() {
    const params = useInitialParams();
    const [form, setForm] = useState(() => ({
        stat: parseParam.str(params, 'stat') ?? 'pts',
        team: parseParam.str(params, 'team'),
        q: parseParam.str(params, 'q') ?? '',
        all: parseParam.oneOf(params, 'all', ['1']) === '1',
    }));
    const [res, setRes] = useState(null);
    const data = res?.data ?? null;
    const error = res?.stat === form.stat ? res.error : '';
    const loading = res?.stat !== form.stat;
    useUrlSync({ stat: form.stat, team: form.team, q: form.q.trim() || null, all: form.all ? '1' : null });

    useEffect(() => {
        let live = true;
        const stat = form.stat;
        fetchProjections({ stat })
            .then((d) => { if (live) setRes({ stat, data: d, error: '' }); })
            .catch((err) => {
                if (!live) return;
                if (err.response?.status === 400 && stat !== 'pts') {
                    setForm((f) => ({ ...f, stat: 'pts' }));
                    return;
                }
                setRes((r) => ({ stat, data: r?.data ?? null,
                    error: err.response?.data?.detail || 'Projections couldn\'t load. Is the impact API (port 8002) running?' }));
            });
        return () => { live = false; };
    }, [form.stat]);

    const teams = useMemo(() => (data ? [...new Set(data.rows.map((r) => r.team).filter(Boolean))].sort() : []), [data]);
    const shown = useMemo(() => {
        if (!data) return [];
        const q = form.q.trim().toLowerCase();
        return data.rows
            .map((r, i) => ({ ...r, rank: i + 1 }))
            .filter((r) => (!form.team || r.team === form.team) && (!q || r.player_name.toLowerCase().includes(q)));
    }, [data, form.team, form.q]);

    if (!data) {
        return error ? <section className="dashboard-card"><p className="error-message">{error}</p></section> : <Loader />;
    }

    const info = data.stat_info;
    const fmt = info.format;
    const bt = data.backtest.all;
    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const groups = [...new Set(data.catalogue.map((s) => s.group))];
    const visible = form.all ? shown : shown.slice(0, SHOW);
    const vsLast = bt ? (bt.mae_last - bt.mae) / bt.mae_last : null;
    const lowCount = data.rows.filter((r) => r.low_weight).length;

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                Next-season projections
                <InfoTooltip label="How the projections are made" title="Under the hood">{data.method}</InfoTooltip>
                <SourceBadge source={data._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="projections" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                What a simple, fully-stated baseline expects from every {seasonLabel(data.latest_season)} player in{' '}
                {seasonLabel(data.season)}: his last three seasons, pulled toward the league average by how noisy the stat
                is, moved along the aging curve. Backtested on every season since {seasonLabel(info.backtest?.season ? info.backtest.season : 2001)}.
            </p>

            <div className="lb-controls pj-controls">
                <label>
                    <span>Stat</span>
                    <select className="input-field" value={form.stat} onChange={(e) => set({ stat: e.target.value })}>
                        {groups.map((g) => (
                            <optgroup key={g} label={g}>
                                {data.catalogue.filter((s) => s.group === g).map((s) => (
                                    <option key={s.key} value={s.key}>{s.label}</option>
                                ))}
                            </optgroup>
                        ))}
                    </select>
                </label>
                <label>
                    <span>Team</span>
                    <select className="input-field" value={form.team ?? ''} onChange={(e) => set({ team: e.target.value || null })}>
                        <option value="">All teams</option>
                        {teams.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                </label>
                <label className="pj-search">
                    <span>Player</span>
                    <input className="input-field" type="search" value={form.q} placeholder="Filter by name"
                        onChange={(e) => set({ q: e.target.value })} />
                </label>
            </div>

            {error && <p className="error-message">{error}</p>}
            <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                {bt && (
                    <p className="rx-verdict pj-verdict">
                        <strong>{info.label}</strong>: over {bt.n.toLocaleString()} player-seasons since{' '}
                        {seasonLabel(data.backtest.by_season[0].season)}, the projection missed by{' '}
                        <strong>{fmtErr(fmt, bt.mae)}{fmt === 'pct' ? ' points' : ''}</strong> on average, against{' '}
                        {fmtErr(fmt, bt.mae_last)} for &ldquo;same as last season&rdquo; ({vsLast >= 0 ? `${Math.round(vsLast * 100)}% better` : `${Math.round(-vsLast * 100)}% worse`})
                        {' '}and {fmtErr(fmt, bt.mae_league)} for the league average. Its 80% range held the actual{' '}
                        {pctTxt(bt.coverage80)} of the time.
                        {info.shrink_source === 'year_to_year' && (
                            <span className="lb-note"> Regression amount from the year-to-year correlation ({info.y2y_r.toFixed(2)}), since
                                what moves this stat between seasons is real change, not in-season noise.</span>
                        )}
                    </p>
                )}

                <h3 className="pj-h3">{seasonLabel(data.season)}: {info.label.toLowerCase()}, {data.players} players</h3>
                <p className="pj-small">
                    Sorted by projection. &ldquo;Own weight&rdquo; is the share of the projection that comes from the player&apos;s
                    own numbers (the rest is the league average); rows under {data.low_weight} are greyed: {lowCount} of{' '}
                    {data.rows.length} here, mostly players with few minutes on file. The range is where 80% of past
                    projections like this one landed.
                </p>
                <TableExport name={`projections ${seasonLabel(data.season)} ${info.label}`} />
                <div className="table-wrapper">
                    <table className="data-table lb-table pj-table">
                        <thead>
                            <tr>
                                <th className="lb-num">#</th>
                                <th>Player</th>
                                <th>Team</th>
                                <th className="lb-num">Age</th>
                                <th className="lb-num">{seasonLabel(data.latest_season)}</th>
                                <th className="lb-num lb-stat">{seasonLabel(data.season)}</th>
                                <th className="lb-num">80% range</th>
                                <th className="lb-num">Change</th>
                                <th className="lb-num">Age step</th>
                                <th className="lb-num">Own weight</th>
                                <th className="lb-num">Seasons</th>
                            </tr>
                        </thead>
                        <tbody>
                            {visible.map((r) => (
                                <tr key={r.player_id} className={r.low_weight ? 'pj-low' : undefined}
                                    title={r.low_weight ? `Own weight ${r.own_weight.toFixed(2)}: mostly the league average` : undefined}>
                                    <td className="lb-num">{r.rank}</td>
                                    <td><PlayerName playerId={r.player_id} name={r.player_name} /></td>
                                    <td>{r.team ?? '—'}</td>
                                    <td className="lb-num">{r.age_next ?? '—'}{!r.age_known && <span className="pj-flag" title="No birth date on file: no age adjustment">*</span>}</td>
                                    <td className="lb-num">
                                        {fmtVal(fmt, r.last_value)}
                                        {r.last_season !== data.latest_season && <span className="pj-range"> ({seasonLabel(r.last_season)})</span>}
                                    </td>
                                    <td className="lb-num lb-stat pj-proj">{fmtVal(fmt, r.projection)}</td>
                                    <td className="lb-num"><span className="pj-range">{fmtVal(fmt, r.lo)} to {fmtVal(fmt, r.hi)}</span></td>
                                    <td className="lb-num">{fmtDiff(fmt, r.change)}</td>
                                    <td className="lb-num">{r.age_known ? fmtDiff(fmt, r.age_adjustment) : '—'}</td>
                                    <td className="lb-num"><span className="pj-weight">{r.own_weight.toFixed(2)}</span></td>
                                    <td className="lb-num">{r.seasons_used}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {shown.length > SHOW && (
                    <p className="pj-more">
                        <button type="button" onClick={() => set({ all: !form.all })}>
                            {form.all ? `Show the top ${SHOW}` : `Show all ${shown.length}`}
                        </button>
                    </p>
                )}
                {shown.length === 0 && <p className="pj-small">No player matches that filter.</p>}
                <p className="pj-small">
                    Change = projection minus the player&apos;s latest season. Age step = the part of the projection that is the
                    aging curve (a minus sign is the typical decline at his age). * no birth date on file, so no age step.
                </p>

                {bt && (
                    <>
                        <h3 className="pj-h3">How it did in the past: {info.label.toLowerCase()}</h3>
                        <p className="pj-small">
                            Every season since {seasonLabel(data.backtest.by_season[0].season)} was projected from the seasons
                            before it, then compared with what happened for players who went on to play 500+ minutes.
                            Bars: the average miss of each method (shorter is better).
                        </p>
                        <ErrorBars bt={bt} format={fmt} label={info.label} />
                        <div className="pj-facts">
                            <div className="pj-fact"><span className="pj-fact-label">80% range held</span>
                                <span className="pj-fact-value">{pctTxt(bt.coverage80)}</span>
                                <span className="pj-fact-note">of actuals, each season judged by the others&apos; errors</span></div>
                            <div className="pj-fact"><span className="pj-fact-label">Bias</span>
                                <span className="pj-fact-value">{fmtDiff(fmt, bt.bias)}</span>
                                <span className="pj-fact-note">actual minus projection, on average</span></div>
                            <div className="pj-fact"><span className="pj-fact-label">Calibration slope</span>
                                <span className="pj-fact-value">{bt.slope?.toFixed(2) ?? '—'}</span>
                                <span className="pj-fact-note">1 = the spread of projections is right; under 1 = should have regressed more</span></div>
                            <div className="pj-fact"><span className="pj-fact-label">Correlation</span>
                                <span className="pj-fact-value">{bt.r?.toFixed(2) ?? '—'}</span>
                                <span className="pj-fact-note">projection vs. actual</span></div>
                        </div>
                        {data.backtest.by_season.length > 3 && <SeasonLines rows={data.backtest.by_season} format={fmt} label={info.label} />}
                    </>
                )}

                <h3 className="pj-h3">Every stat&apos;s backtest</h3>
                <TableExport name="projection backtest" />
                <div className="table-wrapper">
                    <table className="data-table lb-table pj-table">
                        <thead>
                            <tr>
                                <th>Stat</th>
                                <th className="lb-num">Player-seasons</th>
                                <th className="lb-num lb-stat">Projection</th>
                                <th className="lb-num">Same as last season</th>
                                <th className="lb-num">League average</th>
                                <th className="lb-num">vs. last season</th>
                                <th className="lb-num">Bias</th>
                                <th className="lb-num">Slope</th>
                                <th className="lb-num">80% range held</th>
                                <th className="lb-num">Regression sample</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.catalogue.map((s) => {
                                const b = s.backtest;
                                const gain = b ? (b.mae_last - b.mae) / b.mae_last : null;
                                return (
                                    <tr key={s.key} className={s.key === data.stat ? 'pj-on' : undefined}>
                                        <td>
                                            <button type="button" className="ss-pick" aria-pressed={s.key === data.stat}
                                                onClick={() => set({ stat: s.key })}>{s.label}</button>
                                        </td>
                                        <td className="lb-num">{b ? b.n.toLocaleString() : '—'}</td>
                                        <td className="lb-num lb-stat">{b ? fmtErr(s.format, b.mae) : '—'}</td>
                                        <td className="lb-num">{b ? fmtErr(s.format, b.mae_last) : '—'}</td>
                                        <td className="lb-num">{b ? fmtErr(s.format, b.mae_league) : '—'}</td>
                                        <td className={`lb-num ${gain == null ? '' : gain >= 0 ? 'pj-better' : 'pj-worse'}`}>
                                            {gain == null ? '—' : `${gain >= 0 ? '' : '−'}${Math.abs(Math.round(gain * 100))}%`}
                                        </td>
                                        <td className="lb-num">{b ? fmtDiff(s.format, b.bias) : '—'}</td>
                                        <td className="lb-num">{b?.slope != null ? b.slope.toFixed(2) : '—'}</td>
                                        <td className="lb-num">{b ? pctTxt(b.coverage80) : '—'}</td>
                                        <td className="lb-num"><span className="pj-range">{Math.round(s.shrink_m).toLocaleString()} {s.sample_label}{s.shrink_source === 'year_to_year' ? ' (year to year)' : ''}</span></td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
                <p className="pj-small">
                    Errors are mean absolute errors{' '}
                    (percentage stats in percentage points). &ldquo;vs. last season&rdquo;: how much smaller the projection&apos;s
                    miss is than simply repeating the latest season; negative means repeating it would have been better.
                    &ldquo;Regression sample&rdquo;: the sample at which a player&apos;s own numbers and the league average get equal
                    weight (from Stat Stability; year to year where in-season noise isn&apos;t what moves the stat).
                </p>

                <div className="era-method">
                    <h3>What this is, and isn&apos;t</h3>
                    {data.caveats.map((c) => <p key={c}>{c}</p>)}
                    <p><strong>Not projected:</strong></p>
                    <ul className="pj-notlist">
                        {data.not_projected.map((n) => <li key={n.what}><strong>{n.what}</strong>: {n.why}.</li>)}
                    </ul>
                </div>
            </div>
        </section>
    );
}
