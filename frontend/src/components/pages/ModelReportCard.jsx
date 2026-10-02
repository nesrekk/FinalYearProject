import React, { useEffect, useMemo, useState } from 'react';
import { fetchReportCardOptions, fetchReportCardPair, fetchReportCardTask } from '../../services/api';
import Loader from '../Loader';
import CopyLinkButton from '../common/CopyLinkButton';
import InfoTooltip from '../common/InfoTooltip';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import { IntervalChart } from '../common/CoachingCharts';
import { signed } from '../../utils/format';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/playfinder.css';
import '../../styles/possessions.css';
import '../../styles/reportcard.css';

// Model Report Card (?page=reportcard): every model the platform scores, season
// by season, each season predicted with only the seasons before it (rolling
// origin), and the seasons pooled with a random-effects estimate
// (GET /report-card/*, from scripts/build_report_card.py).
// Inputs in the link: task, metric, v (checkpoint), a, b (the two models compared).

const DEC = { log_loss: 4, brier: 4, game_rmse: 3, poss_rmse: 5, mae: 2, rmse: 2, cover80: 3 };
const fmtP = (p) => (p == null ? '—' : p < 0.001 ? '< 0.001' : p.toFixed(3));
const fmtVal = (metric, v) => {
    if (v == null) return '—';
    if (metric === 'cover80') return `${(v * 100).toFixed(1)}%`;
    return Number(v).toFixed(DEC[metric] ?? 3);
};
const fmtDiff = (metric, v) => {
    if (v == null) return '—';
    if (metric === 'cover80') return `${signed(v * 100, 1)} pp`;
    return signed(v, DEC[metric] ?? 3);
};

function formFromParams(p, o) {
    const tasks = o.tasks.map((t) => t.key);
    const task = parseParam.oneOf(p, 'task', tasks) ?? tasks[0];
    const info = o.tasks.find((t) => t.key === task);
    const models = info.models.map((m) => m.key);
    return {
        task,
        metric: parseParam.oneOf(p, 'metric', info.metrics.map((m) => m.key)),
        variant: parseParam.oneOf(p, 'v', info.variants.map((v) => v.key)),
        a: parseParam.oneOf(p, 'a', models),
        b: parseParam.oneOf(p, 'b', models),
    };
}

function Stat({ k, v, sub, title, tone }) {
    return (
        <div className={`px-stat${tone ? ` rc-stat--${tone}` : ''}`} title={title}>
            <div className="px-stat-k">{k}</div>
            <div className="px-stat-v">{v}</div>
            {sub && <div className="px-stat-sub">{sub}</div>}
        </div>
    );
}

// Better (lower error, or coverage nearer 80%) is good; the sign of a difference says which model is lower.
function betterWord(d, a, b) {
    if (!d) return '';
    if (d.mu == null) return '';
    const clear = d.ci_lo > 0 || d.ci_hi < 0;
    return clear ? `${d.mu < 0 ? a : b} lower, pooled over ${d.k} seasons` : `No pooled difference over ${d.k} seasons`;
}

function Grid({ d, onPick }) {
    const metric = d.metric;
    const rows = [...d.models].sort((x, y) => (x.mean_rank ?? 99) - (y.mean_rank ?? 99));
    const n = d.models.length;
    return (
        <>
            <TableExport name={`report card ${d.task} ${metric} ${d.variant || ''}`.trim()} />
            <div className="table-wrapper">
                <table className="data-table lb-table px-table rc-grid">
                    <thead>
                        <tr>
                            <th>Model</th>
                            {d.seasons.map((s) => <th key={s.season} className="lb-num" title={`${s.n.toLocaleString()} ${d.units_label}`}>{s.label}</th>)}
                            <th className="lb-num" title="Seasons it ranked first">First</th>
                            <th className="lb-num" title="Mean rank over the seasons it was scored">Mean rank</th>
                            <th className="lb-num" title={`Pooled difference from ${d.reference_label} (random effects), 95% interval`}>vs reference</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((m) => (
                            <tr key={m.key} className={m.key === d.reference ? 'rc-ref' : ''}>
                                <td><button type="button" className="rc-link" onClick={() => onPick(m.key)}>{m.label}</button></td>
                                {m.seasons.map((c) => (
                                    <td key={c.season} className={`lb-num rc-cell${c.rank === 1 ? ' rc-first' : ''}`}
                                        style={c.rank ? { '--p': n > 1 ? (n - c.rank) / (n - 1) : 1 } : undefined}
                                        title={c.value == null ? 'Not scored this season' : `Rank ${c.rank} of ${d.seasons.find((s) => s.season === c.season)?.models}; 95% interval ${fmtVal(metric, c.ci_lo)} to ${fmtVal(metric, c.ci_hi)}`}>
                                        {fmtVal(metric, c.value)}
                                    </td>
                                ))}
                                <td className="lb-num">{m.first}/{m.scored}</td>
                                <td className="lb-num">{m.mean_rank == null ? '—' : m.mean_rank.toFixed(1)}</td>
                                <td className="lb-num">
                                    {m.vs_reference ? (
                                        <span className={d.lower_is_better === false ? '' : m.vs_reference.ci_hi < 0 ? 'rc-good' : m.vs_reference.ci_lo > 0 ? 'rc-bad' : ''}
                                            title={`95% interval ${fmtDiff(metric, m.vs_reference.ci_lo)} to ${fmtDiff(metric, m.vs_reference.ci_hi)}; next season: ${fmtDiff(metric, m.vs_reference.pi_lo)} to ${fmtDiff(metric, m.vs_reference.pi_hi)}`}>
                                            {fmtDiff(metric, m.vs_reference.mu)}
                                        </span>
                                    ) : m.key === d.reference ? 'reference' : '—'}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle px-foot">
                Shading: darker = better rank that season ({d.lower_is_better === false ? 'coverage nearer 80%' : 'lower is better'}); the outlined cell ranked first.
                &ldquo;vs reference&rdquo; is the random-effects pooled difference from {d.reference_label} (negative = lower), coloured when its 95% interval excludes zero.
                Hover a cell for its interval; click a model to compare it below.
            </p>
        </>
    );
}

function PairView({ pair, metric, lowerIsBetter }) {
    const p = pair.pooled;
    const items = pair.seasons.map((s) => ({
        key: String(s.season), label: s.label, value: s.diff, lo: s.ci_lo, hi: s.ci_hi,
        tone: s.ci_hi < 0 ? 'good' : s.ci_lo > 0 ? 'bad' : null,
        note: `${pair.a_label} ${fmtVal(metric, s.value_a)} vs ${pair.b_label} ${fmtVal(metric, s.value_b)} · p ${fmtP(s.p_boot)} (bootstrap), ${fmtP(s.p_perm)} (sign-flip)`,
    }));
    if (p) {
        items.push({ key: 'pooled', label: 'Pooled (random effects)', value: p.mu, lo: p.ci_lo, hi: p.ci_hi, highlight: true,
            tone: p.ci_hi < 0 ? 'good' : p.ci_lo > 0 ? 'bad' : null, note: `Hartung-Knapp interval, ${p.k} seasons, p ${fmtP(p.p)}` });
        if (p.pi_lo != null) {
            items.push({ key: 'next', label: 'Next season (prediction)', value: p.mu, lo: p.pi_lo, hi: p.pi_hi,
                note: 'Where a new season\'s difference should fall if seasons keep varying as they have' });
        }
    }
    const coverNote = lowerIsBetter === false ? ' (coverage: the difference in share inside the 80% range, not better or worse by sign)' : '';
    return (
        <>
            {p && (
                <div className="px-stats">
                    <Stat k="Pooled difference" v={fmtDiff(metric, p.mu)} tone={p.ci_hi < 0 ? 'pos' : p.ci_lo > 0 ? 'neg' : null}
                        sub={`95% interval ${fmtDiff(metric, p.ci_lo)} to ${fmtDiff(metric, p.ci_hi)} · p ${fmtP(p.p)}`} />
                    <Stat k="Seasons ahead" v={`${p.a_better} – ${p.b_better}`}
                        sub={`clearly (interval excludes 0): ${p.a_clear} – ${p.b_clear}`} title={`${pair.a_label} – ${pair.b_label}`} />
                    <Stat k="Season-to-season spread (τ)" v={p.tau == null ? '—' : fmtVal(metric === 'cover80' ? 'cover80' : metric, p.tau)}
                        sub={`I² ${p.i2 == null ? '—' : `${Math.round(p.i2 * 100)}%`} beyond within-season noise · Q p ${fmtP(p.q_p)}`} />
                    <Stat k="Next season" v={p.pi_lo == null ? '—' : `${fmtDiff(metric, p.pi_lo)} to ${fmtDiff(metric, p.pi_hi)}`}
                        sub={p.pi_lo == null ? 'needs 3+ seasons' : (p.pi_lo < 0 && p.pi_hi > 0 ? 'either model could come out ahead' : 'same order expected')} />
                </div>
            )}
            <IntervalChart items={items} refLine={0} refLabel="No difference" fmt={(v) => fmtDiff(metric, v)}
                legend={`${pair.a_label} − ${pair.b_label}, 95% interval${coverNote}`}
                name={`report card ${pair.task} ${pair.a} vs ${pair.b} ${metric} ${pair.variant || ''}`.trim()}
                ariaLabel={`${pair.a_label} minus ${pair.b_label}, by season`} />
            <p className="page-subtitle px-foot">
                Negative = {pair.a_label} has the lower {pair.metric_label.toLowerCase()}. Season intervals: paired {pair.cluster_by === 'game' ? 'game' : 'team-season'}-clustered
                bootstrap ({pair.resamples.toLocaleString()} resamples). The pooled row treats each season&apos;s true difference as a draw from a distribution
                (DerSimonian-Laird); its interval is Hartung-Knapp&apos;s, wider than a plain normal one with this few seasons.
            </p>
        </>
    );
}

export default function ModelReportCard() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [data, setData] = useState(null);   // { key, data } | { key, error }
    const [pair, setPair] = useState(null);

    useEffect(() => {
        fetchReportCardOptions()
            .then((o) => { setOptions(o); setForm(formFromParams(params, o)); })
            .catch(() => setOptionsError('The Model Report Card couldn\'t load. Is the impact API (port 8002) running, and has scripts/build_report_card.py been run?'));
    }, [params]);

    const info = options && form ? options.tasks.find((t) => t.key === form.task) : null;
    const metric = form?.metric ?? info?.metrics[0].key;
    const variant = form?.variant ?? info?.variants[0].key;
    useUrlSync(form && { task: form.task === options.tasks[0].key ? null : form.task, metric: form.metric, v: form.variant, a: form.a, b: form.b });

    const key = info ? `${form.task}|${metric}|${variant}` : null;
    useEffect(() => {
        if (!key) return undefined;
        let live = true;
        fetchReportCardTask(form.task, { metric, variant })
            .then((d) => { if (live) setData({ key, data: d }); })
            .catch((e) => { if (live) setData({ key, error: e.response?.data?.detail || 'This task couldn\'t load.' }); });
        return () => { live = false; };
    }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

    const d = data?.key === key ? data.data : null;
    const err = data?.key === key ? data.error : '';
    // default pair: the two best by mean rank
    const ranked = useMemo(() => (d ? [...d.models].filter((m) => m.scored).sort((x, y) => (x.mean_rank ?? 99) - (y.mean_rank ?? 99)) : []), [d]);
    const a = form?.a && d?.models.some((m) => m.key === form.a) ? form.a : ranked[0]?.key;
    const bRaw = form?.b && d?.models.some((m) => m.key === form.b) ? form.b : ranked.find((m) => m.key !== a)?.key;
    const b = bRaw === a ? ranked.find((m) => m.key !== a)?.key : bRaw;
    const pairKey = d && a && b ? `${key}|${a}|${b}` : null;
    useEffect(() => {
        if (!pairKey) return undefined;
        let live = true;
        fetchReportCardPair(form.task, a, b, { metric, variant })
            .then((p) => { if (live) setPair({ key: pairKey, data: p }); })
            .catch((e) => { if (live) setPair({ key: pairKey, error: e.response?.data?.detail || 'This comparison couldn\'t load.' }); });
        return () => { live = false; };
    }, [pairKey]); // eslint-disable-line react-hooks/exhaustive-deps

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form || !info) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const pr = pair?.key === pairKey ? pair : null;
    const best = d ? ranked[0] : null;
    const contested = d ? d.pairs.filter((x) => x.flips > 0) : [];
    const refPooled = d ? d.models.filter((m) => m.vs_reference) : [];

    return (
        <section className="dashboard-card lb-card px-page rc-page">
            <h2 className="card-title hb-page-title">
                Model Report Card
                <InfoTooltip label="How the seasons are scored" title="Rolling origin">
                    {'Each season is predicted by models rebuilt from the seasons before it only: every hyperparameter, scale and form is chosen again by the model\'s own rule on the earlier seasons, then the season is scored once. '
                        + 'Season intervals are a cluster bootstrap (games, or team-seasons for the simulator). The seasons are pooled with a random-effects model (DerSimonian-Laird, Hartung-Knapp interval), '
                        + 'which treats each season\'s true difference as a draw from a distribution: τ is that distribution\'s spread, and the prediction interval is where next season\'s difference should land.'}
                </InfoTooltip>
                <SourceBadge source={d?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="reportcard" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Every model on the platform, scored season by season with only what was known before each season, so a lead that holds in one test season
                can be told apart from one that flips. {info.what}
            </p>

            <div className="pf-toggles rc-tabs" role="tablist" aria-label="Task">
                {options.tasks.map((t) => (
                    <button key={t.key} type="button" role="tab" className="pf-pill" aria-selected={form.task === t.key} aria-pressed={form.task === t.key}
                        onClick={() => setForm({ task: t.key, metric: null, variant: null, a: null, b: null })}>{t.short}</button>
                ))}
            </div>
            <div className="lb-controls pf-controls">
                {info.metrics.length > 1 && (
                    <label>
                        <span>Score</span>
                        <select className="input-field" value={metric} onChange={(e) => set({ metric: e.target.value })}>
                            {info.metrics.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
                        </select>
                    </label>
                )}
                {info.variants.length > 1 && (
                    <label>
                        <span>Checkpoint</span>
                        <select className="input-field" value={variant} onChange={(e) => set({ variant: e.target.value })}>
                            {info.variants.map((v) => <option key={v.key} value={v.key}>{v.label}</option>)}
                        </select>
                    </label>
                )}
            </div>

            {err && <p className="error-message">{err}</p>}
            {!d && !err && <Loader />}
            {d && (
                <>
                    <div className="px-stats">
                        <Stat k="Seasons scored" v={d.seasons.length} sub={`${d.seasons[0].label} to ${d.seasons[d.seasons.length - 1].label}`} />
                        <Stat k="Best mean rank" v={best?.label ?? '—'} sub={best ? `first in ${best.first} of ${best.scored} seasons, mean rank ${best.mean_rank?.toFixed(1)}` : ''} />
                        <Stat k="Pairs whose order flips" v={`${contested.length} of ${d.pairs.length}`}
                            sub={`${d.pairs.filter((x) => x.clear_flips > 0).length} flip clearly (a season's interval on the other side)`} />
                        <Stat k="Units a season" v={Math.round(d.seasons.reduce((s, x) => s + x.n, 0) / d.seasons.length).toLocaleString()} sub={d.units_label} />
                    </div>

                    <h3 className="px-h">{d.metric_label}, season by season{d.variant_label ? ` (${d.variant_label.toLowerCase()})` : ''}</h3>
                    <Grid d={d} onPick={(m) => { if (m !== a) set({ a: m, b: a }); }} />

                    {refPooled.length > 0 && (
                        <>
                            <h3 className="px-h">Pooled over the seasons, against {d.reference_label.toLowerCase()}</h3>
                            <IntervalChart
                                items={[...refPooled].sort((x, y) => x.vs_reference.mu - y.vs_reference.mu).map((m) => ({
                                    key: m.key, label: m.label, value: m.vs_reference.mu, lo: m.vs_reference.ci_lo, hi: m.vs_reference.ci_hi,
                                    tone: d.lower_is_better === false ? null : m.vs_reference.ci_hi < 0 ? 'good' : m.vs_reference.ci_lo > 0 ? 'bad' : null,
                                    note: `Lower in ${m.vs_reference.a_better} of ${m.vs_reference.k} seasons · τ ${m.vs_reference.tau == null ? '—' : fmtVal(metric, m.vs_reference.tau)} · next season ${fmtDiff(metric, m.vs_reference.pi_lo)} to ${fmtDiff(metric, m.vs_reference.pi_hi)}`,
                                }))}
                                refLine={0} refLabel={d.reference_label} fmt={(v) => fmtDiff(metric, v)} legend="Pooled difference, 95% interval (Hartung-Knapp)"
                                name={`report card ${d.task} pooled vs ${d.reference} ${metric} ${d.variant || ''}`.trim()}
                                ariaLabel={`Every model's pooled difference from ${d.reference_label}`}
                                onPick={(it) => set({ a: it.key, b: d.reference })} />
                        </>
                    )}

                    <h3 className="px-h">Two models, season by season</h3>
                    <div className="lb-controls pf-controls">
                        <label>
                            <span>Model A</span>
                            <select className="input-field" value={a ?? ''} onChange={(e) => set({ a: e.target.value, b: e.target.value === b ? a : b })}>
                                {d.models.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
                            </select>
                        </label>
                        <label>
                            <span>Model B</span>
                            <select className="input-field" value={b ?? ''} onChange={(e) => set({ b: e.target.value, a: e.target.value === a ? b : a })}>
                                {d.models.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
                            </select>
                        </label>
                    </div>
                    {pr?.error && <p className="error-message">{pr.error}</p>}
                    {!pr && <Loader />}
                    {pr?.data && <PairView pair={{ ...pr.data, task: d.task, variant: d.variant }} metric={metric} lowerIsBetter={d.lower_is_better} />}
                    {pr?.data?.pooled && <p className="page-subtitle px-foot"><strong>{betterWord(pr.data.pooled, pr.data.a_label, pr.data.b_label)}</strong>.</p>}

                    <h3 className="px-h">Where the order flips</h3>
                    <TableExport name={`report card ${d.task} flips ${metric} ${d.variant || ''}`.trim()} />
                    <div className="table-wrapper">
                        <table className="data-table lb-table px-table rc-table">
                            <thead>
                                <tr>
                                    <th>A</th><th>B</th>
                                    <th className="lb-num" title="Seasons A scored better – seasons B scored better">Seasons ahead</th>
                                    <th className="lb-num" title="Seasons whose interval excludes zero, for A – for B">Clearly</th>
                                    <th className="lb-num" title="Seasons whose difference has the opposite sign to the pooled one">Flips</th>
                                    <th className="lb-num">Pooled A − B</th>
                                    <th className="lb-num">95% interval</th>
                                    <th className="lb-num" title="Between-season spread of the true difference">τ</th>
                                    <th className="lb-num">p</th>
                                </tr>
                            </thead>
                            <tbody>
                                {d.pairs.slice(0, 40).map((x) => (
                                    <tr key={`${x.a}|${x.b}`} className={x.a === a && x.b === b ? 'rc-sel' : ''}>
                                        <td><button type="button" className="rc-link" onClick={() => set({ a: x.a, b: x.b })}>{x.a_label}</button></td>
                                        <td>{x.b_label}</td>
                                        <td className="lb-num">{x.a_better} – {x.b_better}</td>
                                        <td className="lb-num">{x.a_clear} – {x.b_clear}</td>
                                        <td className="lb-num">{x.flips}{x.clear_flips ? ` (${x.clear_flips} clear)` : ''}</td>
                                        <td className="lb-num">{fmtDiff(metric, x.mu)}</td>
                                        <td className="lb-num">{fmtDiff(metric, x.ci_lo)} to {fmtDiff(metric, x.ci_hi)}</td>
                                        <td className="lb-num">{x.tau == null ? '—' : fmtVal(metric, x.tau)}</td>
                                        <td className="lb-num">{fmtP(x.p)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="page-subtitle px-foot">
                        Every pair of models, the most-flipped first{d.pairs.length > 40 ? ` (40 of ${d.pairs.length} shown; the export has the shown rows)` : ''}.
                        A flip is a season whose difference points the other way from the pooled one; a clear flip is one whose own 95% interval does.
                    </p>

                    {d.choices.length > 0 && (
                        <>
                            <h3 className="px-h">What was chosen before each season</h3>
                            <TableExport name={`report card ${d.task} choices`} />
                            <div className="table-wrapper">
                                <table className="data-table lb-table px-table rc-table">
                                    <thead>
                                        <tr>
                                            <th>Choice</th>
                                            {d.choices[0].values.map((v) => <th key={v.season} className="lb-num">{v.label}</th>)}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {d.choices.map((c) => (
                                            <tr key={`${c.model}|${c.parameter}`}>
                                                <td title={c.criterion}>{c.label}</td>
                                                {d.choices[0].values.map((s) => {
                                                    const v = c.values.find((x) => x.season === s.season);
                                                    const num = v && Number(v.value);
                                                    return <td key={s.season} className="lb-num" title={v ? `chosen on ${v.chosen_on}` : ''}>{v == null ? '—' : Number.isFinite(num) && !/^\d+$/.test(v.value) ? num.toFixed(Math.abs(num) >= 100 ? 0 : 3) : v.value}</td>;
                                                })}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <p className="page-subtitle px-foot">Each column was chosen on the seasons before it only, by the model&apos;s own rule (hover a name for the criterion).</p>
                        </>
                    )}
                    {d.not_on_file && <p className="page-subtitle rc-nof"><strong>Not on file.</strong> {d.not_on_file}</p>}
                </>
            )}
        </section>
    );
}
