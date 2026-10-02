import React, { useEffect, useState } from 'react';
import { fetchHotStreakOptions, fetchHotStreaks } from '../../services/api';
import Loader from '../Loader';
import CopyLinkButton from '../common/CopyLinkButton';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import { bySign, withSign } from '../../utils/format';
import '../../styles/gamelog.css';

// Hot Streak Checker (?page=hotstreaks, GET /games/hot-streaks): the league's
// hottest (or coldest) last-N-game runs as of a date, how unusual each is for
// that player, how many would look this unusual by chance, and how much of a
// run like it has carried on historically. Link: season, stat, window, as_of,
// dir, n.

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const LIMITS = [25, 50, 100];

export default function HotStreaks() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [result, setResult] = useState(null); // { key, data } | { key, error }

    useEffect(() => {
        fetchHotStreakOptions()
            .then((o) => {
                setOptions(o);
                const seasons = o.seasons.map((s) => s.season);
                const n = parseParam.int(params, 'n');
                const asOf = parseParam.str(params, 'as_of');
                setForm({
                    season: parseParam.int(params, 'season', { min: Math.min(...seasons), max: Math.max(...seasons) }) ?? Math.max(...seasons),
                    stat: parseParam.oneOf(params, 'stat', o.stats.map((s) => s.key)) ?? 'pts',
                    window: parseParam.oneOf(params, 'window', o.windows.map(String)) ? Number(params.get('window')) : 10,
                    asOf: asOf && /^\d{4}-\d{2}-\d{2}$/.test(asOf) ? asOf : null,
                    direction: parseParam.oneOf(params, 'dir', ['hot', 'cold']) ?? 'hot',
                    limit: LIMITS.includes(n) ? n : 25,
                });
            })
            .catch(() => setOptionsError('The Hot Streak Checker couldn\'t load. Is the impact API (port 8002) running?'));
    }, [params]);

    useUrlSync(form && {
        season: form.season, stat: form.stat, window: form.window, as_of: form.asOf,
        dir: form.direction === 'hot' ? null : form.direction, n: form.limit === 25 ? null : form.limit,
    });

    const reqKey = form ? JSON.stringify(form) : null;
    useEffect(() => {
        if (!form) return undefined;
        let active = true;
        fetchHotStreaks({
            season: form.season, stat: form.stat, window: form.window, as_of: form.asOf ?? undefined,
            direction: form.direction, limit: form.limit,
        })
            .then((d) => { if (active) setResult({ key: reqKey, data: d }); })
            .catch((e) => { if (active) setResult({ key: reqKey, error: e.response?.data?.detail || 'The list couldn\'t load.' }); });
        return () => { active = false; };
    }, [form, reqKey]);
    // While a new request runs, the previous list stays up, dimmed.
    const loading = result?.key !== reqKey;
    const data = result?.data ?? null;
    const error = result?.error ?? '';

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const seasonInfo = options.seasons.find((s) => s.season === form.season);
    const pct = data?.format === 'pct';
    const show = (v) => (v == null ? '—' : pct ? `${(v * 100).toFixed(1)}%` : v.toFixed(1));
    const signedShow = (v) => (v == null ? '—' : withSign(v, show(Math.abs(v))));
    // Colour follows the gap as shown (one decimal, in points for a percentage), so 0.0 isn't tinted.
    const gapTone = (v) => bySign(pct ? v * 100 : v, 1, 'pp-pos', 'pp-neg');
    // In sentences: shooting gaps are percentage points, not percent.
    const gapWords = (v) => (pct && v != null ? withSign(v, `${(Math.abs(v) * 100).toFixed(1)} percentage points`) : signedShow(v));
    const statWords = data ? (data.stat_label.includes('%') ? data.stat_label : data.stat_label.toLowerCase()) : '';
    const s = data?.summary;
    const nx = data?.next_games;
    const word = form.direction;
    const rules = options.rules;

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                Who&apos;s {word}, and is it real?
                <InfoTooltip label="How the Hot Streak Checker works" title="Two separate questions">
                    How unusual: for each player, {rules.draws.toLocaleString()} random sets of N games from his own season
                    so far; the share at least as {word} is the p-value (it says the run is more than chance clumping, not
                    that it will last). How much carries on: across every player-season 2020-21 to 2025-26, the share of a
                    run&apos;s gap from baseline that showed up again in his next N games. Baseline = his games this
                    season before the run, plus part of his previous season (a whole season for shooting %, much less for
                    minutes and usage, chosen on held-out seasons). Full details on the Methodology page.
                </InfoTooltip>
                <SourceBadge source={data?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="hotstreaks" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Every player&apos;s last N games as of a date, against his own baseline. Most runs are noise, and the page
                says how many: with hundreds of players, a few dozen look unusual by chance alone. Shooting runs are the
                noisiest; minutes and role changes last. Regular season {seasonLabel(options.seasons[0].season)} to{' '}
                {seasonLabel(options.seasons[options.seasons.length - 1].season)} only (game-by-game data starts in 2020-21).
            </p>

            <div className="tab-bar lb-modes" role="tablist" aria-label="Direction" style={{ marginTop: 'var(--space-4)' }}>
                {[['hot', 'Hottest runs'], ['cold', 'Coldest runs']].map(([id, text]) => (
                    <button key={id} type="button" role="tab" aria-selected={form.direction === id}
                        className={`tab-btn ${form.direction === id ? 'tab-btn--active' : ''}`} onClick={() => set({ direction: id })}>
                        {text}
                    </button>
                ))}
            </div>
            <div className="lb-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={form.season} onChange={(e) => set({ season: Number(e.target.value), asOf: null })}>
                        {[...options.seasons].reverse().map((x) => <option key={x.season} value={x.season}>{seasonLabel(x.season)}</option>)}
                    </select>
                </label>
                <label>
                    <span>Stat</span>
                    <select className="input-field" value={form.stat} onChange={(e) => set({ stat: e.target.value })}>
                        {options.stats.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
                    </select>
                </label>
                <label>
                    <span>Last</span>
                    <select className="input-field" value={form.window} onChange={(e) => set({ window: Number(e.target.value) })}>
                        {options.windows.map((w) => <option key={w} value={w}>{w} games</option>)}
                    </select>
                </label>
                <label>
                    <span>As of</span>
                    <input className="input-field" type="date" value={form.asOf ?? seasonInfo.last_date}
                        min={seasonInfo.first_date} max={seasonInfo.last_date}
                        onChange={(e) => set({ asOf: e.target.value || null })} />
                </label>
                <label>
                    <span>Show</span>
                    <select className="input-field" value={form.limit} onChange={(e) => set({ limit: Number(e.target.value) })}>
                        {LIMITS.map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                </label>
            </div>

            {error && <p className="error-message">{error}</p>}
            {loading && !data && <Loader />}
            {data && (
                <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                    <p className="rx-verdict">
                        <strong>{s.significant}</strong> of {s.tested} players checked are unusually {word} at p &lt; {s.alpha};
                        about <strong>{s.expected_by_chance}</strong> would be by chance alone
                        {s.significant <= s.expected_by_chance * 1.3
                            ? `, so for ${statWords} over ${data.window} games, this list is essentially noise.`
                            : ', so some of these are real changes (often a new role: check the minutes).'}{' '}
                        Historically about {Math.round(s.share_carries_on * 100)}% of a {data.window}-game {statWords} gap
                        carried on into the next {data.window} games (range {Math.round(s.share_lo * 100)}-{Math.round(s.share_hi * 100)}%).
                    </p>
                    {nx && (
                        <p className="page-subtitle gf-note">
                            What these {nx.players} did next ({data.window} games after {data.as_of}): on average {gapWords(nx.mean_gap)} from
                            baseline during the run, {gapWords(nx.mean_next_vs_baseline)} after it (the model expected{' '}
                            {gapWords(nx.mean_expected_vs_baseline)}), so {Math.round(nx.share_carried * 100)}% carried on.
                        </p>
                    )}
                    <p className="page-subtitle lb-summary">
                        As of {data.as_of}: players who played within {rules.recent_days} days of it, with {rules.min_base_games}+ games
                        before the run averaging {rules.min_base_mpg}+ minutes
                        {options.stats.find((x) => x.key === data.stat)?.min_per_game ? ` and ${options.stats.find((x) => x.key === data.stat).min_per_game}+ attempts a game` : ''}
                        {' '}({s.skipped} more didn&apos;t qualify). Ranked by how far the run sits from his own random {data.window}-game
                        sets, in standard deviations. Greyed: within normal noise (p ≥ {s.alpha}).
                    </p>
                    {data.results.length === 0 ? <p className="empty-message">No qualifying runs.</p> : (
                        <>
                            <TableExport name={`${word} ${data.stat} last ${data.window} ${data.as_of}`} />
                            <div className="table-wrapper">
                                <table className="data-table lb-table gl-table">
                                    <thead>
                                        <tr>
                                            <th>#</th><th>Player</th><th>Run</th>
                                            <th className="lb-num">Last {data.window}</th><th className="lb-num">Baseline</th>
                                            <th className="lb-num">Gap</th><th className="lb-num">SDs</th><th className="lb-num">p</th>
                                            <th className="lb-num">Minutes</th>
                                            <th className="lb-num">Expected next</th>
                                            {nx && <th className="lb-num">Actual next</th>}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.results.map((r, i) => (
                                            <tr key={r.player_id} className={r.unusual.p >= s.alpha ? 'pp-small' : ''}>
                                                <td>{i + 1}</td>
                                                <td><PlayerName playerId={r.player_id} name={r.player_name} size={24} /></td>
                                                <td>{r.window.from.slice(5)} to {r.window.to.slice(5)}</td>
                                                <td className="lb-num lb-stat">{show(r.window.value)}</td>
                                                <td className="lb-num">{show(r.baseline.value)}</td>
                                                <td className={`lb-num ${gapTone(r.gap)}`}>{signedShow(r.gap)}</td>
                                                <td className="lb-num">{r.unusual.z.toFixed(1)}</td>
                                                <td className="lb-num">{r.unusual.p < 0.001 ? '<0.001' : r.unusual.p.toFixed(3)}</td>
                                                <td className="lb-num" title="Average minutes before the run → during it">{r.minutes.before} → {r.minutes.window}</td>
                                                <td className="lb-num">{show(r.persistence.expected_next)}</td>
                                                {nx && <td className="lb-num">{r.what_happened_next ? `${show(r.what_happened_next.value)}${r.what_happened_next.games < data.window ? ` (${r.what_happened_next.games} g)` : ''}` : '—'}</td>}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                            <p className="page-subtitle pp-foot">
                                Expected next = baseline + the historical share of the gap (plus a small league-wide drift).
                                It&apos;s an average across players, not a forecast for this one. A player with no previous season
                                on file gets a season-only baseline and its own share.
                            </p>
                        </>
                    )}
                </div>
            )}
        </section>
    );
}
