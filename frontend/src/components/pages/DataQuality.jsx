import React, { useEffect, useMemo, useState } from 'react';
import {
    fetchDataQualityCheck, fetchDataQualityGames, fetchDataQualityOverview, fetchDataQualitySensitivity,
} from '../../services/api';
import Loader from '../Loader';
import CopyLinkButton from '../common/CopyLinkButton';
import InfoTooltip from '../common/InfoTooltip';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import { IntervalChart } from '../common/CoachingCharts';
import { signed } from '../../utils/format';
import { isPlainClick, pageHref, parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/playfinder.css';
import '../../styles/possessions.css';
import '../../styles/dataquality.css';

// Data Quality (?page=quality): the paper's data-quality audit as a page. Every error class of the
// public feeds with its live re-check, a quality flag on every game 2020-21 on, and "does it matter?":
// three headline results re-scored with the flagged games dropped (GET /data-quality/*, from
// scripts/paper_data_audit.py and scripts/build_data_quality.py; definitions in api/data_quality_lib.py).
// Inputs in the link: v (classes | games | matter); games: season, level, cls, team, sort, pg;
// matter: r (result), scope, ph (phase), pair, metric.

const VIEWS = [
    { key: 'classes', label: 'Error classes' },
    { key: 'games', label: 'Games' },
    { key: 'matter', label: 'Does it matter?' },
];
const KIND_LABEL = { repaired: 'Repaired', 'worked around': 'Worked around', excluded: 'Excluded', disclosed: 'Disclosed' };
const LEVEL_LABEL = { excluded: 'Excluded', flagged: 'Flagged', worked_around: 'Worked around', clean: 'Clean' };
const SORTS = [
    { key: 'classes', label: 'Most classes' }, { key: 'untracked', label: 'Most minutes untracked' },
    { key: 'events', label: 'Most flagged events' }, { key: 'date', label: 'Date' },
];
const PAGE = 50;
const PHASE_LABEL = { tune: 'Tune', validate: 'Validate', test: 'Test', all: 'All seasons' };
const METRIC_LABEL = { game_rmse: 'Game RMSE', ppp: 'Points per possession', log_loss: 'Log loss', brier: 'Brier score' };
const DEC = { game_rmse: 3, ppp: 3, log_loss: 4, brier: 4, share: 3 };
const VERDICT_LABEL = {
    same: 'Same call', 'no longer clear': 'No longer clear', 'becomes clear': 'Becomes clear', flips: 'Flips',
};

const fmtN = (n) => (n == null ? '—' : Number(n).toLocaleString());
const fmtP = (p) => (p == null ? '—' : p < 0.001 ? '< 0.001' : p.toFixed(3));
const fmtV = (metric, v) => (v == null ? '—' : Number(v).toFixed(DEC[metric] ?? 3));
const fmtD = (metric, v) => (v == null ? '—' : signed(v, DEC[metric] ?? 3));

function fmtLive(v) {
    if (v.live == null) return '—';
    const f = v.fmt;
    if (f === 'pct0' || f === 'pct1' || (v.live > 0 && v.live < 1 && !Number.isInteger(v.live))) {
        return `${(v.live * 100).toFixed(f === 'pct0' ? 0 : 2)}%`;
    }
    return Number.isInteger(v.live) ? fmtN(v.live) : v.live.toFixed(1);
}
function fmtStored(v) {
    return fmtLive({ ...v, live: v.stored });
}

function Stat({ k, v, sub, title, tone }) {
    return (
        <div className={`px-stat${tone ? ` dq-stat--${tone}` : ''}`} title={title}>
            <div className="px-stat-k">{k}</div>
            <div className="px-stat-v">{v}</div>
            {sub && <div className="px-stat-sub">{sub}</div>}
        </div>
    );
}

function formFromParams(p) {
    return {
        view: parseParam.oneOf(p, 'v', VIEWS.map((v) => v.key)) ?? 'classes',
        season: parseParam.int(p, 'season', { min: 2000, max: 2100 }),
        level: parseParam.oneOf(p, 'level', Object.keys(LEVEL_LABEL)),
        cls: parseParam.str(p, 'cls'),
        team: parseParam.str(p, 'team'),
        sort: parseParam.oneOf(p, 'sort', SORTS.map((s) => s.key)) ?? 'classes',
        pg: parseParam.int(p, 'pg', { min: 0 }) ?? 0,
        result: parseParam.oneOf(p, 'r', ['impact', 'possessions', 'availability']) ?? 'impact',
        scope: parseParam.str(p, 'scope'),
        phase: parseParam.oneOf(p, 'ph', ['tune', 'validate', 'test', 'all']),
        pair: parseParam.str(p, 'pair'),
        metric: parseParam.str(p, 'metric'),
    };
}

// ── Error classes ────────────────────────────────────────────────────────────

function LiveCell({ c, state, open, onToggle }) {
    if (c.live.mode === 'build') {
        return <span className="dq-live dq-live--build" title={c.live.why}>Build only</span>;
    }
    if (!state) return <span className="dq-live">Checking…</span>;
    if (state.error) return <span className="dq-live dq-live--bad" title={state.error}>Failed</span>;
    const d = state.data;
    const ok = d.agree === d.total;
    return (
        <button type="button" className={`dq-live dq-live--${ok ? 'ok' : 'bad'}`} aria-expanded={open} onClick={onToggle}
            title={`${d.agree} of ${d.total} numbers equal the audit's at the precision the paper prints; ${d.seconds} s`}>
            {ok ? `Agrees (${d.total})` : `${d.total - d.agree} of ${d.total} differ`}
        </button>
    );
}

function LiveDetail({ c, state }) {
    if (c.live.mode === 'build') {
        return <p className="dq-detail-p"><strong>Checked at build only.</strong> {c.live.why} The build re-runs it and stops if the audit no longer matches.</p>;
    }
    const d = state?.data;
    if (!d) return null;
    return (
        <>
            {d.note && <p className="dq-detail-p">{d.note}</p>}
            <table className="dq-live-table">
                <thead><tr><th>Number</th><th>Season</th><th className="lb-num">Now</th><th className="lb-num">Audit</th><th>Agrees</th></tr></thead>
                <tbody>
                    {d.values.map((v) => (
                        <tr key={`${v.key}|${v.season}`}>
                            <td>{v.label}</td>
                            <td>{v.season_label}</td>
                            <td className="lb-num">{fmtLive(v)}</td>
                            <td className="lb-num">{fmtStored(v)}</td>
                            <td className={v.ok ? 'dq-ok' : 'dq-bad'}>{v.ok ? 'yes' : 'no'}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
            <p className="dq-detail-p dq-sub">Checked {new Date(d.checked_at).toLocaleString()} in {d.seconds} s (cached until the API restarts).</p>
        </>
    );
}

function ClassesView({ ov, live, onGames }) {
    const [open, setOpen] = useState(null);
    const perGame = ov.classes.filter((c) => c.per_game);
    const maxShare = Math.max(...perGame.flatMap((c) => c.per_game.by_season.map((s) => s.games / Math.max(1, ov.levels.reduce((a, l) => a + (l.by_season.find((x) => x.season === s.season)?.games ?? 0), 0)))));
    const seasonGames = Object.fromEntries(ov.seasons.map((s) => [s.season, ov.levels.reduce((a, l) => a + (l.by_season.find((x) => x.season === s.season)?.games ?? 0), 0)]));
    return (
        <>
            <h3 className="px-h">The {ov.classes.length} error classes</h3>
            <TableExport name="data quality error classes" />
            <div className="table-wrapper">
                <table className="data-table lb-table px-table dq-table">
                    <thead>
                        <tr>
                            <th>Error class</th>
                            <th>How detected</th>
                            <th>Size (the audit)</th>
                            <th>Handling</th>
                            <th title="The class re-measured from the tables as they are now, against the audit's stored numbers">Live check</th>
                            <th className="lb-num" title="Games of 2020-21 to 2025-26 the class touches, by its per-game rule">Games touched</th>
                        </tr>
                    </thead>
                    <tbody>
                        {ov.feeds.map((feed) => (
                            <React.Fragment key={feed}>
                                <tr className="dq-feed"><td colSpan={6}>{feed}</td></tr>
                                {ov.classes.filter((c) => c.feed === feed).map((c) => (
                                    <React.Fragment key={c.key}>
                                        <tr>
                                            <td><strong>{c.name}</strong></td>
                                            <td className="dq-text">{c.detection}</td>
                                            <td className="dq-text">{c.size}</td>
                                            <td className="dq-text"><span className={`dq-kind dq-kind--${c.handling_kind.replace(' ', '-')}`}>{KIND_LABEL[c.handling_kind]}</span> {c.handling}</td>
                                            <td><LiveCell c={c} state={live[c.key]} open={open === c.key} onToggle={() => setOpen(open === c.key ? null : c.key)} />
                                                {c.live.mode === 'build' && <button type="button" className="dq-why" onClick={() => setOpen(open === c.key ? null : c.key)} aria-expanded={open === c.key}>why</button>}
                                            </td>
                                            <td className="lb-num">
                                                {c.per_game
                                                    ? <button type="button" className="dq-link" onClick={() => onGames({ cls: c.key, season: null })} title={`Per-game rule: ${c.per_game.what}`}>{fmtN(c.per_game.games)}</button>
                                                    : <span className="dq-sub" title={c.not_per_game}>not per game</span>}
                                            </td>
                                        </tr>
                                        {open === c.key && (
                                            <tr className="dq-detail"><td colSpan={6}><LiveDetail c={c} state={live[c.key]} /></td></tr>
                                        )}
                                    </React.Fragment>
                                ))}
                            </React.Fragment>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle px-foot">
                Sizes are the audit&apos;s stored numbers (scripts/paper_data_audit.py, the paper&apos;s Table audit). The live check re-measures
                {' '}{ov.classes.filter((c) => c.live.mode === 'live').length} classes with its own SQL against the tables as they are now and compares
                each number with the audit&apos;s at the precision the paper prints; the other {ov.classes.filter((c) => c.live.mode === 'build').length} need
                a replay of every game in Python and are checked by the build. Click a check for its numbers.
            </p>

            <h3 className="px-h">Games touched, by season</h3>
            <TableExport name="data quality games touched by season" />
            <div className="table-wrapper">
                <table className="data-table lb-table px-table dq-matrix">
                    <thead>
                        <tr>
                            <th>Class (per-game rule)</th>
                            {ov.seasons.map((s) => <th key={s.season} className="lb-num">{s.label}</th>)}
                            <th className="lb-num">All</th>
                        </tr>
                    </thead>
                    <tbody>
                        {perGame.map((c) => (
                            <tr key={c.key}>
                                <td title={c.per_game.what}>{c.name} <span className={`dq-level dq-level--${c.per_game.level}`}>{LEVEL_LABEL[c.per_game.level]}</span></td>
                                {c.per_game.by_season.map((s) => (
                                    <td key={s.season} className="lb-num dq-heat" style={{ '--p': maxShare ? s.games / seasonGames[s.season] / maxShare : 0 }}>
                                        {s.games ? <button type="button" className="dq-link" onClick={() => onGames({ cls: c.key, season: s.season })}>{fmtN(s.games)}</button> : '·'}
                                    </td>
                                ))}
                                <td className="lb-num">{fmtN(c.per_game.games)}</td>
                            </tr>
                        ))}
                        {ov.levels.map((l) => (
                            <tr key={l.key} className="dq-level-row">
                                <td title={l.what}><span className={`dq-level dq-level--${l.key}`}>{l.label}</span> games</td>
                                {l.by_season.map((s) => (
                                    <td key={s.season} className="lb-num">
                                        {s.games ? <button type="button" className="dq-link" onClick={() => onGames({ level: l.key, season: s.season, cls: null })}>{fmtN(s.games)}</button> : '·'}
                                    </td>
                                ))}
                                <td className="lb-num">{fmtN(l.games)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle px-foot">
                A game&apos;s level is the worst handling among the classes that touch it: <strong>excluded</strong> (left out of every ranking),
                {' '}<strong>flagged</strong> (kept, with an error left in the data), <strong>worked around</strong> (only errors the pipeline repairs
                or works around), <strong>clean</strong>. Zero-distance threes are on nearly every game&apos;s shot chart (distance is taken from the
                coordinates everywhere), so almost no game is clean. Shading: share of the season&apos;s games. Click a count for the games.
            </p>
        </>
    );
}

// ── Games ────────────────────────────────────────────────────────────────────

function ReplayLink({ game, onNavigate }) {
    const params = { game };
    const href = `${pageHref('analytics', params)}#replay`;
    return (
        <a className="pf-replay" href={href}
            onClick={(e) => { if (isPlainClick(e)) { e.preventDefault(); onNavigate('analytics', 'replay', params); } }}>Replay</a>
    );
}

function GamesView({ ov, form, set, onNavigate }) {
    const [state, setState] = useState(null);
    const key = JSON.stringify([form.season, form.level, form.cls, form.team, form.sort, form.pg]);
    useEffect(() => {
        let live = true;
        fetchDataQualityGames({ season: form.season, level: form.level, cls: form.cls, team: form.team, sort: form.sort,
            dir: form.sort === 'date' ? 'desc' : 'desc', limit: PAGE, offset: form.pg * PAGE })
            .then((d) => { if (live) setState({ key, data: d }); })
            .catch((e) => { if (live) setState({ key, error: e.response?.data?.detail || 'The games couldn\'t load.' }); });
        return () => { live = false; };
    }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
    const d = state?.key === key ? state.data : null;
    const err = state?.key === key ? state.error : '';
    const perGame = ov.classes.filter((c) => c.per_game);
    const pages = d ? Math.ceil(d.total / PAGE) : 0;
    return (
        <>
            <div className="lb-controls pf-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={form.season ?? ''} onChange={(e) => set({ season: e.target.value ? Number(e.target.value) : null, pg: 0 })}>
                        <option value="">All</option>
                        {ov.seasons.map((s) => <option key={s.season} value={s.season}>{s.label}</option>)}
                    </select>
                </label>
                <label>
                    <span>Level</span>
                    <select className="input-field" value={form.level ?? ''} onChange={(e) => set({ level: e.target.value || null, pg: 0 })}>
                        <option value="">Any</option>
                        {ov.levels.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
                    </select>
                </label>
                <label>
                    <span>Error class</span>
                    <select className="input-field" value={form.cls ?? ''} onChange={(e) => set({ cls: e.target.value || null, pg: 0 })}>
                        <option value="">Any</option>
                        {perGame.map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}
                    </select>
                </label>
                <label>
                    <span>Team</span>
                    <select className="input-field" value={form.team ?? ''} onChange={(e) => set({ team: e.target.value || null, pg: 0 })}>
                        <option value="">Any</option>
                        {(d?.teams ?? (form.team ? [form.team] : [])).map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                </label>
                <label>
                    <span>Sort</span>
                    <select className="input-field" value={form.sort} onChange={(e) => set({ sort: e.target.value, pg: 0 })}>
                        {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                    </select>
                </label>
            </div>
            {err && <p className="error-message">{err}</p>}
            {!d && !err && <Loader />}
            {d && (
                <>
                    <p className="page-subtitle dq-count">{fmtN(d.total)} game{d.total === 1 ? '' : 's'}{pages > 1 ? ` · page ${form.pg + 1} of ${pages}` : ''}</p>
                    <TableExport name={`data quality games${form.cls ? ` ${form.cls}` : ''}${form.season ? ` ${form.season}` : ''}`} />
                    <div className="table-wrapper">
                        <table className="data-table lb-table px-table dq-games">
                            <thead>
                                <tr><th>Date</th><th>Game</th><th>Level</th><th>What touched it</th><th>Play-by-play</th></tr>
                            </thead>
                            <tbody>
                                {d.games.map((g) => (
                                    <tr key={g.game_id}>
                                        <td className="dq-nowrap">{g.date}</td>
                                        <td className="dq-nowrap">{g.away} @ {g.home}<div className="dq-sub">{g.game_id}</div></td>
                                        <td><span className={`dq-level dq-level--${g.level}`}>{LEVEL_LABEL[g.level]}</span></td>
                                        <td>
                                            <ul className="dq-hits">
                                                {g.details.map((x) => (
                                                    <li key={x.key} className={`dq-hit dq-hit--${x.level}`}><strong>{x.short}:</strong> {x.text}</li>
                                                ))}
                                            </ul>
                                        </td>
                                        <td>{g.level === 'excluded' && g.classes.includes('cup_finals') ? <span className="dq-sub">—</span> : <ReplayLink game={g.game_id} onNavigate={onNavigate} />}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    {pages > 1 && (
                        <div className="pf-toggles dq-pager">
                            <button type="button" className="pf-pill" disabled={form.pg === 0} onClick={() => set({ pg: form.pg - 1 })}>Previous</button>
                            <button type="button" className="pf-pill" disabled={form.pg + 1 >= pages} onClick={() => set({ pg: form.pg + 1 })}>Next</button>
                        </div>
                    )}
                    <p className="page-subtitle px-foot">
                        Every ESPN regular-season game 2020-21 on (the three NBA Cup finals included). What touched a game lists every class with its size there,
                        including the ones the pipeline works around; the level counts only the worst. ESPN&apos;s clock trails the shot chart&apos;s on every game
                        (the game&apos;s median gap is shown), so only an event outside its own period flags the clock.
                    </p>
                </>
            )}
        </>
    );
}

// ── Does it matter? ──────────────────────────────────────────────────────────

function MatterView({ ov, form, set }) {
    const [state, setState] = useState(null);
    useEffect(() => {
        let live = true;
        fetchDataQualitySensitivity(form.result)
            .then((d) => { if (live) setState({ key: form.result, data: d }); })
            .catch((e) => { if (live) setState({ key: form.result, error: e.response?.data?.detail || 'This result couldn\'t load.' }); });
        return () => { live = false; };
    }, [form.result]);
    const d = state?.key === form.result ? state.data : null;
    const err = state?.key === form.result ? state.error : '';
    const scope = d && d.scopes.some((s) => s.key === form.scope) ? form.scope : d?.scopes[0].key;
    const phase = d && d.phases.includes(form.phase) ? form.phase : d?.phases.includes('test') ? 'test' : d?.phases[0];
    const pairKey = d && d.pairs.some((p) => `${p.a}:${p.b}` === form.pair) ? form.pair : d ? `${d.headline.a}:${d.headline.b}` : null;
    const pair = d?.pairs.find((p) => `${p.a}:${p.b}` === pairKey);
    const metric = d && d.metrics.includes(form.metric) && form.metric !== 'share' ? form.metric : d?.metric;
    const rows = useMemo(() => {
        if (!d || !pair) return [];
        return d.sets.map((s) => ({
            set: s,
            cell: s.cells.find((c) => c.a === pair.a && c.b === pair.b && c.phase === phase && c.metric === metric
                && (c.scope === scope || s.key === 'none')),
        })).filter((x) => x.cell);
    }, [d, pair, phase, metric, scope]);
    const full = rows.find((x) => x.set.key === 'none')?.cell;

    return (
        <>
            <div className="pf-toggles dq-results" role="tablist" aria-label="Result">
                {Object.entries(ov.results).map(([k, r]) => (
                    <button key={k} type="button" role="tab" className="pf-pill" aria-selected={form.result === k} aria-pressed={form.result === k}
                        onClick={() => set({ result: k, scope: null, phase: null, pair: null, metric: null })}>{r.short}</button>
                ))}
            </div>
            {err && <p className="error-message">{err}</p>}
            {!d && !err && <Loader />}
            {d && pair && (
                <>
                    <h3 className="px-h">{d.label}</h3>
                    <p className="page-subtitle">{d.what}</p>
                    <div className="lb-controls pf-controls">
                        {d.scopes.length > 1 && (
                            <label>
                                <span>Games dropped</span>
                                <select className="input-field" value={scope} onChange={(e) => set({ scope: e.target.value })}>
                                    {d.scopes.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                                </select>
                            </label>
                        )}
                        {d.phases.length > 1 && (
                            <label>
                                <span>Seasons scored</span>
                                <select className="input-field" value={phase} onChange={(e) => set({ phase: e.target.value })}>
                                    {d.phases.map((p) => <option key={p} value={p}>{PHASE_LABEL[p]} ({d.phase_seasons[p]})</option>)}
                                </select>
                            </label>
                        )}
                        <label>
                            <span>Comparison</span>
                            <select className="input-field" value={pairKey} onChange={(e) => set({ pair: e.target.value })}>
                                {d.pairs.map((p) => <option key={`${p.a}:${p.b}`} value={`${p.a}:${p.b}`}>{p.a_label} − {p.b_label}</option>)}
                            </select>
                        </label>
                        {d.metrics.filter((m) => m !== 'share').length > 1 && (
                            <label>
                                <span>Score</span>
                                <select className="input-field" value={metric} onChange={(e) => set({ metric: e.target.value })}>
                                    {d.metrics.filter((m) => m !== 'share').map((m) => <option key={m} value={m}>{METRIC_LABEL[m] ?? m}</option>)}
                                </select>
                            </label>
                        )}
                    </div>

                    {full && (
                        <div className="px-stats">
                            <Stat k="Every game" v={fmtD(metric, full.diff)} sub={`95% interval ${fmtD(metric, full.ci_lo)} to ${fmtD(metric, full.ci_hi)} · ${fmtN(full.n)} ${d.result === 'possessions' ? 'possessions' : 'games'}`} />
                            {(() => {
                                const f = rows.find((x) => x.set.key === 'flagged');
                                if (!f) return null;
                                const c = f.cell;
                                return (
                                    <Stat k="Flagged games dropped" v={fmtD(metric, c.diff)} tone={c.verdict === 'same' ? null : 'warn'}
                                        sub={`95% interval ${fmtD(metric, c.ci_lo)} to ${fmtD(metric, c.ci_hi)} · ${fmtN(f.set.games_dropped)} of ${fmtN(f.set.games_in_scope)} games dropped`} />
                                );
                            })()}
                            {(() => {
                                const f = rows.find((x) => x.set.key === 'flagged')?.cell;
                                if (!f || f.rand_p == null) return null;
                                return (
                                    <Stat k="Random drops of as many games" v={`${fmtD(metric, f.rand_lo)} to ${fmtD(metric, f.rand_hi)}`}
                                        sub={`${f.rand_draws} draws, same count each season · p ${fmtP(f.rand_p)}: the flagged games move it ${f.rand_p <= 0.05 ? 'more than' : 'no more than'} random ones`} />
                                );
                            })()}
                            <Stat k="Calls unchanged" v={`${rows.filter((x) => x.set.key !== 'none' && x.cell.verdict === 'same').length} of ${rows.length - 1}`}
                                sub={`drop sets whose interval says what every game's says; ${rows.filter((x) => x.cell.beyond_random).length} move it more than random drops (p ≤ 0.05)`} />
                        </div>
                    )}

                    <IntervalChart
                        items={rows.map(({ set: s, cell: c }) => ({
                            key: s.key, label: s.key === 'none' ? 'Every game' : `${s.label} (−${fmtN(s.games_dropped)})`, shortLabel: s.key === 'none' ? 'Every game' : s.label,
                            value: c.diff, lo: c.ci_lo, hi: c.ci_hi, highlight: s.key === 'none',
                            tone: c.ci_hi < 0 ? 'good' : c.ci_lo > 0 ? 'bad' : null,
                            note: `${pair.a_label} ${fmtV(metric, c.value_a)} vs ${pair.b_label} ${fmtV(metric, c.value_b)}`
                                + (c.rand_p != null ? ` · random drops ${fmtD(metric, c.rand_lo)} to ${fmtD(metric, c.rand_hi)} (p ${fmtP(c.rand_p)})` : ''),
                        }))}
                        refLine={0} refLabel="No difference" fmt={(v) => fmtD(metric, v)}
                        legend={`${pair.a_label} − ${pair.b_label}, 95% interval`}
                        name={`data quality ${d.result} ${pair.a} vs ${pair.b} ${metric} ${phase} ${scope}`}
                        ariaLabel={`${pair.a_label} minus ${pair.b_label} with each drop set`} />

                    <TableExport name={`data quality ${d.result} ${pair.a} vs ${pair.b} ${metric} ${phase} ${scope}`} />
                    <div className="table-wrapper">
                        <table className="data-table lb-table px-table dq-sens">
                            <thead>
                                <tr>
                                    <th>Games dropped</th>
                                    <th className="lb-num">Games</th>
                                    <th className="lb-num">{pair.a_label}</th>
                                    <th className="lb-num">{pair.b_label}</th>
                                    <th className="lb-num">Difference</th>
                                    <th className="lb-num">95% interval</th>
                                    <th className="lb-num" title="2.5th to 97.5th percentile of the difference with as many random games dropped, the same count each season">Random drops</th>
                                    <th className="lb-num" title="Share of random drops that move the difference at least as far from every game's">p vs random</th>
                                    <th>Against every game</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map(({ set: s, cell: c }) => (
                                    <tr key={s.key} className={s.key === 'none' ? 'dq-full' : ''}>
                                        <td title={s.rule || ''}>{s.label}{s.level && <span className={`dq-level dq-level--${s.level}`}>{LEVEL_LABEL[s.level]}</span>}</td>
                                        <td className="lb-num">{s.key === 'none' ? fmtN(s.games_in_scope) : `−${fmtN(s.games_dropped)}`}</td>
                                        <td className="lb-num">{fmtV(metric, c.value_a)}</td>
                                        <td className="lb-num">{fmtV(metric, c.value_b)}</td>
                                        <td className="lb-num">{fmtD(metric, c.diff)}</td>
                                        <td className="lb-num">{fmtD(metric, c.ci_lo)} to {fmtD(metric, c.ci_hi)}</td>
                                        <td className="lb-num">{c.rand_p == null ? <span className="dq-sub" title="Fewer than 50 games dropped, or every game">—</span> : `${fmtD(metric, c.rand_lo)} to ${fmtD(metric, c.rand_hi)}`}</td>
                                        <td className={`lb-num${c.beyond_random ? ' dq-bad' : ''}`}>{fmtP(c.rand_p)}</td>
                                        <td>{s.key === 'none' ? <span className="dq-sub">reference</span> : <span className={`dq-verdict dq-verdict--${(c.verdict || '').replace(/ /g, '-')}`}>{VERDICT_LABEL[c.verdict] ?? '—'}</span>}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="page-subtitle px-foot">
                        Negative = {pair.a_label} is lower. Intervals: paired bootstrap by game ({fmtN(d.resamples)} resamples), as the paper&apos;s tests.
                        {' '}&ldquo;Same call&rdquo; = the interval says what every game&apos;s says (both exclude zero on the same side, or both include it).
                        {' '}Dropping games moves a result even when the games are fine (fewer to fit and to score), so each drop set of 50+ games is compared with
                        {' '}{rows.find((x) => x.cell.rand_draws)?.cell.rand_draws ?? 30} random drops of as many games from the same seasons: p is the share of them
                        {' '}moving the difference at least as far. Many drop sets, phases and comparisons are tested on this page, so about 1 in 20 p-values under 0.05
                        {' '}is expected by chance. A class that touches more than half the games is not dropped on its own.
                        {d.result === 'availability' && ' The stored odds are not refitted: only the scored games change, and a kept game\'s expected minutes still come from earlier games, some of them flagged.'}
                        {d.result === 'impact' && scope === 'everywhere' && ' Every model is refitted on the kept stints with the protocol\'s hyperparameters held fixed (λ, prior scale, the tracker\'s five).'}
                    </p>
                </>
            )}
        </>
    );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function DataQuality({ onNavigate }) {
    const params = useInitialParams();
    const [form, setForm] = useState(() => formFromParams(params));
    const [ov, setOv] = useState(null);
    const [error, setError] = useState('');
    const [live, setLive] = useState({});

    useEffect(() => {
        fetchDataQualityOverview()
            .then(setOv)
            .catch((e) => setError(e.response?.data?.detail
                || 'Data Quality couldn\'t load. Is the impact API (port 8002) running, and have scripts/paper_data_audit.py and scripts/build_data_quality.py been run?'));
    }, []);
    // every live check, once per visit, all at once (each is cached by the API after its first run)
    useEffect(() => {
        if (!ov) return undefined;
        let on = true;
        ov.classes.filter((c) => c.live.mode === 'live' && !live[c.key]).forEach((c) => {
            fetchDataQualityCheck(c.key)
                .then((d) => { if (on) setLive((s) => ({ ...s, [c.key]: { data: d } })); })
                .catch((e) => { if (on) setLive((s) => ({ ...s, [c.key]: { error: e.response?.data?.detail || 'check failed' } })); });
        });
        return () => { on = false; };
    }, [ov]); // eslint-disable-line react-hooks/exhaustive-deps

    const cls = ov && form.cls && ov.classes.some((c) => c.key === form.cls && c.per_game) ? form.cls : null;
    useUrlSync(ov && {
        v: form.view === 'classes' ? null : form.view,
        season: form.view === 'games' ? form.season : null, level: form.view === 'games' ? form.level : null,
        cls: form.view === 'games' ? cls : null, team: form.view === 'games' ? form.team : null,
        sort: form.view === 'games' && form.sort !== 'classes' ? form.sort : null, pg: form.view === 'games' && form.pg ? form.pg : null,
        r: form.view === 'matter' && form.result !== 'impact' ? form.result : null, scope: form.view === 'matter' ? form.scope : null,
        ph: form.view === 'matter' ? form.phase : null, pair: form.view === 'matter' ? form.pair : null,
        metric: form.view === 'matter' ? form.metric : null,
    });

    if (error) return <section className="dashboard-card"><p className="error-message">{error}</p></section>;
    if (!ov) return <Loader />;
    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const checked = Object.values(live).filter((x) => x.data);
    const agreeing = checked.filter((x) => x.data.agree === x.data.total).length;
    const lv = Object.fromEntries(ov.levels.map((l) => [l.key, l]));
    const nLive = ov.classes.filter((c) => c.live.mode === 'live').length;

    return (
        <section className="dashboard-card lb-card px-page dq-page">
            <h2 className="card-title hb-page-title">
                Data Quality
                <InfoTooltip label="Where this comes from" title="The audit, live">
                    {'The error classes are the paper\'s data-quality audit (scripts/paper_data_audit.py): every error found in the ESPN play-by-play, the NBA shot chart and the season tables, re-measured from the database. '
                        + 'The live check re-measures each class with independent SQL against the tables now; the build (scripts/build_data_quality.py) re-runs the whole audit and stops if a single stored number has changed. '
                        + 'Each game 2020-21 on carries the classes that touch it, and three headline results are re-scored with the flagged games dropped, against random drops of as many games.'}
                </InfoTooltip>
                <SourceBadge source={ov._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="quality" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                The public feeds&apos; {ov.classes.length} known error classes, each checked again against the tables as they are now, a quality flag on
                every game of the play-by-play era, and whether dropping the flagged games changes what the platform concludes.
            </p>

            <div className="px-stats">
                <Stat k="Live checks" v={checked.length < nLive ? `${checked.length} of ${nLive}…` : `${agreeing} of ${nLive} agree`}
                    tone={checked.length === nLive && agreeing < nLive ? 'warn' : null}
                    sub={`with the audit's numbers now; ${ov.classes.length - nLive} more checked at build${ov.build?.audit_check?.identical ? ` (all ${ov.build.audit_check.rows} audit numbers reproduced)` : ''}`} />
                <Stat k="Games 2020-21 on" v={fmtN(ov.games)} sub={`${fmtN(lv.flagged?.games)} flagged · ${fmtN(lv.excluded?.games)} excluded · ${fmtN(lv.worked_around?.games)} worked around`} />
                {Object.entries(ov.results).map(([k, r]) => {
                    const h = r.headline;
                    if (!h?.full) return null;
                    return (
                        <Stat key={k} k={`Does it matter: ${r.short.toLowerCase()}`}
                            v={`${h.same} of ${h.cells} same`}
                            sub={`calls of ${h.a_label} − ${h.b_label} unchanged by a drop set; ${h.beyond_random} of ${h.controlled} move it more than random drops`} />
                    );
                })}
            </div>

            <div className="pf-toggles dq-tabs" role="tablist" aria-label="View">
                {VIEWS.map((v) => (
                    <button key={v.key} type="button" role="tab" className="pf-pill" aria-selected={form.view === v.key} aria-pressed={form.view === v.key}
                        onClick={() => set({ view: v.key })}>{v.label}</button>
                ))}
            </div>

            {form.view === 'classes' && <ClassesView ov={ov} live={live} onGames={(p) => set({ view: 'games', level: null, team: null, pg: 0, ...p })} />}
            {form.view === 'games' && <GamesView ov={ov} form={{ ...form, cls }} set={set} onNavigate={onNavigate} />}
            {form.view === 'matter' && <MatterView ov={ov} form={form} set={set} />}

            <p className="page-subtitle dq-nof">
                <strong>Not on file.</strong> The season-table classes (a wrong team on a season row, two age conventions) have no per-game flag, and shots
                at (0, 0) single out no game in this era (they are real shots at the rim). Games before 2020-21 have no play-by-play here, so no flag.
                {ov.build?.run?.started && ` Flags and re-scores built ${ov.build.run.started.slice(0, 10)}.`}
            </p>
        </section>
    );
}
