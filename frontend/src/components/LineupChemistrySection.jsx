import React, { useEffect, useState } from 'react';
import { fetchLineupChemistry } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import PlayerName from './common/PlayerName';
import TeamLogo from './common/TeamLogo';
import TeamLink from './common/TeamLink';
import TableExport from './common/TableExport';
import CopyLinkButton from './common/CopyLinkButton';
import SaveViewButton from './common/SaveViewButton';
import { currentPageParam, parseParam, useInitialParams, useUrlSync } from '../utils/useUrlState';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const URL_KEYS = ['season', 'order', 'min'];
const DEFAULT_MIN = 100;

function netRatingColor(v) {
    if (v == null) return 'var(--text-secondary)';
    if (v > 0) return 'var(--positive)';
    if (v < 0) return 'var(--negative)';
    return 'var(--text-secondary)';
}

function formFromParams(p) {
    return {
        season: parseParam.int(p, 'season', { min: 1990, max: 2100 }),
        order: parseParam.oneOf(p, 'order', ['best', 'worst']) ?? 'best',
        minMinutes: parseParam.num(p, 'min', { min: 0, max: 3000 }) ?? DEFAULT_MIN,
    };
}

export default function LineupChemistrySection() {
    const params = useInitialParams();
    const [form, setForm] = useState(() => formFromParams(params));
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    const shownSeason = form.season ?? data?.season ?? null;
    useUrlSync({ season: shownSeason, order: form.order, min: form.minMinutes === '' ? 0 : form.minMinutes });

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
        let active = true;
        const timer = setTimeout(async () => {
            setLoading(true);
            setError('');
            try {
                const res = await fetchLineupChemistry(form.order, form.minMinutes === '' ? 0 : form.minMinutes, 15, form.season ?? undefined);
                if (active) setData(res);
            } catch (e) {
                if (active) {
                    setData(null);
                    setError(e?.response?.data?.detail || 'Could not load lineup chemistry data. Is the impact API (port 8002) running?');
                }
            } finally {
                if (active) setLoading(false);
            }
        }, 250);
        return () => { active = false; clearTimeout(timer); };
    }, [form.order, form.minMinutes, form.season]);

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));

    if (!data && loading) return <Loader />;
    if (!data) return <section className="dashboard-card"><p className="error-message">{error}</p></section>;

    const seasons = [...data.seasons_available].reverse();
    const stints = data.source === 'stints';
    const check = data.season_check;

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Lineup Chemistry
                    <InfoTooltip label="How this works" title="Real 5-man lineups, not a trade simulation">
                        {data.methodology}
                    </InfoTooltip>
                    <SourceBadge source={data._source} />
                    <CopyLinkButton />
                    <SaveViewButton pageId="analytics" />
                </h3>
                <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
                    Real five-man lineups that shared the floor, ranked by net rating (points per 100 possessions).
                    Not a simulation: only lineups that actually played together, with a stated minutes floor.
                </p>
                <div className="lb-controls">
                    <label>
                        <span>Season</span>
                        <select className="input-field" value={data.season} onChange={(e) => set({ season: Number(e.target.value) })}>
                            {seasons.map((s) => (
                                <option key={s} value={s}>
                                    {seasonLabel(s)}{data.sources[String(s)] === 'lineup_stats' ? ' · top 2,000 only' : ''}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label>
                        <span>Min. shared minutes</span>
                        <input className="input-field" type="number" min={0} max={3000} step={10} value={form.minMinutes}
                            onChange={(e) => set({ minMinutes: e.target.value === '' ? '' : Number(e.target.value) })} />
                    </label>
                </div>
                <div className="tab-bar" role="tablist" aria-label="Order" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
                    <button type="button" role="tab" aria-selected={form.order === 'best'}
                        className={`tab-btn ${form.order === 'best' ? 'tab-btn--active' : ''}`} onClick={() => set({ order: 'best' })}>
                        Best Chemistry
                    </button>
                    <button type="button" role="tab" aria-selected={form.order === 'worst'}
                        className={`tab-btn ${form.order === 'worst' ? 'tab-btn--active' : ''}`} onClick={() => set({ order: 'worst' })}>
                        Worst Chemistry
                    </button>
                </div>
                <p className="rx-verdict" style={{ marginTop: '0.75rem' }}>
                    <strong>{seasonLabel(data.season)}, {data.source_label}.</strong>{' '}
                    {data.lineups_qualified.toLocaleString()} of {data.lineups_total.toLocaleString()} lineups have at least{' '}
                    {data.min_minutes.toFixed(0)} shared minutes.{' '}
                    {stints && check && (
                        <>
                            Every stint of every game was rebuilt from play-by-play: {check.games_ok} of {check.games} games reconcile
                            with the real final score, game length and team totals, and {Math.round(check.tracked_minutes_share * 1000) / 10}% of
                            the season&apos;s minutes are tracked (the rest had a player with no id in the play-by-play, or a game that
                            didn&apos;t reconcile).
                        </>
                    )}
                    {!stints && 'Only the 2,000 most-used lineups a season are stored for seasons before 2020-21, so shorter-used units are missing.'}
                </p>
                {error && <p className="error-message">{error}</p>}
            </div>

            <div className={loading ? 'dashboard-card lb-results lb-results--stale' : 'dashboard-card lb-results'} aria-busy={loading} style={{ marginTop: '1rem' }}>
                {data.results.length === 0 ? (
                    <p className="empty-message">No lineup clears the minutes floor. Lower it to see more.</p>
                ) : (
                    <>
                        <TableExport name={`lineup chemistry ${seasonLabel(data.season)} ${form.order}`} />
                        <div className="hb-table-wrapper table-wrapper">
                            <table className="data-table lb-table">
                                <thead>
                                    <tr>
                                        <th>Rank</th>
                                        <th>Lineup</th>
                                        <th>Team</th>
                                        <th className="lb-num">GP</th>
                                        <th className="lb-num">Min</th>
                                        <th className="lb-num">Poss</th>
                                        <th className="lb-num">ORtg</th>
                                        <th className="lb-num">DRtg</th>
                                        <th className="lb-num lb-stat">Net</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.results.map((r) => (
                                        <tr key={r.rank}>
                                            <td>{r.rank}</td>
                                            <td>
                                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px' }}>
                                                    {r.players.map((p) => (
                                                        <PlayerName key={p.player_id} playerId={p.player_id} name={p.player_name} size={22} />
                                                    ))}
                                                </div>
                                            </td>
                                            <td><TeamLink abbr={r.team_abbreviation} season={data.season}><TeamLogo abbreviation={r.team_abbreviation} size={20} /></TeamLink></td>
                                            <td className="lb-num">{r.gp ?? '—'}</td>
                                            <td className="lb-num">{r.min.toFixed(0)}</td>
                                            <td className="lb-num">{r.poss != null ? r.poss.toLocaleString() : '—'}</td>
                                            <td className="lb-num">{r.off_rating.toFixed(1)}</td>
                                            <td className="lb-num">{r.def_rating.toFixed(1)}</td>
                                            <td className="lb-num lb-stat" style={{ color: netRatingColor(r.net_rating), fontWeight: 700 }}>
                                                <Icon
                                                    name={r.net_rating > 0 ? 'arrow_upward' : r.net_rating < 0 ? 'arrow_downward' : 'remove'}
                                                    size="0.9em"
                                                    style={{ verticalAlign: 'middle', marginRight: 2 }}
                                                />
                                                {r.net_rating > 0 ? '+' : ''}{r.net_rating.toFixed(1)}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
