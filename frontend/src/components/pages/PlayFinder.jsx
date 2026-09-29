import React, { useEffect, useRef, useState } from 'react';
import { fetchGameFinderPlayers, fetchPlayFinder, fetchPlayFinderOptions } from '../../services/api';
import Loader from '../Loader';
import AutocompleteDropdown from '../common/AutocompleteDropdown';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import { isPlainClick, pageHref, parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/gamelog.css';
import '../../styles/playfinder.css';

// Play Finder (?page=plays): every play of every regular-season game 2020-21
// on (GET /plays/finder over play_finder_events), filtered by player, play,
// season/date, team, period, clock, score and distance. Each result opens
// Game Replay at that moment (?page=analytics&game=&t=&ev=#replay).
// Inputs in the link: player, cat, from, to, df, dt, game, team, opp, home,
// per (1,2,3,4,ot), cmin/cmax (seconds left in the period), mmin/mmax (the
// player's team's lead before the play), clutch, dmin/dmax (feet), sort, n, pg.

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const day = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US',
    { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
const PAGE_SIZES = [25, 50, 100, 200];
const PERIOD_TEXT = { 1: 'Q1', 2: 'Q2', 3: 'Q3', 4: 'Q4', ot: 'OT' };
const SORT_TEXT = { newest: 'Newest first', oldest: 'Oldest first', dist: 'Longest shots first' };
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
// Clock windows: [label, cmin, cmax] in seconds left in the period.
const CLOCKS = [
    ['Any time', null, null],
    ['Last 5:00', null, 300],
    ['Last 2:00', null, 120],
    ['Last minute', null, 60],
    ['Last 24 seconds', null, 24],
    ['Last 5 seconds', null, 5],
    ['First 2:00 (of a quarter)', 600, null],
];

const PRESETS = [
    { label: "Bam Adebayo's 83 (Mar 10, 2026)", player: { id: 1628389, name: 'Bam Adebayo' }, df: '2026-03-10', dt: '2026-03-10', sort: 'oldest' },
    { label: 'Clutch threes, 2025-26', cat: 'made3', clutch: true, from: 2026, to: 2026 },
    { label: 'Makes in the last 3 seconds of Q4/OT, down 1-3 or tied', cat: 'made', per: ['4', 'ot'], cmax: 3, mmin: -3, mmax: 0 },
    { label: 'Made shots from 40+ feet', cat: 'made', dmin: 40, sort: 'dist' },
    { label: "Jokić's assists on threes, 2023-24", player: { id: 203999, name: 'Nikola Jokić' }, cat: 'ast3', from: 2024, to: 2024 },
];

const EMPTY = {
    player: null, cat: '', from: null, to: null, df: null, dt: null, game: null, team: null, opp: null, home: null,
    per: [], cmin: null, cmax: null, mmin: null, mmax: null, clutch: false, dmin: null, dmax: null, sort: 'newest',
};

function formFromParams(p, o) {
    const range = { min: o.seasons.from, max: o.seasons.to };
    const player = parseParam.int(p, 'player', { min: 1 });
    const date = (k) => { const v = parseParam.str(p, k); return v && DATE_RE.test(v) ? v : null; };
    const n = parseParam.int(p, 'n');
    return {
        ...EMPTY,
        player: player ? { id: player, name: null } : null,
        cat: parseParam.oneOf(p, 'cat', o.categories.map((c) => c.key)) ?? '',
        from: parseParam.int(p, 'from', range),
        to: parseParam.int(p, 'to', range),
        df: date('df'),
        dt: date('dt'),
        game: parseParam.str(p, 'game'),
        team: parseParam.oneOf(p, 'team', o.teams),
        opp: parseParam.oneOf(p, 'opp', o.teams),
        home: parseParam.oneOf(p, 'home', ['home', 'away']),
        per: (parseParam.list(p, 'per') ?? []).filter((x) => o.periods.includes(x)),
        cmin: parseParam.num(p, 'cmin', { min: 0, max: 720 }),
        cmax: parseParam.num(p, 'cmax', { min: 0, max: 720 }),
        mmin: parseParam.int(p, 'mmin', { min: -80, max: 80 }),
        mmax: parseParam.int(p, 'mmax', { min: -80, max: 80 }),
        clutch: p.get('clutch') === '1',
        dmin: parseParam.int(p, 'dmin', { min: 0, max: 94 }),
        dmax: parseParam.int(p, 'dmax', { min: 0, max: 94 }),
        sort: parseParam.oneOf(p, 'sort', o.sorts) ?? 'newest',
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
    const labels = shown.map((p) => `${p.player_name} (${seasonLabel(p.from)}${p.to !== p.from ? ` to ${seasonLabel(p.to)}` : ''})`);
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

// A number input that keeps what's typed and reports a number or null.
function NumberField({ label, value, onChange, min, max, placeholder }) {
    return (
        <label>
            <span>{label}</span>
            <input className="input-field" type="number" inputMode="numeric" min={min} max={max} placeholder={placeholder}
                value={value ?? ''} onChange={(e) => {
                    const v = e.target.value;
                    if (v === '' || !Number.isFinite(Number(v))) onChange(null);
                    else onChange(Math.max(min, Math.min(max, Math.round(Number(v)))));
                }} />
        </label>
    );
}

function replayParams(r) {
    return { game: r.game_id, t: r.seconds_elapsed, ev: r.event_id };
}

function PlayRow({ r, index, onNavigate }) {
    const params = replayParams(r);
    const href = `${pageHref('analytics', params)}#replay`;
    const [sf, sa] = r.score_after;
    return (
        <tr>
            <td>{index}</td>
            <td className="pf-nowrap">{day(r.date)}</td>
            <td className="pf-nowrap">{r.clock}{r.clutch && <span className="pf-tag" title="Clutch: final 5 minutes of Q4/OT, within 5 before the play">Clutch</span>}</td>
            <td>
                {r.identified ? <PlayerName playerId={r.player_id} name={r.player_name} size={22} />
                    : <span className="pf-unid" title={r.team_play ? 'A team play: no player' : 'ESPN gives this player no id'}>
                        {r.player_name || 'Unidentified'}{!r.team_play && r.player_name ? ' (no id)' : ''}
                    </span>}
            </td>
            <td className="pf-nowrap">
                <TeamLink abbr={r.team} season={r.season} logoSize={18} />
                <span className="pf-vs">{r.home ? 'vs' : '@'}</span>
                <TeamLink abbr={r.opponent} season={r.season} logoSize={18} />
            </td>
            <td className="pf-nowrap"><span className={`pf-cat pf-cat--${r.cat}`}>{r.cat_label}</span></td>
            <td className="pf-desc">{r.description}</td>
            <td className="lb-num pf-nowrap" title={`Before the play: ${r.score_before[0]}–${r.score_before[1]}`}>
                <span className={sf > sa ? 'pf-pos' : sf < sa ? 'pf-neg' : ''}>{sf}–{sa}</span>
            </td>
            <td className="lb-num">{r.dist == null ? '—' : r.dist}</td>
            <td>
                <a className="pf-replay" href={href}
                    onClick={(e) => { if (isPlainClick(e)) { e.preventDefault(); onNavigate('analytics', 'replay', params); } }}>
                    Replay
                </a>
            </td>
        </tr>
    );
}

export default function PlayFinder({ onNavigate }) {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        fetchPlayFinderOptions()
            .then((o) => { setOptions(o); setForm(formFromParams(params, o)); })
            .catch(() => setOptionsError('The Play Finder couldn\'t load. Is the impact API (port 8002) running?'));
    }, [params]);

    useUrlSync(form && {
        player: form.player?.id, cat: form.cat || null, from: form.from, to: form.to, df: form.df, dt: form.dt,
        game: form.game, team: form.team, opp: form.opp, home: form.home, per: form.per, cmin: form.cmin, cmax: form.cmax,
        mmin: form.mmin, mmax: form.mmax, clutch: form.clutch ? 1 : null, dmin: form.dmin, dmax: form.dmax,
        sort: form.sort === 'newest' ? null : form.sort, n: form.pageSize === 50 ? null : form.pageSize, pg: form.page || null,
    });

    const perKey = form?.per.join(',');
    useEffect(() => {
        if (!options || !form) return undefined;
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                const d = await fetchPlayFinder({
                    player_id: form.player?.id, cat: form.cat || undefined, season_from: form.from ?? undefined,
                    season_to: form.to ?? undefined, date_from: form.df ?? undefined, date_to: form.dt ?? undefined,
                    game: form.game ?? undefined, team: form.team ?? undefined, opp: form.opp ?? undefined,
                    home: form.home ?? undefined, period: perKey || undefined, clock_min: form.cmin ?? undefined,
                    clock_max: form.cmax ?? undefined, margin_min: form.mmin ?? undefined, margin_max: form.mmax ?? undefined,
                    clutch: form.clutch || undefined, dist_min: form.dmin ?? undefined, dist_max: form.dmax ?? undefined,
                    sort: form.sort, limit: form.pageSize, offset: form.page * form.pageSize,
                });
                setData(d);
                if (form.player && !form.player.name && d.filters.player_name) {
                    setForm((f) => ({ ...f, player: { id: f.player.id, name: d.filters.player_name } }));
                }
            } catch (err) {
                setData(null);
                const detail = err.response?.data?.detail;
                setError(typeof detail === 'string' ? detail : 'The search failed. Check the filters.');
            } finally {
                setLoading(false);
            }
        }, 350);
        return () => clearTimeout(timer);
        // form.player?.name only fills in a label; it must not trigger a refetch.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [options, form?.player?.id, form?.cat, form?.from, form?.to, form?.df, form?.dt, form?.game, form?.team, form?.opp,
        form?.home, perKey, form?.cmin, form?.cmax, form?.mmin, form?.mmax, form?.clutch, form?.dmin, form?.dmax, form?.sort,
        form?.pageSize, form?.page]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, page: 0, ...patch }));
    const applyPreset = (p) => set({ ...EMPTY, ...p, per: p.per ?? [] });
    const seasons = [];
    for (let s = options.seasons.to; s >= options.seasons.from; s -= 1) seasons.push(s);
    const clockIdx = CLOCKS.findIndex(([, a, b]) => a === form.cmin && b === form.cmax);
    const togglePeriod = (p) => set({ per: form.per.includes(p) ? form.per.filter((x) => x !== p) : [...form.per, p] });
    const first = data ? data.offset + 1 : 0;
    const last = data ? data.offset + data.results.length : 0;
    const maxPage = data ? Math.min(Math.floor((data.total - 1) / data.limit), Math.floor(data.max_offset / data.limit)) : 0;
    const gameRow = form.game && data?.results[0];

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                Find plays
                <InfoTooltip label="Where the plays come from" title="Every play since 2020-21">
                    {`${options.notes.coverage} ${options.notes.accuracy}`}
                </InfoTooltip>
                <SourceBadge source={data?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="plays" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Every shot, assist, block, steal, turnover, foul, free throw and rebound of every regular-season game from{' '}
                {seasonLabel(options.seasons.from)} to {seasonLabel(options.seasons.to)}, from the play-by-play. Filter by
                player, play, date, team, quarter, clock, score and shot distance; every result opens Game Replay at that
                moment. Earlier seasons aren&apos;t here: there is no play-by-play before {seasonLabel(options.seasons.from)}.
            </p>

            <div className="lb-presets sl-examples" aria-label="Examples">
                {PRESETS.map((p) => <button key={p.label} type="button" onClick={() => applyPreset(p)}>{p.label}</button>)}
            </div>

            <div className="lb-controls pf-controls">
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
                    <span>Play</span>
                    <select className="input-field" value={form.cat} onChange={(e) => set({ cat: e.target.value })}>
                        <option value="">Any play</option>
                        {options.categories.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                    </select>
                </label>
                <label>
                    <span>From season</span>
                    <select className="input-field" value={form.from ?? ''} onChange={(e) => set({ from: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">{seasonLabel(options.seasons.from)}</option>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>To season</span>
                    <select className="input-field" value={form.to ?? ''} onChange={(e) => set({ to: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">{seasonLabel(options.seasons.to)}</option>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>From date</span>
                    <input className="input-field" type="date" min={options.dates.from} max={options.dates.to} value={form.df ?? ''}
                        onChange={(e) => set({ df: e.target.value || null })} />
                </label>
                <label>
                    <span>To date</span>
                    <input className="input-field" type="date" min={options.dates.from} max={options.dates.to} value={form.dt ?? ''}
                        onChange={(e) => set({ dt: e.target.value || null })} />
                </label>
                <label>
                    <span>Team</span>
                    <select className="input-field" value={form.team ?? ''} onChange={(e) => set({ team: e.target.value || null })}>
                        <option value="">Any team</option>
                        {options.teams.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                </label>
                <label>
                    <span>Opponent</span>
                    <select className="input-field" value={form.opp ?? ''} onChange={(e) => set({ opp: e.target.value || null })}>
                        <option value="">Any opponent</option>
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
                    <span>Clock (left in period)</span>
                    <select className="input-field" value={clockIdx === -1 ? 'custom' : String(clockIdx)}
                        onChange={(e) => { const c = CLOCKS[Number(e.target.value)]; if (c) set({ cmin: c[1], cmax: c[2] }); }}>
                        {CLOCKS.map(([l], i) => <option key={l} value={i}>{l}</option>)}
                        {clockIdx === -1 && <option value="custom">{`${form.cmin ?? 0}s to ${form.cmax ?? 720}s left`}</option>}
                    </select>
                </label>
                <NumberField label="Lead from" value={form.mmin} min={-80} max={80} placeholder="e.g. -3" onChange={(v) => set({ mmin: v })} />
                <NumberField label="Lead to" value={form.mmax} min={-80} max={80} placeholder="e.g. 0" onChange={(v) => set({ mmax: v })} />
                <NumberField label="Shot from (ft)" value={form.dmin} min={0} max={94} onChange={(v) => set({ dmin: v })} />
                <NumberField label="Shot to (ft)" value={form.dmax} min={0} max={94} onChange={(v) => set({ dmax: v })} />
                <label>
                    <span>Sort</span>
                    <select className="input-field" value={form.sort} onChange={(e) => set({ sort: e.target.value })}>
                        {options.sorts.map((s) => <option key={s} value={s}>{SORT_TEXT[s] || s}</option>)}
                    </select>
                </label>
                <label>
                    <span>Per page</span>
                    <select className="input-field" value={form.pageSize} onChange={(e) => set({ pageSize: Number(e.target.value) })}>
                        {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                </label>
            </div>

            <div className="pf-toggles">
                <div className="pf-periods" role="group" aria-label="Periods">
                    <span className="pf-label">Period</span>
                    {options.periods.map((p) => (
                        <button key={p} type="button" aria-pressed={form.per.includes(p)} className="pf-pill"
                            onClick={() => togglePeriod(p)}>{PERIOD_TEXT[p] || p}</button>
                    ))}
                </div>
                <label className="pf-check">
                    <input type="checkbox" checked={form.clutch} onChange={(e) => set({ clutch: e.target.checked })} />
                    Clutch only (final {options.clutch.seconds / 60} min of Q4/OT, within {options.clutch.margin})
                </label>
                {form.game && (
                    <span className="gf-player-chip pf-game-chip">
                        One game{gameRow ? `: ${day(gameRow.date)}, ${gameRow.home ? `${gameRow.opponent} @ ${gameRow.team}` : `${gameRow.team} @ ${gameRow.opponent}`}` : ` (${form.game})`}
                        <button type="button" className="cb-remove" aria-label="Any game" onClick={() => set({ game: null })}>×</button>
                    </span>
                )}
                <button type="button" className="action-btn pf-reset" onClick={() => set({ ...EMPTY })}>Clear filters</button>
            </div>
            <p className="page-subtitle gf-note">
                Lead is the player&apos;s team&apos;s margin just before the play (negative = behind). Shot distance only matches
                shots and the assists and blocks on them. {options.notes.distance}
            </p>

            {error && <p className="error-message">{error}</p>}
            {loading && !data && <Loader />}
            {data && (
                <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                    <p className="page-subtitle lb-summary">
                        <strong>{data.total.toLocaleString()}</strong> play{data.total === 1 ? '' : 's'} match
                        {data.points > 0 && <> · {data.points.toLocaleString()} points scored on the made shots and free throws among them</>}.
                    </p>
                    {data.by_cat.length > 1 && (
                        <ul className="pf-breakdown" aria-label="Plays by kind">
                            {data.by_cat.map((c) => <li key={c.key}><strong>{c.n.toLocaleString()}</strong> {c.label.toLowerCase()}</li>)}
                        </ul>
                    )}
                    {data.most.length > 0 && (
                        <p className="gf-most">
                            Most such plays: {data.most.slice(0, 5).map((p, i) => (
                                <React.Fragment key={p.player_id}>
                                    {i > 0 && ', '}<strong>{p.player_name}</strong> {p.n.toLocaleString()}
                                </React.Fragment>
                            ))}.
                        </p>
                    )}
                    {data.results.length === 0 ? <p className="empty-message">No plays match these filters.</p> : (
                        <>
                            <TableExport name="plays" />
                            <div className="table-wrapper">
                                <table className="data-table lb-table pf-table">
                                    <thead>
                                        <tr>
                                            <th>#</th><th>Date</th><th>Time</th><th>Player</th><th>Game</th><th>Play</th>
                                            <th>Description</th><th className="lb-num" title="Player's team first, after the play">Score</th>
                                            <th className="lb-num">Ft</th><th>Replay</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.results.map((r, i) => (
                                            <PlayRow key={`${r.event_id}-${r.cat}`} r={r} index={data.offset + i + 1} onNavigate={onNavigate} />
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                    {data.total > data.limit && (
                        <div className="gf-pager">
                            <button type="button" className="action-btn" disabled={form.page === 0}
                                onClick={() => setForm((f) => ({ ...f, page: f.page - 1 }))}>← Previous</button>
                            <span>{first.toLocaleString()}–{last.toLocaleString()} of {data.total.toLocaleString()}</span>
                            <button type="button" className="action-btn" disabled={form.page >= maxPage}
                                onClick={() => setForm((f) => ({ ...f, page: f.page + 1 }))}>Next →</button>
                            <span>
                                (Export saves the rows on this page.
                                {data.total > data.max_offset + data.limit && ` Paging stops at ${(data.max_offset + data.limit).toLocaleString()} rows: narrow the filters to see the rest.`})
                            </span>
                        </div>
                    )}
                    <p className="page-subtitle pf-foot">
                        Score: the player&apos;s team first, after the play. {data.notes.definitions} {data.notes.unidentified}
                    </p>
                </div>
            )}
        </section>
    );
}
