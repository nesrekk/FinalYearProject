import React, { useEffect, useRef, useState } from 'react';
import { fetchTradeTeams, fetchTradeRoster, fetchTradeImpact } from '../../services/api';
import TradeContractValue from '../common/TradeContractValue';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import Icon from '../common/Icon';
import TeamLogo from '../common/TeamLogo';
import PlayerName from '../common/PlayerName';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import CopyLinkButton from '../common/CopyLinkButton';
import SaveViewButton from '../common/SaveViewButton';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/trade-impact.css';
import { currentSeason, isLiveSeason, seasonRange } from '../../utils/season';

// One screen for a 1-for-1 trade: projected wins (Trade Analyzer's model),
// starting-five spacing (Gravity Index, additive over any five) and payroll
// and surplus (Contract Value). Nothing new is estimated here; each block
// says which seasons it covers and shows nothing when the data isn't there.

const DEFAULT_SEASON = 2025; // the latest season with all three blocks on file

function fmt(v, digits = 1) {
    return v == null ? '—' : Number(v).toFixed(digits);
}

function signed(v, digits = 2) {
    if (v == null) return '—';
    // Round first so a value that prints as zero never shows as "−0.0".
    const n = Number(Number(v).toFixed(digits));
    return `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n).toFixed(digits)}`;
}

function money(v, { sign = false } = {}) {
    if (v == null) return '—';
    const a = Math.abs(v);
    const body = a >= 1e6 ? `$${(a / 1e6).toFixed(1)}M` : `$${Math.round(a / 1e3).toLocaleString()}K`;
    if (v < 0) return `−${body}`;
    return sign && v > 0 ? `+${body}` : body;
}

function pct(v) {
    return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}

function ordinal(n) {
    if (n == null) return '—';
    const r = Math.round(n);
    const s = ['th', 'st', 'nd', 'rd'];
    const v = r % 100;
    return `${r}${s[(v - 20) % 10] || s[v] || s[0]}`;
}

function deltaColor(delta, { neutral = false } = {}) {
    if (delta == null || neutral || Math.abs(delta) < 1e-9) return 'var(--text-3)';
    return delta > 0 ? 'var(--positive)' : 'var(--negative)';
}

function Delta({ value, format, neutral = false }) {
    return <span style={{ color: deltaColor(value, { neutral }), fontWeight: 700 }}>{format(value)}</span>;
}

function PlayerRow({ label, player }) {
    if (!player) return null;
    return (
        <div className="hb-trade-card">
            <div className="page-subtitle" style={{ marginBottom: 6 }}>{label}</div>
            <span className="hb-trade-name"><PlayerName playerId={player.player_id} name={player.player_name} size={36} /></span>
            <span className="entity-row-sub" style={{ display: 'block', marginTop: 4 }}>
                {player.archetype || 'Unclustered'} · {fmt(player.pts)} pts · {fmt(player.min)} mpg
            </span>
        </div>
    );
}

function Block({ title, children }) {
    return (
        <div className="ti-block">
            <h4 className="ti-block-title">{title}</h4>
            {children}
        </div>
    );
}

function Unavailable({ reason }) {
    return <p className="page-subtitle ti-unavailable" style={{ margin: 0 }}>{reason}</p>;
}

function WinsBlock({ wins }) {
    if (!wins?.available) return <Unavailable reason={wins?.reason || 'No projection available.'} />;
    return (
        <>
            <div className="ti-bigrow">
                <span className="ti-big">{fmt(wins.before_wins, 1)}</span>
                <Icon name="arrow_forward" size={18} />
                <span className="ti-big">{fmt(wins.after_wins, 1)}</span>
                <Delta value={wins.delta_wins} format={(v) => `${signed(v, 1)} wins`} />
            </div>
            <p className="page-subtitle" style={{ margin: '4px 0 0', fontSize: '0.78rem' }}>
                Projected win% {pct(wins.before_pct)} → {pct(wins.after_pct)} ({signed(wins.delta_pct * 100, 1)} pts),
                on an {wins.season_games}-game scale. Roster net rating {signed(wins.net_rating.before, 1)} → {signed(wins.net_rating.after, 1)},
                TS% {fmt(wins.ts_pct.before, 3)} → {fmt(wins.ts_pct.after, 3)}, minute-weighted.
            </p>
        </>
    );
}

function FiveList({ report, replacedId, incomingId }) {
    return (
        <ul className="ti-five">
            {report.players.map((p) => {
                const swappedOut = p.player_id === replacedId;
                const swappedIn = p.player_id === incomingId;
                return (
                    <li key={p.player_id} className={swappedOut ? 'ti-five-out' : swappedIn ? 'ti-five-in' : ''}>
                        <span className="ti-five-name">
                            <PlayerName playerId={p.player_id} name={p.player_name} size={20} />
                            {swappedOut && <span className="ti-tag">leaves</span>}
                            {swappedIn && <span className="ti-tag ti-tag-in">arrives</span>}
                        </span>
                        <span className="ti-five-val">{signed(p.gravity)}</span>
                    </li>
                );
            })}
        </ul>
    );
}

function Ortg({ report }) {
    const pred = report.predicted_ortg_change;
    if (pred) {
        return (
            <span>
                predicted ORtg vs. a median-spacing lineup {signed(pred.vs_median_lineup, 1)}{' '}
                (95% CI {signed(pred.ci_low, 1)} to {signed(pred.ci_high, 1)})
            </span>
        );
    }
    return <span>{report.no_effect_message || 'No ORtg estimate.'}</span>;
}

function SpacingBlock({ spacing, side, seasonLabel, outgoingName }) {
    if (!spacing?.available) return <Unavailable reason={spacing?.reason || 'No spacing data.'} />;
    const team = spacing[side];
    if (!team?.available) return <Unavailable reason={team?.reason || 'No spacing data for this team.'} />;
    const { before, after, lineup } = team;
    const ruleText = team.rule === 'outgoing_starter'
        ? `${outgoingName} was in that five, so the incoming player takes that spot.`
        : `${outgoingName} wasn't in that five; the incoming player is assumed to take the spot of ${team.replaced_player_name}, the starter with the fewest minutes per game.`;
    return (
        <>
            <p className="page-subtitle" style={{ margin: '0 0 6px', fontSize: '0.78rem' }}>
                Likely starting five = the most-used real five this season: {lineup.poss.toLocaleString()} possessions,{' '}
                {fmt(lineup.off_rating)} ORtg, {signed(lineup.net_rating, 1)} net. {ruleText}
            </p>
            <div className="ti-fives">
                <div>
                    <div className="ti-five-head">Before · spacing <strong>{signed(before.spacing)}</strong> · {ordinal(before.percentile_vs_real_lineups)} pct.</div>
                    <FiveList report={before} replacedId={team.replaced_player_id} />
                </div>
                <div>
                    <div className="ti-five-head">After · spacing <strong>{signed(after.spacing)}</strong> · {ordinal(after.percentile_vs_real_lineups)} pct.</div>
                    <FiveList report={after} incomingId={after.players.find((p) => !before.players.some((q) => q.player_id === p.player_id))?.player_id} />
                </div>
            </div>
            <div className="ti-bigrow" style={{ marginTop: 8 }}>
                <Delta value={team.delta_spacing} format={(v) => `${signed(v)} spacing`} />
                <span className="page-subtitle" style={{ fontSize: '0.78rem' }}>
                    {ordinal(before.percentile_vs_real_lineups)} → {ordinal(after.percentile_vs_real_lineups)} percentile of{' '}
                    {team.n_real_lineups.toLocaleString()} real {seasonLabel} lineups with 100+ possessions (median {signed(team.median_real_spacing)}).
                </span>
            </div>
            <p className="page-subtitle" style={{ margin: '4px 0 0', fontSize: '0.78rem' }}>
                Before: <Ortg report={before} />. After: <Ortg report={after} />.
                {after.real_lineup && (
                    <> The five after the trade really played together: {fmt(after.real_lineup.off_rating)} ORtg over {after.real_lineup.poss.toLocaleString()} possessions.</>
                )}
            </p>
        </>
    );
}

function PayrollBlock({ payroll, side }) {
    if (!payroll?.available) return <Unavailable reason={payroll?.reason || 'No salary data.'} />;
    const team = payroll[side];
    if (!team?.available) {
        return (
            <>
                <Unavailable reason={team?.reason || 'No salary data for this team.'} />
                {team?.before && (
                    <p className="page-subtitle" style={{ margin: '4px 0 0', fontSize: '0.78rem' }}>
                        Priced payroll before the trade: {money(team.before.payroll)} across {team.before.n_priced} players who played.
                    </p>
                )}
            </>
        );
    }
    const rows = [
        ['Priced payroll', money(team.before.payroll), money(team.after.payroll), team.delta_payroll, (v) => money(v, { sign: true }), true],
        ['Surplus (fair value − salary)', money(team.before.surplus), money(team.after.surplus), team.delta_surplus, (v) => money(v, { sign: true }), false],
    ];
    return (
        <>
            <table className="data-table ti-mini">
                <thead><tr><th></th><th>Before</th><th>After</th><th>Change</th></tr></thead>
                <tbody>
                    {rows.map(([label, b, a, d, f, neutral]) => (
                        <tr key={label}>
                            <td>{label}</td><td>{b}</td><td>{a}</td>
                            <td><Delta value={d} format={f} neutral={neutral} /></td>
                        </tr>
                    ))}
                </tbody>
            </table>
            <p className="page-subtitle" style={{ margin: '4px 0 0', fontSize: '0.78rem' }}>
                {team.before.n_priced} players with matched salaries who played for the team; not the full cap sheet.
                Cost per win this season: {money(payroll.cost_per_win)}.
                Outgoing {money(team.outgoing.salary)} (surplus {money(team.outgoing.surplus, { sign: true })}),
                incoming {money(team.incoming.salary)} (surplus {money(team.incoming.surplus, { sign: true })}).
            </p>
        </>
    );
}

function TeamCard({ data, side }) {
    const trade = data.trade[side];
    return (
        <div className="dashboard-card" style={{ flex: '1 1 340px', minWidth: 0 }}>
            <h3 className="hb-trade-heading">
                <TeamLogo abbreviation={trade.team} size={24} />
                {trade.team}
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginBottom: '0.75rem' }}>
                <PlayerRow label="Sends" player={trade.sends} />
                <PlayerRow label="Receives" player={trade.receives} />
            </div>
            <Block title="Projected wins"><WinsBlock wins={data.wins[side]} /></Block>
            <Block title="Starting-five spacing">
                <SpacingBlock spacing={data.spacing} side={side} seasonLabel={data.season_label} outgoingName={trade.sends.player_name} />
            </Block>
            <Block title="Payroll and surplus"><PayrollBlock payroll={data.payroll} side={side} /></Block>
        </div>
    );
}

// Every number from both cards in one exportable table.
function GlanceTable({ data }) {
    const cell = (side, pick, format) => {
        try {
            const v = pick(side);
            return v == null ? '—' : format(v);
        } catch {
            return '—';
        }
    };
    const w = (s) => data.wins[s];
    const sp = (s) => (data.spacing.available && data.spacing[s].available ? data.spacing[s] : null);
    const pay = (s) => (data.payroll.available && data.payroll[s].available ? data.payroll[s] : null);
    const rows = [
        ['Projected win%', (s) => w(s).available && w(s).before_pct, (s) => w(s).available && w(s).after_pct, (s) => w(s).available && w(s).delta_pct, pct, (v) => signed(v * 100, 1) + ' pts'],
        ['Projected wins (82 games)', (s) => w(s).available && w(s).before_wins, (s) => w(s).available && w(s).after_wins, (s) => w(s).available && w(s).delta_wins, (v) => fmt(v, 1), (v) => signed(v, 1)],
        ['Starting-five spacing', (s) => sp(s)?.before.spacing, (s) => sp(s)?.after.spacing, (s) => sp(s)?.delta_spacing, (v) => signed(v), (v) => signed(v)],
        ['Spacing percentile', (s) => sp(s)?.before.percentile_vs_real_lineups, (s) => sp(s)?.after.percentile_vs_real_lineups, (s) => sp(s) && sp(s).after.percentile_vs_real_lineups - sp(s).before.percentile_vs_real_lineups, (v) => fmt(v, 1), (v) => signed(v, 1)],
        ['Priced payroll', (s) => pay(s)?.before.payroll, (s) => pay(s)?.after.payroll, (s) => pay(s)?.delta_payroll, money, (v) => money(v, { sign: true })],
        ['Contract surplus', (s) => pay(s)?.before.surplus, (s) => pay(s)?.after.surplus, (s) => pay(s)?.delta_surplus, money, (v) => money(v, { sign: true })],
    ];
    const a = data.trade.team_a.team;
    const b = data.trade.team_b.team;
    return (
        <div className="dashboard-card" style={{ marginTop: '1rem' }}>
            <h4 className="section-heading" style={{ marginTop: 0 }}>Both sides at a glance</h4>
            <TableExport />
            <div className="table-wrapper">
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>Metric</th>
                            <th>{a} before</th><th>{a} after</th><th>{a} change</th>
                            <th>{b} before</th><th>{b} after</th><th>{b} change</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map(([label, before, after, delta, f, fd]) => (
                            <tr key={label}>
                                <td>{label}</td>
                                <td>{cell('team_a', before, f)}</td>
                                <td>{cell('team_a', after, f)}</td>
                                <td>{cell('team_a', delta, fd)}</td>
                                <td>{cell('team_b', before, f)}</td>
                                <td>{cell('team_b', after, f)}</td>
                                <td>{cell('team_b', delta, fd)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

// Keep a player id only if they're on the roster that just loaded.
const onRoster = (roster) => (id) => (roster.some((p) => String(p.player_id) === id) ? id : '');

export default function TradeImpact({ onNavigate }) {
    // Same link parameters as the Trade Analyzer (?season=&ta=&pa=&tb=&pb=),
    // so the two pages can hand a trade to each other.
    const params = useInitialParams();
    const linkedId = (key) => (parseParam.int(params, key, { min: 1 }) ?? '').toString();
    const [season, setSeason] = useState(() => parseParam.int(params, 'season', { min: 2010, max: currentSeason() }) ?? DEFAULT_SEASON);
    const [teams, setTeams] = useState([]);
    const [teamA, setTeamA] = useState(() => parseParam.str(params, 'ta')?.toUpperCase() ?? '');
    const [teamB, setTeamB] = useState(() => parseParam.str(params, 'tb')?.toUpperCase() ?? '');
    const [rosterA, setRosterA] = useState([]);
    const [rosterB, setRosterB] = useState([]);
    const [playerAId, setPlayerAId] = useState(() => linkedId('pa'));
    const [playerBId, setPlayerBId] = useState(() => linkedId('pb'));
    const autoRun = useRef(Boolean(playerAId && playerBId));

    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        let active = true;
        fetchTradeTeams(season)
            .then((d) => { if (active) setTeams(d.teams || []); })
            .catch(() => { if (active) setTeams([]); });
        return () => { active = false; };
    }, [season]);

    useEffect(() => {
        if (!teamA) { setRosterA([]); return; }
        let active = true;
        fetchTradeRoster(teamA, season)
            .then((d) => { if (!active) return; setRosterA(d.roster || []); setPlayerAId(onRoster(d.roster || [])); })
            .catch(() => { if (active) setRosterA([]); });
        return () => { active = false; };
    }, [teamA, season]);

    useEffect(() => {
        if (!teamB) { setRosterB([]); return; }
        let active = true;
        fetchTradeRoster(teamB, season)
            .then((d) => { if (!active) return; setRosterB(d.roster || []); setPlayerBId(onRoster(d.roster || [])); })
            .catch(() => { if (active) setRosterB([]); });
        return () => { active = false; };
    }, [teamB, season]);

    useUrlSync({ season, ta: teamA, pa: playerAId, tb: teamB, pb: playerBId });

    const changeSeason = (v) => { setSeason(v); setPlayerAId(''); setPlayerBId(''); };
    const changeTeamA = (v) => { setTeamA(v); setPlayerAId(''); };
    const changeTeamB = (v) => { setTeamB(v); setPlayerBId(''); };

    const canRun = teamA && teamB && playerAId && playerBId && teamA !== teamB;
    const rostersReady = rosterA.some((p) => String(p.player_id) === playerAId)
        && rosterB.some((p) => String(p.player_id) === playerBId);

    async function run() {
        setLoading(true);
        setError('');
        setData(null);
        try {
            setData(await fetchTradeImpact({ season, teamA, playerAId: Number(playerAId), teamB, playerBId: Number(playerBId) }));
        } catch (e) {
            setError(e?.response?.data?.detail || 'Failed to compute the trade impact.');
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        if (!autoRun.current || !canRun || !rostersReady) return;
        autoRun.current = false;
        run();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [canRun, rostersReady]);

    const nameOf = (roster, id) => roster.find((p) => p.player_id === Number(id))?.player_name;

    return (
        <div className="page page-trade fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="account_balance" /></span>
                    Trade Impact
                    <InfoTooltip label="How this works" title="What this combines">
                        Three existing numbers for one 1-for-1 trade, before and after, for both teams: the Trade
                        Analyzer's projected win% (a regression on the roster's minute-weighted net rating and TS%),
                        the Spacing Lab's lineup spacing for each team's likely starting five (the Gravity Index is
                        additive over any five players), and Contract Value's payroll and surplus. Nothing new is
                        estimated; the notes under the results say what each number is and isn't.
                    </InfoTooltip>
                    <CopyLinkButton />
                    <SaveViewButton pageId="tradeimpact" />
                </h2>
                <p className="page-subtitle">
                    Pick a season and two teams, then a player from each roster. Coverage: win projection 2009-10 onward,
                    spacing 2013-14 onward (tracking era), payroll 2005-06 to 2016-17, 2018-19, 2019-20 and 2024-25.
                    2024-25 is the latest season with all three.
                </p>

                <div className="input-row">
                    <select className="input-field" value={season} onChange={(e) => changeSeason(Number(e.target.value))} aria-label="Season">
                        {seasonRange(2010).map((s) => <option key={s} value={s}>{`${s - 1}-${String(s).slice(-2)}`}{isLiveSeason(s) ? ' (so far)' : ''}</option>)}
                    </select>
                    <select className="input-field" value={teamA} onChange={(e) => changeTeamA(e.target.value)} aria-label="Team A">
                        <option value="">Team A…</option>
                        {teams.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                    <select className="input-field" value={teamB} onChange={(e) => changeTeamB(e.target.value)} aria-label="Team B">
                        <option value="">Team B…</option>
                        {teams.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                </div>
                <div className="input-row">
                    <select className="input-field" value={playerAId} onChange={(e) => setPlayerAId(e.target.value)}
                        disabled={!rosterA.length} aria-label="Player from Team A">
                        <option value="">{teamA ? `Player from ${teamA}…` : 'Pick Team A first'}</option>
                        {rosterA.map((p) => <option key={p.player_id} value={p.player_id}>{p.player_name} ({fmt(p.pts)} ppg)</option>)}
                    </select>
                    <select className="input-field" value={playerBId} onChange={(e) => setPlayerBId(e.target.value)}
                        disabled={!rosterB.length} aria-label="Player from Team B">
                        <option value="">{teamB ? `Player from ${teamB}…` : 'Pick Team B first'}</option>
                        {rosterB.map((p) => <option key={p.player_id} value={p.player_id}>{p.player_name} ({fmt(p.pts)} ppg)</option>)}
                    </select>
                    <button type="button" className="btn-primary" disabled={!canRun || loading} onClick={run}>
                        {loading ? 'Working…' : 'Show impact'}
                    </button>
                    {canRun && onNavigate && (
                        <button type="button" className="table-export-btn"
                            onClick={() => onNavigate('trade', undefined, { season, ta: teamA, pa: playerAId, tb: teamB, pb: playerBId })}>
                            <Icon name="swap_horiz" size={15} /> Open in Trade Analyzer
                        </button>
                    )}
                </div>
                {teamA && teamA === teamB && <p className="error-message" style={{ marginTop: '0.5rem' }}>Pick two different teams.</p>}
                {error && <p className="error-message" style={{ marginTop: '0.5rem' }}>{error}</p>}
            </div>

            {loading && <Loader />}

            {data && !loading && (
                <>
                    <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginTop: '1rem' }}>
                        <TeamCard data={data} side="team_a" />
                        <TeamCard data={data} side="team_b" />
                    </div>
                    <GlanceTable data={data} />
                    {data.payroll.available && (
                        <TradeContractValue
                            season={season}
                            players={[
                                { id: Number(playerAId), name: nameOf(rosterA, playerAId) || 'Player A' },
                                { id: Number(playerBId), name: nameOf(rosterB, playerBId) || 'Player B' },
                            ]}
                        />
                    )}
                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h4 className="section-heading" style={{ marginTop: 0 }}>
                            What these numbers are, and aren't <SourceBadge source={data._source} />
                        </h4>
                        <dl className="ti-notes">
                            <dt>Projected wins</dt><dd>{data.notes.wins}</dd>
                            <dt>Spacing</dt><dd>{data.notes.spacing}</dd>
                            <dt>Payroll and surplus</dt><dd>{data.notes.payroll}</dd>
                        </dl>
                    </div>
                </>
            )}
        </div>
    );
}
