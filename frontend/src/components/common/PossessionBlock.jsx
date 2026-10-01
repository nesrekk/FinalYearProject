import React, { useEffect, useState } from 'react';
import { fetchPlayerPossessions } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from './InfoTooltip';
import TableExport from './TableExport';
import '../../styles/possessions.css';

// The player profile's possession block (GET /possessions/player/{id}): the
// team's points per possession, offence and defence, by how the possession
// began, on the floor vs off it. `seasons` comes from /player-profile/{id};
// the block fetches the rest itself.

const label = (s) => `${s - 1}-${String(s).slice(-2)}`;
const f3 = (v) => (v == null ? '—' : v.toFixed(3));
const signed = (v) => (v == null ? '—' : Math.abs(v) < 0.0005 ? '0.000' : `${v > 0 ? '+' : '−'}${Math.abs(v).toFixed(3)}`);

function Ppp({ c }) {
    if (!c) return <td className="lb-num">—</td>;
    return (
        <td className={`lb-num${c.small ? ' pb-small' : ''}`} title={`${c.poss.toLocaleString()} possessions, ${c.pts.toLocaleString()} points`}>
            {f3(c.ppp)}
        </td>
    );
}

function Diff({ d, side, small }) {
    if (!d) return <td className="lb-num">—</td>;
    const good = side === 'off' ? d.diff > 0 : d.diff < 0;
    const tone = small ? ' pb-small' : d.excludes_zero ? (good ? ' oo-pos' : ' oo-neg') : '';
    return (
        <td className={`lb-num${tone}`} title={`95% interval ${signed(d.lo)} to ${signed(d.hi)}${d.excludes_zero ? '' : ' (includes zero: within noise)'}`}>
            {signed(d.diff)}
        </td>
    );
}

export default function PossessionBlock({ playerId, seasons, onNavigate }) {
    const [season, setSeason] = useState(seasons[seasons.length - 1]);
    const [teamIdx, setTeamIdx] = useState(0);
    const key = `${playerId}-${season}`;
    const [res, setRes] = useState(null); // { key, data } | { key, error }

    useEffect(() => {
        let active = true;
        fetchPlayerPossessions(playerId, season)
            .then((d) => { if (active) { setRes({ key, data: d }); setTeamIdx(0); } })
            .catch((e) => { if (active) setRes({ key, error: e.response?.data?.detail || 'Possessions couldn\'t load.' }); });
        return () => { active = false; };
    }, [playerId, season, key]);

    const d = res?.key === key ? res.data : null;
    const error = res?.key === key ? res.error : '';
    const team = d?.teams[Math.min(teamIdx, (d?.teams.length ?? 1) - 1)];
    const all = team?.rows.find((r) => r.start_type === 'all');
    const labels = {
        all: 'Every possession', made_fg: 'After a made shot', dreb: 'After a defensive rebound', steal: 'After a steal',
        dead_tov: 'After a dead-ball turnover', made_ft: 'After a made last free throw', dreb_ft: 'After a rebounded free throw',
        team_dreb: 'After a team rebound', period_start: 'Start of a period', jump_ball: 'After a held ball', other: 'Other',
    };

    return (
        <section id="pp-possessions" className="dashboard-card pp-section">
            <h2 className="card-title pp-section-title">
                Possessions on and off the floor
                <InfoTooltip label="How possessions are counted" title="From the play-by-play">
                    Every possession since 2020-21, cut from ESPN&apos;s play-by-play, sorted by how it began (after the other
                    team scored, after a defensive rebound, after a steal...). &ldquo;On&rdquo; = on the floor when the possession
                    began, from the five-man stints; &ldquo;off&rdquo; = the team&apos;s other possessions in the same games. Only
                    stints with all ten players identified count. Intervals treat possessions as independent, so they run a
                    little narrow. This describes the lineups the player was part of; it doesn&apos;t separate one player from the other four (RAPM does).
                </InfoTooltip>
            </h2>
            <div className="lb-controls pb-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={season} onChange={(e) => setSeason(Number(e.target.value))}>
                        {[...seasons].reverse().map((x) => <option key={x} value={x}>{label(x)}</option>)}
                    </select>
                </label>
                {d && d.teams.length > 1 && (
                    <label>
                        <span>Team</span>
                        <select className="input-field" value={teamIdx} onChange={(e) => setTeamIdx(Number(e.target.value))}>
                            {d.teams.map((t, i) => <option key={t.team} value={i}>{t.team} ({t.games} games)</option>)}
                        </select>
                    </label>
                )}
            </div>
            {error && <p className="error-message">{error}</p>}
            {!d && !error && <Loader />}
            {d && team && (
                <>
                    {all?.off_on && all?.off_off_court && (
                        <p className="page-subtitle" style={{ marginTop: 0 }}>
                            {d.label}, {team.team}, {team.games} games: the team scored {f3(all.off_on.ppp)} points a possession with
                            {' '}{d.player_name ?? 'this player'} on the floor and {f3(all.off_off_court.ppp)} without
                            {all.def_on && all.def_off_court && <>, and allowed {f3(all.def_on.ppp)} vs {f3(all.def_off_court.ppp)}</>}.
                            League: {f3(d.league.all)}.
                        </p>
                    )}
                    <TableExport name={`possessions on off ${d.player_name ?? playerId} ${d.label} ${team.team}`} />
                    <div className="table-wrapper">
                        <table className="data-table lb-table pb-table">
                            <thead>
                                <tr>
                                    <th rowSpan={2}>Possession began</th>
                                    <th colSpan={3} className="pb-group">Offence: points per possession</th>
                                    <th colSpan={3} className="pb-group">Defence: points allowed</th>
                                    <th rowSpan={2} className="lb-num" title="The league's points per possession for this kind of possession">League</th>
                                </tr>
                                <tr>
                                    <th className="lb-num">On</th><th className="lb-num">Off</th><th className="lb-num" title="On minus off">Diff</th>
                                    <th className="lb-num">On</th><th className="lb-num">Off</th><th className="lb-num" title="On minus off (negative = fewer allowed with the player on)">Diff</th>
                                </tr>
                            </thead>
                            <tbody>
                                {team.rows.map((r) => (
                                    <tr key={r.start_type}>
                                        <td>{r.start_type === 'all' ? <strong>{labels.all}</strong> : labels[r.start_type] ?? r.start_type}</td>
                                        <Ppp c={r.off_on} /><Ppp c={r.off_off_court} /><Diff d={r.off_diff} side="off" small={r.off_on?.small || r.off_off_court?.small} />
                                        <Ppp c={r.def_on} /><Ppp c={r.def_off_court} /><Diff d={r.def_diff} side="def" small={r.def_on?.small || r.def_off_court?.small} />
                                        <td className="lb-num">{f3(d.league[r.start_type])}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="page-subtitle pp-foot">
                        Grey: under {d.min_poss} possessions on or off the floor. Green or red: the 95% interval of the difference clears zero. Hover a
                        value for its possessions, a difference for its interval.{' '}
                        <button type="button" className="pp-link" onClick={() => onNavigate('possessions', null, { team: team.team, season: d.season })}>
                            Open {team.team} in the Possession Explorer
                        </button>
                    </p>
                </>
            )}
        </section>
    );
}
