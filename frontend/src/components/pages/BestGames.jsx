import React, { useEffect, useState } from 'react';
import { fetchBestGames, fetchBestGamesOptions, fetchUpsets } from '../../services/api';
import Loader from '../Loader';
import CopyLinkButton from '../common/CopyLinkButton';
import InfoTooltip from '../common/InfoTooltip';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import TeamLogo from '../common/TeamLogo';
import { isPlainClick, pageHref, parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/gamelog.css';
import '../../styles/playfinder.css';
import '../../styles/bestgames.css';

// Best Games & Upsets (?page=bestgames): the most exciting regular-season
// games since 2020-21 (GET /best-games, from best_games: win-probability
// swing, lead changes, comebacks, overtime) and the biggest upsets since
// 2010-11 (GET /upsets, from the Season Simulator's held-out pre-game odds).
// Each best game opens Game Replay at its biggest swing.
// Inputs in the link: v (games|upsets), season, team, sort, ot, side, mg
// (games played by both teams), n, pg.

const day = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US',
    { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
const LOGO = { NOH: 'NOP', NJN: 'BKN' };
const PAGE_SIZES = [25, 50, 100];
const MIN_GAMES = [0, 10, 20, 30];
const OT_TEXT = (n) => (n === 1 ? 'OT' : `${n}OT`);
const pct = (v, d = 0) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const lowPct = (v) => (v == null ? '—' : v < 0.005 ? '<0.5%' : `${(v * 100).toFixed(v < 0.1 ? 1 : 0)}%`);
const clockText = (s) => {
    if (s == null) return '';
    if (s < 2880) return `Q${1 + Math.floor(s / 720)} ${Math.floor((720 - (s % 720)) / 60)}:${String(Math.floor((720 - (s % 720)) % 60)).padStart(2, '0')}`;
    const o = s - 2880;
    const left = 300 - (o % 300);
    return `${Math.floor(o / 300) === 0 ? 'OT' : `${1 + Math.floor(o / 300)}OT`} ${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, '0')}`;
};

function Team({ abbr, season }) {
    const logo = LOGO[abbr] ?? abbr;
    return (
        <TeamLink abbr={logo} season={season}>
            <TeamLogo abbreviation={logo} size={18} /><span>{abbr}</span>
        </TeamLink>
    );
}

function formFromParams(p, o) {
    const best = o.seasons.best.map((s) => s.season);
    const all = o.seasons.upsets.map((s) => s.season);
    const view = parseParam.oneOf(p, 'v', ['games', 'upsets']) ?? 'games';
    const n = parseParam.int(p, 'n');
    const range = view === 'games' ? [Math.min(...best), Math.max(...best)] : [Math.min(...all), Math.max(...all)];
    return {
        view,
        season: parseParam.int(p, 'season', { min: range[0], max: range[1] }),
        team: parseParam.oneOf(p, 'team', o.teams),
        sort: parseParam.oneOf(p, 'sort', view === 'games' ? o.sorts.map((s) => s.key) : o.upset_sorts.map((s) => s.key))
            ?? (view === 'games' ? 'excitement' : 'chance'),
        ot: p.get('ot') === '1',
        side: parseParam.oneOf(p, 'side', ['won', 'lost']),
        mg: MIN_GAMES.includes(parseParam.int(p, 'mg')) ? parseParam.int(p, 'mg') : 0,
        pageSize: PAGE_SIZES.includes(n) ? n : 25,
        page: parseParam.int(p, 'pg', { min: 0 }) ?? 0,
    };
}

function replayLink(g) {
    const params = { game: g.game_id };
    if (g.peak) { params.t = g.peak.seconds_elapsed; params.ev = g.peak.event_id; }
    return params;
}

function ReplayLink({ params, onNavigate, children = 'Replay' }) {
    const href = `${pageHref('analytics', params)}#replay`;
    return (
        <a className="pf-replay" href={href}
            onClick={(e) => { if (isPlainClick(e)) { e.preventDefault(); onNavigate('analytics', 'replay', params); } }}>
            {children}
        </a>
    );
}

function Score({ g }) {
    const homeWon = g.pts_home > g.pts_away;
    return (
        <span className="bg-score">
            <span className={homeWon ? '' : 'bg-win'}>{g.pts_away}</span>
            <span className="bg-dash">–</span>
            <span className={homeWon ? 'bg-win' : ''}>{g.pts_home}</span>
            {g.overtimes > 0 && <span className="pf-tag" title={`${g.overtimes} overtime${g.overtimes > 1 ? 's' : ''}`}>{OT_TEXT(g.overtimes)}</span>}
        </span>
    );
}

function Matchup({ g }) {
    return (
        <span className="pf-nowrap">
            <Team abbr={g.away} season={g.season} /><span className="pf-vs">@</span><Team abbr={g.home} season={g.season} />
        </span>
    );
}

function GameRow({ g, max, onNavigate }) {
    return (
        <tr>
            <td>{g.rank}</td>
            <td className="pf-nowrap">{day(g.date)}</td>
            <td><Matchup g={g} /></td>
            <td className="pf-nowrap"><Score g={g} /></td>
            <td className="lb-num">
                <span className="bg-bar" aria-hidden="true"><span style={{ width: `${Math.max(3, (g.excitement / max) * 100)}%` }} /></span>
                <strong>{g.excitement.toFixed(1)}</strong>
                <span className="bg-sub" title="Rank among every ranked game in the same seasons">#{g.rank_all.toLocaleString()}</span>
            </td>
            <td className="lb-num">{g.swing.toFixed(1)}</td>
            <td className="lb-num">{g.lead_changes}</td>
            <td className="lb-num" title="The winner's biggest deficit">{g.comeback ? `${g.comeback} pts` : '—'}</td>
            <td className="lb-num" title="The winner's lowest win probability at any point">{lowPct(g.win_min_wp)}</td>
            <td className="lb-num" title="Pre-game win chance of the team that won">{pct(g.winner_pregame)}</td>
            <td className="pf-desc" title={g.peak?.description}>
                {g.peak ? <><strong>{clockText(g.peak.seconds_elapsed)}</strong> {g.peak.description}</> : '—'}
            </td>
            <td><ReplayLink params={replayLink(g)} onNavigate={onNavigate} /></td>
        </tr>
    );
}

function UpsetRow({ u, onNavigate }) {
    const gp = u.games_played;
    return (
        <tr>
            <td>{u.rank}</td>
            <td className="pf-nowrap">{day(u.date)}</td>
            <td><Matchup g={{ away: u.away, home: u.home, season: u.season }} /></td>
            <td className="pf-nowrap"><Score g={{ pts_home: u.pts_home, pts_away: u.pts_away, overtimes: u.overtimes ?? 0 }} /></td>
            <td className="pf-nowrap"><Team abbr={u.winner} season={u.season} /></td>
            <td className="lb-num"><strong>{lowPct(u.winner_chance)}</strong></td>
            <td className="lb-num" title="Points the winner was expected to lose by, on the model's ratings and home court">
                {u.expected_margin < 0 ? `−${Math.abs(u.expected_margin).toFixed(1)}` : `+${u.expected_margin.toFixed(1)}`}
            </td>
            <td className="lb-num">{u.margin}</td>
            <td className="lb-num" title="Games each team had played that morning (home, away)">{gp[0]}, {gp[1]}</td>
            <td className="pf-desc">
                {[u.loser_back_to_back && `${u.loser} on a back-to-back`, u.winner_back_to_back && `${u.winner} on a back-to-back`]
                    .filter(Boolean).join('; ') || '—'}
            </td>
            <td className="lb-num">{u.excitement == null ? '—' : u.excitement.toFixed(1)}</td>
            <td>{u.replay_id ? <ReplayLink params={{ game: u.replay_id }} onNavigate={onNavigate} /> : <span className="bg-sub" title="No play-by-play before 2020-21">—</span>}</td>
        </tr>
    );
}

function Calibration({ options }) {
    const rows = options.calibration.filter((c) => c.games > 0);
    return (
        <details className="bg-cal">
            <summary>Do the long shots come in as often as the model says?</summary>
            <p className="page-subtitle">
                Every game since 2010-11 grouped by the underdog&apos;s pre-game win chance (each chance from a model fitted
                without that season). The two columns should match; the favourite won{' '}
                {pct(options.favourites.won, 1)} of {options.favourites.games.toLocaleString()} games against{' '}
                {pct(options.favourites.mean_chance, 1)} expected, and home teams won{' '}
                {pct(options.favourites.home_win_rate, 1)} outside 2019-20 and 2020-21.
            </p>
            <div className="table-wrapper">
                <table className="data-table lb-table">
                    <thead>
                        <tr><th>Underdog&apos;s chance</th><th className="lb-num">Games</th><th className="lb-num">Model says</th><th className="lb-num">Underdog won</th></tr>
                    </thead>
                    <tbody>
                        {rows.map((c) => (
                            <tr key={c.lo}>
                                <td>{Math.round(c.lo * 100)}–{Math.round(c.hi * 100)}%</td>
                                <td className="lb-num">{c.games.toLocaleString()}</td>
                                <td className="lb-num">{pct(c.expected, 1)}</td>
                                <td className="lb-num">{pct(c.actual, 1)}{c.games < 30 && <span className="bg-sub"> (n={c.games})</span>}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </details>
    );
}

export default function BestGames({ onNavigate }) {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        fetchBestGamesOptions()
            .then((o) => { setOptions(o); setForm(formFromParams(params, o)); })
            .catch(() => setOptionsError('Best Games & Upsets couldn\'t load. Is the impact API (port 8002) running?'));
    }, [params]);

    const isGames = form?.view === 'games';
    useUrlSync(form && {
        v: form.view === 'games' ? null : form.view, season: form.season, team: form.team,
        sort: form.sort === (isGames ? 'excitement' : 'chance') ? null : form.sort,
        ot: isGames && form.ot ? 1 : null, side: !isGames && form.team ? form.side : null,
        mg: !isGames && form.mg ? form.mg : null, n: form.pageSize === 25 ? null : form.pageSize, pg: form.page || null,
    });

    useEffect(() => {
        if (!options || !form) return undefined;
        let live = true;
        const timer = setTimeout(() => {
            setLoading(true);
            setError('');
            const common = { season: form.season ?? undefined, team: form.team ?? undefined, sort: form.sort,
                limit: form.pageSize, offset: form.page * form.pageSize };
            const req = form.view === 'games'
                ? fetchBestGames({ ...common, ot: form.ot || undefined })
                : fetchUpsets({ ...common, side: form.team ? form.side ?? undefined : undefined, min_games: form.mg || undefined });
            req.then((d) => { if (live) setData(d); })
                .catch((err) => {
                    if (!live) return;
                    setData(null);
                    const detail = err.response?.data?.detail;
                    setError(typeof detail === 'string' ? detail : 'The search failed. Check the filters.');
                })
                .finally(() => { if (live) setLoading(false); });
        }, 120);
        return () => { live = false; clearTimeout(timer); };
    }, [options, form]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, page: 0, ...patch }));
    const switchView = (view) => setForm((f) => ({
        ...f, view, page: 0, sort: view === 'games' ? 'excitement' : 'chance', ot: false, side: null, mg: 0,
        season: f.season && (view === 'games' ? f.season >= options.seasons.best[0].season : true) ? f.season : null,
    }));
    const seasonList = (isGames ? options.seasons.best : options.seasons.upsets).slice().reverse();
    const sorts = isGames ? options.sorts : options.upset_sorts;
    const matches = data && (isGames ? data.filters.ot !== undefined : data.filters.min_games !== undefined);
    const shown = matches ? data : null;
    const first = shown ? shown.offset + 1 : 0;
    const last = shown ? shown.offset + shown.results.length : 0;
    const maxPage = shown ? Math.min(Math.floor((shown.total - 1) / shown.limit), Math.floor(shown.max_offset / shown.limit)) : 0;
    const maxX = shown && isGames ? Math.max(...shown.results.map((g) => g.excitement), 1) : 1;

    return (
        <section className="dashboard-card lb-card">
            <h2 className="card-title hb-page-title">
                Best games &amp; biggest upsets
                <InfoTooltip label="How games are scored" title="Where the numbers come from">
                    {`${options.notes.coverage} ${options.notes.formula} ${options.notes.weights} ${options.notes.reconciliation}`}
                </InfoTooltip>
                <SourceBadge source={shown?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="bestgames" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Two ways to find the games worth watching again. <strong>Best games</strong> scores every regular-season game from{' '}
                {options.seasons.best[0].label} on by how much the win probability swung, how often the lead changed, whether it
                went to overtime and how close it finished. <strong>Upsets</strong> lists the games the pre-game model gave the
                winner the least chance in, since 2010-11. Regular season only: no playoff play-by-play or pre-game odds are on file.
            </p>

            <div className="pf-toggles bg-views" role="group" aria-label="View">
                <button type="button" className="pf-pill" aria-pressed={isGames} onClick={() => switchView('games')}>Best games</button>
                <button type="button" className="pf-pill" aria-pressed={!isGames} onClick={() => switchView('upsets')}>Biggest upsets</button>
            </div>

            <div className="lb-controls pf-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={form.season ?? ''} onChange={(e) => set({ season: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">All ({isGames ? options.seasons.best[0].label : options.seasons.upsets[0].label} on)</option>
                        {seasonList.map((s) => <option key={s.season} value={s.season}>{s.label}</option>)}
                    </select>
                </label>
                <label>
                    <span>Team</span>
                    <select className="input-field" value={form.team ?? ''} onChange={(e) => set({ team: e.target.value || null, side: null })}>
                        <option value="">Any team</option>
                        {options.teams.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                </label>
                <label>
                    <span>Sort by</span>
                    <select className="input-field" value={form.sort} onChange={(e) => set({ sort: e.target.value })}>
                        {sorts.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                    </select>
                </label>
                {!isGames && (
                    <>
                        <label>
                            <span>{form.team ? `${form.team} as` : 'Team as'}</span>
                            <select className="input-field" value={form.side ?? ''} disabled={!form.team} onChange={(e) => set({ side: e.target.value || null })}>
                                <option value="">Winner or loser</option>
                                <option value="won">The team that won</option>
                                <option value="lost">The favourite that lost</option>
                            </select>
                        </label>
                        <label>
                            <span>Games played</span>
                            <select className="input-field" value={form.mg} onChange={(e) => set({ mg: Number(e.target.value) })}>
                                {MIN_GAMES.map((n) => <option key={n} value={n}>{n ? `Both teams ${n}+ games in` : 'Any point of the season'}</option>)}
                            </select>
                        </label>
                    </>
                )}
                <label>
                    <span>Per page</span>
                    <select className="input-field" value={form.pageSize} onChange={(e) => set({ pageSize: Number(e.target.value) })}>
                        {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                </label>
            </div>
            {isGames && (
                <div className="pf-toggles">
                    <label className="pf-check">
                        <input type="checkbox" checked={form.ot} onChange={(e) => set({ ot: e.target.checked })} />
                        Overtime games only
                    </label>
                    <button type="button" className="action-btn pf-reset"
                        onClick={() => set({ season: null, team: null, sort: 'excitement', ot: false })}>Clear filters</button>
                </div>
            )}

            {error && <p className="error-message">{error}</p>}
            {loading && !shown && <Loader />}
            {shown && (
                <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                    {isGames ? (
                        <p className="page-subtitle lb-summary">
                            <strong>{shown.total.toLocaleString()}</strong> game{shown.total === 1 ? '' : 's'}
                            {shown.summary && <> · an average game scores {shown.summary.mean_excitement} ({shown.summary.mean_lead_changes} lead
                                changes, {pct(shown.summary.overtime_share, 1)} go to overtime, {shown.summary.mean_margin} points&apos; margin)</>}.
                        </p>
                    ) : (
                        <p className="page-subtitle lb-summary">
                            <strong>{shown.total.toLocaleString()}</strong>{' '}
                            {shown.filters.side === 'won' ? `game${shown.total === 1 ? '' : 's'} ${shown.filters.team} won as the underdog`
                                : shown.filters.side === 'lost' ? `game${shown.total === 1 ? '' : 's'} ${shown.filters.team} lost as the favourite`
                                    : `of ${shown.games.toLocaleString()} games ${shown.total === 1 ? 'was' : 'were'} won by the underdog`}
                            {shown.filters.side ? ` (of ${shown.games.toLocaleString()} games, ${pct(shown.upset_rate, 1)})` : ` (${pct(shown.upset_rate, 1)})`}; the
                            underdogs that won averaged a {pct(shown.mean_winner_chance, 1)} chance.
                        </p>
                    )}
                    {shown.results.length === 0 ? <p className="empty-message">No games match these filters.</p> : (
                        <>
                            <TableExport name={isGames ? 'best-games' : 'upsets'} />
                            <div className="table-wrapper">
                                {isGames ? (
                                    <table className="data-table lb-table pf-table bg-table">
                                        <thead>
                                            <tr>
                                                <th>#</th><th>Date</th><th>Game</th><th>Score</th>
                                                <th className="lb-num" title={options.formula.text}>Excitement</th>
                                                <th className="lb-num" title="Sum of the win-probability changes from play to play; 1.0 = 100 points in all">Swing</th>
                                                <th className="lb-num">Lead changes</th>
                                                <th className="lb-num">Comeback</th>
                                                <th className="lb-num" title="The winner's lowest win probability at any point">Winner&apos;s low</th>
                                                <th className="lb-num" title="The winner's pre-game win chance">Pre-game</th>
                                                <th>Biggest swing</th><th>Replay</th>
                                            </tr>
                                        </thead>
                                        <tbody>{shown.results.map((g) => <GameRow key={g.game_id} g={g} max={maxX} onNavigate={onNavigate} />)}</tbody>
                                    </table>
                                ) : (
                                    <table className="data-table lb-table pf-table bg-table">
                                        <thead>
                                            <tr>
                                                <th>#</th><th>Date</th><th>Game</th><th>Score</th><th>Winner</th>
                                                <th className="lb-num" title="The winner's pre-game win chance">Chance</th>
                                                <th className="lb-num" title="Points the winner was expected to win by (negative = expected to lose)">Expected</th>
                                                <th className="lb-num">Won by</th>
                                                <th className="lb-num" title="Games each team had played that morning (home, away)">Games in</th>
                                                <th>Notes</th>
                                                <th className="lb-num" title={options.formula.text}>Excitement</th>
                                                <th>Replay</th>
                                            </tr>
                                        </thead>
                                        <tbody>{shown.results.map((u) => <UpsetRow key={u.game_id} u={u} onNavigate={onNavigate} />)}</tbody>
                                    </table>
                                )}
                            </div>
                        </>
                    )}
                    {shown.total > shown.limit && (
                        <div className="gf-pager">
                            <button type="button" className="action-btn" disabled={form.page === 0}
                                onClick={() => setForm((f) => ({ ...f, page: f.page - 1 }))}>← Previous</button>
                            <span>{first.toLocaleString()}–{last.toLocaleString()} of {shown.total.toLocaleString()}</span>
                            <button type="button" className="action-btn" disabled={form.page >= maxPage}
                                onClick={() => setForm((f) => ({ ...f, page: f.page + 1 }))}>Next →</button>
                            <span>
                                (Export saves the rows on this page.
                                {shown.total > shown.max_offset + shown.limit && ` Paging stops at ${(shown.max_offset + shown.limit).toLocaleString()} rows: narrow the filters to see the rest.`})
                            </span>
                        </div>
                    )}
                    {isGames ? (
                        <p className="page-subtitle pf-foot">
                            {options.notes.formula} {options.notes.weights} Replay opens Game Replay at the play that moved win
                            probability most (its clock is in the last column). {options.notes.reconciliation} Excitement rank (
                            #) is among every ranked game in the same seasons. Winner&apos;s low is the winner&apos;s lowest win
                            probability at any moment; a comeback is the winner&apos;s biggest deficit.
                        </p>
                    ) : (
                        <>
                            <p className="page-subtitle pf-foot">
                                {options.notes.upsets} Games with no Replay link are before 2020-21, where no play-by-play is on file.
                                Early in a season a team&apos;s rating is mostly last year&apos;s: use &ldquo;Games played&rdquo; to drop
                                the first weeks.
                            </p>
                            <Calibration options={options} />
                        </>
                    )}
                </div>
            )}
        </section>
    );
}
