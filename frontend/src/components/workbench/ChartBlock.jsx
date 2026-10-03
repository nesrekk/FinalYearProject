import React, { Suspense, lazy, useEffect, useLayoutEffect, useRef, useState } from 'react';
import ChartExport from '../common/ChartExport';
import Icon from '../common/Icon';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import { runWorkbenchAging, runWorkbenchContext, runWorkbenchQuery, runWorkbenchTrend, workbenchError } from '../../services/api';
import { LIMITS } from '../../utils/workbenchStore';
import { useAutosave } from '../../utils/useAutosave';
import { entityWord, formatValue, intervalText, seasonLabel, setFits } from './workbenchShared';
import { rowUnit } from './tableSpec';
import { BIN_CHOICES, CHART_TYPES, MAX_AGING_PLAYERS, buildChart, categories, chartDefaults, isGameData, unitWord } from './chartSpec';
import { MethodLinks } from './TableBlock';

// Plot (and d3 under it) loads only when a board has a chart on it.
const PlotChart = lazy(() => import('./PlotChart'));

// ── Settings ───────────────────────────────────────────────────────────

function StatSelect({ ds, value, onChange, label, none }) {
    const groups = [];
    for (const c of ds.columns) {
        let g = groups.find((x) => x.name === c.group);
        if (!g) groups.push(g = { name: c.group, cols: [] });
        g.cols.push(c);
    }
    return (
        <label className="wb-field">
            <span>{label}</span>
            <select className="wb-select" value={value || ''} onChange={(e) => onChange(e.target.value || null)}>
                {none ? <option value="">{none}</option> : !value && <option value="">Choose a stat…</option>}
                {groups.map((g) => (
                    <optgroup key={g.name} label={g.name}>
                        {g.cols.map((c) => (
                            <option key={c.key} value={c.key} disabled={c.status !== 'verified'} title={c.status !== 'verified' ? `Not offered: ${c.reason}` : c.note || ''}>
                                {c.label}{c.status !== 'verified' ? ' (not offered)' : c.first_season > ds.seasons.from ? ` · from ${seasonLabel(c.first_season)}` : ''}
                            </option>
                        ))}
                    </optgroup>
                ))}
            </select>
        </label>
    );
}

function TitleInput({ value, onSave }) {
    const [draft, setDraft, flush] = useAutosave(value, onSave);
    return <input className="wb-input" value={draft} maxLength={LIMITS.title} placeholder="(automatic)" onChange={(e) => setDraft(e.target.value)} onBlur={flush} />;
}

function ChartSettings({ settings: s, title, catalogue, ds, sets, onChange, onTitle }) {
    const seasons = [];
    for (let v = ds.seasons.to; v >= ds.seasons.from; v -= 1) seasons.push(v);
    const usable = sets.filter((x) => setFits(x, ds));
    const boundSet = sets.find((x) => x.id === s.setId);
    const aging = catalogue.aging;
    const cats = categories(ds);
    const type = s.chart;
    const verifiedKey = (d, k) => d.columns.some((c) => c.key === k && c.status === 'verified');

    const changeType = (chart) => {
        const d = chartDefaults(ds, chart);
        const patch = { chart };
        if (chart === 'scatter') {
            if (!verifiedKey(ds, s.x) || s.x === s.y) patch.x = d.x === s.y ? d.y : d.x;
            if (!verifiedKey(ds, s.y)) patch.y = d.y;
        } else if (!verifiedKey(ds, s.y)) patch.y = d.y;
        if (chart === 'heatmap' && !cats.some((c) => c.key === s.split)) patch.split = 'season';
        if (chart === 'bar' && s.split === 'season' && s.chart === 'heatmap') patch.split = null;
        onChange(patch);
    };
    const changeDataset = (key) => {
        const next = catalogue.datasets.find((d) => d.key === key);
        const d = chartDefaults(next, type);
        const bound = sets.find((x) => x.id === s.setId);
        const keep = (k) => (verifiedKey(next, k) ? k : null);
        onChange({
            dataset: key,
            x: keep(s.x) || d.x || null,
            y: keep(s.y) || d.y || null,
            size: keep(s.size),
            color: ['member', 'none'].includes(s.color) ? s.color : keep(s.color) || 'member',
            groupBy: next.group_by.includes(s.groupBy) ? s.groupBy : 'none',
            facet: s.facet === 'member' || categories(next).some((c) => c.key === s.facet) ? s.facet : 'none',
            split: categories(next).some((c) => c.key === s.split) ? s.split : (type === 'heatmap' ? 'season' : null),
            lineX: (s.lineX === 'date' && isGameData(next)) || (s.lineX === 'age' && aging?.dataset === next.key) ? s.lineX : 'season',
            minGames: isGameData(next) === isGameData(ds) ? s.minGames : null,
            minPoss: next.poss_floor ?? null,
            per: next.per_modes.some((p) => p.key === s.per) ? s.per : 'game',
            setId: bound && setFits(bound, next) ? bound.id : null,
            seasonFrom: null,
            seasonTo: null,
        });
    };
    const contextLabel = {
        scatter: `Every ${entityWord(ds.entity, false)} in grey behind the set`,
        line: `Grey band: where every ${entityWord(ds.entity, false)} sits each season`,
        bar: `Grey band: every ${entityWord(ds.entity, false)}’s middle half and median`,
        histogram: `Grey bars: every ${entityWord(ds.entity, false)}`,
        box: `A grey box for every ${entityWord(ds.entity, false)}`,
        heatmap: `A row for every ${entityWord(ds.entity, false)} (median)`,
    }[type];
    return (
        <div className="wb-settings wb-chart-settings">
            <div className="wb-field wb-field--wide">
                <span id="wb-chart-type">Chart</span>
                <div className="wb-seg" role="group" aria-labelledby="wb-chart-type">
                    {CHART_TYPES.map((c) => (
                        <button key={c.key} type="button" className={`wb-seg-btn${type === c.key ? ' wb-seg-btn--on' : ''}`} aria-pressed={type === c.key} onClick={() => changeType(c.key)}>
                            <Icon name={c.icon} size={15} /> {c.label}
                        </button>
                    ))}
                </div>
            </div>
            <label className="wb-field">
                <span>Title</span>
                <TitleInput value={title} onSave={onTitle} />
            </label>
            <label className="wb-field">
                <span>Data</span>
                <select className="wb-select" value={ds.key} onChange={(e) => changeDataset(e.target.value)}>
                    {catalogue.datasets.map((d) => (
                        <option key={d.key} value={d.key}>{d.label} ({seasonLabel(d.seasons.from)} to {seasonLabel(d.seasons.to)})</option>
                    ))}
                </select>
            </label>
            <label className="wb-field">
                <span>Set</span>
                <select className="wb-select" value={s.setId || ''} onChange={(e) => onChange({ setId: e.target.value || null, seasonFrom: null, seasonTo: null })}>
                    <option value="">{CHART_TYPES.find((c) => c.key === type)?.needsSet ? 'Choose a set…' : `None (all ${entityWord(ds.entity)})`}</option>
                    {usable.map((x) => <option key={x.id} value={x.id}>{x.name} ({x.members.length}){x.kind !== ds.entity ? ': units they’re in' : ''}</option>)}
                </select>
            </label>
            {boundSet && boundSet.kind !== ds.entity && (
                <label className="wb-field">
                    <span>Units with</span>
                    <select className="wb-select" value={s.playersMatch} onChange={(e) => onChange({ playersMatch: e.target.value })}>
                        <option value="any">any of {boundSet.name}</option>
                        <option value="all">all of {boundSet.name} together</option>
                    </select>
                </label>
            )}
            <div className="wb-field">
                <span id={`chart-seasons-${ds.key}`}>Seasons</span>
                <div className="wb-row" role="group" aria-labelledby={`chart-seasons-${ds.key}`}>
                    <select className="wb-select" aria-label="First season" value={s.seasonFrom ?? ''} onChange={(e) => onChange({ seasonFrom: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">{s.setId ? 'last 5 seasons' : 'latest season'}</option>
                        {seasons.map((v) => <option key={v} value={v}>{seasonLabel(v)}</option>)}
                    </select>
                    <span className="wb-meta">to</span>
                    <select className="wb-select" aria-label="Last season" value={s.seasonTo ?? ''} onChange={(e) => onChange({ seasonTo: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">latest</option>
                        {seasons.map((v) => <option key={v} value={v}>{seasonLabel(v)}</option>)}
                    </select>
                </div>
            </div>
            {type === 'scatter' && (
                <>
                    <StatSelect ds={ds} label="Across (x)" value={s.x} onChange={(x) => onChange({ x })} />
                    <StatSelect ds={ds} label="Up (y)" value={s.y} onChange={(y) => onChange({ y })} />
                    <label className="wb-field">
                        <span>Each dot is</span>
                        <select className="wb-select" value={ds.group_by.includes(s.groupBy) && s.groupBy !== 'all' ? s.groupBy : 'none'} onChange={(e) => onChange({ groupBy: e.target.value })}>
                            <option value="none">One {rowUnit(ds)}</option>
                            <option value="entity">One {entityWord(ds.entity, false)}, seasons combined</option>
                            {cats.map((c) => <option key={c.key} value={c.key}>{c.label}, combined</option>)}
                        </select>
                    </label>
                    <StatSelect ds={ds} label="Dot size" value={s.size} none="Same size" onChange={(size) => onChange({ size })} />
                    <label className="wb-field">
                        <span>Colour</span>
                        <select className="wb-select" value={s.color} onChange={(e) => onChange({ color: e.target.value })}>
                            <option value="member">The set’s colours</option>
                            <option value="none">One colour</option>
                            {ds.columns.filter((c) => c.status === 'verified').map((c) => <option key={c.key} value={c.key}>By {c.label}</option>)}
                        </select>
                    </label>
                    <label className="wb-field">
                        <span>Small multiples</span>
                        <select className="wb-select" value={s.facet} onChange={(e) => onChange({ facet: e.target.value })}>
                            <option value="none">One panel</option>
                            {s.setId && <option value="member">A panel per {entityWord(ds.entity, false)}</option>}
                            {cats.map((c) => <option key={c.key} value={c.key}>A panel per {c.label.toLowerCase()}</option>)}
                        </select>
                    </label>
                </>
            )}
            {type !== 'scatter' && (
                <StatSelect ds={ds} label={type === 'heatmap' ? 'Colour by' : 'Stat'} value={s.y} onChange={(y) => onChange({ y })} />
            )}
            {type === 'line' && (
                <label className="wb-field">
                    <span>Across</span>
                    <select className="wb-select" value={s.lineX} onChange={(e) => onChange({ lineX: e.target.value })}>
                        <option value="season">Seasons</option>
                        {isGameData(ds) && <option value="date">Every game (by date)</option>}
                        {aging?.dataset === ds.key && <option value="age">Ages, against the typical aging curve</option>}
                    </select>
                </label>
            )}
            {type === 'line' && s.lineX === 'age' && aging?.dataset === ds.key && (
                <label className="wb-field">
                    <span>Curve from</span>
                    <select className="wb-select" value={s.agingEra} onChange={(e) => onChange({ agingEra: e.target.value })}>
                        {aging.eras.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}
                    </select>
                </label>
            )}
            {(type === 'bar' || type === 'heatmap') && (
                <label className="wb-field">
                    <span>{type === 'bar' ? 'Split by' : 'Across'}</span>
                    <select className="wb-select" value={s.split || (type === 'heatmap' ? 'season' : '')} onChange={(e) => onChange({ split: e.target.value || null })}>
                        {type === 'bar' && <option value="">No split (seasons combined)</option>}
                        {cats.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                    </select>
                </label>
            )}
            {type === 'histogram' && (
                <label className="wb-field">
                    <span>Bars</span>
                    <select className="wb-select" value={s.bins} onChange={(e) => onChange({ bins: Number(e.target.value) })}>
                        {BIN_CHOICES.map((b) => <option key={b} value={b}>{b ? `about ${b}` : 'automatic (about 20)'}</option>)}
                    </select>
                </label>
            )}
            {type === 'box' && (
                <label className="wb-field">
                    <span>Show</span>
                    <select className="wb-select" value={s.style} onChange={(e) => onChange({ style: e.target.value })}>
                        <option value="box">Boxes</option>
                        <option value="dots">Every row as a dot</option>
                    </select>
                </label>
            )}
            {ds.per_modes.length > 1 && (
                <label className="wb-field">
                    <span>Counting stats</span>
                    <select className="wb-select" value={s.per} onChange={(e) => onChange({ per: e.target.value })}>
                        {ds.per_modes.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                    </select>
                </label>
            )}
            <label className="wb-field">
                <span>At least</span>
                <span className="wb-row">
                    <input className="wb-input wb-input--num" type="number" min="0" step="1" value={s.minGames ?? ''} placeholder="0"
                        onChange={(e) => onChange({ minGames: e.target.value === '' ? null : Math.max(0, Math.round(Number(e.target.value))) || null })} />
                    <span className="wb-meta">games a {type === 'scatter' ? 'dot' : 'row'}{isGameData(ds) ? ' (where rows combine games)' : ''}</span>
                </span>
            </label>
            {ds.poss_floor != null && (
                <label className="wb-field">
                    <span>And at least</span>
                    <span className="wb-row">
                        <input className="wb-input wb-input--num" type="number" min="0" step="10" value={s.minPoss ?? ''} placeholder="0"
                            onChange={(e) => onChange({ minPoss: e.target.value === '' ? null : Math.max(0, Math.round(Number(e.target.value))) || null })} />
                        <span className="wb-meta">possessions (suggested {ds.poss_floor})</span>
                    </span>
                </label>
            )}
            {(s.setId || type !== 'scatter') && (
                <label className="wb-check">
                    <input type="checkbox" checked={s.context} onChange={(e) => onChange({ context: e.target.checked })} disabled={!s.setId && type === 'histogram'} />
                    {' '}{contextLabel}
                </label>
            )}
            {['scatter', 'line', 'bar'].includes(type) && [s.x, s.y].some((k) => ds.columns.find((c) => c.key === k)?.interval) && !(type === 'line' && s.lineX === 'age') && (
                <label className="wb-check">
                    <input type="checkbox" checked={s.ci} onChange={(e) => onChange({ ci: e.target.checked })} />
                    {' '}Draw the interval the model gives ({[...new Set([s.x, s.y].map((k) => ds.columns.find((c) => c.key === k)?.interval).filter(Boolean))].join('; ')}; one {ds.entity}-season at a time)
                </label>
            )}
            {type === 'scatter' && (
                <label className="wb-check">
                    <input type="checkbox" checked={s.trend} disabled={s.facet !== 'none'} onChange={(e) => onChange({ trend: e.target.checked })} />
                    {' '}Straight-line fit with r and its 95% interval{s.facet !== 'none' ? ' (one panel only)' : ''}
                </label>
            )}
        </div>
    );
}

// ── Data behind the chart (a table, so it can be read without the picture
// and exported as CSV/JSON) ─────────────────────────────────────────────

function DataTable({ main, ctx, keys, name, ds }) {
    if (!main?.rows?.length && !ctx?.groups) return null;
    const cols = (main?.columns || []).filter((c) => keys.includes(c.key));
    const fields = (main?.fields || []).filter((f) => ['season', 'date', 'team', 'opponent', 'home', 'win'].includes(f));
    const label = { season: 'Season', date: 'Date', team: 'Team', opponent: 'Opp', home: 'Where', win: 'Result' };
    const show = (f, v) => (v == null ? '—' : f === 'season' ? seasonLabel(v) : f === 'home' ? (v ? 'Home' : 'Away') : f === 'win' ? (v ? 'W' : 'L') : String(v));
    const nameOf = (r) => (r.player_names ? r.player_names.join(', ') : r.player_a_name ? `${r.player_a_name} + ${r.player_b_name}` : '')
        || r.player_name || r.team_name || r.franchise || '';
    const groups = ctx?.groups?.length ? ctx.groups : ctx?.overall ? [{ key: null, ...ctx.overall }] : [];
    const cf = ctx?.column;
    return (
        <details className="wb-notes wb-chart-data">
            <summary>Data behind this chart ({(main?.rows?.length || 0).toLocaleString()} rows{groups.length ? ` + the grey population’s summary` : ''})</summary>
            {main?.rows?.length > 0 && (
                <>
                    <TableExport name={name} />
                    <div className="wb-table-wrap">
                        <table className="wb-table">
                            <thead>
                                <tr>
                                    {main.rows.some(nameOf) && <th scope="col">{ds.entity === 'team' ? 'Team' : 'Player'}</th>}
                                    {fields.map((f) => <th key={f} scope="col">{label[f]}</th>)}
                                    <th scope="col" className="wb-num">G</th>
                                    {cols.map((c) => <th key={c.key} scope="col" className="wb-num">{c.short || c.label}</th>)}
                                    {cols.filter((c) => c.interval).map((c) => <th key={`ci-${c.key}`} scope="col" className="wb-num">{c.short || c.label}: {c.interval}</th>)}
                                    {cols.map((c) => <th key={`n-${c.key}`} scope="col" className="wb-num">n ({c.n_unit})</th>)}
                                </tr>
                            </thead>
                            <tbody>
                                {main.rows.map((r, i) => (
                                    <tr key={i}>
                                        {main.rows.some(nameOf) && <td>{nameOf(r)}</td>}
                                        {fields.map((f) => <td key={f}>{show(f, r[f])}</td>)}
                                        <td className="wb-num">{r.n_games == null ? '—' : Math.round(r.n_games).toLocaleString()}</td>
                                        {cols.map((c) => <td key={c.key} className={`wb-num${r.reliability?.[c.key]?.noisy ? ' wb-noisy' : ''}`}>{formatValue(c.format, r[c.key])}</td>)}
                                        {cols.filter((c) => c.interval).map((c) => <td key={`ci-${c.key}`} className="wb-num">{intervalText(c.format, r.ci?.[c.key]) || '—'}</td>)}
                                        {cols.map((c) => <td key={`n-${c.key}`} className="wb-num">{r.n?.[c.key] == null ? '—' : Math.round(r.n[c.key]).toLocaleString()}</td>)}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
            {groups.length > 0 && cf && (
                <>
                    <p className="wb-meta">Grey population: {cf.label}{ctx.by ? ` by ${ctx.by === 'win' ? 'result' : ctx.by}` : ''}, every {ds.entity} matching the same filters ({ctx.n_rows.toLocaleString()} rows{ctx.n_missing ? `, ${ctx.n_missing.toLocaleString()} without a value` : ''}).</p>
                    <TableExport name={`${name} population`} />
                    <div className="wb-table-wrap">
                        <table className="wb-table">
                            <thead>
                                <tr>
                                    {ctx.by && <th scope="col">{label[ctx.by] || ctx.by}</th>}
                                    <th scope="col" className="wb-num">n</th>
                                    {['p10', 'p25', 'p50', 'p75', 'p90'].map((p) => <th key={p} scope="col" className="wb-num">{p === 'p50' ? 'Median' : `${p.slice(1)}th`}</th>)}
                                </tr>
                            </thead>
                            <tbody>
                                {groups.map((g) => (
                                    <tr key={String(g.key)}>
                                        {ctx.by && <td>{show(ctx.by, g.key)}</td>}
                                        <td className="wb-num">{g.n.toLocaleString()}</td>
                                        {['p10', 'p25', 'p50', 'p75', 'p90'].map((p) => <td key={p} className="wb-num">{formatValue(cf.format, g[p])}</td>)}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </details>
    );
}

// The aging chart's numbers: every season of each player, and the curve.
function AgingTable({ data, name }) {
    const f = (v) => formatValue(data.summary.kind === 'pct' || data.summary.kind === 'rate' ? 'pct' : 'signed1', v);
    const signed = (v) => (data.summary.kind === 'pct' || data.summary.kind === 'rate' ? `${v > 0 ? '+' : ''}${f(v)}` : f(v));
    return (
        <details className="wb-notes wb-chart-data">
            <summary>Data behind this chart ({data.players.reduce((a, p) => a + p.seasons.length, 0).toLocaleString()} seasons + the curve)</summary>
            <TableExport name={name} />
            <div className="wb-table-wrap">
                <table className="wb-table">
                    <thead>
                        <tr>
                            <th scope="col">Player</th><th scope="col">Season</th><th scope="col" className="wb-num">Age</th>
                            <th scope="col" className="wb-num">{data.summary.label}</th><th scope="col" className="wb-num">League</th>
                            <th scope="col" className="wb-num">vs league</th><th scope="col" className="wb-num">Minutes</th><th scope="col">On the curve?</th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.players.flatMap((p) => p.seasons.map((x) => (
                            <tr key={`${p.player_id}-${x.season}-${x.team}`}>
                                <td>{p.player_name}</td><td>{seasonLabel(x.season)}</td><td className="wb-num">{x.age ?? '—'}</td>
                                <td className="wb-num">{formatValue(data.summary.kind === 'pct' || data.summary.kind === 'rate' ? 'pct' : 'num1', x.value)}</td>
                                <td className="wb-num">{formatValue(data.summary.kind === 'pct' || data.summary.kind === 'rate' ? 'pct' : 'num1', x.league_average)}</td>
                                <td className="wb-num">{x.vs_league == null ? '—' : signed(x.vs_league)}</td>
                                <td className="wb-num">{x.minutes == null ? '—' : x.minutes.toLocaleString()}</td>
                                <td>{x.qualified ? 'yes' : `no (${x.note})`}</td>
                            </tr>
                        )))}
                    </tbody>
                </table>
            </div>
            <TableExport name={`${name} curve`} />
            <div className="wb-table-wrap">
                <table className="wb-table">
                    <thead><tr><th scope="col" className="wb-num">Age</th><th scope="col" className="wb-num">Typical level</th><th scope="col" className="wb-num">95% range</th><th scope="col" className="wb-num">Pairs of seasons</th></tr></thead>
                    <tbody>
                        {data.curve.map((c) => (
                            <tr key={c.age}><td className="wb-num">{c.age}</td><td className="wb-num">{signed(c.level)}</td><td className="wb-num">{c.lo == null ? '—' : `${signed(c.lo)} to ${signed(c.hi)}`}</td><td className="wb-num">{c.pairs == null ? '—' : c.pairs.toLocaleString()}{c.thin ? ' (thin)' : ''}</td></tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="wb-meta">{data.method}</p>
        </details>
    );
}

// ── The block ──────────────────────────────────────────────────────────

// The chart area's size: measured once on mount and again whenever it changes.
function useSize(ref, deps) {
    const [size, setSize] = useState({ w: 0, h: 0 });
    useLayoutEffect(() => {
        const el = ref.current;
        if (!el) return undefined;
        const update = (width, height) => setSize((s) => (Math.abs(s.w - width) < 1 && Math.abs(s.h - height) < 1 ? s : { w: Math.floor(width), h: Math.floor(height) }));
        const r = el.getBoundingClientRect();
        update(r.width, r.height);
        if (typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(([entry]) => update(entry.contentRect.width, entry.contentRect.height));
        ro.observe(el);
        return () => ro.disconnect();
    }, [ref, deps]);
    return size;
}

export default function ChartBlock({ block, board, catalogue, editing, onSettings, onTitle, label }) {
    const { settings } = block;
    const ds = catalogue.datasets.find((d) => d.key === settings.dataset) || catalogue.datasets[0];
    const set = board.sets.find((x) => x.id === settings.setId) || null;
    const plan = buildChart(settings, ds, set, catalogue.aging);
    const reqKey = plan.main || plan.context || plan.aging
        ? JSON.stringify({ main: plan.main, context: plan.context, kind: plan.contextKind, aging: plan.aging || null }) : '';
    const [result, setResult] = useState({ key: '', main: null, ctx: null, aging: null, error: '', ctxError: '' });
    const [trend, setTrend] = useState({ key: '', data: null, error: '' });
    const areaRef = useRef(null);
    const svgRef = useRef(null);
    const size = useSize(areaRef, `${editing}-${result.key === reqKey}`);

    useEffect(() => {
        if (!reqKey) return undefined;
        let alive = true;
        const { main, context, kind, aging } = JSON.parse(reqKey);
        if (aging) {
            runWorkbenchAging(aging).then(
                (a) => alive && setResult({ key: reqKey, main: null, ctx: null, aging: a, error: '', ctxError: '' }),
                (e) => alive && setResult({ key: reqKey, main: null, ctx: null, aging: null, error: workbenchError(e), ctxError: '' }),
            );
            return () => { alive = false; };
        }
        const ctxCall = !context ? Promise.resolve(null) : kind === 'rows' ? runWorkbenchQuery(context) : runWorkbenchContext(context);
        Promise.all([
            main ? runWorkbenchQuery(main) : Promise.resolve(null),
            ctxCall.then((d) => ({ d }), (e) => ({ e: workbenchError(e) })),
        ]).then(
            ([m, c]) => alive && setResult({ key: reqKey, main: m, ctx: c.d || null, aging: null, error: '', ctxError: c.e || '' }),
            (e) => alive && setResult({ key: reqKey, main: null, ctx: null, aging: null, error: workbenchError(e), ctxError: '' }),
        );
        return () => { alive = false; };
    }, [reqKey]);

    const current = result.key === reqKey ? result : null;
    const ctxTruncated = plan.contextKind === 'rows' && current?.ctx?.truncated;
    // The fit uses exactly the points drawn: the grey population when it is
    // drawn, else the set's points.
    const trendSpec = plan.enc?.trend && current && !current.error
        ? (plan.contextKind === 'rows' && current.ctx && !ctxTruncated ? plan.context : (!current.main?.truncated ? plan.main : null))
        : null;
    const trendKey = trendSpec ? JSON.stringify({ spec: trendSpec, x: plan.enc.x, y: plan.enc.y }) : '';
    useEffect(() => {
        if (!trendKey) return undefined;
        let alive = true;
        runWorkbenchTrend(JSON.parse(trendKey)).then(
            (data) => alive && setTrend({ key: trendKey, data, error: '' }),
            (e) => alive && setTrend({ key: trendKey, data: null, error: workbenchError(e) }),
        );
        return () => { alive = false; };
    }, [trendKey]);
    const trendNow = trend.key === trendKey ? trend : null;

    const entityKey = ds.entity === 'team' ? 'franchise' : 'player_id';
    const members = set ? set.members : [];
    const keys = plan.main?.columns || plan.context?.spec?.columns || [];
    const rowOne = rowUnit(ds);
    const rowGroup = plan.main?.group_by ?? plan.context?.spec?.group_by ?? 'none';
    const ready = current && !current.error && (current.main || current.ctx || current.aging);
    const name = label;
    const notes = [...new Set([...(current?.main?.notes || []), ...(plan.contextKind === 'rows' ? current?.ctx?.notes || [] : [])])]
        .filter((n) => !n.startsWith('Showing '));

    return (
        <div className={`wb-chart-block${editing ? ' wb-chart-block--editing' : ''}`}>
            {editing && (
                <ChartSettings settings={settings} title={block.title} catalogue={catalogue} ds={ds} sets={board.sets} onChange={onSettings} onTitle={onTitle} />
            )}
            {plan.problem && <p className="wb-hint">{plan.problem}</p>}
            {!plan.problem && !current && <p className="wb-meta" role="status">Loading…</p>}
            {current?.error && <p className="wb-error" role="alert">{current.error}</p>}
            {current?.ctxError && <p className="wb-warn">Grey population left out: {current.ctxError}</p>}
            {ctxTruncated && (
                <p className="wb-warn">
                    Grey population left out: {current.ctx.n.matched.toLocaleString()} {unitWord(ds, rowGroup)} match, more than the
                    {' '}{current.ctx.spec.limit.toLocaleString()} a chart draws. Narrow the seasons, {rowGroup === 'none' && isGameData(ds)
                        ? 'or make each dot combine games (Each dot is)' : 'or raise the games floor'} in Settings.
                </p>
            )}
            {current?.main && current.main.rows.length === 0 && (
                <p className="wb-hint">No {unitWord(ds, rowGroup)} match{set ? ` for ${set.name}` : ''}. Widen the seasons{plan.floorApplies ? ' or lower the games floor' : ''} in Settings.</p>
            )}
            {current?.main?.truncated && <p className="wb-warn">Only the first {current.main.rows.length.toLocaleString()} of {current.main.n.matched.toLocaleString()} rows are drawn. Narrow the seasons.</p>}
            {trendNow?.error && <p className="wb-warn">No fit: {trendNow.error}</p>}
            {ready && current.aging && (
                <p className="wb-summary">
                    {current.aging.summary.label} against each season’s league average · curve from {current.aging.summary.era_label.toLowerCase()}
                    {plan.agingCut ? ` · the first ${MAX_AGING_PLAYERS} of ${set.name}` : ''}
                    {current.aging.missing.length ? ` · ${current.aging.missing.length} without seasons on file` : ''}
                    <SourceBadge source={current.aging._source} />
                </p>
            )}
            {ready && !current.aging && (
                <p className="wb-summary">
                    {seasonLabel(plan.seasonFrom)}{plan.seasonTo !== plan.seasonFrom ? ` to ${seasonLabel(plan.seasonTo)}` : ''}
                    {plan.floorApplies ? ` · ${settings.minGames}+ games a ${plan.type === 'scatter' ? 'dot' : 'row'}` : ''}
                    {keys.some((k) => ds.columns.find((c) => c.key === k)?.kind === 'count') ? ` · counting stats ${ds.per_modes.find((p) => p.key === (plan.main || plan.context.spec).per)?.label}` : ''}
                    <SourceBadge source={(current.main || current.ctx)?._source} />
                </p>
            )}
            <div ref={areaRef} className="wb-chart-area">
                {ready && size.w > 80 && (
                    <Suspense fallback={<p className="wb-meta" role="status">Loading the chart library…</p>}>
                        <PlotChart
                            type={plan.type}
                            enc={plan.enc}
                            ds={ds}
                            main={current.main}
                            ctx={ctxTruncated ? null : current.ctx}
                            aging={current.aging}
                            trend={trendNow?.data}
                            members={members.filter((m) => !current.main || current.main.rows.some((r) => r[entityKey] === m.id))}
                            entityKey={entityKey}
                            setName={set?.name}
                            populationWord={entityWord(ds.entity)}
                            populationOne={entityWord(ds.entity, false)}
                            rowOne={rowOne}
                            rowWord={unitWord(ds, rowGroup)}
                            seasonFrom={plan.seasonFrom}
                            seasonTo={plan.seasonTo}
                            width={size.w}
                            height={size.h}
                            svgRef={svgRef}
                            title={name}
                        />
                    </Suspense>
                )}
            </div>
            {ready && (
                <div className="wb-chart-foot">
                    <ChartExport svgRef={svgRef} name={name} />
                    {current.aging ? <AgingTable data={current.aging} name={name} /> : (
                        <DataTable main={current.main} ctx={plan.contextKind === 'summary' ? current.ctx : null} keys={keys} name={name} ds={ds} />
                    )}
                    <MethodLinks columns={current.aging ? [{ method: 'aging' }] : (current.main || current.ctx)?.columns || (current.ctx?.column ? [current.ctx.column] : [])} />
                    {notes.length > 0 && (
                        <details className="wb-notes">
                            <summary>Notes on this data ({notes.length})</summary>
                            <ul>{notes.map((n) => <li key={n}>{n}</li>)}</ul>
                        </details>
                    )}
                </div>
            )}
        </div>
    );
}
