import React, { useEffect, useMemo, useState } from 'react';
import { fetchOnOff, fetchOnOffStars } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import TableExport from './common/TableExport';
import CopyLinkButton from './common/CopyLinkButton';
import PlayerName from './common/PlayerName';
import TeamLogo from './common/TeamLogo';
import TeamLink from './common/TeamLink';
import { currentPageParam, parseParam, useInitialParams, useUrlSync } from '../utils/useUrlState';

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const signed = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}`);
const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const int = (v) => (v == null ? '—' : Math.round(v).toLocaleString());
const pct = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
const tone = (v) => (v == null || v === 0 ? '' : v > 0 ? 'oo-pos' : 'oo-neg');

const VIEWS = [
    { id: 'team', label: 'One team' },
    { id: 'league', label: 'League leaders' },
    { id: 'stars', label: 'Stars: team with and without' },
];
const URL_KEYS = ['season', 'team', 'view', 'min', 'sort', 'dir'];
const CI_RANGE = 30; // points per 100 possessions from zero to the bar's edge

const COLUMNS = {
    player_name: { label: 'Player', text: true },
    team_abbreviation: { label: 'Team', text: true },
    games: { label: 'GP', title: 'Games he played' },
    minutes_on: { label: 'Min on', title: 'Minutes on the floor' },
    minutes_off: { label: 'Min off', title: 'Minutes his team played without him, in games he played' },
    ortg_on: { label: 'ORtg on' },
    drtg_on: { label: 'DRtg on' },
    net_on: { label: 'Net on', title: 'Team net rating with him on the floor' },
    net_off: { label: 'Net off', title: 'Team net rating with him off the floor, in games he played' },
    on_off_net: { label: 'On − Off', title: 'Net on minus net off, per 100 possessions' },
    usg_pct: { label: 'Usage', title: 'His share of the team\'s shooting possessions while on the floor' },
};

function formFromParams(p) {
    return {
        season: parseParam.int(p, 'season', { min: 2000, max: 2100 }),
        team: parseParam.str(p, 'team')?.toUpperCase() ?? null,
        view: parseParam.oneOf(p, 'view', VIEWS.map((v) => v.id)) ?? 'team',
        minMinutes: parseParam.num(p, 'min', { min: 0, max: 3000 }) ?? 500,
        sort: parseParam.oneOf(p, 'sort', Object.keys(COLUMNS)) ?? 'on_off_net',
        dir: parseParam.oneOf(p, 'dir', ['asc', 'desc']) ?? 'desc',
    };
}

// A small range bar: zero line in the middle, the 95% interval as a whisker,
// the estimate as a dot (filled when the interval clears zero).
function CiBar({ est, lo, hi, excludes }) {
    if (est == null) return null;
    const w = 120;
    const h = 16;
    const mid = w / 2;
    const x = (v) => mid + Math.max(-1, Math.min(1, v / CI_RANGE)) * (mid - 5);
    const color = est > 0 ? 'var(--positive)' : est < 0 ? 'var(--negative)' : 'var(--text-3)';
    return (
        <svg className="oo-ci" width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true" data-export-skip>
            <line x1={mid} x2={mid} y1={2} y2={h - 2} stroke="var(--line)" strokeWidth={1} opacity={0.5} />
            {lo != null && (
                <line x1={x(lo)} x2={x(hi)} y1={h / 2} y2={h / 2} stroke={excludes ? color : 'var(--text-3)'}
                    strokeWidth={2} strokeLinecap="round" />
            )}
            <circle cx={x(est)} cy={h / 2} r={3.5} fill={excludes ? color : 'var(--surface)'} stroke={color} strokeWidth={1.5} />
        </svg>
    );
}

function IntervalCell({ row }) {
    if (row.on_off_ci_low == null) return <td className="lb-num oo-ci-cell">—</td>;
    const text = `${signed(row.on_off_ci_low)} to ${signed(row.on_off_ci_high)}`;
    return (
        <td className="lb-num oo-ci-cell" title={`95% interval ${text}${row.ci_excludes_zero ? ' (clear of zero)' : ' (includes zero: within noise)'}`}>
            <CiBar est={row.on_off_net} lo={row.on_off_ci_low} hi={row.on_off_ci_high} excludes={row.ci_excludes_zero} />
            <span className="oo-ci-text">{text}</span>
        </td>
    );
}

function SortHeader({ colKey, sort, dir, onSort, className }) {
    const c = COLUMNS[colKey];
    const active = sort === colKey;
    const ariaSort = active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none';
    return (
        <th className={className} aria-sort={ariaSort} title={c.title}>
            <button type="button" className={`oo-sort ${active ? 'oo-sort--active' : ''}`} onClick={() => onSort(colKey)}>
                {c.label}{active && <span aria-hidden="true">{dir === 'asc' ? ' ▲' : ' ▼'}</span>}
            </button>
        </th>
    );
}

function sortRows(rows, sort, dir) {
    const text = COLUMNS[sort]?.text;
    const sign = dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
        const va = a[sort];
        const vb = b[sort];
        if (va == null && vb == null) return 0;
        if (va == null) return 1;
        if (vb == null) return -1;
        return text ? sign * String(va).localeCompare(String(vb)) : sign * (va - vb);
    });
}

export default function OnOffSection() {
    const params = useInitialParams();
    const [form, setForm] = useState(() => formFromParams(params));
    const [data, setData] = useState(null);
    const [stars, setStars] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    const shownSeason = form.season ?? data?.season ?? stars?.season ?? null;
    const shownTeam = form.view === 'team' ? (form.team ?? data?.team ?? null) : null;
    useUrlSync({
        season: shownSeason, team: shownTeam, view: form.view === 'team' ? null : form.view,
        min: form.view === 'stars' ? null : (form.minMinutes === '' ? 0 : form.minMinutes),
        sort: form.sort === 'on_off_net' ? null : form.sort, dir: form.dir === 'desc' ? null : form.dir,
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
                if (form.view === 'stars') {
                    setStars(await fetchOnOffStars({ season: form.season ?? undefined }));
                } else {
                    const res = await fetchOnOff({
                        season: form.season ?? undefined,
                        team: form.view === 'team' ? (form.team ?? undefined) : undefined,
                        min_minutes: form.minMinutes === '' ? 0 : form.minMinutes,
                    });
                    // The team view is a fresh URL only once the endpoint has picked a team.
                    if (form.view === 'team' && res.team == null) res.team = res.teams[0];
                    setData(res);
                }
            } catch (e) {
                setError(e?.response?.data?.detail || 'Could not load on/off data. Is the impact API (port 8002) running?');
            } finally {
                setLoading(false);
            }
        }, 250);
        return () => clearTimeout(timer);
    }, [form.season, form.team, form.view, form.minMinutes]);

    // The team view loads whichever team the endpoint picked when the link had none.
    useEffect(() => {
        if (form.view === 'team' && form.team == null && data?.team) setForm((f) => ({ ...f, team: data.team }));
    }, [form.view, form.team, data]);

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const onSort = (key) => setForm((f) => (
        f.sort === key ? { ...f, dir: f.dir === 'asc' ? 'desc' : 'asc' } : { ...f, sort: key, dir: COLUMNS[key].text ? 'asc' : 'desc' }
    ));

    const source = form.view === 'stars' ? stars : data;
    const rows = useMemo(() => {
        if (form.view === 'stars') return stars ? sortRows(stars.stars, form.sort, form.dir) : [];
        return data ? sortRows(data.players, form.sort, form.dir) : [];
    }, [form.view, form.sort, form.dir, data, stars]);

    if (!source && loading) return <Loader />;
    if (!source) return <section className="dashboard-card"><p className="error-message">{error}</p></section>;

    const seasons = [...source.seasons_available].reverse();
    const season = source.season;
    const isTeam = form.view === 'team' && data;
    const isLeague = form.view === 'league' && data;
    const isStars = form.view === 'stars' && stars;
    const floor = form.minMinutes === '' ? 0 : form.minMinutes;
    const showTeamCol = !isTeam;
    const cols = ['player_name', ...(showTeamCol ? ['team_abbreviation'] : []), 'games', 'minutes_on', 'minutes_off',
        'ortg_on', 'drtg_on', 'net_on', 'net_off', 'on_off_net'];

    return (
        <section className="dashboard-card lb-card oo-card">
            <h2 className="card-title hb-page-title">
                On/off: the team with him on the floor, and without
                <InfoTooltip label="How on/off is computed" title="Under the hood">
                    {source.method}
                </InfoTooltip>
                <SourceBadge source={source._source} />
                <CopyLinkButton />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Every minute of every regular-season game since 2020-21, rebuilt from play-by-play (not just the stored
                lineups). Net rating with a player on the floor, the same without him in the games he played, and the
                difference with a 95% interval, because most on/off numbers are noise.
            </p>

            <div className="tab-bar lb-modes" role="tablist" aria-label="View" style={{ marginTop: '0.75rem' }}>
                {VIEWS.map((v) => (
                    <button key={v.id} type="button" role="tab" aria-selected={form.view === v.id}
                        className={`tab-btn ${form.view === v.id ? 'tab-btn--active' : ''}`}
                        onClick={() => set({ view: v.id })}>
                        {v.label}
                    </button>
                ))}
            </div>

            <div className="lb-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={season} onChange={(e) => set({ season: Number(e.target.value) })}>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                {isTeam && (
                    <label>
                        <span>Team</span>
                        <select className="input-field" value={data.team} onChange={(e) => set({ team: e.target.value })}>
                            {data.teams.map((tm) => <option key={tm} value={tm}>{tm}</option>)}
                        </select>
                    </label>
                )}
                {!isStars && (
                    <label>
                        <span>Min. minutes on</span>
                        <input className="input-field" type="number" min={0} max={3000} step={50} value={form.minMinutes}
                            onChange={(e) => set({ minMinutes: e.target.value === '' ? '' : Number(e.target.value) })} />
                    </label>
                )}
            </div>

            {error && <p className="error-message">{error}</p>}

            <div className={loading ? 'lb-results lb-results--stale' : 'lb-results'} aria-busy={loading}>
                {isTeam && data.team_summary && (
                    <p className="rx-verdict">
                        <strong>{data.team} {seasonLabel(season)}, {data.team_summary.wins}-{data.team_summary.losses},
                            net {signed(data.team_summary.net_rating)}</strong> per 100 possessions over{' '}
                        {int(data.team_summary.poss)} possessions (ORtg {num(data.team_summary.ortg)}, DRtg {num(data.team_summary.drtg)}).
                        The rebuilt lineups account for {pct(data.team_summary.tracked_share)} of its player-minutes.{' '}
                        {data.star ? (
                            <>Its top-usage regular is <strong>{data.star.player_name}</strong> ({(data.star.usg_pct * 100).toFixed(1)}% usage):
                                the team is <strong className={tone(data.star.net_on)}>{signed(data.star.net_on)}</strong> with him on the floor
                                and <strong className={tone(data.star.net_off)}>{signed(data.star.net_off)}</strong> without him,
                                a gap of {signed(data.star.on_off_net)} ({signed(data.star.on_off_ci_low)} to {signed(data.star.on_off_ci_high)}).</>
                        ) : `No player reached ${int(data.star_minutes)} minutes, so no top-usage regular is named.`}{' '}
                        {data.noise.qualified > 0 && (
                            <>Of {data.noise.qualified} players with {int(floor)}+ minutes, {data.noise.ci_excludes_zero} have an
                                interval clear of zero; about {data.noise.expected_by_chance} would by chance.</>
                        )}
                    </p>
                )}
                {isLeague && (
                    <p className="rx-verdict">
                        <strong>{seasonLabel(season)}: {data.noise.qualified} players with {int(floor)}+ minutes on the floor.</strong>{' '}
                        {data.noise.ci_excludes_zero} of them have a 95% interval clear of zero; about {data.noise.expected_by_chance} would
                        by chance alone, so most of the rest is noise. A traded player appears once per team.
                        League check: possession-weighted on-court net across all players is {signed(data.season_summary.league_net_on_weighted, 2)}{' '}
                        (it should be about zero), and the lineups cover {pct(data.season_summary.tracked_share)} of player-minutes.
                    </p>
                )}
                {isStars && (
                    <p className="rx-verdict">
                        <strong>{seasonLabel(season)}: each team&apos;s top-usage regular ({int(stars.star_minutes)}+ minutes) and how the team
                            did with and without him.</strong>{' '}
                        This is the &ldquo;fragility&rdquo; half of the Heliocentricity idea, measured from real minutes rather than simulated:
                        a big gap means the team fell apart when he sat. It is also who his backups are, and when he sat (bench units,
                        garbage time), so read it with the interval.
                        {stars.teams_without_star.length > 0 && ` No player reached the floor for ${stars.teams_without_star.join(', ')}.`}
                    </p>
                )}

                <TableExport name={`on off ${isStars ? 'stars' : isTeam ? data.team : 'league'} ${seasonLabel(season)}`} />
                <div className="table-wrapper">
                    <table className="data-table lb-table oo-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                {cols.map((k) => (
                                    <SortHeader key={k} colKey={k} sort={form.sort} dir={form.dir} onSort={onSort}
                                        className={COLUMNS[k].text ? undefined : `lb-num ${k === 'on_off_net' ? 'lb-stat' : ''}`} />
                                ))}
                                <th className="lb-num" title="Bootstrap 95% interval for On − Off, resampling his games">95% interval</th>
                                <SortHeader colKey="usg_pct" sort={form.sort} dir={form.dir} onSort={onSort} className="lb-num" />
                                {isStars && <th className="lb-num" title="The team's full-season net rating">Team net</th>}
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r, i) => {
                                const short = !r.qualified;
                                const flags = [
                                    short && `Under ${int(isStars ? stars.star_minutes : floor)} minutes on the floor: treat as noise`,
                                    r.few_off_minutes && `Only ${int(r.minutes_off)} minutes without him: the off-court side is a small sample`,
                                ].filter(Boolean);
                                return (
                                    <tr key={`${r.player_id}-${r.team_abbreviation}`} className={short ? 'sl-short' : undefined}
                                        title={flags.join('. ') || undefined}>
                                        <td>{i + 1}</td>
                                        <td>
                                            <PlayerName playerId={r.player_id} name={r.player_name ?? `#${r.player_id}`} size={24} />
                                            {flags.length > 0 && <span className="oo-flag" aria-label={flags.join('. ')} title={flags.join('. ')} data-export-as={short ? ' (small sample)' : ' (few off minutes)'}>†</span>}
                                        </td>
                                        {showTeamCol && (
                                            <td className="oo-team"><TeamLink abbr={r.team_abbreviation} season={season} /></td>
                                        )}
                                        <td className="lb-num">{r.games}</td>
                                        <td className="lb-num">{int(r.minutes_on)}</td>
                                        <td className="lb-num">{int(r.minutes_off)}</td>
                                        <td className="lb-num">{num(r.ortg_on)}</td>
                                        <td className="lb-num">{num(r.drtg_on)}</td>
                                        <td className={`lb-num ${tone(r.net_on)}`}>{signed(r.net_on)}</td>
                                        <td className={`lb-num ${tone(r.net_off)}`}>{signed(r.net_off)}</td>
                                        <td className={`lb-num lb-stat ${tone(r.on_off_net)}`}>{signed(r.on_off_net)}</td>
                                        <IntervalCell row={r} />
                                        <td className="lb-num">{r.usg_pct == null ? '—' : `${(r.usg_pct * 100).toFixed(1)}%`}</td>
                                        {isStars && (
                                            <td className="lb-num">{r.team ? `${signed(r.team.net_rating)} (${r.team.wins}-${r.team.losses})` : '—'}</td>
                                        )}
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
                {rows.length === 0 && <p className="empty-message">No player clears the minutes floor. Lower it to see more.</p>}
                <p className="page-subtitle lb-summary oo-foot">
                    Ratings are points per 100 possessions (FGA + 0.44 FTA − OREB + TOV, the Basketball-Reference convention, so about 3
                    points under NBA.com&apos;s scale). Off-court minutes are the team&apos;s minutes without him in games he played; games he
                    missed are in With/Without a Star instead. † marks a row under the minutes floor or with few off-court minutes.
                    Descriptive, not adjusted for teammates or opponents: not RAPM.
                </p>
            </div>
        </section>
    );
}
