import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchLeaderboardOptions, fetchRegression } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const FORMATS = {
    num1: (v) => v.toFixed(1),
    num2: (v) => v.toFixed(2),
    pct: (v) => `${(v * 100).toFixed(1)}%`,
    signed1: (v) => `${v > 0 ? '+' : ''}${v.toFixed(1)}`,
    int: (v) => v.toFixed(0),
};
const fmt = (format, v) => (v == null ? '—' : FORMATS[format](v));
// A change in a stat, in its own units (percentages as percentage points).
const fmtDelta = (format, v) => {
    const sign = v > 0 ? '+' : v < 0 ? '−' : '';
    const a = Math.abs(v);
    if (format === 'pct') return `${sign}${(a * 100).toFixed(2)} pts`;
    return `${sign}${a < 1 ? a.toFixed(3) : a.toFixed(2)}`;
};
const strength = (r) => {
    const a = Math.abs(r);
    if (a < 0.1) return 'essentially no';
    if (a < 0.3) return 'a weak';
    if (a < 0.5) return 'a moderate';
    return 'a strong';
};

const PRESETS = [
    { label: 'Does usage cost efficiency?', x: 'usg_pct', y: 'ts_pct', from: 2016 },
    { label: 'Assists vs. turnovers', x: 'ast', y: 'tov', from: 2016 },
    { label: '3-point volume vs. accuracy', x: 'fg3a', y: 'fg3_pct', from: 2016 },
    { label: 'Does age help BPM?', x: 'age', y: 'bpm', from: 1990 },
];

// ─── Scatter (hand-built SVG, like the rest of the app's charts) ───────────
// The viewBox follows the container's width so labels render at their real
// size on phones instead of being scaled down with the drawing.
const M = { l: 58, r: 16, t: 14, b: 46 };

function niceTicks(min, max, count = 6) {
    const span = max - min || 1;
    const step0 = span / count;
    const mag = 10 ** Math.floor(Math.log10(step0));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) ?? 10 * mag;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) ticks.push(Number(v.toFixed(10)));
    return ticks;
}

function Scatter({ data }) {
    const svgRef = useRef(null);
    const boxRef = useRef(null);
    const [hover, setHover] = useState(null);
    const [W, setW] = useState(720);
    const H = W < 520 ? 340 : 440;

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
    const { points, fit } = data;
    const xf = data.x.format;
    const yf = data.y.format;

    const scale = useMemo(() => {
        const xs = points.map((p) => p.x);
        const ys = points.map((p) => p.y);
        const pad = (a, b) => (b - a || 1) * 0.04;
        const x0 = Math.min(...xs) - pad(Math.min(...xs), Math.max(...xs));
        const x1 = Math.max(...xs) + pad(Math.min(...xs), Math.max(...xs));
        const y0 = Math.min(...ys) - pad(Math.min(...ys), Math.max(...ys));
        const y1 = Math.max(...ys) + pad(Math.min(...ys), Math.max(...ys));
        const sx = (v) => M.l + ((v - x0) / (x1 - x0)) * (W - M.l - M.r);
        const sy = (v) => H - M.b - ((v - y0) / (y1 - y0)) * (H - M.t - M.b);
        return { x0, x1, y0, y1, sx, sy };
    }, [points, W, H]);
    const { x0, x1, y0, y1, sx, sy } = scale;

    // Fitted line clipped to the plot box.
    const lineY = (v) => fit.intercept_raw + fit.slope * v;
    let la = x0;
    let lb = x1;
    if (fit.slope !== 0) {
        const xa = (y0 - fit.intercept_raw) / fit.slope;
        const xb = (y1 - fit.intercept_raw) / fit.slope;
        la = Math.max(x0, Math.min(xa, xb));
        lb = Math.min(x1, Math.max(xa, xb));
    }

    const onMove = (e) => {
        const svg = svgRef.current;
        if (!svg) return;
        const pt = svg.createSVGPoint();
        pt.x = e.clientX;
        pt.y = e.clientY;
        const p = pt.matrixTransform(svg.getScreenCTM().inverse());
        let best = null;
        let bestD = 14 * 14;
        for (const q of points) {
            const d = (sx(q.x) - p.x) ** 2 + (sy(q.y) - p.y) ** 2;
            if (d < bestD) { bestD = d; best = q; }
        }
        setHover(best);
    };

    const summary = `${data.y.label} against ${data.x.label}, ${points.length.toLocaleString()} player-seasons drawn; r = ${fit.r.toFixed(2)}.`;

    return (
        <div className="rx-chart" ref={boxRef}>
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary}
                onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
                {niceTicks(y0, y1).map((t) => (
                    <g key={`y${t}`}>
                        <line className="rx-grid" x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                        <text className="rx-tick" x={M.l - 8} y={sy(t)} textAnchor="end" dominantBaseline="middle">{fmt(yf, t)}</text>
                    </g>
                ))}
                {niceTicks(x0, x1, W < 520 ? 4 : 6).map((t) => (
                    <g key={`x${t}`}>
                        <line className="rx-grid" y1={M.t} y2={H - M.b} x1={sx(t)} x2={sx(t)} />
                        <text className="rx-tick" x={sx(t)} y={H - M.b + 16} textAnchor="middle">{fmt(xf, t)}</text>
                    </g>
                ))}
                <text className="rx-axis" x={(M.l + W - M.r) / 2} y={H - 8} textAnchor="middle">{data.x.label}</text>
                <text className="rx-axis" transform={`translate(14 ${(M.t + H - M.b) / 2}) rotate(-90)`} textAnchor="middle">{data.y.label}</text>
                <g className="rx-points">
                    {points.map((q) => (
                        <circle key={`${q.player_id}-${q.season}`} cx={sx(q.x)} cy={sy(q.y)} r={2.4} />
                    ))}
                </g>
                {la < lb && <line className="rx-fit" x1={sx(la)} y1={sy(lineY(la))} x2={sx(lb)} y2={sy(lineY(lb))} />}
                {hover && <circle className="rx-hover" cx={sx(hover.x)} cy={sy(hover.y)} r={5} />}
            </svg>
            {hover && (
                <div
                    className="rx-tooltip"
                    style={{
                        left: `${(sx(hover.x) / W) * 100}%`,
                        top: `${(sy(hover.y) / H) * 100}%`,
                    }}
                >
                    <strong>{hover.player_name}</strong> {seasonLabel(hover.season)} {hover.team}
                    <br />
                    {data.x.label}: {fmt(xf, hover.x)} · {data.y.label}: {fmt(yf, hover.y)}
                </div>
            )}
        </div>
    );
}

function OutlierTable({ title, rows, data }) {
    return (
        <div className="rx-outliers">
            <h3 className="section-heading">{title}</h3>
            <TableExport name={`${data.y.label} vs ${data.x.label} ${title}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            <th>Player</th>
                            <th>Season</th>
                            <th className="lb-num">{data.x.label}</th>
                            <th className="lb-num">{data.y.label}</th>
                            <th className="lb-num">vs. line</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={`${r.player_id}-${r.season}`}>
                                <td><PlayerName playerId={r.player_id} name={r.player_name} /></td>
                                <td>{seasonLabel(r.season)}</td>
                                <td className="lb-num">{fmt(data.x.format, r.x)}</td>
                                <td className="lb-num">{fmt(data.y.format, r.y)}</td>
                                <td className="lb-num">{fmtDelta(data.y.format, r.residual)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

// The form from a shared link (utils/useUrlState.js), with defaults for
// anything missing or invalid.
function formFromParams(p, o) {
    const keys = o.stats.map((s) => s.key);
    const season = (key, fallback) => parseParam.int(p, key, { min: o.seasons.from, max: o.seasons.to }) ?? fallback;
    return {
        x: parseParam.oneOf(p, 'x', keys) ?? 'usg_pct',
        y: parseParam.oneOf(p, 'y', keys) ?? 'ts_pct',
        from: season('from', 2016),
        to: season('to', o.seasons.to),
        minGp: parseParam.int(p, 'gp', { min: 0, max: 82 }) ?? 30,
        minMpg: parseParam.num(p, 'mpg', { min: 0, max: 48 }) ?? 20,
        within: p.get('within') !== '0',
    };
}

export default function RegressionExplorer() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        fetchLeaderboardOptions()
            .then((o) => {
                setOptions(o);
                setForm(formFromParams(params, o));
            })
            .catch(() => setOptionsError('The stat list couldn\'t load. Is the impact API (port 8002) running?'));
    }, [params]);

    useUrlSync(form && {
        x: form.x, y: form.y, from: form.from, to: form.to,
        gp: form.minGp || 0, mpg: form.minMpg || 0, within: form.within ? null : 0,
    });

    useEffect(() => {
        if (!form) return undefined;
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                setData(await fetchRegression({
                    x: form.x, y: form.y, season_from: form.from, season_to: form.to,
                    min_gp: form.minGp || 0, min_mpg: form.minMpg || 0, within_season: form.within,
                }));
            } catch (err) {
                setData(null);
                setError(err.response?.data?.detail || 'Failed to run the regression.');
            } finally {
                setLoading(false);
            }
        }, 300);
        return () => clearTimeout(timer);
    }, [form]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const groups = [...new Set(options.stats.map((s) => s.group))];
    const seasons = [];
    for (let s = options.seasons.to; s >= options.seasons.from; s -= 1) seasons.push(s);
    const statSelect = (value, onChange, label) => (
        <select className="input-field" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)}>
            {groups.map((g) => (
                <optgroup key={g} label={g}>
                    {options.stats.filter((s) => s.group === g).map((s) => (
                        <option key={s.key} value={s.key}>{s.label}</option>
                    ))}
                </optgroup>
            ))}
        </select>
    );
    const f = data?.fit;
    const range = data && (data.filters.season_from === data.filters.season_to
        ? seasonLabel(data.filters.season_to)
        : `${seasonLabel(data.filters.season_from)} to ${seasonLabel(data.filters.season_to)}`);
    const significant = f && (f.ci_low > 0 || f.ci_high < 0);

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                How do two stats move together?
                <InfoTooltip label="How the Regression Explorer works" title="Under the hood">
                    {data?.method ?? 'Least squares of one stat on another across player-seasons, with standard errors clustered by player.'}
                </InfoTooltip>
                <SourceBadge source={data?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="regression" />
            </h2>

            <div className="lb-presets" aria-label="Presets">
                {PRESETS.map((p) => (
                    <button key={p.label} type="button"
                        onClick={() => setForm((fm) => ({ ...fm, x: p.x, y: p.y, from: p.from, to: options.seasons.to }))}>
                        {p.label}
                    </button>
                ))}
            </div>

            <div className="lb-controls">
                <label><span>X (across)</span>{statSelect(form.x, (v) => set({ x: v }), 'X stat')}</label>
                <label><span>Y (up)</span>{statSelect(form.y, (v) => set({ y: v }), 'Y stat')}</label>
                <label>
                    <span>From</span>
                    <select className="input-field" value={form.from} onChange={(e) => set({ from: Number(e.target.value) })}>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>To</span>
                    <select className="input-field" value={form.to} onChange={(e) => set({ to: Number(e.target.value) })}>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>Min. games</span>
                    <input className="input-field" type="number" min={0} max={82} value={form.minGp}
                        onChange={(e) => set({ minGp: e.target.value === '' ? '' : Number(e.target.value) })} />
                </label>
                <label>
                    <span>Min. minutes a game</span>
                    <input className="input-field" type="number" min={0} max={48} value={form.minMpg}
                        onChange={(e) => set({ minMpg: e.target.value === '' ? '' : Number(e.target.value) })} />
                </label>
            </div>
            <div className="sim-filters">
                <label>
                    <input type="checkbox" checked={form.within} onChange={(e) => set({ within: e.target.checked })} />
                    Compare within each season (removes league-wide trends)
                </label>
                <button type="button" className="cb-add" onClick={() => set({ x: form.y, y: form.x })}>Swap X and Y</button>
            </div>

            {error && <p className="error-message">{error}</p>}
            {loading && !data && <Loader />}
            {data && f && (
                <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                    <p className="rx-verdict">
                        Across {f.n.toLocaleString()} player-seasons ({f.n_players.toLocaleString()} players, {range}),
                        {' '}{data.x.label} and {data.y.label} have <strong>{strength(f.r)} relationship</strong>
                        {' '}(r = {f.r.toFixed(2)}, R² = {f.r2.toFixed(3)}).
                        {' '}One standard deviation more {data.x.label}
                        {' '}({fmtDelta(data.x.format, f.sd_x).replace(/^\+/, '')}) goes with{' '}
                        <strong>{fmtDelta(data.y.format, f.per_sd_x)}</strong> {data.y.label}
                        {' '}(95% interval {fmtDelta(data.y.format, f.ci_low * f.sd_x)} to{' '}
                        {fmtDelta(data.y.format, f.ci_high * f.sd_x)}
                        {significant ? '' : ', which includes zero'}).
                    </p>
                    <p className="page-subtitle lb-summary">
                        {data.filters.within_season ? 'Within-season comparison. ' : 'Raw values, all seasons pooled. '}
                        Errors clustered by player. {data.filters.min_gp}+ games, {data.filters.min_mpg}+ minutes.
                        {data.points_sampled && ` The chart shows a fixed sample of ${data.points.length.toLocaleString()} points; the fit uses all ${f.n.toLocaleString()}.`}
                        {data.notes.map((n) => <span key={n} className="lb-note"> {n}.</span>)}
                        {' '}An association, not a cause.
                    </p>
                    <Scatter data={data} />
                    <div className="rx-outlier-grid">
                        <OutlierTable title="Furthest above the line" rows={data.above_line} data={data} />
                        <OutlierTable title="Furthest below the line" rows={data.below_line} data={data} />
                    </div>
                </div>
            )}
        </section>
    );
}
