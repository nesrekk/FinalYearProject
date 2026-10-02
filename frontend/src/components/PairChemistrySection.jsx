import React, { useEffect, useMemo, useState } from 'react';
import { fetchPairGrid } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import TableExport from './common/TableExport';
import CopyLinkButton from './common/CopyLinkButton';
import SaveViewButton from './common/SaveViewButton';
import PlayerHeadshot from './common/PlayerHeadshot';
import PlayerName from './common/PlayerName';
import { currentPageParam, parseParam, useInitialParams, useUrlSync } from '../utils/useUrlState';
import { signed } from '../utils/format';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const int = (v) => Math.round(v).toLocaleString();
const lastName = (name) => (name ? name.split(' ').slice(1).join(' ') || name : '?');

// Which rating the grid shows. `better` turns a difference from the team
// figure into "good for the team" (lower defensive rating is better).
const METRICS = {
    net: { label: 'Net rating', key: 'net_rating', better: (v, team) => v - team, show: signed },
    off: { label: 'Offense', key: 'off_rating', better: (v, team) => v - team, show: (v) => v.toFixed(1) },
    def: { label: 'Defense', key: 'def_rating', better: (v, team) => team - v, show: (v) => v.toFixed(1) },
};
const URL_KEYS = ['season', 'team', 'm', 'min', 'n'];
const SCALE = 15; // points per 100 possessions from the team figure for full colour

function tint(diff) {
    const share = Math.min(Math.abs(diff) / SCALE, 1);
    const pct = Math.round(share * 38);
    if (pct < 3) return undefined;
    const tone = diff > 0 ? 'var(--positive)' : 'var(--negative)';
    return `color-mix(in srgb, ${tone} ${pct}%, var(--surface))`;
}

function formFromParams(p) {
    const n = parseParam.int(p, 'n');
    return {
        season: parseParam.int(p, 'season', { min: 1990, max: 2100 }),
        team: parseParam.str(p, 'team')?.toUpperCase() ?? null,
        metric: parseParam.oneOf(p, 'm', Object.keys(METRICS)) ?? 'net',
        minMinutes: parseParam.num(p, 'min', { min: 0, max: 2000 }) ?? 100,
        maxPlayers: [8, 10, 12, 15].includes(n) ? n : 12,
    };
}

export default function PairChemistrySection() {
    const params = useInitialParams();
    const [form, setForm] = useState(() => formFromParams(params));
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [focus, setFocus] = useState(null); // { a, b } player ids

    const shownSeason = form.season ?? data?.season ?? null;
    const shownTeam = form.team ?? data?.team ?? null;
    useUrlSync({
        season: shownSeason, team: shownTeam, m: form.metric,
        min: form.minMinutes === '' ? 0 : form.minMinutes, n: form.maxPlayers,
    });

    // This tool lives inside the Analytics page, so its inputs would stay in
    // the link after switching tabs; drop them when it closes.
    const [page] = useState(currentPageParam);
    useEffect(() => () => {
        const url = new URL(window.location.href);
        if (url.searchParams.get('page') !== page) return;
        URL_KEYS.forEach((k) => url.searchParams.delete(k));
        window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
    }, [page]);

    useEffect(() => {
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                const res = await fetchPairGrid({
                    season: form.season ?? undefined, team: form.team ?? undefined,
                    min_minutes: form.minMinutes === '' ? 0 : form.minMinutes, max_players: form.maxPlayers,
                });
                setData(res);
                setFocus(null);
            } catch (e) {
                setData(null);
                setError(e?.response?.data?.detail || 'Could not load the chemistry grid. Is the impact API (port 8002) running?');
            } finally {
                setLoading(false);
            }
        }, 250);
        return () => clearTimeout(timer);
    }, [form.season, form.team, form.minMinutes, form.maxPlayers]);

    const metric = METRICS[form.metric];
    const cellOf = useMemo(() => {
        const map = new Map();
        (data?.pairs ?? []).forEach((c) => {
            map.set(`${c.a}-${c.b}`, c);
            map.set(`${c.b}-${c.a}`, c);
        });
        return map;
    }, [data]);

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));

    if (!data && loading) return <Loader />;
    if (!data) return <section className="dashboard-card"><p className="error-message">{error}</p></section>;

    const t = data.team_summary;
    const stints = data.source === 'stints';
    const cov = data.coverage;
    const teamValue = t[metric.key];
    const names = Object.fromEntries(data.players.map((p) => [p.player_id, p.player_name]));
    const qualified = data.pairs.filter((c) => c.qualified);
    const ranked = [...qualified].sort((x, y) => metric.better(y[metric.key], teamValue) - metric.better(x[metric.key], teamValue));
    const focusCell = focus && (focus.a === focus.b
        ? data.players.find((p) => p.player_id === focus.a)
        : cellOf.get(`${focus.a}-${focus.b}`));
    const seasons = [...data.seasons_available].reverse();

    return (
        <section className="dashboard-card lb-card pc-card">
            <h2 className="card-title hb-page-title">
                Who plays well together
                <InfoTooltip label="How the chemistry grid works" title="Under the hood">
                    {data.method}
                </InfoTooltip>
                <SourceBadge source={data._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="analytics" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                How the team did with each pair of its players on the floor at the same time: from every stint of every
                game rebuilt from play-by-play (2020-21 on), or from the 2,000 most-used lineups stored for earlier
                seasons. Pick a team and season; each cell is one pair.
            </p>

            <div className="lb-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={data.season}
                        onChange={(e) => set({ season: Number(e.target.value) })}>
                        {seasons.map((s) => (
                            <option key={s} value={s}>
                                {seasonLabel(s)}{data.sources?.[String(s)] === 'lineup_stats' ? ' · top 2,000 only' : ''}
                            </option>
                        ))}
                    </select>
                </label>
                <label>
                    <span>Team</span>
                    <select className="input-field" value={data.team} onChange={(e) => set({ team: e.target.value })}>
                        {data.teams.map((tm) => <option key={tm} value={tm}>{tm}</option>)}
                    </select>
                </label>
                <label>
                    <span>Min. shared minutes</span>
                    <input className="input-field" type="number" min={0} max={2000} step={25} value={form.minMinutes}
                        onChange={(e) => set({ minMinutes: e.target.value === '' ? '' : Number(e.target.value) })} />
                </label>
                <label>
                    <span>Players shown</span>
                    <select className="input-field" value={form.maxPlayers} onChange={(e) => set({ maxPlayers: Number(e.target.value) })}>
                        {data.max_players_choices.map((n) => <option key={n} value={n}>{n} most-used</option>)}
                    </select>
                </label>
            </div>

            <div className="tab-bar lb-modes" role="tablist" aria-label="Rating shown" style={{ marginTop: '1rem' }}>
                {Object.entries(METRICS).map(([id, m]) => (
                    <button key={id} type="button" role="tab" aria-selected={form.metric === id}
                        className={`tab-btn ${form.metric === id ? 'tab-btn--active' : ''}`}
                        onClick={() => set({ metric: id })}>
                        {m.label}
                    </button>
                ))}
            </div>

            {error && <p className="error-message">{error}</p>}

            <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                <p className="rx-verdict">
                    <strong>{data.team} {seasonLabel(data.season)}, {t.wins}-{t.losses}.</strong>{' '}
                    {stints ? (
                        <>
                            Every stint from play-by-play: {int(t.minutes)} of its {int(cov.minutes)} minutes are tracked
                            (<strong>{Math.round(t.coverage * 100)}%</strong>, {t.lineups.toLocaleString()} distinct lineups). {t.note}{' '}
                            In those minutes the team&apos;s {metric.label.toLowerCase()} was <strong>{metric.show(teamValue)}</strong>
                            {form.metric !== 'net' && ' points per 100 possessions'}; colours compare each pair to that.
                        </>
                    ) : (
                        <>
                            The stored lineups cover {int(t.minutes)} of its about {int(t.regulation_minutes)} regulation
                            minutes (<strong>{Math.round(t.coverage * 100)}%</strong>, {t.lineups} lineups). Only the league&apos;s
                            2,000 most-used lineups are stored for seasons before 2020-21, so bench units are under-counted. In these
                            lineups the team&apos;s {metric.label.toLowerCase()} was <strong>{metric.show(teamValue)}</strong>
                            {form.metric !== 'net' && ' points per 100 possessions'}; colours compare each pair to that.
                        </>
                    )}
                </p>
                {stints && (cov.excluded.length > 0 || cov.partial.length > 0) && (
                    <details className="pc-coverage">
                        <summary>
                            {cov.excluded.length > 0 && `${cov.excluded.length} game${cov.excluded.length === 1 ? '' : 's'} excluded`}
                            {cov.excluded.length > 0 && cov.partial.length > 0 && ' · '}
                            {cov.partial.length > 0 && `${cov.partial.length} game${cov.partial.length === 1 ? '' : 's'} partly tracked (${Math.round(cov.partial_minutes)} min left out)`}
                        </summary>
                        <ul>
                            {cov.excluded.map((g) => (
                                <li key={g.game_id}>{g.date} {g.home ? 'vs' : 'at'} {g.opponent}: excluded, {g.reason}</li>
                            ))}
                            {cov.partial.map((g) => (
                                <li key={g.game_id}>{g.date} {g.home ? 'vs' : 'at'} {g.opponent}: {g.minutes_lost} min with a player who has no id in the play-by-play</li>
                            ))}
                        </ul>
                    </details>
                )}

                <div className="pc-legend" aria-hidden="true">
                    <span className="pc-legend-swatch" style={{ background: tint(-SCALE) }} /> worse than the team
                    <span className="pc-legend-swatch" style={{ background: tint(SCALE) }} /> better than the team
                    <span className="pc-legend-swatch pc-cell--thin" /> under {form.minMinutes || 0} shared minutes
                </div>

                <div className="table-wrapper pc-grid-wrap">
                    <table className="pc-grid">
                        <caption className="pc-caption">
                            {metric.label} with each pair on the floor, {data.team} {seasonLabel(data.season)}
                        </caption>
                        <thead>
                            <tr>
                                <th scope="col" className="pc-corner">Player</th>
                                {data.players.map((p) => (
                                    <th key={p.player_id} scope="col" className="pc-colhead" title={p.player_name}>
                                        <PlayerHeadshot playerId={p.player_id} playerName={p.player_name} size={28} />
                                        <span>{lastName(p.player_name)}</span>
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {data.players.map((row) => (
                                <tr key={row.player_id}>
                                    <th scope="row" className="pc-rowhead">
                                        <span className="pc-rowname">{row.player_name}</span>
                                        <span className="pc-rowmin">
                                            {int(row.minutes)}
                                            {row.season_minutes_all_teams ? ` of ${int(row.season_minutes_all_teams)}` : ''} min
                                        </span>
                                    </th>
                                    {data.players.map((col) => {
                                        const self = row.player_id === col.player_id;
                                        const c = self ? row : cellOf.get(`${row.player_id}-${col.player_id}`);
                                        const on = focus && ((focus.a === row.player_id && focus.b === col.player_id)
                                            || (focus.b === row.player_id && focus.a === col.player_id));
                                        if (!c) {
                                            return (
                                                <td key={col.player_id} className="pc-cell pc-cell--none"
                                                    title={`${row.player_name} and ${col.player_name}: never on the floor together${stints ? '' : ' in a stored lineup'}`}>
                                                    —
                                                </td>
                                            );
                                        }
                                        const v = c[metric.key];
                                        const thin = !self && !c.qualified;
                                        const cls = ['pc-cell', self && 'pc-cell--self', thin && 'pc-cell--thin', on && 'pc-cell--on']
                                            .filter(Boolean).join(' ');
                                        const label = self
                                            ? `${row.player_name} on the floor: ${metric.label} ${metric.show(v)}, ${int(c.minutes)} minutes`
                                            : `${row.player_name} and ${col.player_name}: ${metric.label} ${metric.show(v)}, ${int(c.minutes)} shared minutes${thin ? ' (below the minutes floor)' : ''}`;
                                        return (
                                            <td key={col.player_id} className={cls}
                                                style={thin || self ? undefined : { background: tint(metric.better(v, teamValue)) }}>
                                                <button type="button" className="pc-cell-btn" aria-label={label} title={label}
                                                    aria-pressed={!!on}
                                                    onMouseEnter={() => setFocus({ a: row.player_id, b: col.player_id })}
                                                    onFocus={() => setFocus({ a: row.player_id, b: col.player_id })}
                                                    onClick={() => setFocus({ a: row.player_id, b: col.player_id })}>
                                                    {metric.show(v)}
                                                </button>
                                            </td>
                                        );
                                    })}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>

                <div className="pc-detail" role="status" aria-live="polite">
                    {!focusCell && <span>Hover, tap or tab to a cell for its minutes and ratings. The diagonal shows each player alone.</span>}
                    {focusCell && focus.a === focus.b && (
                        <span>
                            <strong>{names[focus.a]}</strong> on the floor{stints ? '' : ' in the stored lineups'}: {int(focusCell.minutes)} min,{' '}
                            {int(focusCell.poss)} possessions, {focusCell.lineups} lineups · ORtg {focusCell.off_rating.toFixed(1)} ·
                            DRtg {focusCell.def_rating.toFixed(1)} · Net {signed(focusCell.net_rating)}
                            {focusCell.season_minutes_all_teams ? ` · ${int(focusCell.season_minutes_all_teams)} season minutes (all teams)` : ''}
                        </span>
                    )}
                    {focusCell && focus.a !== focus.b && (
                        <span>
                            <strong>{names[focus.a]} + {names[focus.b]}</strong>: {int(focusCell.minutes)} shared min,{' '}
                            {int(focusCell.poss)} possessions, {focusCell.lineups} lineups · ORtg {focusCell.off_rating.toFixed(1)} ·
                            DRtg {focusCell.def_rating.toFixed(1)} · Net {signed(focusCell.net_rating)}
                            {' '}({signed(focusCell.net_rating - t.net_rating)} vs. the team&apos;s {signed(t.net_rating)})
                            {!focusCell.qualified && <em> · below the {form.minMinutes || 0}-minute floor, treat as noise</em>}
                        </span>
                    )}
                    {focus && !focusCell && <span>These two never shared the floor{stints ? '' : ' in a stored lineup'}.</span>}
                </div>

                <h3 className="section-heading pc-list-title">Pairs with {form.minMinutes || 0}+ shared minutes, best to worst by {metric.label.toLowerCase()}</h3>
                <p className="page-subtitle lb-summary" style={{ marginTop: 0 }}>
                    {qualified.length} of {data.pairs.length} pairs among these {data.players.length} players clear the floor.
                    Ratings are points per 100 possessions.
                </p>
                {ranked.length === 0 ? (
                    <p className="empty-message">No pair clears the minutes floor. Lower it to see more.</p>
                ) : (
                    <>
                        <TableExport name={`pair chemistry ${data.team} ${seasonLabel(data.season)}`} />
                        <div className="table-wrapper">
                            <table className="data-table lb-table">
                                <thead>
                                    <tr>
                                        <th>#</th>
                                        <th>Player</th>
                                        <th>With</th>
                                        <th className="lb-num">Shared min</th>
                                        <th className="lb-num">Poss</th>
                                        <th className="lb-num">Lineups</th>
                                        <th className="lb-num">ORtg</th>
                                        <th className="lb-num">DRtg</th>
                                        <th className="lb-num lb-stat">Net</th>
                                        <th className="lb-num">Net vs. team</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {ranked.map((c, i) => (
                                        <tr key={`${c.a}-${c.b}`}>
                                            <td>{i + 1}</td>
                                            <td><PlayerName playerId={c.a} name={names[c.a]} size={24} /></td>
                                            <td><PlayerName playerId={c.b} name={names[c.b]} size={24} /></td>
                                            <td className="lb-num">{int(c.minutes)}</td>
                                            <td className="lb-num">{int(c.poss)}</td>
                                            <td className="lb-num">{c.lineups}</td>
                                            <td className="lb-num">{c.off_rating.toFixed(1)}</td>
                                            <td className="lb-num">{c.def_rating.toFixed(1)}</td>
                                            <td className="lb-num lb-stat">{signed(c.net_rating)}</td>
                                            <td className="lb-num">{signed(c.net_rating - t.net_rating)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}
            </div>
        </section>
    );
}
