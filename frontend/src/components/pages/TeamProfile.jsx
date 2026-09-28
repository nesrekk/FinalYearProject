import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchTeamProfile } from '../../services/api';
import Loader from '../Loader';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import ChartExport from '../common/ChartExport';
import TeamLink from '../common/TeamLink';
import TeamLogo from '../common/TeamLogo';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/profile.css';
import '../../styles/teamprofile.css';

// One page per team-season (?page=team&abbr=BOS&season=2024), the team twin
// of the player profile. Everything comes from GET /team-profile/{abbr}; a
// block with no data for this season is listed under "Not on file" with the
// reason, never shown empty.

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
const rankText = (r, n) => (r ? `${ordinal(r)} of ${n}` : null);
const CURRENT = new Set(['ATL', 'BOS', 'BKN', 'CHA', 'CHI', 'CLE', 'DAL', 'DEN', 'DET', 'GSW', 'HOU', 'IND', 'LAC',
    'LAL', 'MEM', 'MIA', 'MIL', 'MIN', 'NOP', 'NYK', 'OKC', 'ORL', 'PHI', 'PHX', 'POR', 'SAC', 'SAS', 'TOR', 'UTA', 'WAS']);
// Logo for a season's code: today's franchise logo only where the code is still in use (PHO -> PHX).
const logoFor = (abbr) => ({ PHO: 'PHX', BRK: 'BKN', CHO: 'CHA' }[abbr] ?? abbr);

function useWidth() {
    const ref = useRef(null);
    const [W, setW] = useState(720);
    useEffect(() => {
        const el = ref.current;
        if (!el || typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(([entry]) => {
            const w = Math.round(entry.contentRect.width);
            if (w > 0) setW(Math.max(280, w));
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, []);
    return [ref, W];
}

function Section({ id, title, info, meta, children }) {
    return (
        <section id={`tp-${id}`} className="dashboard-card pp-section">
            <h2 className="card-title pp-section-title">{title}{info}</h2>
            {meta && <p className="page-subtitle pp-meta">{meta}</p>}
            {children}
        </section>
    );
}

// ── Header ──────────────────────────────────────────────────────────────
function Hero({ d, onSeason }) {
    const t = d.summary.team;
    const seasons = d.franchise_history;
    const facts = [
        t.arena,
        t.attend ? `${Math.round(t.attend).toLocaleString()} fans (${Math.round(t.attend_g).toLocaleString()} a game)` : null,
        t.age ? `Average age ${num(t.age)}` : null,
    ].filter(Boolean);
    return (
        <header className="pp-hero tp-hero">
            <TeamLogo abbreviation={logoFor(d.abbreviation)} size={120} className="tp-hero-logo" />
            <div className="pp-hero-text">
                <span className="page-eyebrow">NBA Hub · Team page</span>
                <h1 className="pp-name">{d.team_name}</h1>
                <p className="pp-facts">{label(d.season)} · {d.abbreviation}{facts.length ? ` · ${facts.join(' · ')}` : ''}</p>
                <div className="pp-chips">
                    <span className="pp-chip pp-chip--hi">{num(t.w, 0)}-{num(t.l, 0)}</span>
                    <span className="pp-chip">{t.playoffs ? 'Made the playoffs' : 'Missed the playoffs'}</span>
                    <span className="pp-chip">
                        Franchise: {seasons.length} season{seasons.length === 1 ? '' : 's'} on file, {label(seasons[0].season)} to{' '}
                        {label(seasons[seasons.length - 1].season)}
                    </span>
                </div>
                {d.note && <p className="pp-warn tp-note">{d.note}</p>}
                <div className="pp-actions tp-actions">
                    <label className="pp-select">
                        <span>Season</span>
                        <select className="input-field" value={d.season} onChange={(e) => onSeason(Number(e.target.value))}>
                            {[...seasons].reverse().map((s) => (
                                <option key={s.season} value={s.season}>
                                    {label(s.season)} · {s.abbreviation} · {num(s.w, 0)}-{num(s.l, 0)}
                                </option>
                            ))}
                        </select>
                    </label>
                    <CopyLinkButton />
                    <SaveViewButton pageId="team" title={`${d.team_name} ${label(d.season)}`} />
                    <SourceBadge source={d._source} />
                </div>
            </div>
        </header>
    );
}

function Glance({ s }) {
    const t = s.team;
    const r = s.ranks;
    const tiles = [
        ['Win %', pct(t.w_pct), rankText(r.w_pct, s.n_teams)],
        ['SRS', signed(t.srs, 2), rankText(r.srs, s.n_teams)],
        ['Off. rating', num(t.o_rtg), rankText(r.o_rtg, s.n_teams)],
        ['Def. rating', num(t.d_rtg), rankText(r.d_rtg, s.n_teams)],
        ['Net rating', signed(t.n_rtg), rankText(r.n_rtg, s.n_teams)],
        ['Pace', num(t.pace), rankText(r.pace, s.n_teams)],
    ].filter(([, v]) => v !== '—');
    return (
        <section className="dashboard-card pp-glance">
            <span className="pp-glance-label">At a glance · ranks among {s.n_teams} teams</span>
            <div className="pp-glance-tiles">
                {tiles.map(([k, v, rk]) => (
                    <div key={k} className="pp-tile">
                        <span className="pp-tile-label">{k}</span>
                        <span className="pp-tile-value">{v}</span>
                        {rk && <span className="tp-tile-rank">{rk}</span>}
                    </div>
                ))}
            </div>
        </section>
    );
}

// ── Franchise history: win% every season, this one highlighted ─────────
function FranchiseChart({ rows, season, onSeason }) {
    const [ref, W] = useWidth();
    const svgRef = useRef(null);
    const H = 150;
    const M = { l: 34, r: 8, t: 10, b: 24 };
    const step = (W - M.l - M.r) / rows.length;
    const sy = (v) => M.t + (1 - v) * (H - M.t - M.b);
    const every = Math.max(1, Math.ceil(rows.length / (W < 520 ? 6 : 12)));
    return (
        <div className="rx-chart" ref={ref}>
            <ChartExport svgRef={svgRef} name={`${rows[0].team_name ?? ''} win pct by season`} />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
                aria-label={`Win percentage every season, ${label(rows[0].season)} to ${label(rows[rows.length - 1].season)}; ${label(season)} highlighted.`}>
                {[0, 0.5, 1].map((t) => (
                    <g key={t}>
                        <line className={t === 0.5 ? 'tp-mid' : 'rx-grid'} x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                        <text className="rx-tick" x={M.l - 5} y={sy(t)} textAnchor="end" dominantBaseline="middle">{t.toFixed(1)}</text>
                    </g>
                ))}
                {rows.map((r, i) => {
                    const wp = r.w + r.l ? r.w / (r.w + r.l) : 0;
                    return (
                        <g key={r.season} className="tp-hbar" onClick={() => onSeason(r.season)}>
                            <rect x={M.l + i * step} y={M.t} width={step} height={H - M.t - M.b} fill="transparent" />
                            <rect x={M.l + i * step + step * 0.12} y={sy(wp)} width={Math.max(1, step * 0.76)} height={sy(0) - sy(wp)}
                                className={`tp-bar ${r.playoffs ? 'tp-bar--po' : ''} ${r.season === season ? 'tp-bar--on' : ''}`} />
                            <title>{`${label(r.season)} ${r.team_name} (${r.abbreviation}): ${r.w}-${r.l}${r.playoffs ? ', playoffs' : ''}${r.srs != null ? `, SRS ${signed(r.srs, 2)}` : ''}`}</title>
                            {(i % every === 0 || r.season === season) && (
                                <text className="rx-tick" x={M.l + i * step + step / 2} y={H - 7} textAnchor="middle">{`'${String(r.season).slice(-2)}`}</text>
                            )}
                        </g>
                    );
                })}
            </svg>
            <p className="tp-key">
                <span><span className="tp-swatch tp-swatch--po" />Made the playoffs</span>
                <span><span className="tp-swatch" />Missed</span>
                <span>Click a bar to open that season.</span>
            </p>
        </div>
    );
}

function Summary({ s }) {
    const t = s.team;
    const lg = s.league || {};
    const r = s.ranks;
    const rows = [
        ['Record', `${num(t.w, 0)}-${num(t.l, 0)}`, null, rankText(r.w_pct, s.n_teams)],
        ['Pythagorean record', t.pw != null ? `${num(t.pw, 0)}-${num(t.pl, 0)}` : '—', null, null],
        ['Margin per game', signed(t.mov, 2), null, rankText(r.mov, s.n_teams)],
        ['SRS (margin + schedule)', signed(t.srs, 2), null, rankText(r.srs, s.n_teams)],
        ['Schedule strength', signed(t.sos, 2), null, null],
        ['Points per game (for / against)', `${num(t.pts_per_game)} / ${num(t.opp_pts_per_game)}`, lg.pts_per_game != null ? num(lg.pts_per_game) : null, null],
        ['Offensive rating', num(t.o_rtg), num(lg.o_rtg), rankText(r.o_rtg, s.n_teams)],
        ['Defensive rating', num(t.d_rtg), num(lg.d_rtg), rankText(r.d_rtg, s.n_teams)],
        ['Pace', num(t.pace), num(lg.pace), rankText(r.pace, s.n_teams)],
        ['Three-point attempt rate', pct(t.x3p_ar), pct(lg.x3p_ar), rankText(r.x3p_ar, s.n_teams)],
        ['True shooting', pct(t.ts_percent), pct(lg.ts_percent), rankText(r.ts_percent, s.n_teams)],
    ].filter(([, v]) => v !== '—' && v !== '— / —');
    const ff = [
        ['Effective FG%', pct(t.e_fg_percent), pct(t.opp_e_fg_percent), pct(lg.e_fg_percent), r.e_fg_percent, r.opp_e_fg_percent],
        ['Turnover %', num(t.tov_percent), num(t.opp_tov_percent), num(lg.tov_percent), r.tov_percent, r.opp_tov_percent],
        ['Offensive rebound % (defence: defensive rebound %)', num(t.orb_percent), num(t.drb_percent), num(lg.orb_percent), r.orb_percent, r.drb_percent],
        ['Free throws per FGA', num(t.ft_fga, 3), num(t.opp_ft_fga, 3), num(lg.ft_fga, 3), r.ft_fga, r.opp_ft_fga],
    ].filter(([, a]) => a !== '—');
    return (
        <Section id="summary" title="Season summary" meta={`${s.coverage} · ${s.ratings_note}`}>
            <div className="tp-two">
                <div className="table-wrapper">
                    <table className="data-table lb-table pp-table">
                        <thead><tr><th>Stat</th><th className="lb-num">Team</th><th className="lb-num">League</th><th className="lb-num">Rank</th></tr></thead>
                        <tbody>
                            {rows.map(([k, v, l, rk]) => (
                                <tr key={k}><td>{k}</td><td className="lb-num lb-stat">{v}</td><td className="lb-num">{l ?? '—'}</td><td className="lb-num">{rk ?? '—'}</td></tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                {ff.length > 0 && (
                    <div className="table-wrapper">
                        <table className="data-table lb-table pp-table">
                            <thead><tr><th>Four factors</th><th className="lb-num">Offence</th><th className="lb-num">Defence</th><th className="lb-num">League</th></tr></thead>
                            <tbody>
                                {ff.map(([k, o, dd, l, ro, rd]) => (
                                    <tr key={k}>
                                        <td className="pp-wrap">{k}</td>
                                        <td className="lb-num">{o} <span className="tp-rank">{ro ? ordinal(ro) : ''}</span></td>
                                        <td className="lb-num">{dd} <span className="tp-rank">{rd ? ordinal(rd) : ''}</span></td>
                                        <td className="lb-num">{l}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        <p className="pp-foot">Defence: what opponents did against this team. Ranks: 1st = best for this team.</p>
                    </div>
                )}
            </div>
        </Section>
    );
}

// ── Game by game ────────────────────────────────────────────────────────
function MarginChart({ games }) {
    const [ref, W] = useWidth();
    const svgRef = useRef(null);
    const H = 170;
    const M = { l: 34, r: 8, t: 10, b: 22 };
    const max = Math.max(20, ...games.map((g) => Math.abs(g.pts_for - g.pts_against)));
    const step = (W - M.l - M.r) / games.length;
    const sy = (v) => M.t + ((max - v) / (2 * max)) * (H - M.t - M.b);
    const monthTicks = games.map((g, i) => [g.date.slice(5, 7), i]).filter(([m], i, arr) => i === 0 || m !== arr[i - 1][0]);
    return (
        <div className="rx-chart" ref={ref}>
            <ChartExport svgRef={svgRef} name="game margins" />
            <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img"
                aria-label={`Final margin of each of ${games.length} games, in order; wins above the line, losses below.`}>
                {[-max, 0, max].map((t) => (
                    <g key={t}>
                        <line className={t === 0 ? 'tp-mid' : 'rx-grid'} x1={M.l} x2={W - M.r} y1={sy(t)} y2={sy(t)} />
                        <text className="rx-tick" x={M.l - 5} y={sy(t)} textAnchor="end" dominantBaseline="middle">{signed(t, 0)}</text>
                    </g>
                ))}
                {games.map((g, i) => {
                    const m = g.pts_for - g.pts_against;
                    return (
                        <rect key={g.date + g.opponent} x={M.l + i * step + step * 0.1} width={Math.max(1, step * 0.8)}
                            y={Math.min(sy(0), sy(m))} height={Math.max(1, Math.abs(sy(m) - sy(0)))}
                            className={m > 0 ? 'tp-win' : 'tp-loss'}>
                            <title>{`${g.date} ${g.home ? 'vs' : g.neutral ? 'vs (neutral)' : '@'} ${g.opponent}: ${g.pts_for}-${g.pts_against}${g.ot ? ' (OT)' : ''}`}</title>
                        </rect>
                    );
                })}
                {monthTicks.map(([mth, i]) => (
                    <text key={`${mth}-${i}`} className="rx-tick" x={M.l + i * step} y={H - 6}>
                        {new Date(`2000-${mth}-01T12:00:00`).toLocaleDateString('en-US', { month: 'short' })}
                    </text>
                ))}
            </svg>
        </div>
    );
}

function Games({ g, season, abbr, onNavigate }) {
    const [showAll, setShowAll] = useState(false);
    const list = showAll ? g.games : g.games.slice(-10);
    return (
        <Section id="games" title="Game by game" meta={`${g.coverage} · ${g.note}`}>
            <MarginChart games={g.games} />
            <div className="tp-two">
                <div className="table-wrapper">
                    <TableExport name={`${abbr} ${label(season)} splits`} />
                    <table className="data-table lb-table pp-table">
                        <thead><tr><th>Split</th><th className="lb-num">W-L</th><th className="lb-num">Games</th><th className="lb-num">Margin</th></tr></thead>
                        <tbody>
                            {g.splits.map((s) => (
                                <tr key={s.key} className={s.n < g.small_split_games ? 'pp-small' : undefined}
                                    title={s.n < g.small_split_games ? `Under ${g.small_split_games} games: a small sample` : undefined}>
                                    <td className="pp-wrap">{s.label}</td>
                                    <td className="lb-num lb-stat">{s.w}-{s.l}</td>
                                    <td className="lb-num">{s.n}</td>
                                    <td className={`lb-num ${tone(s.margin)}`}>{signed(s.margin)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <p className="pp-foot">
                        Rest splits are raw: a team&apos;s back-to-backs also bunch on the road and against particular opponents. League-wide
                        effects are on <button type="button" className="pp-link" onClick={() => onNavigate('analytics', 'fatigue')}>Schedule Fatigue</button>.
                    </p>
                </div>
                <div className="table-wrapper">
                    <TableExport name={`${abbr} ${label(season)} games`} />
                    <table className="data-table lb-table pp-table">
                        <thead><tr><th>Date</th><th>Opponent</th><th className="lb-num">Score</th><th className="lb-num">Rest</th></tr></thead>
                        <tbody>
                            {[...list].reverse().map((x) => (
                                <tr key={x.date + x.opponent}>
                                    <td>{x.date}</td>
                                    <td className="oo-team">{x.home ? 'vs' : x.neutral ? 'vs*' : '@'}{' '}
                                        <TeamLink abbr={x.opponent} season={season} logoSize={18} />
                                    </td>
                                    <td className={`lb-num ${x.pts_for > x.pts_against ? 'pp-pos' : 'pp-neg'}`}>
                                        {x.pts_for > x.pts_against ? 'W' : 'L'} {x.pts_for}-{x.pts_against}{x.ot ? ' OT' : ''}
                                    </td>
                                    <td className="lb-num">{x.rest_days == null ? '—' : x.rest_days === 0 ? 'B2B' : `${x.rest_days}d`}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <p className="pp-foot tp-more">
                        {g.games.length > 10 && (
                            <button type="button" className="pp-link" onClick={() => setShowAll((v) => !v)}>
                                {showAll ? 'Show the last 10 only' : `Show all ${g.games.length} games`}
                            </button>
                        )}
                        {g.games.some((x) => x.neutral) && ' * neutral site.'}
                    </p>
                </div>
            </div>
        </Section>
    );
}

function Luck({ l, season, abbr, onNavigate }) {
    const rec = (w, lo) => `${w}-${lo}`;
    return (
        <Section id="luck" title="Luck & schedule" meta={`${l.coverage} · ${l.note}`}>
            <p className="rx-verdict">
                <strong>{rec(l.wins, l.losses)} against {num(l.exp_wins)} expected wins from points:</strong>{' '}
                <span className={tone(l.luck)}>{signed(l.luck)}</span> wins of luck ({ordinal(l.luck_rank)} luckiest of {l.n_teams}).
                SRS <strong>{signed(l.srs, 2)}</strong> ({ordinal(l.srs_rank)}), against a schedule of {signed(l.sos, 2)} (
                {ordinal(l.sos_rank)} hardest). Games decided by 3 or fewer: {rec(l.close3_w, l.close3_l)}; by 5 or fewer:{' '}
                {rec(l.close5_w, l.close5_l)}; overtime: {rec(l.ot_w, l.ot_l)}. Close-game records are small samples, and luck
                barely carries into the next season.
            </p>
            <button type="button" className="pp-link" onClick={() => onNavigate('analytics', 'luck', { season, team: abbr })}>
                Open Luck &amp; Schedule for {label(season)} →
            </button>
        </Section>
    );
}

function Roster({ r, season, abbr }) {
    const cols = [['gp', 'GP', 0], ['min', 'MPG', 1], ['pts', 'PTS', 1], ['reb', 'REB', 1], ['ast', 'AST', 1]];
    return (
        <Section id="roster" title="Roster" meta={`${r.coverage} · from ${r.source}, ordered by minutes · ${r.note}`}>
            <TableExport name={`${abbr} ${label(season)} roster`} />
            <div className="table-wrapper">
                <table className="data-table lb-table pp-table">
                    <thead>
                        <tr>
                            <th>Player</th>
                            {cols.map(([k, h]) => <th key={k} className="lb-num">{h}</th>)}
                            <th className="lb-num">TS%</th><th className="lb-num">USG</th><th className="lb-num">BPM</th><th>Role</th>
                        </tr>
                    </thead>
                    <tbody>
                        {r.players.map((p) => (
                            <tr key={p.player_id} className={p.small_sample ? 'pp-small' : undefined}
                                title={p.small_sample ? 'Under 10 games: a small sample' : undefined}>
                                <td>
                                    <PlayerName playerId={p.player_id} name={p.player_name ?? `#${p.player_id}`} size={24} />
                                    {p.split && (
                                        <span className="pp-tag" title={`Traded during the season${p.other_teams?.length ? `; also played for ${p.other_teams.join(', ')}` : ''}: games, minutes and points with this team only`}
                                            data-export-as=" (part season)">part season</span>
                                    )}
                                </td>
                                {cols.map(([k, , d]) => <td key={k} className="lb-num">{num(p[k], d)}</td>)}
                                <td className="lb-num">{pct(p.ts_pct)}</td>
                                <td className="lb-num">{pct(p.usg_pct)}</td>
                                <td className={`lb-num ${tone(p.bpm)}`}>{signed(p.bpm)}</td>
                                <td className="pp-wrap">{p.role ?? '—'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            {r.roles_note && <p className="pp-foot">{r.roles_note}</p>}
            {r.left_out.length > 0 && (
                <p className="pp-foot">
                    Left out: {r.left_out.map((x) => `${x.player_name} (listed under ${abbr} in the season stats, but the play-by-play has him playing only for ${x.played_for.join(', ')})`).join('; ')}.
                </p>
            )}
        </Section>
    );
}

function Payroll({ p, season, abbr, onNavigate }) {
    const row = (x) => (
        <tr key={x.player_id}>
            <td><PlayerName playerId={x.player_id} name={x.player_name} size={22} /></td>
            <td className="lb-num">{money(x.salary)}</td>
            <td className="lb-num">{money(x.fair_value)}</td>
            <td className={`lb-num lb-stat ${tone(x.surplus)}`}>{money(x.surplus)}</td>
        </tr>
    );
    return (
        <Section id="payroll" title="Payroll & contract value" meta={`${p.coverage} · ${p.note}`}>
            <p className="rx-verdict">
                <strong>{money(p.payroll)}</strong> across {p.players} paid players ({ordinal(p.payroll_rank)} of {p.n_teams});
                their wins above replacement were worth {money(p.fair_value)}, a surplus of{' '}
                <strong className={tone(p.surplus)}>{money(p.surplus)}</strong>.
                {p.unattributed_players > 0 && ` ${p.unattributed_players} traded players that season aren't attributed to any team.`}
            </p>
            <TableExport name={`${abbr} ${label(season)} contract value`} />
            {[['Best value', p.best], ['Worst value', p.worst]].map(([t, list]) => (
                <div key={t} className="table-wrapper tp-table-gap">
                    <table className="data-table lb-table pp-table">
                        <thead><tr><th>{t}</th><th className="lb-num">Salary</th><th className="lb-num">Worth</th><th className="lb-num">Surplus</th></tr></thead>
                        <tbody>{list.map(row)}</tbody>
                    </table>
                </div>
            ))}
            <button type="button" className="pp-link" onClick={() => onNavigate('analytics', 'contracts')}>Open Contract Value →</button>
        </Section>
    );
}

function Lineups({ l, pairs, season, abbr, onNavigate }) {
    const row = (x, i) => (
        <tr key={i}>
            <td className="pp-wrap tp-five">
                {x.players.map((p) => <PlayerName key={p.player_id} playerId={p.player_id} name={p.player_name ?? `#${p.player_id}`} size={20} />)}
            </td>
            <td className="lb-num">{num(x.minutes, 0)}</td>
            <td className="lb-num">{num(x.off_rating)}</td>
            <td className="lb-num">{num(x.def_rating)}</td>
            <td className={`lb-num lb-stat ${tone(x.net_rating)}`}>{signed(x.net_rating)}</td>
        </tr>
    );
    const head = (t) => (
        <thead><tr><th>{t}</th><th className="lb-num">Min</th><th className="lb-num">ORtg</th><th className="lb-num">DRtg</th><th className="lb-num">Net</th></tr></thead>
    );
    return (
        <Section id="lineups" title="Five-man lineups" meta={`${l.coverage} · ${l.note}`}>
            <p className="rx-verdict">
                {l.source === 'stints' ? (
                    <>
                        Every stint from play-by-play: {l.stored.toLocaleString()} distinct five-man lineups over {num(l.stored_minutes, 0)} tracked
                        minutes{l.coverage_share != null ? ` (${pct(l.coverage_share, 0)} of its minutes` : ''}
                        {l.stint_coverage?.excluded?.length ? `; ${l.stint_coverage.excluded.length} game${l.stint_coverage.excluded.length === 1 ? '' : 's'} excluded` : ''}
                        {l.stint_coverage?.partial?.length ? `; ${num(l.stint_coverage.partial_minutes, 0)} min in ${l.stint_coverage.partial.length} game${l.stint_coverage.partial.length === 1 ? '' : 's'} left out for a player with no id in the play-by-play` : ''}
                        {l.coverage_share != null ? ')' : ''}; {l.qualified} played {l.min_minutes}+ minutes together.
                    </>
                ) : (
                    <>
                        {l.stored} of this team&apos;s lineups are stored ({num(l.stored_minutes, 0)} minutes
                        {l.coverage_share != null ? `, ${pct(l.coverage_share, 0)} of its minutes` : ''}); {l.qualified} played {l.min_minutes}+ minutes together.
                    </>
                )}
            </p>
            {l.qualified > 0 ? (
                <>
                    <TableExport name={`${abbr} ${label(season)} lineups`} />
                    {[['Best lineups', l.best], ['Worst lineups', l.worst]].filter(([, list]) => list.length > 0).map(([t, list]) => (
                        <div key={t} className="table-wrapper tp-table-gap">
                            <table className="data-table lb-table pp-table">
                                {head(t)}
                                <tbody>{list.map(row)}</tbody>
                            </table>
                        </div>
                    ))}
                </>
            ) : <p className="pp-foot">No stored lineup reached {l.min_minutes} minutes.</p>}
            <div className="tp-links">
                {pairs.available && (
                    <button type="button" className="pp-link" onClick={() => onNavigate('analytics', 'pairs', { season, team: abbr })}>
                        Open the Pair Chemistry grid →
                    </button>
                )}
                <button type="button" className="pp-link" onClick={() => onNavigate('analytics', 'lineups')}>Open Lineup Chemistry →</button>
            </div>
        </Section>
    );
}

function OnOff({ o, season, abbr, onNavigate }) {
    const row = (x) => (
        <tr key={x.player_id}>
            <td><PlayerName playerId={x.player_id} name={x.player_name} size={22} /></td>
            <td className="lb-num">{num(x.minutes_on, 0)}</td>
            <td className={`lb-num ${tone(x.net_on)}`}>{signed(x.net_on)}</td>
            <td className={`lb-num ${tone(x.net_off)}`}>{signed(x.net_off)}</td>
            <td className={`lb-num lb-stat ${tone(x.on_off_net)}`}>{signed(x.on_off_net)}</td>
            <td className="lb-num" title={x.ci_excludes_zero ? 'Clear of zero' : 'Includes zero: within noise'}>
                {signed(x.ci_low)} to {signed(x.ci_high)}{x.ci_excludes_zero ? '' : ' ·'}
            </td>
        </tr>
    );
    const head = (t) => (
        <thead><tr><th>{t}</th><th className="lb-num">Min on</th><th className="lb-num">Net on</th><th className="lb-num">Net off</th><th className="lb-num">On − off</th><th className="lb-num">95% interval</th></tr></thead>
    );
    return (
        <Section id="onoff" title="On/off" meta={`${o.coverage} · ${o.note}`}>
            <p className="rx-verdict">
                {o.star ? (
                    <>Top-usage regular: <strong>{o.star.player_name}</strong>. The team was <span className={tone(o.star.net_on)}>{signed(o.star.net_on)}</span> per
                        100 with him and <span className={tone(o.star.net_off)}>{signed(o.star.net_off)}</span> without him. </>
                ) : `No player reached ${o.star_minutes} minutes. `}
                Of {o.qualified} players with {o.min_minutes}+ minutes, {o.clear_of_zero} have an interval clear of zero (about{' '}
                {num(o.qualified * 0.05)} would by chance). · marks an interval that includes zero.
            </p>
            <TableExport name={`${abbr} ${label(season)} on off`} />
            {[['Biggest gaps', o.top], ['Smallest gaps', o.bottom]].filter(([, list]) => list.length > 0).map(([t, list]) => (
                <div key={t} className="table-wrapper tp-table-gap">
                    <table className="data-table lb-table pp-table">
                        {head(t)}
                        <tbody>{list.map(row)}</tbody>
                    </table>
                </div>
            ))}
            <button type="button" className="pp-link" onClick={() => onNavigate('analytics', 'onoff', { season, team: abbr })}>
                Open On/Off for the whole roster →
            </button>
        </Section>
    );
}

function ShotMix({ m, season, abbr }) {
    const short = { 'Restricted Area': 'Restricted area', 'In The Paint (Non-RA)': 'Paint (non-RA)', 'Mid-Range': 'Mid-range',
        'Corner 3': 'Corner 3', 'Above the Break 3': 'Above-the-break 3' };
    const maxShare = Math.max(...m.zones.flatMap((z) => [z.share, z.opp_share, z.league_share].filter((v) => v != null)));
    const bar = (v, cls) => (v == null ? null : <span className={`tp-sbar ${cls}`} style={{ width: `${(v / maxShare) * 100}%` }} />);
    return (
        <Section id="shots" title="Shot mix" meta={`${m.coverage} · ${m.note}`}>
            <p className="rx-verdict">
                {m.fga.toLocaleString()} of the team&apos;s shots and {m.opp_fga.toLocaleString()} of its opponents&apos;, by zone, next to the
                league&apos;s mix that season.
            </p>
            <TableExport name={`${abbr} ${label(season)} shot mix`} />
            <div className="table-wrapper">
                <table className="data-table lb-table pp-table tp-shots">
                    <thead>
                        <tr>
                            <th>Zone</th><th className="lb-num">Team share</th><th className="lb-num">League</th>
                            <th className="lb-num">Team FG%</th><th className="lb-num">League FG%</th>
                            <th className="lb-num">Opp. share</th><th className="lb-num">Opp. FG%</th><th className="tp-bars-col" aria-hidden="true" data-export-skip />
                        </tr>
                    </thead>
                    <tbody>
                        {m.zones.map((z) => (
                            <tr key={z.zone}>
                                <td>{short[z.zone] ?? z.zone}</td>
                                <td className="lb-num lb-stat">{pct(z.share)}</td>
                                <td className="lb-num">{pct(z.league_share)}</td>
                                <td className={`lb-num ${tone(z.fg_pct != null && z.league_fg_pct != null ? z.fg_pct - z.league_fg_pct : null)}`}>{pct(z.fg_pct)}</td>
                                <td className="lb-num">{pct(z.league_fg_pct)}</td>
                                <td className="lb-num">{pct(z.opp_share)}</td>
                                <td className={`lb-num ${tone(z.opp_fg_pct != null && z.league_fg_pct != null ? z.league_fg_pct - z.opp_fg_pct : null)}`}>{pct(z.opp_fg_pct)}</td>
                                <td className="tp-bars-col" aria-hidden="true" data-export-skip>
                                    <span className="tp-bars">{bar(z.share, 'tp-sbar--team')}{bar(z.league_share, 'tp-sbar--lg')}{bar(z.opp_share, 'tp-sbar--opp')}</span>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            <p className="pp-foot tp-key">
                <span><span className="tp-swatch tp-swatch--team" />Team</span>
                <span><span className="tp-swatch tp-swatch--lg" />League</span>
                <span><span className="tp-swatch tp-swatch--opp" />Opponents</span>
                <span>FG% in green/red: better/worse than the league for this team (opponents: lower is better).</span>
            </p>
        </Section>
    );
}

const BLOCKS = [
    ['games', 'Game by game'], ['luck', 'Luck & schedule'], ['roster', 'Roster'], ['payroll', 'Payroll'],
    ['lineups', 'Lineups'], ['on_off', 'On/off'], ['shot_mix', 'Shot mix'],
];

export default function TeamProfile({ onNavigate }) {
    const params = useInitialParams();
    const [query, setQuery] = useState(() => ({
        abbr: parseParam.str(params, 'abbr')?.toUpperCase() ?? null,
        season: parseParam.int(params, 'season', { min: 1900, max: 2100 }),
    }));
    const [result, setResult] = useState(null); // { data, key } | { error, key }
    const key = `${query.abbr}-${query.season}`;

    useUrlSync(result?.data ? { abbr: result.data.abbreviation, season: result.data.season } : null);

    useEffect(() => {
        if (!query.abbr) return undefined;
        let active = true;
        fetchTeamProfile(query.abbr, query.season ?? undefined)
            .then((data) => { if (active) setResult({ data, key }); })
            .catch((e) => {
                if (active) setResult({ key, error: e.response?.data?.detail || 'The team page couldn’t load. Is the impact API (port 8002) running?' });
            });
        return () => { active = false; };
    }, [query, key]);

    const d = result?.data;
    const missing = useMemo(() => (d ? BLOCKS.filter(([k]) => !d[k].available).map(([k, name]) => [name, d[k].reason]) : []), [d]);
    // Each season under its own code (SEA in 2005, OKC in 2010), so no "played as" note.
    const onSeason = (season) => setQuery({
        abbr: d?.franchise_history.find((r) => r.season === season)?.abbreviation ?? query.abbr, season,
    });

    if (!query.abbr) {
        return (
            <section className="dashboard-card">
                <p className="page-subtitle">No team chosen. Open one by clicking a team&apos;s logo or name (standings, Team Comparison, any table), or search with Ctrl+K.</p>
            </section>
        );
    }
    if (result?.error) return <section className="dashboard-card"><p className="error-message">{result.error}</p></section>;
    if (!d) return <Loader />;
    const stale = result.key !== key;
    const jump = (k) => document.getElementById(`tp-${k}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const shown = [['summary', 'Summary', true], ...BLOCKS.map(([k, name]) => [k === 'on_off' ? 'onoff' : k === 'shot_mix' ? 'shots' : k, name, d[k].available])]
        .filter(([, , ok]) => ok);

    return (
        <div className={`pp tp${stale ? ' lb-results--stale' : ''}`} aria-busy={stale}>
            <Hero d={d} onSeason={onSeason} />
            <Glance s={d.summary} />
            <nav className="pp-jump" aria-label="Sections on this page">
                {shown.map(([k, text]) => <button key={k} type="button" onClick={() => jump(k)}>{text}</button>)}
                {missing.length > 0 && <button type="button" onClick={() => jump('missing')}>Not on file</button>}
            </nav>

            <Section id="franchise" title="Franchise history"
                meta={`Every season of this franchise on file (Basketball-Reference team summaries), under each season's own name.`}
                info={<InfoTooltip label="How franchises are joined" title="Franchises">
                    Franchises follow the NBA&apos;s own records: the Charlotte Hornets of 1988-2002 count with today&apos;s Hornets, the New
                    Orleans Hornets with the Pelicans, the Seattle SuperSonics with the Thunder, the New Jersey Nets with Brooklyn.
                </InfoTooltip>}>
                <FranchiseChart rows={d.franchise_history} season={d.season} onSeason={onSeason} />
            </Section>
            <Summary s={d.summary} />
            {d.games.available && <Games g={d.games} season={d.season} abbr={d.abbreviation} onNavigate={onNavigate} />}
            {d.luck.available && <Luck l={d.luck} season={d.season} abbr={d.abbreviation} onNavigate={onNavigate} />}
            {d.roster.available && <Roster r={d.roster} season={d.season} abbr={d.abbreviation} />}
            {d.payroll.available && <Payroll p={d.payroll} season={d.season} abbr={d.abbreviation} onNavigate={onNavigate} />}
            {d.lineups.available && <Lineups l={d.lineups} pairs={d.pairs} season={d.season} abbr={d.abbreviation} onNavigate={onNavigate} />}
            {d.on_off.available && <OnOff o={d.on_off} season={d.season} abbr={d.abbreviation} onNavigate={onNavigate} />}
            {d.shot_mix.available && <ShotMix m={d.shot_mix} season={d.season} abbr={d.abbreviation} />}

            {missing.length > 0 && (
                <section id="tp-missing" className="dashboard-card pp-section pp-missing">
                    <h2 className="card-title pp-section-title">Not on file for the {label(d.season)} {d.team_name}</h2>
                    <dl>
                        {missing.map(([what, why]) => <div key={what}><dt>{what}</dt><dd>{why}</dd></div>)}
                    </dl>
                </section>
            )}
            {!CURRENT.has(d.franchise) && (
                <p className="page-subtitle pp-foot">This franchise no longer exists; it&apos;s shown under its own name and code.</p>
            )}
        </div>
    );
}
