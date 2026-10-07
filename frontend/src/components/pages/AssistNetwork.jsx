import React, { useEffect, useState } from 'react';
import { fetchAssistOptions, fetchAssistPairs, fetchTeamAssists } from '../../services/api';
import Loader from '../Loader';
import AssistNetworkChart from '../common/AssistNetwork';
import CopyLinkButton from '../common/CopyLinkButton';
import InfoTooltip from '../common/InfoTooltip';
import PlayerName from '../common/PlayerName';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import { pickNetwork } from '../../utils/assistNetwork';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import '../../styles/rapm.css';
import '../../styles/assists.css';
import { LiveSeasonTag } from '../common/LiveSeasonNote';

// Assist Network (?page=assists&team=&season=&player=&n=&min=&v=): who
// assists whom on a team, from every assisted basket in the play-by-play
// since 2020-21 (GET /assists/*). A network of the top players by minutes,
// the full passer -> scorer table, every player's assisted shares, and the
// league's top duos (v=duos).

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const pct = (v, d = 0) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const PLAYER_COUNTS = [6, 8, 10, 12, 15];
const MIN_EDGES = [1, 5, 10, 20, 30, 50];
const PAIRS_SHOWN = 25;

function autoMinEdge(players, edges, n) {
    // Smallest floor that keeps the drawing readable: at most 36 lines.
    const { lines } = pickNetwork(players, edges, n, 1);
    return MIN_EDGES.find((m) => lines.filter((e) => e.ast >= m).length <= 36) ?? MIN_EDGES[MIN_EDGES.length - 1];
}

function PlayerPanel({ data, playerId, onClose }) {
    const p = data.players.find((x) => x.player_id === playerId);
    if (!p) return null;
    const targets = data.edges.filter((e) => e.passer_id === playerId);
    const feeders = data.edges.filter((e) => e.scorer_id === playerId);
    const lg = data.league;
    const maxT = Math.max(1, ...targets.map((e) => e.ast));
    const maxF = Math.max(1, ...feeders.map((e) => e.ast));
    const share = (made, assisted, league, small, what) => (
        <div className="an-share" title={small ? `Only ${made} made ${what}: a small sample` : undefined}>
            <div className="an-share-k">{what} assisted</div>
            <div className={`an-share-v${small ? ' sl-short' : ''}`}>{made ? pct(assisted / made) : '—'}</div>
            <div className="an-share-sub">{assisted} of {made} made · league {pct(league)}</div>
        </div>
    );
    return (
        <div className="an-player">
            <h3 className="an-player-title">
                <PlayerName playerId={p.player_id} name={p.player_name} size={28} />
                <span className="page-subtitle" style={{ margin: 0 }}>{p.games} games · {Math.round(p.minutes).toLocaleString()} min for {data.team}</span>
                <button type="button" className="pp-link rp-link" onClick={onClose}>Clear</button>
            </h3>
            <div className="an-shares">
                <div className="an-share">
                    <div className="an-share-k">Assists</div>
                    <div className="an-share-v">{p.ast.toLocaleString()}</div>
                    <div className="an-share-sub">{p.ast_pts.toLocaleString()} points on them · {p.ast3_given} threes</div>
                </div>
                {share(p.fgm2, p.ast_fgm2, lg.share2, p.small2, '2s')}
                {share(p.fgm3, p.ast_fgm3, lg.share3, p.small3, '3s')}
                {share(p.kinds.rim.fgm, p.kinds.rim.assisted, lg.share_rim, p.kinds.rim.fgm < data.min_fgm3, 'Layups & dunks')}
            </div>
            <div className="an-lists">
                <div>
                    <h4 className="rp-panel-title">His passes go to</h4>
                    {targets.length ? (
                        <ul className="an-list">
                            {targets.slice(0, 8).map((e) => (
                                <li key={e.scorer_id}>
                                    {e.scorer_id ? <PlayerName playerId={e.scorer_id} name={e.scorer_name} size={20} /> : <span>Unidentified</span>}
                                    <span className="an-list-n">{e.ast} · {pct(e.ast / p.ast)} · {e.kinds.three} 3s</span>
                                    <span className="an-list-bar" aria-hidden="true"><span style={{ width: `${(100 * e.ast) / maxT}%` }} /></span>
                                </li>
                            ))}
                        </ul>
                    ) : <p className="empty-message">No assists on file.</p>}
                </div>
                <div>
                    <h4 className="rp-panel-title">His assisted baskets come from</h4>
                    {feeders.length ? (
                        <ul className="an-list">
                            {feeders.slice(0, 8).map((e) => (
                                <li key={e.passer_id}>
                                    <PlayerName playerId={e.passer_id} name={e.passer_name} size={20} />
                                    <span className="an-list-n">{e.ast} · {pct(e.ast / (p.ast_fgm2 + p.ast_fgm3))} · {e.kinds.three} 3s</span>
                                    <span className="an-list-bar" aria-hidden="true"><span style={{ width: `${(100 * e.ast) / maxF}%` }} /></span>
                                </li>
                            ))}
                        </ul>
                    ) : <p className="empty-message">No assisted baskets on file.</p>}
                </div>
            </div>
            <p className="page-subtitle pp-foot" style={{ marginBottom: 0 }}>
                Share of passes = of his {p.ast} assists; share of baskets = of his {p.ast_fgm2 + p.ast_fgm3} assisted makes
                {p.ast_unknown_passer > 0 && ` (${p.ast_unknown_passer} of them from a passer ESPN gives no id to)`}.
            </p>
        </div>
    );
}

function TeamView({ data, form, set }) {
    const [allPairs, setAllPairs] = useState(false);
    const minEdge = form.min ?? autoMinEdge(data.players, data.edges, form.n);
    const net = pickNetwork(data.players, data.edges, form.n, minEdge);
    const t = data.totals;
    const top = data.edges.find((e) => e.scorer_id !== 0);
    const pairs = allPairs ? data.edges : data.edges.slice(0, PAIRS_SHOWN);
    const players = data.players.filter((p) => p.minutes >= 1);
    return (
        <div>
            <p className="rx-verdict">
                <strong>{data.team} {data.season_label}: {t.assisted.toLocaleString()} of {t.fgm.toLocaleString()} made shots assisted ({pct(t.share, 1)}),{' '}
                    {t.rank ? `#${t.rank} of ${t.n_teams}` : ''}</strong> (league {pct(data.league.assisted_share, 1)}).
                {top && <> Top pair: {top.passer_name} → {top.scorer_name}, {top.ast} assists in {top.games} games.</>}
            </p>
            <div className="lb-controls an-controls">
                <label>
                    <span>Players shown</span>
                    <select className="input-field" value={form.n} onChange={(e) => set({ n: Number(e.target.value) })}>
                        {PLAYER_COUNTS.map((n) => <option key={n} value={n}>Top {n} by minutes</option>)}
                    </select>
                </label>
                <label>
                    <span>Lines with at least</span>
                    <select className="input-field" value={form.min ?? ''} onChange={(e) => set({ min: e.target.value ? Number(e.target.value) : null })}>
                        <option value="">Auto ({minEdge} assists)</option>
                        {MIN_EDGES.map((m) => <option key={m} value={m}>{m} assist{m === 1 ? '' : 's'}</option>)}
                    </select>
                </label>
            </div>
            <AssistNetworkChart players={data.players} edges={data.edges} nPlayers={form.n} minEdge={minEdge}
                selected={form.player} onSelect={(id) => set({ player: id })}
                name={`assist network ${data.team} ${data.season_label}`} />
            <p className="page-subtitle lb-summary">
                {net.nodes.length} players by minutes; lines for pairs with {minEdge}+ assists ({net.lines.length} of them). The drawing covers
                {' '}{net.insideAst.toLocaleString()} of the team&apos;s {net.totalAst.toLocaleString()} assists between named players ({pct(net.insideAst / (net.totalAst || 1))});
                the table below has every pair. Click a player for his passes.
                {(t.unknown_passer > 0 || t.unknown_scorer > 0) && ` Not in any pair: ${t.unknown_passer} assisted baskets whose passer ESPN gives no id to${t.unknown_scorer ? `, and ${t.unknown_scorer} assists to a scorer with no id ("Unidentified" in the table)` : ''}.`}
            </p>

            {form.player && <PlayerPanel data={data} playerId={form.player} onClose={() => set({ player: null })} />}

            <h3 className="rp-panel-title" style={{ marginTop: 'var(--space-5)' }}>Every passer → scorer pair</h3>
            <TableExport name={`assist pairs ${data.team} ${data.season_label}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table an-table">
                    <thead>
                        <tr>
                            <th>Passer</th><th aria-label="to" /><th>Scorer</th>
                            <th className="lb-num">Ast</th>
                            <th className="lb-num" title="Games with at least one assist in this pair">Games</th>
                            <th className="lb-num" title="Points on the assisted baskets">Pts</th>
                            <th className="lb-num" title="Layups, dunks, alley-oops, finger rolls, tips">Layups/dunks</th>
                            <th className="lb-num" title="Floaters and hooks">Floaters/hooks</th>
                            <th className="lb-num" title="Every other two">2-pt jumpers</th>
                            <th className="lb-num">3s</th>
                        </tr>
                    </thead>
                    <tbody>
                        {pairs.map((e) => (
                            <tr key={`${e.passer_id}-${e.scorer_id}`}>
                                <td><PlayerName playerId={e.passer_id} name={e.passer_name} size={20} /></td>
                                <td className="an-arrowcell" aria-hidden="true">→</td>
                                <td>{e.scorer_id ? <PlayerName playerId={e.scorer_id} name={e.scorer_name} size={20} /> : 'Unidentified'}</td>
                                <td className="lb-num lb-stat">{e.ast}</td>
                                <td className="lb-num">{e.games}</td>
                                <td className="lb-num">{e.pts}</td>
                                <td className="lb-num">{e.kinds.rim}</td>
                                <td className="lb-num">{e.kinds.floater}</td>
                                <td className="lb-num">{e.kinds.jumper}</td>
                                <td className="lb-num">{e.kinds.three}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            {data.edges.length > PAIRS_SHOWN && (
                <button type="button" className="pp-link rp-link an-more" onClick={() => setAllPairs((v) => !v)}>
                    {allPairs ? `Show the top ${PAIRS_SHOWN}` : `Show all ${data.edges.length} pairs`}
                </button>
            )}

            <h3 className="rp-panel-title" style={{ marginTop: 'var(--space-5)' }}>Who is set up, who sets up</h3>
            <p className="page-subtitle" style={{ marginTop: 0 }}>
                Share of each player&apos;s made shots that were assisted. League this season: {pct(data.league.share2)} of made 2s, {pct(data.league.share3)} of made 3s,
                {' '}{pct(data.league.share_rim)} of layups and dunks. Greyed: under {data.min_fgm2} made 2s or {data.min_fgm3} made 3s.
            </p>
            <TableExport name={`assisted shares ${data.team} ${data.season_label}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            <th>Player</th>
                            <th className="lb-num">GP</th>
                            <th className="lb-num">Min</th>
                            <th className="lb-num">Ast</th>
                            <th className="lb-num" title="Points on the baskets he assisted">Pts created</th>
                            <th className="lb-num">Made 2s</th>
                            <th className="lb-num" title="Share of his made 2s that were assisted">% ast&apos;d</th>
                            <th className="lb-num">Made 3s</th>
                            <th className="lb-num" title="Share of his made 3s that were assisted">% ast&apos;d</th>
                            <th>Feeds most</th>
                            <th>Fed most by</th>
                        </tr>
                    </thead>
                    <tbody>
                        {players.map((p) => (
                            <tr key={p.player_id}>
                                <td><PlayerName playerId={p.player_id} name={p.player_name} size={22} /></td>
                                <td className="lb-num">{p.games}</td>
                                <td className="lb-num">{Math.round(p.minutes).toLocaleString()}</td>
                                <td className="lb-num lb-stat">{p.ast}</td>
                                <td className="lb-num">{p.ast_pts}</td>
                                <td className="lb-num">{p.fgm2}</td>
                                <td className={`lb-num${p.small2 ? ' sl-short' : ''}`}>{pct(p.share2)}</td>
                                <td className="lb-num">{p.fgm3}</td>
                                <td className={`lb-num${p.small3 ? ' sl-short' : ''}`}>{pct(p.share3)}</td>
                                <td>{p.top_target ? `${p.top_target.player_name} (${p.top_target.ast})` : '—'}</td>
                                <td>{p.top_feeder ? `${p.top_feeder.player_name} (${p.top_feeder.ast})` : '—'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function DuosView({ season, sort, setSort, onOpen }) {
    const key = `${season}-${sort}`;
    const [res, setRes] = useState(null);
    useEffect(() => {
        let active = true;
        fetchAssistPairs(season, sort, 50)
            .then((d) => { if (active) setRes({ key, data: d }); })
            .catch((e) => { if (active) setRes({ key, error: e.response?.data?.detail || 'The duos couldn\'t load.' }); });
        return () => { active = false; };
    }, [season, sort, key]);
    const d = res?.key === key ? res.data : null;
    if (res?.key === key && res.error) return <p className="error-message">{res.error}</p>;
    if (!d) return <Loader />;
    return (
        <div>
            <div className="lb-controls an-controls">
                <label>
                    <span>Rank by</span>
                    <select className="input-field" value={sort} onChange={(e) => setSort(e.target.value)}>
                        {Object.entries(d.sorts).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                </label>
            </div>
            <p className="page-subtitle lb-summary">
                The top 50 of {d.n_pairs.toLocaleString()} passer → scorer pairs in {d.season_label}, each within one team (a traded player&apos;s
                pairs are counted per team). Click a team for its network.
            </p>
            <TableExport name={`top assist duos ${d.season_label}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table an-table">
                    <thead>
                        <tr>
                            <th className="lb-num">#</th><th>Team</th><th>Passer</th><th aria-label="to" /><th>Scorer</th>
                            <th className="lb-num">Ast</th><th className="lb-num">Games</th><th className="lb-num">Pts</th>
                            <th className="lb-num">Layups/dunks</th><th className="lb-num">3s</th>
                        </tr>
                    </thead>
                    <tbody>
                        {d.pairs.map((e, i) => (
                            <tr key={`${e.team_abbreviation}-${e.passer_id}-${e.scorer_id}`}>
                                <td className="lb-num">{i + 1}</td>
                                <td>
                                    <button type="button" className="pp-link rp-link" onClick={() => onOpen(e.team_abbreviation, e.passer_id)}>
                                        {e.team_abbreviation}
                                    </button>
                                </td>
                                <td><PlayerName playerId={e.passer_id} name={e.passer_name} size={20} /></td>
                                <td className="an-arrowcell" aria-hidden="true">→</td>
                                <td><PlayerName playerId={e.scorer_id} name={e.scorer_name} size={20} /></td>
                                <td className={`lb-num${sort === 'ast' ? ' lb-stat' : ''}`}>{e.ast}</td>
                                <td className="lb-num">{e.games}</td>
                                <td className={`lb-num${sort === 'pts' ? ' lb-stat' : ''}`}>{e.pts}</td>
                                <td className={`lb-num${sort === 'rim' ? ' lb-stat' : ''}`}>{e.kinds.rim}</td>
                                <td className={`lb-num${sort === 'three' ? ' lb-stat' : ''}`}>{e.kinds.three}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

export default function AssistNetwork() {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [teamRes, setTeamRes] = useState(null); // { key, data } | { key, error }

    useEffect(() => {
        fetchAssistOptions()
            .then((o) => {
                setOptions(o);
                const season = parseParam.int(params, 'season', { min: 2000, max: 2100 });
                const s = o.seasons.includes(season) ? season : o.default_season;
                const team = parseParam.str(params, 'team')?.toUpperCase();
                const teams = o.teams[String(s)] ?? [];
                const n = parseParam.int(params, 'n', { min: 2, max: 30 });
                const min = parseParam.int(params, 'min', { min: 1, max: 500 });
                setForm({
                    season: s,
                    team: teams.includes(team) ? team : (teams.includes('DEN') ? 'DEN' : teams[0]),
                    player: parseParam.int(params, 'player', { min: 1 }) ?? null,
                    n: PLAYER_COUNTS.includes(n) ? n : 10,
                    min: MIN_EDGES.includes(min) ? min : null,
                    view: parseParam.oneOf(params, 'v', ['team', 'duos']) ?? 'team',
                    sort: parseParam.oneOf(params, 'sort', Object.keys(o.sorts)) ?? 'ast',
                });
            })
            .catch(() => setOptionsError('The assist network couldn\'t load. Is the impact API (port 8002) running, and has scripts/build_assist_network.py been run?'));
    }, [params]);

    useUrlSync(form && {
        team: form.view === 'team' ? form.team : null, season: form.season,
        player: form.view === 'team' ? form.player : null, n: form.view === 'team' && form.n !== 10 ? form.n : null,
        min: form.view === 'team' ? form.min : null, v: form.view === 'team' ? null : form.view,
        sort: form.view === 'duos' && form.sort !== 'ast' ? form.sort : null,
    });

    const teamKey = form ? `${form.team}-${form.season}` : null;
    useEffect(() => {
        if (!form || form.view !== 'team') return undefined;
        let active = true;
        fetchTeamAssists(form.team, form.season)
            .then((d) => { if (active) setTeamRes({ key: teamKey, data: d }); })
            .catch((e) => { if (active) setTeamRes({ key: teamKey, error: e.response?.data?.detail || 'The network couldn\'t load.' }); });
        return () => { active = false; };
    }, [form?.team, form?.season, form?.view, teamKey]); // eslint-disable-line react-hooks/exhaustive-deps

    const team = teamRes?.key === teamKey ? teamRes.data : null;
    // A player id from the URL that isn't on this team is dropped once the team loads.
    const selected = team && form?.player && team.players.some((p) => p.player_id === form.player) ? form.player : null;

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const teams = options.teams[String(form.season)] ?? [];
    const teamError = teamRes?.key === teamKey ? teamRes.error : '';
    const seasons = [...options.seasons].reverse();

    return (
        <section className="dashboard-card lb-card oo-card">
            <h2 className="card-title hb-page-title">
                Assist network: who feeds whom
                <InfoTooltip label="How the assist network is built" title="Under the hood">{options.method}</InfoTooltip>
                <SourceBadge source={team?._source ?? options._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="assists" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Every assisted basket since 2020-21, from the play-by-play: which passer set up which scorer, on what kind of shot,
                and how much of each player&apos;s scoring is set up by someone else.
            </p>

            <div className="tab-bar lb-modes" role="tablist" aria-label="View">
                {[['team', 'Team network'], ['duos', "League's top duos"]].map(([k, t]) => (
                    <button key={k} type="button" role="tab" aria-selected={form.view === k}
                        className={`tab-btn ${form.view === k ? 'tab-btn--active' : ''}`} onClick={() => set({ view: k })}>{t}</button>
                ))}
            </div>

            <div className="lb-controls an-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={form.season} onChange={(e) => {
                        const s = Number(e.target.value);
                        const ts = options.teams[String(s)] ?? [];
                        set({ season: s, team: ts.includes(form.team) ? form.team : ts[0], player: null });
                    }}>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                    <LiveSeasonTag season={form.season} />
                </label>
                {form.view === 'team' && (
                    <label>
                        <span>Team</span>
                        <select className="input-field" value={form.team} onChange={(e) => set({ team: e.target.value, player: null })}>
                            {teams.map((t) => <option key={t} value={t}>{t}</option>)}
                        </select>
                    </label>
                )}
                {form.view === 'team' && (
                    <div className="an-teamlink"><TeamLink abbr={form.team} season={form.season} /></div>
                )}
            </div>

            {form.view === 'team' ? (
                <div className={team ? 'lb-results' : 'lb-results lb-results--stale'} aria-busy={!team}>
                    {teamError && <p className="error-message">{teamError}</p>}
                    {!team && !teamError && <Loader />}
                    {team && <TeamView data={team} form={{ ...form, player: selected }} set={set} />}
                </div>
            ) : (
                <DuosView season={form.season} sort={form.sort} setSort={(s) => set({ sort: s })}
                    onOpen={(t, pid) => set({ view: 'team', team: t, player: pid })} />
            )}
        </section>
    );
}
