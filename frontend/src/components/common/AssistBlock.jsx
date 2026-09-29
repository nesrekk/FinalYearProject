import React, { useEffect, useState } from 'react';
import { fetchPlayerAssists } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from './InfoTooltip';
import PlayerName from './PlayerName';
import TableExport from './TableExport';
import '../../styles/assists.css';

// The player profile's assist block (GET /assists/player/{id}): for a
// season, his top targets and top feeders, how much of his scoring was
// assisted against the league, and every season on file. `seasons` comes
// from /player-profile/{id}; the block fetches the rest itself.

const label = (s) => `${s - 1}-${String(s).slice(-2)}`;
const pct = (v) => (v == null ? '—' : `${Math.round(v * 100)}%`);

function Pairs({ rows, total, who, title, empty }) {
    const max = Math.max(1, ...rows.map((e) => e.ast));
    return (
        <div>
            <h4 className="rp-panel-title">{title}</h4>
            {rows.length ? (
                <ul className="an-list">
                    {rows.map((e) => {
                        const id = who === 'scorer' ? e.scorer_id : e.passer_id;
                        const name = who === 'scorer' ? e.scorer_name : e.passer_name;
                        return (
                            <li key={`${e.team_abbreviation}-${id}`}>
                                {id ? <PlayerName playerId={id} name={name} size={20} /> : <span>Unidentified</span>}
                                <span className="an-list-n" title={`${e.kinds.rim} layups/dunks, ${e.kinds.floater} floaters/hooks, ${e.kinds.jumper} 2-pt jumpers, ${e.kinds.three} threes`}>
                                    {e.ast} · {pct(total ? e.ast / total : null)} · {e.kinds.three} 3s
                                </span>
                                <span className="an-list-bar" aria-hidden="true"><span style={{ width: `${(100 * e.ast) / max}%` }} /></span>
                            </li>
                        );
                    })}
                </ul>
            ) : <p className="empty-message">{empty}</p>}
        </div>
    );
}

export default function AssistBlock({ playerId, seasons, onNavigate }) {
    const [season, setSeason] = useState(seasons[seasons.length - 1]);
    const key = `${playerId}-${season}`;
    const [res, setRes] = useState(null); // { key, data } | { key, error }

    useEffect(() => {
        let active = true;
        fetchPlayerAssists(playerId, season)
            .then((d) => { if (active) setRes({ key, data: d }); })
            .catch((e) => { if (active) setRes({ key, error: e.response?.data?.detail || 'Assists couldn\'t load.' }); });
        return () => { active = false; };
    }, [playerId, season, key]);

    const d = res?.key === key ? res.data : null;
    const error = res?.key === key ? res.error : '';
    const s = d?.summary;
    const assisted = s ? s.ast_fgm2 + s.ast_fgm3 : 0;
    const rank = (r, n, floor, what) => (r ? `#${r} of ${n} with ${floor}+ made ${what}` : `under ${floor} made ${what}, not ranked`);

    return (
        <section id="pp-assists" className="dashboard-card pp-section">
            <h2 className="card-title pp-section-title">
                Assists: who he feeds, who feeds him
                <InfoTooltip label="How assists are counted" title="From the play-by-play">
                    Every assisted basket since 2020-21: ESPN names the passer on each made shot, matched to a player by the same
                    parser as the Game Log, so his assists here equal his Game Log&apos;s. Assisted share = assisted makes / all his
                    makes of that kind. Assists are the scorekeeper&apos;s call, which varies a little by arena.
                </InfoTooltip>
            </h2>
            <div className="lb-controls an-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={season} onChange={(e) => setSeason(Number(e.target.value))}>
                        {[...seasons].reverse().map((x) => <option key={x} value={x}>{label(x)}</option>)}
                    </select>
                </label>
            </div>
            {error && <p className="error-message">{error}</p>}
            {!d && !error && <Loader />}
            {d && (
                <>
                    <div className="an-shares">
                        <div className="an-share">
                            <div className="an-share-k">Assists</div>
                            <div className="an-share-v">{s.ast.toLocaleString()}</div>
                            <div className="an-share-sub">{s.ast_pts.toLocaleString()} points on them · {d.n_targets} different scorers</div>
                        </div>
                        <div className="an-share" title={rank(s.share2_rank, s.share2_ranked, d.rank_floor.share2, '2s')}>
                            <div className="an-share-k">2s assisted</div>
                            <div className={`an-share-v${s.small2 ? ' sl-short' : ''}`}>{pct(s.share2)}</div>
                            <div className="an-share-sub">{s.ast_fgm2} of {s.fgm2} · league {pct(d.league.share2)}</div>
                        </div>
                        <div className="an-share" title={rank(s.share3_rank, s.share3_ranked, d.rank_floor.share3, '3s')}>
                            <div className="an-share-k">3s assisted</div>
                            <div className={`an-share-v${s.small3 ? ' sl-short' : ''}`}>{pct(s.share3)}</div>
                            <div className="an-share-sub">{s.ast_fgm3} of {s.fgm3} · league {pct(d.league.share3)}</div>
                        </div>
                        <div className="an-share">
                            <div className="an-share-k">Layups & dunks assisted</div>
                            <div className={`an-share-v${s.kinds.rim.fgm < d.min_fgm3 ? ' sl-short' : ''}`}>{pct(s.kinds.rim.share)}</div>
                            <div className="an-share-sub">{s.kinds.rim.assisted} of {s.kinds.rim.fgm} · league {pct(d.league.share_rim)}</div>
                        </div>
                    </div>
                    <p className="page-subtitle" style={{ marginTop: 0 }}>
                        {d.season_label} ({d.teams.join(', ')}), league rank by share assisted (#1 = most assisted):
                        {' '}2s {rank(s.share2_rank, s.share2_ranked, d.rank_floor.share2, '2s')}; 3s {rank(s.share3_rank, s.share3_ranked, d.rank_floor.share3, '3s')}.
                        {' '}Low = he creates his own shots; high = he finishes what others set up.
                    </p>
                    <div className="an-lists">
                        <Pairs rows={d.targets} total={s.ast} who="scorer" title="His passes go to" empty="No assists on file this season." />
                        <Pairs rows={d.feeders} total={assisted} who="passer" title="His assisted baskets come from" empty="No assisted baskets on file this season." />
                    </div>
                    <p className="page-subtitle pp-foot">
                        Top {d.targets.length} of {d.n_targets} scorers he assisted and {d.feeders.length} of {d.n_feeders} teammates who assisted him; percentages are of his assists and of his assisted baskets.{' '}
                        {d.teams.map((t) => (
                            <button key={t} type="button" className="pp-link" onClick={() => onNavigate('assists', null, { team: t, season: d.season, player: d.player_id })}>
                                Open {t}&apos;s assist network
                            </button>
                        ))}
                    </p>
                    <h4 className="rp-panel-title" style={{ marginTop: 'var(--space-4)' }}>Every season</h4>
                    <TableExport name={`assists ${d.player_name}`} />
                    <div className="table-wrapper">
                        <table className="data-table lb-table pp-table">
                            <thead>
                                <tr>
                                    <th>Season</th><th>Team</th><th className="lb-num">Min</th><th className="lb-num">Ast</th>
                                    <th className="lb-num" title="Points on the baskets he assisted">Pts created</th>
                                    <th>Top target</th>
                                    <th className="lb-num">2s ast&apos;d</th><th className="lb-num">3s ast&apos;d</th>
                                </tr>
                            </thead>
                            <tbody>
                                {d.history.map((h) => (
                                    <tr key={h.season}>
                                        <td>{h.season_label}</td>
                                        <td>{h.teams.join('/')}</td>
                                        <td className="lb-num">{Math.round(h.minutes).toLocaleString()}</td>
                                        <td className="lb-num">{h.ast}</td>
                                        <td className="lb-num">{h.ast_pts}</td>
                                        <td>{h.top_target ? `${h.top_target.player_name} (${h.top_target.ast})` : '—'}</td>
                                        <td className={`lb-num${h.small2 ? ' sl-short' : ''}`} title={`${h.ast_fgm2} of ${h.fgm2}`}>{pct(h.share2)}</td>
                                        <td className={`lb-num${h.small3 ? ' sl-short' : ''}`} title={`${h.ast_fgm3} of ${h.fgm3}`}>{pct(h.share3)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="page-subtitle pp-foot">Greyed: under {d.min_fgm2} made 2s or {d.min_fgm3} made 3s. Hover a share for the counts.</p>
                </>
            )}
        </section>
    );
}
