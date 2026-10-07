import React, { useEffect, useState } from 'react';
import { fetchBreakouts, fetchLeaderboardOptions } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import { signed as signedNum } from '../../utils/format';
import '../../styles/stability.css';
import { LiveSeasonTag } from '../common/LiveSeasonNote';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const FORMATS = {
    num1: (v) => v.toFixed(1),
    num2: (v) => v.toFixed(2),
    pct: (v) => `${(v * 100).toFixed(1)}%`,
    signed1: (v) => signedNum(v, 1, '-'), // sign of the shown value; hyphen minus as before
    int: (v) => v.toFixed(0),
};
const fmt = (format, v) => (v == null ? '—' : FORMATS[format](v));
const signed = (v, d = 2) => signedNum(v, d);
const DEFAULT_STATS = ['pts', 'ts_pct', 'usg_pct', 'ast_pct', 'reb_pct', 'bpm'];
const TOP_N = [10, 25, 50, 100];

// The form from a shared link (utils/useUrlState.js). season null = the
// latest season the API has.
function formFromParams(p, o) {
    const usable = new Set(o.stats.filter((s) => s.key !== 'age').map((s) => s.key));
    const stats = [...new Set(parseParam.list(p, 'stats') ?? [])].filter((k) => usable.has(k)).slice(0, 8);
    const n = parseParam.int(p, 'n');
    return {
        season: parseParam.int(p, 'season', { min: o.seasons.from + 1, max: o.seasons.to }),
        stats: stats.length ? stats : DEFAULT_STATS,
        direction: parseParam.oneOf(p, 'dir', ['up', 'down']) ?? 'up',
        minGp: parseParam.int(p, 'gp', { min: 0, max: 82 }) ?? 30,
        minMpg: parseParam.num(p, 'mpg', { min: 0, max: 48 }) ?? 15,
        topN: TOP_N.includes(n) ? n : 25,
    };
}

export default function BreakoutDetector() {
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

    // The season is written once known, so a link keeps showing the same
    // season after a newer one is loaded.
    const shownSeason = form?.season ?? data?.season ?? null;
    useUrlSync(form && {
        season: shownSeason, dir: form.direction, stats: form.stats,
        gp: form.minGp || 0, mpg: form.minMpg || 0, n: form.topN,
    });

    useEffect(() => {
        if (!options || !form) return undefined;
        const timer = setTimeout(async () => {
            if (!form.stats.length) {
                setData(null);
                setError('Pick at least one stat.');
                return;
            }
            setLoading(true);
            setError('');
            try {
                setData(await fetchBreakouts({
                    season: form.season ?? undefined, stats: form.stats.join(','), direction: form.direction,
                    min_gp: form.minGp || 0, min_mpg: form.minMpg || 0, top_n: form.topN,
                }));
            } catch (err) {
                setData(null);
                setError(err.response?.data?.detail || 'Failed to load breakouts.');
            } finally {
                setLoading(false);
            }
        }, 300);
        return () => clearTimeout(timer);
    }, [options, form]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const toggleStat = (key) => set({
        stats: form.stats.includes(key) ? form.stats.filter((k) => k !== key) : [...form.stats, key].slice(0, 8),
    });
    const usable = options.stats.filter((s) => s.key !== 'age');
    const seasons = data?.seasons_available ? [...data.seasons_available].reverse() : [];
    const p = data?.persistence;
    const up = form.direction === 'up';
    const anyNoisy = !!data?.results.some((r) => Object.values(r.stats).some((v) => v.noisy));

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                Who jumped, who slipped
                <InfoTooltip label="How the Breakout Detector works" title="Under the hood">
                    {data?.method ?? 'Each stat is z-scored within its season; the score is the average change in standing from the season before.'}
                    {' '}Ages are as listed by NBA.com, which counts age later in the season than Basketball-Reference,
                    so roughly 45% of players show a year older than on Basketball-Reference.
                </InfoTooltip>
                <SourceBadge source={data?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="breakouts" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Biggest season-over-season changes in a player&apos;s standing in the league, across the stats you pick.
                Standing, not raw numbers, so league-wide changes in pace or shooting don&apos;t count.
            </p>

            <div className="tab-bar lb-modes" role="tablist" aria-label="Direction" style={{ marginTop: '0.75rem' }}>
                {[['up', 'Breakouts'], ['down', 'Declines']].map(([id, label]) => (
                    <button key={id} type="button" role="tab" aria-selected={form.direction === id}
                        className={`tab-btn ${form.direction === id ? 'tab-btn--active' : ''}`}
                        onClick={() => set({ direction: id })}>
                        {label}
                    </button>
                ))}
            </div>

            <fieldset className="bd-stats">
                <legend>Stats in the score ({form.stats.length} of up to 8)</legend>
                {usable.map((s) => (
                    <label key={s.key} className={form.stats.includes(s.key) ? 'bd-chip bd-chip--on' : 'bd-chip'}>
                        <input type="checkbox" checked={form.stats.includes(s.key)} onChange={() => toggleStat(s.key)}
                            disabled={!form.stats.includes(s.key) && form.stats.length >= 8} />
                        {s.label}
                    </label>
                ))}
            </fieldset>

            <div className="lb-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={data?.season ?? ''} disabled={!seasons.length}
                        onChange={(e) => set({ season: Number(e.target.value) })}>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)} vs {seasonLabel(s - 1)}</option>)}
                    </select>
                    <LiveSeasonTag season={data?.season} />
                </label>
                <label>
                    <span>Min. games (both seasons)</span>
                    <input className="input-field" type="number" min={0} max={82} value={form.minGp}
                        onChange={(e) => set({ minGp: e.target.value === '' ? '' : Number(e.target.value) })} />
                </label>
                <label>
                    <span>Min. minutes a game</span>
                    <input className="input-field" type="number" min={0} max={48} value={form.minMpg}
                        onChange={(e) => set({ minMpg: e.target.value === '' ? '' : Number(e.target.value) })} />
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
                    {p && (
                        <p className="rx-verdict">
                            <strong>Expect some of it to fade.</strong> Historically, the 20 biggest breakouts of a season
                            kept a median <strong>{Math.round(p.median_share_kept * 100)}%</strong> of their jump the
                            following year ({p.players.toLocaleString()} players, {seasonLabel(p.from)} to {seasonLabel(p.to)},
                            same stats and filters).
                        </p>
                    )}
                    <p className="page-subtitle lb-summary">
                        {up ? 'Biggest rises' : 'Biggest drops'} from {seasonLabel(data.season - 1)} to {seasonLabel(data.season)} among{' '}
                        {data.pool.toLocaleString()} players who played {data.filters.min_gp}+ games and {data.filters.min_mpg}+
                        minutes in both. Score = average change in standing, in standard deviations.
                        {anyNoisy && (
                            <> Greyed with <span className="ss-flag" aria-hidden="true">*</span>: that stat&apos;s sample in one
                                of the two seasons is under 0.5 reliability, so most of the change can be luck
                                (<a className="ss-link" href="?page=stability">Stat Stability</a>).</>
                        )}
                    </p>
                    {data.results.length === 0 ? (
                        <p className="empty-message">No players qualify in both seasons.</p>
                    ) : (
                        <>
                            <TableExport name={`${up ? 'breakouts' : 'declines'} ${seasonLabel(data.season)}`} />
                            <div className="table-wrapper">
                                <table className="data-table lb-table">
                                    <thead>
                                        <tr>
                                            <th>#</th>
                                            <th>Player</th>
                                            <th>Team</th>
                                            <th className="lb-num">Age</th>
                                            <th className="lb-num lb-stat">Score</th>
                                            <th className="lb-num">MIN</th>
                                            {data.stats.map((s) => <th key={s.key} className="lb-num">{s.label}</th>)}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.results.map((r) => (
                                            <tr key={r.player_id}>
                                                <td>{r.rank}</td>
                                                <td><PlayerName playerId={r.player_id} name={r.player_name} /></td>
                                                <td>{r.team}</td>
                                                <td className="lb-num">{r.age ?? '—'}</td>
                                                <td className="lb-num lb-stat">{signed(r.score)}</td>
                                                <td className="lb-num">{fmt('num1', r.min_before)} → {fmt('num1', r.min)}</td>
                                                {data.stats.map((s) => {
                                                    const v = r.stats[s.key];
                                                    const rel = v.reliability;
                                                    return (
                                                        <td key={s.key} className={v.noisy ? 'lb-num ss-noisy-cell' : 'lb-num'}
                                                            title={rel ? `Reliability ${rel.before.toFixed(2)} → ${rel.now.toFixed(2)}` : undefined}>
                                                            {fmt(s.format, v.before)} → {fmt(s.format, v.now)}{' '}
                                                            <span className="cb-z">({signed(v.delta_z, 1)})</span>
                                                            {v.noisy && <span className="ss-flag" aria-label="mostly noise">*</span>}
                                                        </td>
                                                    );
                                                })}
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                </div>
            )}
        </section>
    );
}
