import React, { useEffect, useMemo, useState } from 'react';
import { fetchCustomLeaderboard, fetchLeaderboardOptions } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import CopyLinkButton from '../common/CopyLinkButton';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import CompositeBuilder from './CompositeBuilder';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;

const FORMATS = {
    num1: (v) => v.toFixed(1),
    num2: (v) => v.toFixed(2),
    pct: (v) => `${(v * 100).toFixed(1)}%`,
    signed1: (v) => `${v > 0 ? '+' : ''}${v.toFixed(1)}`,
    int: (v) => v.toFixed(0),
};
const fmt = (format, v) => (v == null ? '—' : FORMATS[format](v));

const CONTEXT_COLUMNS = [
    { key: 'gp', label: 'GP', format: 'int' },
    { key: 'min', label: 'MIN', format: 'num1' },
    { key: 'pts', label: 'PTS', format: 'num1' },
    { key: 'reb', label: 'REB', format: 'num1' },
    { key: 'ast', label: 'AST', format: 'num1' },
    { key: 'ts_pct', label: 'TS%', format: 'pct' },
];
const ATTEMPT_LABELS = { fga: 'FGA', fg3a: '3PA', fta: 'FTA' };
const TOP_N = [10, 25, 50, 100];

// The form from a shared link (utils/useUrlState.js), falling back to the
// defaults for anything missing or no longer valid.
function formFromParams(p, o) {
    const first = o.seasons.from;
    const last = o.seasons.to;
    const stat = o.stats.find((s) => s.key === p.get('stat')) ?? o.stats.find((s) => s.key === 'pts');
    const floor = Math.max(first, stat.first_season);
    const season = (key) => Math.max(floor, parseParam.int(p, key, { min: first, max: last }) ?? last);
    const n = parseParam.int(p, 'n');
    return {
        stat: stat.key,
        from: season('from'),
        to: season('to'),
        minGp: parseParam.int(p, 'gp', { min: 0, max: 82 }) ?? 30,
        minMpg: parseParam.num(p, 'mpg', { min: 0, max: 48 }) ?? 20,
        minAttempts: stat.attempts ? parseParam.num(p, 'att', { min: 0, max: 40 }) : null,
        team: parseParam.oneOf(p, 'team', o.teams.map((t) => t.team)) ?? '',
        order: parseParam.oneOf(p, 'order', ['high', 'low']) ?? '',
        topN: TOP_N.includes(n) ? n : 25,
    };
}

// One-click starting points; every control stays editable afterwards.
const PRESETS = [
    { label: 'Best scoring seasons ever', set: { stat: 'pts', from: 'first', to: 'last', minGp: 40 } },
    { label: 'Sharpest 3-point shooters this season', set: { stat: 'fg3_pct', from: 'last', to: 'last', minAttempts: 5 } },
    { label: 'Highest usage since 2015-16', set: { stat: 'usg_pct', from: 2016, to: 'last' } },
    { label: 'Top BPM seasons since 1973-74', set: { stat: 'bpm', from: 1974, to: 'last', minGp: 50 } },
];

export default function LeaderboardBuilder() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [mode, setMode] = useState(() => (params.get('mode') === 'metric' ? 'composite' : 'single'));

    useEffect(() => {
        fetchLeaderboardOptions()
            .then((o) => {
                setOptions(o);
                setForm(formFromParams(params, o));
            })
            .catch(() => setOptionsError('The leaderboard options couldn\'t load. Is the impact API (port 8002) running?'));
    }, [params]);

    const stat = useMemo(() => options?.stats.find((s) => s.key === form?.stat), [options, form?.stat]);

    // Keep the link in step with the controls. The composite mode writes its
    // own inputs (CompositeBuilder); each mode clears the other's keys.
    useUrlSync(!form ? null : mode === 'composite'
        ? { mode: 'metric', stat: null, att: null, order: null }
        : {
            mode: null, w: null, stat: form.stat, from: form.from, to: form.to,
            gp: form.minGp || 0, mpg: form.minMpg || 0,
            att: stat?.attempts ? form.minAttempts : null,
            team: form.team, order: form.order, n: form.topN,
        });

    // Re-run whenever a control changes (debounced for typing in number boxes).
    useEffect(() => {
        if (!form || !stat || mode !== 'single') return undefined;
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                const res = await fetchCustomLeaderboard({
                    stat: form.stat,
                    season_from: form.from,
                    season_to: form.to,
                    min_gp: form.minGp || 0,
                    min_mpg: form.minMpg || 0,
                    min_attempts: stat.attempts ? (form.minAttempts ?? stat.default_min_attempts) : undefined,
                    team: form.team || undefined,
                    order: form.order || undefined,
                    top_n: form.topN,
                });
                setData(res);
            } catch (err) {
                setData(null);
                setError(err.response?.data?.detail || 'Failed to load the leaderboard.');
            } finally {
                setLoading(false);
            }
        }, 300);
        return () => clearTimeout(timer);
    }, [form, stat, mode]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const first = options.seasons.from;
    const last = options.seasons.to;
    const statFirst = Math.max(first, stat?.first_season ?? first);
    const seasons = [];
    for (let s = last; s >= statFirst; s -= 1) seasons.push(s);
    const groups = [...new Set(options.stats.map((s) => s.group))];
    const teams = options.teams.filter((t) => t.to >= Math.min(form.from, form.to) && t.from <= Math.max(form.from, form.to));
    const defaultOrder = stat?.higher_is_better ? 'high' : 'low';

    const changeStat = (key) => {
        const next = options.stats.find((s) => s.key === key);
        const floor = Math.max(first, next.first_season);
        set({
            stat: key,
            order: '',
            minAttempts: null,
            from: Math.max(form.from, floor),
            to: Math.max(form.to, floor),
        });
    };

    const applyPreset = (p) => {
        const next = options.stats.find((s) => s.key === p.set.stat);
        const resolve = (v) => (v === 'first' ? Math.max(first, next.first_season) : v === 'last' ? last : v);
        setForm({
            stat: p.set.stat, from: resolve(p.set.from), to: resolve(p.set.to),
            minGp: p.set.minGp ?? 30, minMpg: 20, minAttempts: p.set.minAttempts ?? null,
            team: '', order: '', topN: 25,
        });
    };

    const attemptsKey = stat?.attempts;
    const range = data && (data.filters.season_from === data.filters.season_to
        ? seasonLabel(data.filters.season_to)
        : `${seasonLabel(data.filters.season_from)} to ${seasonLabel(data.filters.season_to)}`);

    return (
        <section className="dashboard-card lb-card">
            <div className="tab-bar lb-modes" role="tablist" aria-label="Leaderboard type">
                {[['single', 'Rank by one stat'], ['composite', 'Build your own metric']].map(([id, label]) => (
                    <button
                        key={id} type="button" role="tab" aria-selected={mode === id}
                        className={`tab-btn ${mode === id ? 'tab-btn--active' : ''}`}
                        onClick={() => setMode(id)}
                    >
                        {label}
                    </button>
                ))}
            </div>
            {mode === 'composite' ? (
                <>
                    <h2 className="card-title hb-page-title">Build your own metric<CopyLinkButton /></h2>
                    <CompositeBuilder stats={options.stats} seasons={options.seasons} teams={options.teams} />
                </>
            ) : (
                <>
                    <h2 className="card-title hb-page-title">
                        Build a leaderboard
                        <InfoTooltip label="How the Leaderboard Builder works" title="Under the hood">
                            Ranks single player-seasons from player_season_stats (regular season). Per-game stats unless the
                            name says %, rating or BPM. Each stat starts in the first season it&apos;s recorded for nearly every
                            player (steals and blocks 1973-74, threes 1979-80, net rating and plus-minus 2009-10); a range
                            reaching back further is trimmed and the page says so. Shooting percentages need a minimum number
                            of attempts per game. {options.notes.join(' ')}
                        </InfoTooltip>
                        <SourceBadge source={data?._source ?? options._source} />
                        <CopyLinkButton />
                    </h2>

                    <div className="lb-presets" aria-label="Presets">
                        {PRESETS.map((p) => (
                            <button key={p.label} type="button" onClick={() => applyPreset(p)}>{p.label}</button>
                        ))}
                    </div>

                    <div className="lb-controls">
                        <label>
                            <span>Stat</span>
                            <select className="input-field" value={form.stat} onChange={(e) => changeStat(e.target.value)}>
                                {groups.map((g) => (
                                    <optgroup key={g} label={g}>
                                        {options.stats.filter((s) => s.group === g).map((s) => (
                                            <option key={s.key} value={s.key}>{s.label}</option>
                                        ))}
                                    </optgroup>
                                ))}
                            </select>
                        </label>
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
                            <input className="input-field" type="number" min={0} max={48} step={1} value={form.minMpg}
                                onChange={(e) => set({ minMpg: e.target.value === '' ? '' : Number(e.target.value) })} />
                        </label>
                        {attemptsKey && (
                            <label>
                                <span>Min. {ATTEMPT_LABELS[attemptsKey]} a game</span>
                                <input className="input-field" type="number" min={0} step={0.5}
                                    value={form.minAttempts ?? stat.default_min_attempts}
                                    onChange={(e) => set({ minAttempts: e.target.value === '' ? 0 : Number(e.target.value) })} />
                            </label>
                        )}
                        <label>
                            <span>Team</span>
                            <select className="input-field" value={form.team} onChange={(e) => set({ team: e.target.value })}>
                                <option value="">All teams</option>
                                {teams.map((t) => <option key={t.team} value={t.team}>{t.team}</option>)}
                            </select>
                        </label>
                        <label>
                            <span>Order</span>
                            <select className="input-field" value={form.order || defaultOrder} onChange={(e) => set({ order: e.target.value })}>
                                <option value="high">Highest first</option>
                                <option value="low">Lowest first</option>
                            </select>
                        </label>
                        <label>
                            <span>Show</span>
                            <select className="input-field" value={form.topN} onChange={(e) => set({ topN: Number(e.target.value) })}>
                                {TOP_N.map((n) => <option key={n} value={n}>{n}</option>)}
                            </select>
                        </label>
                    </div>

                    {error && <p className="error-message">{error}</p>}
                    {loading && !data && <Loader />}
                    {data && (
                        <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                            <p className="page-subtitle lb-summary">
                                <strong>{data.stat.label}</strong>, {range}: {data.qualified.toLocaleString()} player-seasons
                                qualify ({data.filters.min_gp}+ games, {data.filters.min_mpg}+ minutes
                                {data.filters.min_attempts ? `, ${data.filters.min_attempts}+ ${ATTEMPT_LABELS[attemptsKey]} a game` : ''}
                                {data.filters.team ? `, ${data.filters.team}` : ''}).
                                {' '}{data.filters.order === 'high' ? 'Highest' : 'Lowest'} first.
                                {data.notes.map((n) => <span key={n} className="lb-note"> {n}</span>)}
                            </p>
                            {data.results.length === 0 ? (
                                <p className="empty-message">No player-seasons pass these filters.</p>
                            ) : (
                                <>
                                    <TableExport name={`${data.stat.label} leaders ${range}`} />
                                    <div className="table-wrapper">
                                        <table className="data-table lb-table">
                                            <thead>
                                                <tr>
                                                    <th>#</th>
                                                    <th>Player</th>
                                                    <th>Season</th>
                                                    <th>Team</th>
                                                    <th className="lb-num lb-stat">{data.stat.label}</th>
                                                    {data.stat.attempts && <th className="lb-num">{ATTEMPT_LABELS[data.stat.attempts]}</th>}
                                                    {CONTEXT_COLUMNS.filter((c) => c.key !== data.stat.key).map((c) => (
                                                        <th key={c.key} className="lb-num">{c.label}</th>
                                                    ))}
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {data.results.map((r) => (
                                                    <tr key={`${r.player_id}-${r.season}`}>
                                                        <td>{r.rank}</td>
                                                        <td><PlayerName playerId={r.player_id} name={r.player_name} /></td>
                                                        <td>{seasonLabel(r.season)}</td>
                                                        <td>{r.team}</td>
                                                        <td className="lb-num lb-stat">{fmt(data.stat.format, r.value)}</td>
                                                        {data.stat.attempts && <td className="lb-num">{fmt('num1', r.context[data.stat.attempts])}</td>}
                                                        {CONTEXT_COLUMNS.filter((c) => c.key !== data.stat.key).map((c) => (
                                                            <td key={c.key} className="lb-num">{fmt(c.format, r.context[c.key])}</td>
                                                        ))}
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </>
                            )}
                        </div>
                    )}
                </>
            )}
        </section>
    );
}
