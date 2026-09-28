import React, { useEffect, useMemo, useState } from 'react';
import { fetchPlayerFullProfile, fetchProfileShotZones, fetchSeasonSimilarityProfile } from '../../services/api';
import Loader from '../Loader';
import CopyLinkButton from '../common/CopyLinkButton';
import GameLogBlock from '../common/GameLogBlock';
import SaveViewButton from '../common/SaveViewButton';
import InfoTooltip from '../common/InfoTooltip';
import PlayerHeadshot from '../common/PlayerHeadshot';
import PlayerName from '../common/PlayerName';
import ScoutingReportCard from '../common/ScoutingReportCard';
import ShotMixHistory from '../common/ShotMixHistory';
import { ShotMakingInfo, ShotMakingTable } from '../common/ShotMaking';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLogo from '../common/TeamLogo';
import ZoneCourtMap from '../common/ZoneCourtMap';
import { openPage, parseParam, useInitialParams } from '../../utils/useUrlState';
import '../../styles/profile.css';

// One page per player (?page=player&id=<NBA person id>). Everything comes
// from GET /player-profile/{id} except the similar seasons (similarity
// service) and the scouting report (its own card). A block with no data for
// this player is listed under "Not on file" with the reason, never shown empty.

const label = (s) => `${s - 1}-${String(s).slice(-2)}`;
const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const pct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const signed = (v, d = 1) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}`);
const money = (v) => (v == null ? '—' : `${v < 0 ? '−' : ''}$${(Math.abs(v) / 1e6).toFixed(1)}M`);
const tone = (v) => (v == null || v === 0 ? '' : v > 0 ? 'pp-pos' : 'pp-neg');
const ordinal = (n) => {
    const s = ['th', 'st', 'nd', 'rd'];
    const v = n % 100;
    return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
};
const height = (inches) => (inches ? `${Math.floor(inches / 12)}-${inches % 12}` : null);
const birth = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US',
    { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
const span = (c) => `${label(c.from)} to ${label(c.to)}`;

// [2006, 2007, 2009] -> "2005-06 to 2006-07, 2008-09"
function seasonRanges(seasons) {
    const out = [];
    for (const s of seasons) {
        const last = out[out.length - 1];
        if (last && s === last[1] + 1) last[1] = s;
        else out.push([s, s]);
    }
    return out.map(([a, b]) => (a === b ? label(a) : `${label(a)} to ${label(b)}`)).join(', ');
}

const VOTED = ['MVP', 'Defensive Player of the Year', 'Rookie of the Year', 'Sixth Man of the Year',
    'Most Improved Player', 'Clutch Player of the Year'];
const TEAMS = ['All-NBA', 'All-BAA', 'All-Defense', 'All-Rookie'];

function Section({ id, title, info, meta, children }) {
    return (
        <section id={`pp-${id}`} className="dashboard-card pp-section">
            <h2 className="card-title pp-section-title">{title}{info}</h2>
            {meta && <p className="page-subtitle pp-meta">{meta}</p>}
            {children}
        </section>
    );
}

function SeasonSelect({ value, seasons, onChange, labelText = 'Season', format = label }) {
    return (
        <label className="pp-select">
            <span>{labelText}</span>
            <select className="input-field" value={value} onChange={(e) => onChange(Number(e.target.value))}>
                {[...seasons].reverse().map((s) => <option key={s} value={s}>{format(s)}</option>)}
            </select>
        </label>
    );
}

// ── Header ──────────────────────────────────────────────────────────────
function Hero({ data }) {
    const p = data.player;
    const cov = data.coverage;
    const facts = [
        p.bio?.position,
        height(p.bio?.height_in),
        p.bio?.weight_lb && `${p.bio.weight_lb} lb`,
        p.bio?.birth_date && `Born ${birth(p.bio.birth_date)}`,
        p.bio?.colleges,
    ].filter(Boolean);
    const rookieNow = p.rookie_season === cov.seasons.to;
    return (
        <header className="pp-hero">
            <PlayerHeadshot playerId={p.player_id} playerName={p.player_name} size={132} className="pp-hero-photo" />
            <div className="pp-hero-text">
                <span className="page-eyebrow">NBA Hub · Player profile</span>
                <h1 className="pp-name">{p.player_name}</h1>
                {facts.length > 0 && <p className="pp-facts">{facts.join(' · ')}</p>}
                <div className="pp-chips">
                    <span className="pp-chip">
                        <TeamLogo abbreviation={p.last_team} size={20} />
                        {p.active ? `${p.last_team} · ${label(p.last_season)}` : `Last played ${label(p.last_season)} · ${p.last_team}`}
                    </span>
                    <span className="pp-chip">
                        {p.n_seasons} season{p.n_seasons === 1 ? '' : 's'} · {label(p.first_season)}
                        {p.last_season !== p.first_season && ` to ${label(p.last_season)}`}
                    </span>
                    {rookieNow && <span className="pp-chip pp-chip--hi">Rookie, {label(p.rookie_season)}</span>}
                    <span className="pp-chip">
                        {p.draft
                            ? `Drafted ${p.draft.year}${p.draft.pick ? ` · round ${p.draft.round}, pick ${p.draft.pick}` : ''} · ${p.draft.team}`
                            : 'No draft pick on record'}
                    </span>
                    {p.role && (
                        <span className="pp-chip">
                            Role: {p.role.role} ({label(p.role.season)})
                            <InfoTooltip label="How roles are assigned" title={p.role.role}>
                                {p.role.description ? `Stands out for: ${p.role.description}. ` : ''}
                                One of 10 roles clustered from how players score, pass, rebound and defend
                                ({p.role_rule}). Shown for his latest qualifying season; the season table lists every year.
                            </InfoTooltip>
                        </span>
                    )}
                    {p.bio?.hall_of_fame && <span className="pp-chip pp-chip--hi">Hall of Fame</span>}
                    {p.greats && <span className="pp-chip pp-chip--hi">{p.greats.group}</span>}
                </div>
                <div className="pp-actions">
                    <CopyLinkButton />
                    <SaveViewButton pageId="player" title={`${p.player_name} — Player profile`} />
                    <SourceBadge source={data._source} />
                </div>
            </div>
        </header>
    );
}

function Glance({ row }) {
    const tiles = [
        ['Points', num(row.pts)], ['Rebounds', num(row.reb)], ['Assists', num(row.ast)],
        ['True shooting', pct(row.ts_pct)], ['BPM', signed(row.bpm)],
    ];
    return (
        <div className={`pp-glance${row.small_sample ? ' pp-small' : ''}`}>
            <span className="pp-glance-label">
                {label(row.season)} · {row.gp ?? '—'} games · {num(row.min)} min
                {row.small_sample && ' · small sample'}
            </span>
            <div className="pp-glance-tiles">
                {tiles.map(([k, v]) => (
                    <div key={k} className="pp-tile">
                        <span className="pp-tile-value">{v}</span>
                        <span className="pp-tile-label">{k}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

// ── Season by season ────────────────────────────────────────────────────
function SeasonsTable({ block }) {
    const rows = block.rows;
    const hasRole = rows.some((r) => r.role);
    const first = rows[0].season;
    const fr = block.first_recorded;
    const lateStats = [
        [fr.stl, 'steals and blocks'], [fr.tov, 'turnovers and usage'], [fr.fg3a, 'threes'],
    ].filter(([s]) => first < s);
    return (
        <Section id="seasons" title="Season by season"
            meta={<>Regular season, per game. Greyed rows: under {block.small_gp} games. A traded season lists games per team.</>}>
            <TableExport />
            <div className="table-wrapper">
                <table className="data-table lb-table pp-table">
                    <thead>
                        <tr>
                            <th>Season</th><th>Team</th><th className="lb-num">Age</th><th className="lb-num">GP</th>
                            <th className="lb-num">MIN</th><th className="lb-num">PTS</th><th className="lb-num">REB</th>
                            <th className="lb-num">AST</th><th className="lb-num">STL</th><th className="lb-num">BLK</th>
                            <th className="lb-num">TS%</th><th className="lb-num">3PA</th><th className="lb-num">3P%</th>
                            <th className="lb-num">FT%</th><th className="lb-num">USG%</th><th className="lb-num">BPM</th>
                            <th className="lb-num">VORP</th>{hasRole && <th>Role</th>}
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.season} className={r.small_sample ? 'pp-small' : ''}>
                                <td>{label(r.season)}</td>
                                <td>
                                    {r.stints.length
                                        ? <span title="Traded: games per team">{r.stints.map((s) => `${s.team} ${s.gp}`).join(' · ')}</span>
                                        : r.team_abbreviation}
                                </td>
                                <td className="lb-num">{num(r.age, 0)}</td>
                                <td className="lb-num">{r.gp ?? '—'}</td>
                                <td className="lb-num">{num(r.min)}</td>
                                <td className="lb-num lb-stat">{num(r.pts)}</td>
                                <td className="lb-num">{num(r.reb)}</td>
                                <td className="lb-num">{num(r.ast)}</td>
                                <td className="lb-num">{num(r.stl)}</td>
                                <td className="lb-num">{num(r.blk)}</td>
                                <td className="lb-num">{pct(r.ts_pct)}</td>
                                <td className="lb-num">{num(r.fg3a)}</td>
                                <td className="lb-num">{r.fg3a ? pct(r.fg3_pct) : '—'}</td>
                                <td className="lb-num">{r.fta ? pct(r.ft_pct) : '—'}</td>
                                <td className="lb-num">{pct(r.usg_pct)}</td>
                                <td className={`lb-num ${tone(r.bpm)}`}>{signed(r.bpm)}</td>
                                <td className="lb-num">{num(r.vorp)}</td>
                                {hasRole && <td>{r.role || '—'}</td>}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle pp-foot">
                {lateStats.length > 0 && (
                    <>A dash means the league didn&apos;t record it yet: {lateStats.map(([s, what]) => `${what} from ${label(s)}`).join(', ')}.{' '}</>
                )}
                Ages from 2009-10 on are NBA.com&apos;s, which count later in the season than Basketball-Reference&apos;s
                (about 45% of players show a year older). BPM and VORP are Basketball-Reference&apos;s published values.
            </p>
        </Section>
    );
}

// ── Awards ──────────────────────────────────────────────────────────────
function Awards({ rows, greats, coverage, onNavigate }) {
    const counts = [];
    for (const a of VOTED) {
        const n = rows.filter((r) => r.award === a && r.winner).length;
        if (n) counts.push(`${a} ×${n}`);
    }
    for (const t of TEAMS) {
        const n = rows.filter((r) => r.award === t).length;
        const firsts = rows.filter((r) => r.award === t && r.detail === '1st team').length;
        if (n) counts.push(`${t} ×${n}${firsts && firsts !== n ? ` (1st team ×${firsts})` : ''}`);
    }
    const stars = rows.filter((r) => r.award === 'All-Star').length;
    if (stars) counts.push(`All-Star ×${stars}`);

    const result = (r) => {
        if (VOTED.includes(r.award)) {
            if (r.winner) return r.vote_share != null ? `Won · ${pct(r.vote_share)} of max points` : 'Won (no vote count published)';
            return `${ordinal(r.finish)} in voting · ${pct(r.vote_share)} of max points`;
        }
        if (r.award === 'All-Star') return r.detail ? `Selected, ${r.detail.replace(/^selected, /, '')}` : 'Selected';
        return r.detail;
    };

    return (
        <Section id="awards" title="Awards and honours"
            meta={<>Basketball-Reference, BAA/NBA only. Season awards and teams through {label(coverage.awards.to)},
                All-Star through {label(coverage.all_star.to)}. Voting finishes count every player who got a vote.</>}>
            {counts.length > 0 && (
                <div className="pp-chips pp-award-counts">
                    {counts.map((c) => <span key={c} className="pp-chip">{c}</span>)}
                </div>
            )}
            {greats && (
                <div className="pp-greats">
                    <p>
                        <strong>On Greats of the Game</strong> ({greats.group}).{' '}
                        <button type="button" className="pp-link" onClick={() => onNavigate('greats')}>See the page</button>
                    </p>
                    <ul>{greats.facts.map((f) => <li key={f}>{f}</li>)}</ul>
                </div>
            )}
            {rows.length > 0 && (
                <>
                    <TableExport />
                    <div className="table-wrapper pp-scroll">
                        <table className="data-table lb-table pp-table">
                            <thead><tr><th>Season</th><th>Award</th><th>Result</th></tr></thead>
                            <tbody>
                                {rows.map((r) => (
                                    <tr key={`${r.season}-${r.award}`} className={r.winner && VOTED.includes(r.award) ? 'pp-won' : ''}>
                                        <td>{label(r.season)}</td>
                                        <td>{r.award}</td>
                                        <td>{result(r)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </Section>
    );
}

// ── Shot zones ──────────────────────────────────────────────────────────
function ShotZones({ playerId, block }) {
    const seasons = block.seasons.map((s) => s.season);
    const [season, setSeason] = useState(block.zones.season);
    const [loaded, setLoaded] = useState({ [block.zones.season]: block.zones });
    const [error, setError] = useState('');
    const data = loaded[season];

    useEffect(() => {
        if (loaded[season]) return undefined;
        let active = true;
        fetchProfileShotZones(playerId, season)
            .then((d) => { if (active) { setError(''); setLoaded((m) => ({ ...m, [season]: d })); } })
            .catch((e) => { if (active) setError(e.response?.data?.detail || 'Could not load that season.'); });
        return () => { active = false; };
    }, [playerId, season, loaded]);

    const fgaBySeason = Object.fromEntries(block.seasons.map((s) => [s.season, s.fga]));
    return (
        <Section id="shots" title="Shot zones"
            meta={<>Regular season, from every located shot. Colour = his FG% against the league&apos;s in that zone
                that season (warm above, cool below). Zones under {block.zone_min_fga} attempts are greyed.</>}>
            <div className="pp-row">
                <SeasonSelect value={season} seasons={seasons} onChange={(s) => { setError(''); setSeason(s); }}
                    format={(s) => `${label(s)} (${fgaBySeason[s]} FGA)`} />
            </div>
            {error && !data && <p className="error-message">{error}</p>}
            {!data && !error && <Loader />}
            {data && (
                <div className="pp-zones">
                    <div className="pp-court">
                        <ZoneCourtMap zones={data.zones} leagueZones={data.league_zones} size={320} />
                    </div>
                    <div className="pp-zones-table">
                        {data.small_sample && (
                            <p className="pp-warn">Only {data.fga} located shots this season: treat the zone numbers as rough.</p>
                        )}
                        <TableExport />
                        <div className="table-wrapper">
                            <table className="data-table lb-table pp-table">
                                <thead>
                                    <tr><th>Zone</th><th className="lb-num">FGA</th><th className="lb-num">Share</th>
                                        <th className="lb-num">FG%</th><th className="lb-num">League</th><th className="lb-num">Diff</th></tr>
                                </thead>
                                <tbody>
                                    {data.zones.map((z) => {
                                        const diff = z.fg_pct != null && z.league_fg_pct != null ? z.fg_pct - z.league_fg_pct : null;
                                        return (
                                            <tr key={z.zone} className={z.small_sample ? 'pp-small' : ''}>
                                                <td>{z.zone}</td>
                                                <td className="lb-num">{z.fga}</td>
                                                <td className="lb-num">{pct(z.share, 0)}</td>
                                                <td className="lb-num lb-stat">{pct(z.fg_pct)}</td>
                                                <td className="lb-num">{pct(z.league_fg_pct)}</td>
                                                <td className={`lb-num ${z.small_sample ? '' : tone(diff)}`}>{diff == null ? '—' : signed(diff * 100)}</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                        <p className="page-subtitle pp-foot">{data.fgm}/{data.fga} in these zones ({pct(data.fga ? data.fgm / data.fga : null)}); backcourt heaves left out.</p>
                    </div>
                </div>
            )}
        </Section>
    );
}

function ShotMakingBlock({ block, coverage, onNavigate, name }) {
    return (
        <Section id="shotmaking" title="Shot-making" info={<ShotMakingInfo />}
            meta={<>Actual eFG% minus what an average shooter would post on the same shots, each regular season
                ({span(coverage)}); seasons under {block.min_fga} attempts are not ranked.{' '}
                <button type="button" className="pp-link" onClick={() => onNavigate('shotcharts', null, { player: name, view: 'shotmaking' })}>
                    Open in Shot Charts
                </button></>}>
            <ShotMakingTable rows={block.rows} minFga={block.min_fga} />
        </Section>
    );
}

function Scouting({ name, seasons }) {
    const [season, setSeason] = useState(seasons[seasons.length - 1]);
    return (
        <Section id="scouting" title="Scouting report"
            meta="Real splits that differ from the league at p < 0.05, from the Exploit Guide (1,500+ minute seasons, 2012-13 on).">
            {seasons.length > 1 && (
                <div className="pp-row"><SeasonSelect value={season} seasons={seasons} onChange={setSeason} /></div>
            )}
            <ScoutingReportCard playerName={name} season={season} titleClassName="pp-subhead" />
        </Section>
    );
}

function Defense({ block, onNavigate }) {
    return (
        <Section id="defense" title="Defense: who he guarded"
            info={(
                <InfoTooltip label="About the DAD Index" title="DAD Index">
                    Difficulty of assignments: the offensive players he was matched up with (NBA tracking), weighted by
                    possessions and each player&apos;s offensive BPM, z-scored among qualified defenders (1,000+ partial
                    possessions) that season, overall and within his position group. DFG% diff = shooters&apos; FG% against
                    him minus their normal FG% (negative is good), with a 95% margin. Not a full defensive rating: no help
                    defense, rebounding or scheme.
                </InfoTooltip>
            )}
            meta={<>DAD Index. Greyed: not qualified that season. DFG% under {block.reliable_min_dfga} defended shots is marked small.{' '}
                <button type="button" className="pp-link" onClick={() => onNavigate('analytics', 'dad')}>Open the DAD Index</button></>}>
            <TableExport />
            <div className="table-wrapper">
                <table className="data-table lb-table pp-table">
                    <thead>
                        <tr><th>Season</th><th>Team</th><th className="lb-num">Partial poss.</th><th className="lb-num">DAD z</th>
                            <th className="lb-num">z in position</th><th className="lb-num">DFG% diff</th><th className="lb-num">Defended FGA</th>
                            <th>Most-guarded</th></tr>
                    </thead>
                    <tbody>
                        {block.rows.map((r) => (
                            <tr key={r.season} className={r.qualified ? '' : 'pp-small'}>
                                <td>{label(r.season)}</td>
                                <td>{r.team}</td>
                                <td className="lb-num">{Math.round(r.total_poss).toLocaleString()}</td>
                                <td className="lb-num lb-stat">{r.qualified ? signed(r.dad_z, 2) : '—'}</td>
                                <td className="lb-num">{r.qualified ? `${signed(r.dad_pos_z, 2)} (${r.pos_group})` : '—'}</td>
                                <td className={`lb-num ${r.small_dfg_sample ? '' : tone(r.dfg_diff == null ? null : -r.dfg_diff)}`}>
                                    {r.dfg_diff == null ? '—' : `${signed(r.dfg_diff * 100)} ± ${num(r.dfg_diff_margin95 * 100)}`}
                                    {r.small_dfg_sample && r.dfg_diff != null && <span className="pp-tag">small</span>}
                                </td>
                                <td className="lb-num">{r.d_fga ?? '—'}</td>
                                <td className="pp-wrap">{r.top_assignments.map((t) => t.player_name).join(', ') || '—'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Section>
    );
}

function Gravity({ rows, onNavigate }) {
    return (
        <Section id="gravity" title="Shooting gravity"
            info={(
                <InfoTooltip label="About Gravity" title="Gravity (a proxy)">
                    A disclosed proxy for how much defenses must respect his shooting, not tracking-based gravity: three
                    z-scores summed within the season among players with 500+ minutes — 3PA per 100 possessions,
                    catch-and-shoot 3P% and the share of his threes taken with a defender within 6 ft (both shrunk toward
                    players with similar volume). Lineup spacing adds up five players&apos; Gravity.
                </InfoTooltip>
            )}
            meta={<>Seasons with 500+ minutes, 2013-14 on. Rank among that season&apos;s pool.{' '}
                <button type="button" className="pp-link" onClick={() => onNavigate('analytics', 'spacing')}>Open the Spacing Lab</button></>}>
            <TableExport />
            <div className="table-wrapper">
                <table className="data-table lb-table pp-table">
                    <thead>
                        <tr><th>Season</th><th className="lb-num">Gravity</th><th className="lb-num">Rank</th>
                            <th className="lb-num">3PA / 100 poss.</th><th className="lb-num">Catch-and-shoot 3P%</th>
                            <th className="lb-num">C&amp;S 3PA</th><th className="lb-num">Contested share</th></tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.season}>
                                <td>{label(r.season)}</td>
                                <td className={`lb-num lb-stat ${tone(r.gravity)}`}>{signed(r.gravity, 2)}</td>
                                <td className="lb-num">{r.rank} of {r.pool}</td>
                                <td className="lb-num">{num(r.three_rate)}</td>
                                <td className="lb-num">{r.cs_fg3a ? pct(r.cs_pct) : '—'}</td>
                                <td className="lb-num">{r.cs_fg3a ?? '—'}</td>
                                <td className="lb-num">{pct(r.contested_share, 0)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Section>
    );
}

function Contracts({ rows, coverage, onNavigate }) {
    return (
        <Section id="contract" title="Contract value"
            info={(
                <InfoTooltip label="How contract value works" title="Fair value and surplus">
                    Fair value = wins above replacement (from Basketball-Reference&apos;s VORP) × that season&apos;s cost per
                    win + the league minimum; surplus = fair value − real salary. A value estimate from public salary
                    data, not a valuation of the contract&apos;s full terms.
                </InfoTooltip>
            )}
            meta={<>Seasons with reliable salary data: {seasonRanges(coverage.included)}.{' '}
                <button type="button" className="pp-link" onClick={() => onNavigate('analytics', 'contracts')}>Open Contract Value</button></>}>
            <TableExport />
            <div className="table-wrapper">
                <table className="data-table lb-table pp-table">
                    <thead>
                        <tr><th>Season</th><th>Team</th><th className="lb-num">Minutes</th><th className="lb-num">Salary</th>
                            <th className="lb-num">WAR</th><th className="lb-num">Fair value</th><th className="lb-num">Surplus</th></tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.season}>
                                <td>{label(r.season)}</td>
                                <td>{r.team}</td>
                                <td className="lb-num">{r.minutes == null ? '—' : r.minutes.toLocaleString()}</td>
                                <td className="lb-num">{money(r.salary)}</td>
                                <td className="lb-num">{num(r.war, 1)}</td>
                                <td className="lb-num">{money(r.fair_value)}</td>
                                <td className={`lb-num lb-stat ${tone(r.surplus)}`}>{money(r.surplus)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Section>
    );
}

function Clutch({ c, coverage, onNavigate }) {
    return (
        <Section id="clutch" title="Clutch"
            info={(
                <InfoTooltip label="About clutch WPA" title="Win probability added">
                    Each play&apos;s swing in the win-probability model (after the play minus before), credited to the
                    player who made it. Clutch = final 5 minutes of the 4th quarter or overtime, score within 5. Totals,
                    not rates: more games means more WPA.
                </InfoTooltip>
            )}
            meta={<>All play-by-play seasons combined ({span(coverage)}), one total per player.{' '}
                <button type="button" className="pp-link" onClick={() => onNavigate('analytics', 'wpa')}>Open Clutch WPA</button></>}>
            {c.small_sample && (
                <p className="pp-warn">Only {c.clutch_plays} clutch plays: a handful of plays can swing this total.</p>
            )}
            <div className="pp-glance-tiles">
                <div className="pp-tile">
                    <span className={`pp-tile-value ${tone(c.clutch_wpa)}`}>{signed(c.clutch_wpa, 1)}</span>
                    <span className="pp-tile-label">Clutch WPA · {ordinal(c.rank)} of {c.pool.toLocaleString()}</span>
                </div>
                <div className="pp-tile">
                    <span className="pp-tile-value">{c.clutch_plays.toLocaleString()}</span>
                    <span className="pp-tile-label">Clutch plays</span>
                </div>
                <div className="pp-tile">
                    <span className={`pp-tile-value ${tone(c.total_wpa)}`}>{signed(c.total_wpa, 1)}</span>
                    <span className="pp-tile-label">All-game WPA</span>
                </div>
                <div className="pp-tile">
                    <span className="pp-tile-value">{c.n_games.toLocaleString()}</span>
                    <span className="pp-tile-label">Games · {c.n_plays.toLocaleString()} plays</span>
                </div>
            </div>
            <p className="page-subtitle pp-foot">Rank among {c.pool.toLocaleString()} players with 3+ clutch plays, by clutch WPA.</p>
        </Section>
    );
}

function Similar({ player, seasons }) {
    const [season, setSeason] = useState(seasons[seasons.length - 1]);
    const [result, setResult] = useState(null); // { season, data } | { season, error }

    useEffect(() => {
        let active = true;
        fetchSeasonSimilarityProfile(player.player_name, season,
            { topN: 5, excludeSelf: true, minGp: 20, onePerPlayer: true, playerId: player.player_id })
            .then((data) => { if (active) setResult({ season, data }); })
            .catch((e) => {
                if (active) {
                    setResult({
                        season,
                        error: e.response?.data?.detail || 'The similarity service (port 8001) didn’t answer. Is it running?',
                    });
                }
            });
        return () => { active = false; };
    }, [player, season]);

    const current = result?.season === season ? result : null;
    const features = Object.fromEntries((current?.data?.features || []).map((f) => [f.key, f.label]));
    return (
        <Section id="similar" title="Most similar seasons"
            info={current?.data && (
                <InfoTooltip label="How similarity works" title="Season similarity">{current.data.methodology}</InfoTooltip>
            )}
            meta="Other players only, one season each, 20+ games. From the Season Similarity model (2009-10 on).">
            <div className="pp-row"><SeasonSelect value={season} seasons={seasons} onChange={setSeason} labelText="His season" /></div>
            {!current && <Loader />}
            {current?.error && <p className="error-message">{current.error}</p>}
            {current?.data && (
                <>
                    <TableExport />
                    <div className="table-wrapper">
                        <table className="data-table lb-table pp-table">
                            <thead>
                                <tr><th>#</th><th>Player</th><th>Season</th><th className="lb-num">GP</th>
                                    <th className="lb-num">Similarity</th><th>Closest on</th><th>Differs most</th></tr>
                            </thead>
                            <tbody>
                                {current.data.results.map((r) => (
                                    <tr key={`${r.player_id}-${r.season}`}>
                                        <td>{r.rank}</td>
                                        <td><PlayerName playerId={r.player_id} name={r.player_name} size={24} /></td>
                                        <td>{label(r.season)}</td>
                                        <td className="lb-num">{r.gp}</td>
                                        <td className="lb-num lb-stat">{r.similarity_score.toFixed(3)}</td>
                                        <td>{r.closest_on.map((k) => features[k] || k).join(', ')}</td>
                                        <td>{features[r.differs_most.feature] || r.differs_most.feature} ({r.differs_most.direction})</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </Section>
    );
}

function Breakouts({ block }) {
    const p = block.persistence;
    const open = (f) => openPage('breakouts', { season: f.season, dir: f.direction });
    return (
        <Section id="breakouts" title="Breakouts and declines"
            meta={<>Seasons he made the Breakout Detector&apos;s top {block.top} for a jump or a drop in league standing
                ({block.stats.join(', ')}; {block.min_gp}+ games and {block.min_mpg}+ minutes in both seasons).
                {p && <> Historically a top-20 breakout keeps about {Math.round(p.median_share_kept * 100)}% of its jump the next season (median, {p.players.toLocaleString()} players).</>}</>}>
            <ul className="pp-flags">
                {block.flags.map((f) => (
                    <li key={`${f.season}-${f.direction}`} className={f.direction === 'up' ? 'pp-pos' : 'pp-neg'}>
                        <strong>{label(f.season)}</strong>: {ordinal(f.rank)}-biggest {f.direction === 'up' ? 'jump' : 'drop'} of{' '}
                        {f.pool} qualified players ({signed(f.score, 2)} in average z-score).{' '}
                        <button type="button" className="pp-link" onClick={() => open(f)}>See that list</button>
                    </li>
                ))}
            </ul>
        </Section>
    );
}

function NextSeason({ block, player, onNavigate }) {
    const rows = block.rows;
    const first = rows[0];
    const fmtP = (f, v) => (v == null ? '—' : f === 'pct' ? pct(v) : f === 'signed1' ? signed(v) : num(v));
    return (
        <Section id="projection" title={`Next season: ${label(block.season)}`}
            info={(
                <InfoTooltip label="How the projection is made" title="A baseline, not a scouting opinion">
                    His last three seasons, weighted 5/4/3 and by sample, pulled toward the league average by how noisy
                    each stat is (Stat Stability), then moved along the aging curve to his age next season. The 80%
                    range is where the same method&apos;s past projections for players like him landed, checked on every
                    season since 2000-01. It knows nothing about role, team, health or a new coach.
                </InfoTooltip>
            )}
            meta={<>Age {first.age_next ?? '—'} next season, from {first.seasons_used} season{first.seasons_used === 1 ? '' : 's'} on file
                {!first.age_known && ' (no birth date on file, so no age step)'}. Rows greyed where under{' '}
                {block.low_weight} of the number comes from his own seasons.{' '}
                <button type="button" className="pp-link" onClick={() => onNavigate('projections', null, { q: player.player_name })}>Open Projections</button></>}>
            <TableExport name={`${player.player_name} projection ${label(block.season)}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table pp-table">
                    <thead>
                        <tr><th>Stat</th><th className="lb-num">{label(block.season - 1)}</th><th className="lb-num lb-stat">{label(block.season)}</th>
                            <th className="lb-num">80% range</th><th className="lb-num">Age step</th><th className="lb-num">Own weight</th></tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={r.stat} className={r.low_weight ? 'sl-short' : undefined}
                                title={r.low_weight ? `Own weight ${r.own_weight.toFixed(2)}: mostly the league average` : undefined}>
                                <td>{r.label}</td>
                                <td className="lb-num">{fmtP(r.format, r.last_value)}{r.last_season !== block.season - 1 && ` (${label(r.last_season)})`}</td>
                                <td className="lb-num lb-stat">{fmtP(r.format, r.projection)}</td>
                                <td className="lb-num">{fmtP(r.format, r.lo)} to {fmtP(r.format, r.hi)}</td>
                                <td className="lb-num">{r.age_known ? (r.format === 'pct' ? `${signed(r.age_adjustment * 100)} pts` : signed(r.age_adjustment)) : '—'}</td>
                                <td className="lb-num">{r.own_weight.toFixed(2)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </Section>
    );
}

function OnOff({ block, coverage, onNavigate }) {
    const rows = block.rows;
    const thin = rows.some((r) => r.few_off_minutes);
    return (
        <Section id="onoff" title="On/off"
            info={(
                <InfoTooltip label="How on/off is computed" title="With him on the floor, and without">
                    Team net rating (points per 100 possessions) with him on the floor, and without him in the games he
                    played, from every minute of play-by-play. The interval comes from resampling his games; a gap
                    whose interval includes zero is within normal noise. Descriptive: not adjusted for teammates or
                    opponents, so it also reflects who his backups were.
                </InfoTooltip>
            )}
            meta={<>Play-by-play lines cover {span(coverage)}; a traded season shows each team. Seasons under{' '}
                {block.qualified_minutes} minutes on the floor are greyed.{' '}
                <button type="button" className="pp-link" onClick={() => onNavigate('analytics', 'onoff')}>Open On/Off</button></>}>
            <TableExport />
            <div className="table-wrapper">
                <table className="data-table lb-table pp-table">
                    <thead>
                        <tr><th>Season</th><th>Team</th><th className="lb-num">GP</th><th className="lb-num">Min on</th>
                            <th className="lb-num">Min off</th><th className="lb-num">Net on</th><th className="lb-num">Net off</th>
                            <th className="lb-num">On − Off</th><th className="lb-num">95% interval</th><th className="lb-num">Usage</th></tr>
                    </thead>
                    <tbody>
                        {rows.map((r) => (
                            <tr key={`${r.season}-${r.team}`} className={r.qualified ? undefined : 'sl-short'}
                                title={r.qualified ? undefined : `Under ${block.qualified_minutes} minutes on the floor: treat as noise`}>
                                <td>{label(r.season)}</td>
                                <td>{r.team}</td>
                                <td className="lb-num">{r.games}</td>
                                <td className="lb-num">{r.minutes_on == null ? '—' : Math.round(r.minutes_on).toLocaleString()}</td>
                                <td className="lb-num">{r.minutes_off == null ? '—' : Math.round(r.minutes_off).toLocaleString()}</td>
                                <td className={`lb-num ${tone(r.net_on)}`}>{signed(r.net_on)}</td>
                                <td className={`lb-num ${tone(r.net_off)}`}>{signed(r.net_off)}</td>
                                <td className={`lb-num lb-stat ${tone(r.on_off_net)}`}>{signed(r.on_off_net)}</td>
                                <td className="lb-num">{r.ci_low == null ? '—' : `${signed(r.ci_low)} to ${signed(r.ci_high)}`}</td>
                                <td className="lb-num">{pct(r.usg_pct)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="page-subtitle pp-foot">
                Off-court = the team&apos;s minutes without him in games he played (games he missed aren&apos;t counted).
                {thin && ` A season with under ${block.few_off_minutes} off-court minutes has a thin off-court side.`}
            </p>
        </Section>
    );
}

// Why each block is missing for this player, in plain words.
function missingReasons(d) {
    const p = d.player;
    const cov = d.coverage;
    const endedBefore = (from) => p.last_season < from;
    const career = `${p.player_name}'s career ended in ${label(p.last_season)}`;
    const out = [];
    if (!d.awards.rows.length && !p.greats) {
        out.push(['Awards', `No awards, All-league teams or All-Star selections on record (season awards through ${label(cov.awards.to)}).`]);
    }
    if (!d.shots.seasons.length) {
        out.push(['Shot zones', endedBefore(cov.shots.from)
            ? `Shot locations start in ${label(cov.shots.from)}; ${career}.`
            : 'No regular-season shot locations stored for him.']);
    }
    if (!d.shot_making.rows.length) {
        out.push(['Shot-making', endedBefore(cov.shot_making.from)
            ? `Shot locations start in ${label(cov.shot_making.from)}; ${career}.`
            : 'No regular-season shots on file for him.']);
    }
    if (!d.scouting.seasons.length) {
        out.push(['Scouting report', endedBefore(cov.scouting.from)
            ? `Play-type data starts in ${label(cov.scouting.from)}; ${career}.`
            : 'Needs a season with 1,500+ minutes and play-type data (2012-13 on).']);
    }
    if (!d.defense.rows.length) {
        out.push(['Defense (DAD Index)', endedBefore(cov.defense.from)
            ? `Matchup tracking starts in ${label(cov.defense.from)}; ${career}.`
            : `No tracked matchup season on file (${span(cov.defense)}).`]);
    }
    if (!d.gravity.rows.length) {
        out.push(['Shooting gravity', endedBefore(cov.gravity.from)
            ? `Shooting tracking starts in ${label(cov.gravity.from)}; ${career}.`
            : `Needs a season with 500+ minutes, ${span(cov.gravity)}.`]);
    }
    if (!d.contracts.rows.length) {
        const lastSalary = cov.contracts.included[cov.contracts.included.length - 1];
        let why = `No matched salary in the seasons with reliable salary data (${seasonRanges(cov.contracts.included)}).`;
        if (endedBefore(cov.contracts.from)) why = `Salary data starts in ${label(cov.contracts.from)}; ${career}.`;
        else if (p.first_season > lastSalary) {
            why = `The latest season with reliable salary data is ${label(lastSalary)}; ${p.player_name} started in ${label(p.first_season)}.`;
        }
        out.push(['Contract value', why]);
    }
    if (!d.clutch) {
        out.push(['Clutch', endedBefore(cov.clutch.from)
            ? `Play-by-play covers ${span(cov.clutch)}; ${career}.`
            : `No clutch plays in the play-by-play (${span(cov.clutch)}).`]);
    }
    if (d.projections && !d.projections.rows.length) {
        const windowMinutes = d.seasons.rows.filter((r) => r.season >= cov.seasons.to - 2)
            .reduce((sum, r) => sum + (r.gp || 0) * (r.min || 0), 0);
        out.push(['Next season', p.active
            ? `No projection: ${Math.round(windowMinutes).toLocaleString()} minutes over the last three seasons, under the 250-minute floor (regressing so few minutes toward the league average would invent a role).`
            : `Projections are made for players with a ${label(cov.seasons.to)} season; ${career}.`]);
    }
    if (d.on_off && !d.on_off.rows.length) {
        out.push(['On/off', endedBefore(cov.on_off.from)
            ? `Play-by-play lines cover ${span(cov.on_off)}; ${career}.`
            : `No game with on-court minutes in the play-by-play lines (${span(cov.on_off)}).`]);
    }
    if (!d.similarity.seasons.length) {
        out.push(['Similar seasons', endedBefore(2010)
            ? `Needs usage, net rating, assist % and rebound %, recorded from 2009-10 on; ${career}.`
            : 'No season with all eight similarity inputs.']);
    }
    if (!d.game_log.seasons.length) {
        out.push(['Game log', endedBefore(cov.game_lines.from)
            ? `Game-by-game lines start in ${label(cov.game_lines.from)} (rebuilt from ESPN play-by-play); ${career}.`
            : `No regular-season games in the play-by-play for him (${span(cov.game_lines)}).`]);
    }
    if (!d.breakouts.flags.length) {
        out.push(['Breakouts', p.n_seasons === 1
            ? 'A breakout compares two seasons in a row; this is his first.'
            : `Never in the Breakout Detector's top ${d.breakouts.top} jumps or drops (needs ${d.breakouts.min_gp}+ games and ${d.breakouts.min_mpg}+ minutes in back-to-back seasons).`]);
    }
    return out;
}

export default function PlayerProfile({ onNavigate }) {
    const params = useInitialParams();
    const id = parseParam.int(params, 'id', { min: 1 });
    const [result, setResult] = useState(null); // { data } | { error }

    useEffect(() => {
        window.scrollTo(0, 0);
        if (!id) return undefined;
        let active = true;
        fetchPlayerFullProfile(id)
            .then((data) => { if (active) setResult({ data }); })
            .catch((e) => {
                if (active) {
                    setResult({
                        error: e.response?.data?.detail || 'The profile couldn’t load. Is the impact API (port 8002) running?',
                    });
                }
            });
        return () => { active = false; };
    }, [id]);

    const d = result?.data;
    const missing = useMemo(() => (d ? missingReasons(d) : []), [d]);

    if (!id) {
        return (
            <section className="dashboard-card">
                <p className="page-subtitle">No player chosen. Open one by clicking any player&apos;s name or photo, or search with Ctrl+K.</p>
            </section>
        );
    }
    if (result?.error) return <section className="dashboard-card"><p className="error-message">{result.error}</p></section>;
    if (!d) return <Loader />;

    const sections = [
        ['seasons', 'Seasons', true],
        ['gamelog', 'Game log', d.game_log.seasons.length > 0],
        ['awards', 'Awards', d.awards.rows.length > 0 || d.player.greats],
        ['shots', 'Shot zones', d.shots.seasons.length > 0],
        ['shotmaking', 'Shot-making', d.shot_making.rows.length > 0],
        ['scouting', 'Scouting', d.scouting.seasons.length > 0],
        ['defense', 'Defense', d.defense.rows.length > 0],
        ['gravity', 'Gravity', d.gravity.rows.length > 0],
        ['contract', 'Contract', d.contracts.rows.length > 0],
        ['clutch', 'Clutch', !!d.clutch],
        ['projection', 'Next season', (d.projections?.rows.length ?? 0) > 0],
        ['onoff', 'On/off', (d.on_off?.rows.length ?? 0) > 0],
        ['similar', 'Similar', d.similarity.seasons.length > 0],
        ['breakouts', 'Breakouts', d.breakouts.flags.length > 0],
    ].filter(([, , ok]) => ok);
    const jump = (key) => document.getElementById(`pp-${key}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const latest = d.seasons.rows[d.seasons.rows.length - 1];

    return (
        <div className="pp">
            <Hero data={d} />
            <Glance row={latest} />
            <nav className="pp-jump" aria-label="Sections on this page">
                {sections.map(([key, text]) => (
                    <button key={key} type="button" onClick={() => jump(key)}>{text}</button>
                ))}
                {missing.length > 0 && <button type="button" onClick={() => jump('missing')}>Not on file</button>}
            </nav>

            <SeasonsTable block={d.seasons} />
            {d.game_log.seasons.length > 0 && (
                <GameLogBlock key={d.player.player_id} playerId={d.player.player_id} seasons={d.game_log.seasons}
                    nbaGp={Object.fromEntries(d.seasons.rows.map((r) => [r.season, r.gp]))} />
            )}
            {(d.awards.rows.length > 0 || d.player.greats) && (
                <Awards rows={d.awards.rows} greats={d.player.greats} coverage={d.coverage} onNavigate={onNavigate} />
            )}
            {d.shots.seasons.length > 0 && (
                <>
                    <ShotZones playerId={d.player.player_id} block={d.shots} />
                    <ShotMixHistory key={d.player.player_name} playerName={d.player.player_name} />
                </>
            )}
            {d.shot_making.rows.length > 0 && (
                <ShotMakingBlock block={d.shot_making} coverage={d.coverage.shot_making} onNavigate={onNavigate}
                    name={d.player.player_name} />
            )}
            {d.scouting.seasons.length > 0 && <Scouting name={d.player.player_name} seasons={d.scouting.seasons} />}
            {d.defense.rows.length > 0 && <Defense block={d.defense} onNavigate={onNavigate} />}
            {d.gravity.rows.length > 0 && <Gravity rows={d.gravity.rows} onNavigate={onNavigate} />}
            {d.contracts.rows.length > 0 && <Contracts rows={d.contracts.rows} coverage={d.coverage.contracts} onNavigate={onNavigate} />}
            {d.clutch && <Clutch c={d.clutch} coverage={d.coverage.clutch} onNavigate={onNavigate} />}
            {(d.projections?.rows.length ?? 0) > 0 && <NextSeason block={d.projections} player={d.player} onNavigate={onNavigate} />}
            {(d.on_off?.rows.length ?? 0) > 0 && <OnOff block={d.on_off} coverage={d.coverage.on_off} onNavigate={onNavigate} />}
            {d.similarity.seasons.length > 0 && <Similar player={d.player} seasons={d.similarity.seasons} />}
            {d.breakouts.flags.length > 0 && <Breakouts block={d.breakouts} />}

            {missing.length > 0 && (
                <section id="pp-missing" className="dashboard-card pp-section pp-missing">
                    <h2 className="card-title pp-section-title">Not on file for {d.player.player_name}</h2>
                    <dl>
                        {missing.map(([what, why]) => (
                            <div key={what}><dt>{what}</dt><dd>{why}</dd></div>
                        ))}
                    </dl>
                </section>
            )}
        </div>
    );
}
