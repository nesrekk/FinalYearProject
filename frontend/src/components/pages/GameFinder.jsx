import React, { useEffect, useRef, useState } from 'react';
import { fetchGameFinder, fetchGameFinderOptions, fetchGameFinderPlayers } from '../../services/api';
import Loader from '../Loader';
import AutocompleteDropdown from '../common/AutocompleteDropdown';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLogo from '../common/TeamLogo';
import TeamLink from '../common/TeamLink';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import { signed } from '../../utils/format';
import '../../styles/gamelog.css';

// Game Finder (?page=gamefinder): every regular-season player-game 2020-21 on
// (player_game_lines via GET /games/finder), filtered by stat conditions, or
// the longest runs of games that all meet them. Inputs live in the link:
// f=pts:gte:30,fga:lt:15 (shooting % as shares, as stored), mode, from, to,
// team, home, result, min, player, sort, order, one, n, pg.

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
const day = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US',
    { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
const OP_TEXT = { gte: '≥', gt: '>', lte: '≤', lt: '<', eq: '=' };
const PAGE_SIZES = [25, 50, 100, 200];
// Typed values are what people write (60 for 60%); the API and link use shares.
const toInput = (format, v) => String(format === 'pct' ? +(v * 100).toFixed(2) : v);
const fromInput = (format, s) => (s === '' || !Number.isFinite(Number(s)) ? null
    : format === 'pct' ? Number(s) / 100 : Number(s));

const PRESETS = [
    { label: '30+ points on under 15 shots', mode: 'games', conds: [['pts', 'gte', 30], ['fga', 'lt', 15]], sort: 'pts' },
    { label: 'Triple-doubles', mode: 'games', conds: [['pts', 'gte', 10], ['reb', 'gte', 10], ['ast', 'gte', 10]], sort: 'pts' },
    { label: '20-20 games', mode: 'games', conds: [['pts', 'gte', 20], ['reb', 'gte', 20]], sort: 'reb' },
    { label: '10+ threes', mode: 'games', conds: [['fg3m', 'gte', 10]], sort: 'fg3m' },
    { label: 'Streak: 25+ points', mode: 'streaks', conds: [['pts', 'gte', 25]], sort: 'pts' },
    { label: 'Streak: double-doubles (pts + reb)', mode: 'streaks', conds: [['pts', 'gte', 10], ['reb', 'gte', 10]], sort: 'pts' },
];

// Game-table columns: [sort key, header, cell]
const GAME_COLS = [
    ['min', 'MIN', (r) => r.min.toFixed(1)],
    ['pts', 'PTS', (r) => r.pts],
    ['reb', 'REB', (r) => r.reb],
    ['ast', 'AST', (r) => r.ast],
    ['stl', 'STL', (r) => r.stl],
    ['blk', 'BLK', (r) => r.blk],
    ['tov', 'TOV', (r) => r.tov],
    ['fga', 'FG', (r) => `${r.fgm}-${r.fga}`],
    ['fg3m', '3P', (r) => `${r.fg3m}-${r.fg3a}`],
    ['fta', 'FT', (r) => `${r.ftm}-${r.fta}`],
    ['ts_pct', 'TS%', (r) => pct(r.ts_pct)],
    // On the floor, from the five-man stints; blank in the 12 games that don't reconcile.
    ['plus_minus', '+/-', (r) => signed(r.plus_minus, 0)],
];
// Which condition stats light up which game column.
const COL_OF = { fgm: 'fga', fg_pct: 'fga', fg3a: 'fg3m', fg3_pct: 'fg3m', ftm: 'fta', ft_pct: 'fta', oreb: 'reb', dreb: 'reb' };

function formFromParams(p, o) {
    const byKey = Object.fromEntries(o.stats.map((s) => [s.key, s]));
    const range = { min: o.seasons.from, max: o.seasons.to };
    const conds = (parseParam.list(p, 'f') ?? [])
        .map((part) => part.split(':'))
        .filter(([k, op, v]) => byKey[k] && OP_TEXT[op] && v !== '' && Number.isFinite(Number(v)))
        .slice(0, o.max_conditions)
        .map(([k, op, v]) => ({ key: k, op, value: toInput(byKey[k].format, Number(v)) }));
    const preset = PRESETS[0];
    const n = parseParam.int(p, 'n');
    const player = parseParam.int(p, 'player', { min: 1 });
    return {
        mode: parseParam.oneOf(p, 'mode', ['games', 'streaks']) ?? (conds.length ? 'games' : preset.mode),
        conds: conds.length ? conds : preset.conds.map(([key, op, v]) => ({ key, op, value: toInput(byKey[key].format, v) })),
        from: parseParam.int(p, 'from', range),
        to: parseParam.int(p, 'to', range),
        team: parseParam.oneOf(p, 'team', o.teams),
        home: parseParam.oneOf(p, 'home', ['home', 'away']),
        result: parseParam.oneOf(p, 'result', ['W', 'L']),
        minMinutes: parseParam.num(p, 'min', { min: 0, max: 60 }) ?? 0,
        player: player ? { id: player, name: null } : null,
        sort: parseParam.oneOf(p, 'sort', o.sorts) ?? (conds.length ? 'pts' : preset.sort),
        order: parseParam.oneOf(p, 'order', ['asc', 'desc']) ?? 'desc',
        onePerPlayer: p.get('one') !== '0',
        pageSize: PAGE_SIZES.includes(n) ? n : 50,
        page: parseParam.int(p, 'pg', { min: 0 }) ?? 0,
    };
}

function PlayerSearch({ onPick }) {
    const inputRef = useRef(null);
    const [q, setQ] = useState('');
    const [hits, setHits] = useState([]);
    useEffect(() => {
        const query = q.trim();
        if (query.length < 2) return undefined;
        let live = true;
        const timer = setTimeout(() => {
            fetchGameFinderPlayers(query)
                .then((d) => { if (live) setHits(d.results); })
                .catch(() => { if (live) setHits([]); });
        }, 200);
        return () => { live = false; clearTimeout(timer); };
    }, [q]);
    const shown = q.trim().length >= 2 ? hits : [];
    const labels = shown.map((p) => `${p.player_name} (${p.games} games, ${seasonLabel(p.from)}${p.to !== p.from ? ` to ${seasonLabel(p.to)}` : ''})`);
    return (
        <>
            <input ref={inputRef} className="input-field" type="search" value={q} placeholder="Any player"
                aria-label="Filter to one player" autoComplete="off" onChange={(e) => setQ(e.target.value)} />
            <AutocompleteDropdown anchorRef={inputRef} items={labels}
                onPick={(l) => {
                    const p = shown[labels.indexOf(l)];
                    if (p) onPick({ id: p.player_id, name: p.player_name });
                    setQ('');
                    setHits([]);
                }} />
        </>
    );
}

export default function GameFinder() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        fetchGameFinderOptions()
            .then((o) => { setOptions(o); setForm(formFromParams(params, o)); })
            .catch(() => setOptionsError('The Game Finder couldn\'t load. Is the impact API (port 8002) running?'));
    }, [params]);

    const byKey = options ? Object.fromEntries(options.stats.map((s) => [s.key, s])) : {};
    const complete = form ? form.conds
        .map((c) => [c.key, c.op, fromInput(byKey[c.key].format, c.value)])
        .filter(([, , v]) => v != null) : [];
    const fParam = complete.map(([k, op, v]) => `${k}:${op}:${+v.toFixed(4)}`).join(',');

    useUrlSync(form && {
        mode: form.mode === 'games' ? null : form.mode, f: fParam, from: form.from, to: form.to, team: form.team,
        home: form.home, result: form.result, min: form.minMinutes || null, player: form.player?.id,
        sort: form.mode === 'games' ? form.sort : null, order: form.mode === 'games' && form.order === 'asc' ? 'asc' : null,
        one: form.mode === 'streaks' && !form.onePerPlayer ? 0 : null, n: form.pageSize === 50 ? null : form.pageSize,
        pg: form.page || null,
    });

    useEffect(() => {
        if (!options || !form) return undefined;
        const timer = setTimeout(async () => {
            if (form.mode === 'streaks' && !fParam) {
                setData(null);
                setError('A streak needs at least one condition.');
                return;
            }
            setLoading(true);
            setError('');
            try {
                const d = await fetchGameFinder({
                    f: fParam, mode: form.mode, season_from: form.from ?? undefined, season_to: form.to ?? undefined,
                    team: form.team ?? undefined, home: form.home ?? undefined, result: form.result ?? undefined,
                    min_minutes: form.minMinutes || 0, player_id: form.player?.id, sort: form.sort, order: form.order,
                    one_per_player: form.onePerPlayer, limit: form.pageSize, offset: form.page * form.pageSize,
                });
                setData(d);
                // A player id from a link: show his name once the API has it.
                if (form.player && !form.player.name && d.filters.player_name) {
                    setForm((f) => ({ ...f, player: { id: f.player.id, name: d.filters.player_name } }));
                }
            } catch (err) {
                setData(null);
                setError(err.response?.data?.detail || 'The search failed.');
            } finally {
                setLoading(false);
            }
        }, 350);
        return () => clearTimeout(timer);
        // form.player?.name only fills in a label; it must not trigger a refetch.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [options, fParam, form?.mode, form?.from, form?.to, form?.team, form?.home, form?.result, form?.minMinutes,
        form?.player?.id, form?.sort, form?.order, form?.onePerPlayer, form?.pageSize, form?.page]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    // Any change but paging goes back to the first page.
    const set = (patch) => setForm((f) => ({ ...f, page: 0, ...patch }));
    const setCond = (i, patch) => set({ conds: form.conds.map((c, j) => (j === i ? { ...c, ...patch } : c)) });
    const applyPreset = (p) => set({
        mode: p.mode, sort: p.sort, order: 'desc',
        conds: p.conds.map(([key, op, v]) => ({ key, op, value: toInput(byKey[key].format, v) })),
    });
    const seasons = [];
    for (let s = options.seasons.to; s >= options.seasons.from; s -= 1) seasons.push(s);
    const hitCols = new Set(complete.map(([k]) => COL_OF[k] || k));
    const sortBy = (key) => set({ sort: key, order: form.sort === key && form.order === 'desc' ? 'asc' : 'desc' });
    const sortHeader = (key, text) => (
        <button type="button" className="gf-sort" onClick={() => sortBy(key)}
            aria-sort={form.sort === key ? (form.order === 'desc' ? 'descending' : 'ascending') : undefined}>
            {text}{form.sort === key ? (form.order === 'desc' ? ' ↓' : ' ↑') : ''}
        </button>
    );
    const condText = complete.length
        ? complete.map(([k, op, v]) => `${byKey[k].label} ${OP_TEXT[op]} ${byKey[k].format === 'pct' ? pct(v) : v}`).join(' and ')
        : 'no stat conditions';
    const first = data ? data.offset + 1 : 0;
    const last = data ? data.offset + data.results.length : 0;
    const range = data ? `${seasonLabel(data.filters.season_from)} to ${seasonLabel(data.filters.season_to)}` : '';

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                Find games
                <InfoTooltip label="Where the games come from" title="Rebuilt from play-by-play">
                    {`${options.notes.coverage} ${options.notes.accuracy} ${options.notes.left_out}`}
                </InfoTooltip>
                <SourceBadge source={data?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="gamefinder" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Every regular-season game any player played from {seasonLabel(options.seasons.from)} to{' '}
                {seasonLabel(options.seasons.to)} ({options.lines.toLocaleString()} player-games). Set conditions to list the
                games that meet all of them, or switch to streaks for the longest runs of games in a row that do. Earlier
                seasons aren&apos;t here: there is no game-level data before {seasonLabel(options.seasons.from)}.
            </p>

            <div className="lb-presets sl-examples" aria-label="Examples">
                {PRESETS.map((p) => <button key={p.label} type="button" onClick={() => applyPreset(p)}>{p.label}</button>)}
            </div>

            <div className="tab-bar lb-modes" role="tablist" aria-label="Result type" style={{ marginTop: 'var(--space-4)' }}>
                {[['games', 'Matching games'], ['streaks', 'Streaks']].map(([id, text]) => (
                    <button key={id} type="button" role="tab" aria-selected={form.mode === id}
                        className={`tab-btn ${form.mode === id ? 'tab-btn--active' : ''}`} onClick={() => set({ mode: id })}>
                        {text}
                    </button>
                ))}
            </div>

            <fieldset className="gf-conds">
                <legend>Conditions: all must hold ({form.conds.length} of up to {options.max_conditions})</legend>
                {form.conds.map((c, i) => {
                    const s = byKey[c.key];
                    return (
                        <div className="gf-cond" key={i}>
                            <select className="input-field" aria-label="Stat" value={c.key}
                                onChange={(e) => {
                                    const next = byKey[e.target.value];
                                    const was = fromInput(s.format, c.value);
                                    setCond(i, { key: next.key, value: was == null || next.format === s.format ? c.value : toInput(next.format, was) });
                                }}>
                                {options.stats.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                            </select>
                            <select className="input-field" aria-label="Comparison" value={c.op} onChange={(e) => setCond(i, { op: e.target.value })}>
                                {Object.entries(OP_TEXT).map(([op, t]) => <option key={op} value={op}>{t}</option>)}
                            </select>
                            <input className="input-field" type="number" inputMode="decimal" step="any" aria-label={`${s.label} value`}
                                value={c.value} onChange={(e) => setCond(i, { value: e.target.value })}
                                placeholder={s.format === 'pct' ? '%' : ''} />
                            <button type="button" className="cb-remove" aria-label={`Remove ${s.label} condition`}
                                onClick={() => set({ conds: form.conds.filter((_, j) => j !== i) })}>×</button>
                        </div>
                    );
                })}
                {form.conds.length < options.max_conditions && (
                    <select className="input-field gf-cond-add" aria-label="Add a condition" value=""
                        onChange={(e) => e.target.value && set({ conds: [...form.conds, { key: e.target.value, op: 'gte', value: '' }] })}>
                        <option value="">+ Add a condition…</option>
                        {options.stats.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
                    </select>
                )}
                {complete.some(([k]) => byKey[k].format === 'pct') && (
                    <p className="page-subtitle gf-note">
                        Percentages are typed as percent (60 = 60%). A game with no attempts has no percentage and never matches;
                        pair a percentage with an attempts condition to skip 1-for-1 nights.
                    </p>
                )}
            </fieldset>

            <div className="lb-controls">
                <label className="gf-player">
                    <span>Player</span>
                    {form.player ? (
                        <span className="gf-player-chip">
                            <PlayerName playerId={form.player.id} name={form.player.name || `Player ${form.player.id}`} size={24} />
                            <button type="button" className="cb-remove" aria-label="Any player" onClick={() => set({ player: null })}>×</button>
                        </span>
                    ) : <PlayerSearch onPick={(p) => set({ player: p })} />}
                </label>
                <label>
                    <span>From</span>
                    <select className="input-field" value={form.from ?? ''} onChange={(e) => set({ from: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">{seasonLabel(options.seasons.from)}</option>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>To</span>
                    <select className="input-field" value={form.to ?? ''} onChange={(e) => set({ to: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">{seasonLabel(options.seasons.to)}</option>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>Team</span>
                    <select className="input-field" value={form.team ?? ''} onChange={(e) => set({ team: e.target.value || null })}>
                        <option value="">Any team</option>
                        {options.teams.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                </label>
                <label>
                    <span>Home / away</span>
                    <select className="input-field" value={form.home ?? ''} onChange={(e) => set({ home: e.target.value || null })}>
                        <option value="">Both</option><option value="home">Home</option><option value="away">Away</option>
                    </select>
                </label>
                <label>
                    <span>Team result</span>
                    <select className="input-field" value={form.result ?? ''} onChange={(e) => set({ result: e.target.value || null })}>
                        <option value="">Either</option><option value="W">Win</option><option value="L">Loss</option>
                    </select>
                </label>
                <label>
                    <span>Min. minutes</span>
                    <input className="input-field" type="number" min={0} max={60} value={form.minMinutes}
                        onChange={(e) => set({ minMinutes: e.target.value === '' ? 0 : Math.max(0, Math.min(60, Number(e.target.value))) })} />
                </label>
                <label>
                    <span>Per page</span>
                    <select className="input-field" value={form.pageSize} onChange={(e) => set({ pageSize: Number(e.target.value) })}>
                        {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                </label>
            </div>
            {form.mode === 'streaks' && (
                <>
                    <div className="sim-filters">
                        <label>
                            <input type="checkbox" checked={form.onePerPlayer} onChange={(e) => set({ onePerPlayer: e.target.checked })} />
                            Longest streak per player only
                        </label>
                    </div>
                    <p className="page-subtitle gf-note">
                        A streak is games in a row, among the games he played that pass the filters above (seasons, team,
                        home/away, result, minutes), that all meet the conditions. Games he sat out don&apos;t break it, and it
                        can carry over from one season to the next.
                    </p>
                </>
            )}

            {error && <p className="error-message">{error}</p>}
            {loading && !data && <Loader />}
            {data && (
                <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                    {data.mode === 'games' ? (
                        <>
                            <p className="page-subtitle lb-summary">
                                <strong>{data.total.toLocaleString()}</strong> of {data.pool_games.toLocaleString()} player-games
                                ({data.pool_players.toLocaleString()} player{data.pool_players === 1 ? '' : 's'}, {range}) meet {condText}.
                            </p>
                            {data.most_games.length > 0 && !data.filters.player_id && (
                                <p className="gf-most">
                                    Most such games: {data.most_games.slice(0, 5).map((p, i) => (
                                        <React.Fragment key={p.player_id}>
                                            {i > 0 && ', '}<strong>{p.player_name}</strong> {p.games}
                                        </React.Fragment>
                                    ))}.
                                </p>
                            )}
                            {data.results.length === 0 ? <p className="empty-message">No games meet these conditions.</p> : (
                                <>
                                    <TableExport name={`games ${fParam || 'all'}`} />
                                    <div className="table-wrapper">
                                        <table className="data-table lb-table gl-table">
                                            <thead>
                                                <tr>
                                                    <th>#</th><th>Player</th><th>{sortHeader('date', 'Date')}</th><th>Team</th><th>Opp</th>
                                                    <th>{sortHeader('margin', 'Result')}</th>
                                                    {GAME_COLS.map(([k, h]) => (
                                                        <th key={k} className={`lb-num${hitCols.has(k) ? ' gf-hit' : ''}`}>{sortHeader(k, h)}</th>
                                                    ))}
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {data.results.map((r, i) => (
                                                    <tr key={`${r.player_id}-${r.date}`}>
                                                        <td>{data.offset + i + 1}</td>
                                                        <td><PlayerName playerId={r.player_id} name={r.player_name} size={24} /></td>
                                                        <td>{day(r.date)}</td>
                                                        <td><TeamLink abbr={r.team} season={r.season}><span>{r.team}</span></TeamLink></td>
                                                        <td><span className="gl-opp">{r.home ? 'vs' : '@'} <TeamLink abbr={r.opponent} season={r.season} logoSize={18} /></span></td>
                                                        <td className={r.win ? 'pp-pos' : 'pp-neg'}>{r.win ? 'W' : 'L'} {r.margin > 0 ? '+' : r.margin < 0 ? '−' : ''}{Math.abs(r.margin)}</td>
                                                        {GAME_COLS.map(([k, , cell]) => (
                                                            <td key={k} className={`lb-num${hitCols.has(k) ? ' gf-hit' : ''}`}>{cell(r)}</td>
                                                        ))}
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                </>
                            )}
                        </>
                    ) : (
                        <>
                            <p className="page-subtitle lb-summary">
                                <strong>{data.total.toLocaleString()}</strong> {data.one_per_player ? 'players have' : 'runs of'}{' '}
                                {data.min_streak}+ games in a row meeting {condText} ({range}).
                            </p>
                            {data.results.length === 0 ? <p className="empty-message">No streaks of {data.min_streak}+ games.</p> : (
                                <>
                                    <TableExport name={`streaks ${fParam}`} />
                                    <div className="table-wrapper">
                                        <table className="data-table lb-table gl-table">
                                            <thead>
                                                <tr>
                                                    <th>#</th><th>Player</th><th className="lb-num gf-hit">Games</th><th>From</th><th>To</th><th>Team</th>
                                                    <th className="lb-num">PTS</th><th className="lb-num">REB</th><th className="lb-num">AST</th>
                                                    <th className="lb-num">MIN</th><th className="lb-num">TS%</th><th className="lb-num">+/-</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {data.results.map((r, i) => (
                                                    <tr key={`${r.player_id}-${r.start_date}`}>
                                                        <td>{data.offset + i + 1}</td>
                                                        <td><PlayerName playerId={r.player_id} name={r.player_name} size={24} /></td>
                                                        <td className="lb-num lb-stat">
                                                            {r.games}
                                                            {r.active && <span className="pp-tag gf-active" title="Still going at his last game on file">Active</span>}
                                                        </td>
                                                        <td>{day(r.start_date)}</td>
                                                        <td>{day(r.end_date)}</td>
                                                        <td>{r.teams.join(', ')}</td>
                                                        <td className="lb-num">{r.averages.pts.toFixed(1)}</td>
                                                        <td className="lb-num">{r.averages.reb.toFixed(1)}</td>
                                                        <td className="lb-num">{r.averages.ast.toFixed(1)}</td>
                                                        <td className="lb-num">{r.averages.min.toFixed(1)}</td>
                                                        <td className="lb-num">{pct(r.averages.ts_pct)}</td>
                                                        <td className="lb-num">{signed(r.averages.plus_minus, 1)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>
                                    <p className="page-subtitle pp-foot">Averages are per game over the streak. Active = still going at his last game on file ({options.last_date}).</p>
                                </>
                            )}
                        </>
                    )}
                    {data.total > data.limit && (
                        <div className="gf-pager">
                            <button type="button" className="action-btn" disabled={form.page === 0}
                                onClick={() => setForm((f) => ({ ...f, page: f.page - 1 }))}>← Previous</button>
                            <span>{first.toLocaleString()}–{last.toLocaleString()} of {data.total.toLocaleString()}</span>
                            <button type="button" className="action-btn" disabled={last >= data.total}
                                onClick={() => setForm((f) => ({ ...f, page: f.page + 1 }))}>Next →</button>
                            <span>(Export saves the rows on this page.)</span>
                        </div>
                    )}
                    <p className="page-subtitle pp-foot">{data.notes.plus_minus} {data.notes.accuracy} {data.notes.left_out}</p>
                </div>
            )}
        </section>
    );
}
