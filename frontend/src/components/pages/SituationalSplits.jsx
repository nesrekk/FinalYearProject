import React, { useEffect, useState } from 'react';
import { fetchSituationalLeaderboard, fetchSituationalOptions } from '../../services/api';
import Loader from '../Loader';
import CopyLinkButton from '../common/CopyLinkButton';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import { SHORT_SIDES, fmtGap, fmtR, fmtValue, repeatWords, seasonLabel } from '../common/situationalFormat';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/splits.css';

// Situational Splits (?page=splits, GET /splits/situational/*): every
// player-season's home/away, back-to-back, long-trip and strong/weak-opponent
// gaps, against the average player's gap, with the chance check (how many
// would stand out if nobody had a real split) and year-to-year carry-over.
// Link: season, split, stat, rank, team, n.

const LIMITS = [25, 50, 100, 200];
// rank id -> [label, sort, order]
const RANKS = {
    above: ['Most above average (by SDs)', 'z', 'desc'],
    below: ['Most below average (by SDs)', 'z', 'asc'],
    gap_high: ['Biggest gap, high to low', 'diff', 'desc'],
    gap_low: ['Biggest gap, low to high', 'diff', 'asc'],
    games: ['Most games on the smaller side', 'games', 'desc'],
};

function Verdict({ lg, pooled, data }) {
    if (!lg) return null;
    // One season's count moves by a few players by chance, so the call is made
    // on all six seasons: more standouts than the shuffle AND a gap that repeats.
    const beatsShuffle = pooled && pooled.outside_95 > pooled.chance_outside_95 * 1.2;
    const repeats = pooled?.yoy_r != null && pooled.yoy_r >= 0.1;
    let call = 'This list is about what chance produces: treat its top as the luckiest and unluckiest samples, not special players.';
    if (beatsShuffle && repeats) call = 'More players stand out than chance allows and the gaps partly repeat: some of this split is real for some players.';
    else if (beatsShuffle) call = 'More players stand out than chance allows, but the gaps don\'t repeat the next season: that season\'s circumstances, not a trait.';
    else if (repeats) call = 'No more players stand out than chance allows, though the gaps repeat a little: a small real part at most.';
    return (
        <p className="rx-verdict">
            <strong>{lg.outside_95}</strong> of {lg.players} qualified players sit outside the 95% range around the
            average player&apos;s gap in {seasonLabel(data.season)}; with each player&apos;s own games shuffled between
            the two sides (so nobody has a real split), the same test flags about{' '}
            <strong>{Math.round(lg.chance_outside_95)}</strong>.
            {pooled && (
                <>
                    {' '}Over all six seasons: {pooled.outside_95} against {Math.round(pooled.chance_outside_95)} shuffled, and a
                    player&apos;s gap predicts his next season&apos;s at r = {fmtR(pooled.yoy_r)}
                    {pooled.yoy_n ? ` (${pooled.yoy_n.toLocaleString()} player pairs)` : ''}: {repeatWords(pooled.yoy_r)}. {call}
                </>
            )}
        </p>
    );
}

function LeagueEffect({ lg, pooled, data }) {
    if (!lg) return null;
    const f = (v) => fmtGap(v, data.stat, data.format);
    const ci = (a, b) => `${fmtGap(a, data.stat, data.format, { unit: false })} to ${fmtGap(b, data.stat, data.format, { unit: false })}`;
    const venue = data.split !== 'home' && pooled?.venue_adj_diff != null;
    return (
        <dl className="sp-league">
            <div>
                <dt>Average player, {seasonLabel(data.season)}</dt>
                <dd>{f(lg.league_diff)}</dd>
                <span>95%: {ci(lg.league_ci_low, lg.league_ci_high)} · {data.a}: {fmtValue(lg.league_value_a, data.stat, data.format)} · {data.b}: {fmtValue(lg.league_value_b, data.stat, data.format)}</span>
            </div>
            {pooled && (
                <div>
                    <dt>All six seasons</dt>
                    <dd>{f(pooled.league_diff)}</dd>
                    <span>95%: {ci(pooled.league_ci_low, pooled.league_ci_high)} · {pooled.players.toLocaleString()} player-seasons</span>
                </div>
            )}
            {venue && (
                <div>
                    <dt>Same, home/away held equal</dt>
                    <dd>{f(pooled.venue_adj_diff)}</dd>
                    <span>Home games: {Math.round(pooled.home_share_a * 100)}% of the {data.a.toLowerCase()} side, {Math.round(pooled.home_share_b * 100)}% of the {data.b.toLowerCase()} side</span>
                </div>
            )}
        </dl>
    );
}

export default function SituationalSplits() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [result, setResult] = useState(null); // { key, data } | { key, error }

    useEffect(() => {
        fetchSituationalOptions()
            .then((o) => {
                setOptions(o);
                const n = parseParam.int(params, 'n');
                setForm({
                    season: parseParam.int(params, 'season', { min: Math.min(...o.seasons), max: Math.max(...o.seasons) }) ?? Math.max(...o.seasons),
                    split: parseParam.oneOf(params, 'split', o.splits.map((s) => s.key)) ?? 'home',
                    stat: parseParam.oneOf(params, 'stat', o.stats.map((s) => s.key)) ?? 'pts',
                    rank: parseParam.oneOf(params, 'rank', Object.keys(RANKS)) ?? 'above',
                    team: parseParam.str(params, 'team')?.toUpperCase().slice(0, 4) || null,
                    limit: LIMITS.includes(n) ? n : 25,
                });
            })
            .catch(() => setOptionsError('Situational Splits couldn\'t load. Is the impact API (port 8002) running?'));
    }, [params]);

    useUrlSync(form && {
        season: form.season, split: form.split, stat: form.stat, rank: form.rank === 'above' ? null : form.rank,
        team: form.team, n: form.limit === 25 ? null : form.limit,
    });

    const reqKey = form ? JSON.stringify(form) : null;
    useEffect(() => {
        if (!form) return undefined;
        let active = true;
        const [, sort, order] = RANKS[form.rank];
        fetchSituationalLeaderboard({
            season: form.season, split: form.split, stat: form.stat, sort, order,
            team: form.team ?? undefined, limit: form.limit,
        })
            .then((d) => { if (active) setResult({ key: reqKey, data: d }); })
            .catch((e) => { if (active) setResult({ key: reqKey, error: e.response?.data?.detail || 'The list couldn\'t load.' }); });
        return () => { active = false; };
    }, [form, reqKey]);
    const loading = result?.key !== reqKey;
    const data = result?.data ?? null;
    const error = result?.error ?? '';

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const splitInfo = options.splits.find((s) => s.key === form.split);
    const statInfo = options.stats.find((s) => s.key === form.stat);
    const overview = options.pooled.filter((r) => r.split === form.split);
    const v = (x) => fmtValue(x, data?.stat, data?.format);
    const g = (x, unit) => fmtGap(x, data?.stat, data?.format, { unit });

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                Home, rest, travel, opponent: does it matter for him?
                <InfoTooltip label="How Situational Splits work" title="His gap against the average player's">
                    {options.method}
                </InfoTooltip>
                <SourceBadge source={data?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="splits" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Every player-season&apos;s gap between two situations, next to the average player&apos;s gap. Most players
                land within noise of the average, and the page counts how many would stand out by chance alone.
                Regular season {seasonLabel(options.seasons[0])} to {seasonLabel(options.seasons[options.seasons.length - 1])} only
                (game-by-game data starts in 2020-21).
            </p>

            <div className="tab-bar lb-modes" role="tablist" aria-label="Situation" style={{ marginTop: 'var(--space-4)' }}>
                {options.splits.map((s) => (
                    <button key={s.key} type="button" role="tab" aria-selected={form.split === s.key}
                        className={`tab-btn ${form.split === s.key ? 'tab-btn--active' : ''}`} onClick={() => set({ split: s.key })}>
                        {s.label}
                    </button>
                ))}
            </div>
            <p className="page-subtitle sp-def">
                {splitInfo.a} minus {splitInfo.b.toLowerCase()}.{' '}
                {form.split === 'rest' && 'Back-to-backs count only second nights he also played the first night of; the players who sit those nights are missing from this side, so their teammates\' minutes go up.'}
                {form.split === 'travel' && `Miles the team traveled since its previous game: ${options.long_trip.toLocaleString()}+ vs. under ${options.short_trip} (including none, mostly home stands); 300-999 miles are in neither.`}
                {form.split === 'opp' && 'Opponent strength = its average margin over the whole season (this game included); middle-10 opponents are in neither side.'}
                {form.split === 'home' && 'The 2020-21 season was played mostly without fans.'}
            </p>

            <div className="lb-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={form.season} onChange={(e) => set({ season: Number(e.target.value), team: null })}>
                        {[...options.seasons].reverse().map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>Stat</span>
                    <select className="input-field" value={form.stat} onChange={(e) => set({ stat: e.target.value })}>
                        {options.stats.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
                    </select>
                </label>
                <label>
                    <span>Rank</span>
                    <select className="input-field" value={form.rank} onChange={(e) => set({ rank: e.target.value })}>
                        {Object.entries(RANKS).map(([k, [text]]) => <option key={k} value={k}>{text}</option>)}
                    </select>
                </label>
                <label>
                    <span>Team</span>
                    <select className="input-field" value={form.team ?? ''} onChange={(e) => set({ team: e.target.value || null })}>
                        <option value="">All teams</option>
                        {(data?.teams ?? (form.team ? [form.team] : [])).map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
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
                    <LeagueEffect lg={data.league} pooled={data.pooled} data={data} />
                    <Verdict lg={data.league} pooled={data.pooled} data={data} />
                    <p className="page-subtitle lb-summary">
                        {data.qualified} of {data.stored} players qualify in {seasonLabel(data.season)}: {data.min_games}+ games on
                        each side{statInfo.side_floor ? ` and ${statInfo.side_floor}+ ${statInfo.format === 'pct' ? (form.stat === 'usg_pct' ? 'team plays' : 'attempts') : 'minutes'} on each side` : ''}.
                        SDs = his gap minus the average player&apos;s, in units of his own game-to-game noise. Greyed: within
                        the 95% range around the average.
                    </p>
                    {data.results.length === 0 ? <p className="empty-message">No qualifying players.</p> : (
                        <>
                            <TableExport name={`${data.split} ${data.stat} splits ${seasonLabel(data.season)}${data.team ? ` ${data.team}` : ''}`} />
                            <div className="table-wrapper">
                                <table className="data-table lb-table sp-table">
                                    <thead>
                                        <tr>
                                            <th>#</th><th>Player</th><th>Team</th>
                                            <th className="lb-num">Games</th>
                                            <th className="lb-num" title={data.a}>{SHORT_SIDES[data.split][0]}</th>
                                            <th className="lb-num" title={data.b}>{SHORT_SIDES[data.split][1]}</th>
                                            <th className="lb-num">Gap</th><th className="lb-num">95% interval</th>
                                            <th className="lb-num">vs. average</th><th className="lb-num">SDs</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {data.results.map((r, i) => {
                                            const stand = r.ci_excludes_league;
                                            return (
                                                <tr key={r.player_id} className={stand ? '' : 'sp-noise'}
                                                    title={stand ? 'Outside the 95% range around the average player\'s gap' : 'Within normal noise'}>
                                                    <td>{data.offset + i + 1}</td>
                                                    <td><PlayerName playerId={r.player_id} name={r.player_name} size={24} /></td>
                                                    <td>{r.teams}</td>
                                                    <td className="lb-num" title={`${data.a}: ${r.games_a} · ${data.b}: ${r.games_b}`}>{r.games_a} / {r.games_b}</td>
                                                    <td className="lb-num">{v(r.value_a)}</td>
                                                    <td className="lb-num">{v(r.value_b)}</td>
                                                    <td className={`lb-num lb-stat ${stand ? (r.vs_league > 0 ? 'sp-pos' : 'sp-neg') : ''}`}>{g(r.diff, true)}</td>
                                                    <td className="lb-num">{g(r.ci_low, false)} to {g(r.ci_high, false)}</td>
                                                    <td className="lb-num">{g(r.vs_league, true)}</td>
                                                    <td className="lb-num">{r.z == null ? '—' : `${r.z < -0.05 ? '−' : ''}${Math.abs(r.z).toFixed(1)}`}</td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                            {data.total > data.results.length && (
                                <p className="page-subtitle pp-foot">Showing {data.results.length} of {data.total}. Raise &ldquo;Show&rdquo; or export for more.</p>
                            )}
                        </>
                    )}

                    <h3 className="sp-h3">{splitInfo.label}: every stat, all six seasons pooled</h3>
                    <p className="page-subtitle sp-def">
                        The average player&apos;s gap, how many players stand out against how many would with the games
                        shuffled, and whether a player&apos;s gap carries into his next season. A split effect is a trait
                        only if it beats the shuffle <em>and</em> repeats.
                    </p>
                    <TableExport name={`${form.split} splits league pooled`} />
                    <div className="table-wrapper">
                        <table className="data-table lb-table sp-table">
                            <thead>
                                <tr>
                                    <th>Stat</th><th className="lb-num">Average gap</th><th className="lb-num">95% interval</th>
                                    {form.split !== 'home' && <th className="lb-num">Home/away held equal</th>}
                                    <th className="lb-num">Players</th><th className="lb-num">Stand out</th>
                                    <th className="lb-num">Shuffled</th><th className="lb-num">Repeats (r)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {overview.map((r) => {
                                    const st = options.stats.find((s) => s.key === r.stat);
                                    const fg = (x, unit = true) => fmtGap(x, r.stat, st.format, { unit });
                                    return (
                                        <tr key={r.stat} className={r.stat === form.stat ? 'sp-current' : ''}>
                                            <td>{st.label}</td>
                                            <td className="lb-num lb-stat">{fg(r.league_diff)}</td>
                                            <td className="lb-num">{fg(r.league_ci_low, false)} to {fg(r.league_ci_high, false)}</td>
                                            {form.split !== 'home' && <td className="lb-num">{fg(r.venue_adj_diff)}</td>}
                                            <td className="lb-num">{r.players.toLocaleString()}</td>
                                            <td className="lb-num">{r.outside_95}</td>
                                            <td className="lb-num">{Math.round(r.chance_outside_95)}</td>
                                            <td className="lb-num">{fmtR(r.yoy_r)}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                    <p className="page-subtitle pp-foot">
                        Stand out = outside the 95% range around that season&apos;s average gap, summed over seasons. Shuffled =
                        the same count with each player&apos;s games randomly reassigned between the two sides (50 shuffles),
                        which is what pure chance produces with these sample sizes: usually a bit more than 5%, because a
                        dozen back-to-backs is a small sample. Repeats = correlation of a player&apos;s gap (vs. average) with
                        his next season&apos;s, players qualified both years.
                    </p>
                </div>
            )}
        </section>
    );
}
