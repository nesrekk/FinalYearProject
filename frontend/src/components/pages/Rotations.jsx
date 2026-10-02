import React, { useEffect, useMemo, useState } from 'react';
import { fetchRotationGame, fetchRotationGames, fetchRotationOptions, fetchTeamRotation } from '../../services/api';
import Loader from '../Loader';
import CopyLinkButton from '../common/CopyLinkButton';
import GameRotationChart from '../common/GameRotationChart';
import InfoTooltip from '../common/InfoTooltip';
import LineupPredictorPanel from '../common/LineupPredictorPanel';
import PlayerName from '../common/PlayerName';
import RotationHeatmap from '../common/RotationHeatmap';
import SaveViewButton from '../common/SaveViewButton';
import SourceBadge from '../common/SourceBadge';
import TableExport from '../common/TableExport';
import TeamLink from '../common/TeamLink';
import { parseParam, useInitialParams, useUrlSync } from '../../utils/useUrlState';
import { bySign, signed } from '../../utils/format';
import '../../styles/rapm.css';
import '../../styles/rotations.css';

// Rotations (?page=rotations&team=&season=&game=): a team-season's rotation
// as a heatmap of every regulation minute, its starting and closing fives,
// and, with a game picked, that game's full rotation chart for both teams
// with the score margin underneath. Everything from the play-by-play stints
// (GET /rotations/*); games whose play-by-play didn't reconcile are listed.
// The season view ends with the Lineup Predictor's "try a lineup" panel
// (common/LineupPredictorPanel.jsx; its five in lu=<ids>).

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const pct = (v, d = 0) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
// Colour follows the value as shown at d decimals, so a cell reading 0.0 isn't tinted.
const tone = (v, d = 1) => bySign(v, d, 'oo-pos', 'oo-neg');
const gameLabel = (g) => `${g.date} · ${g.home ? 'vs' : '@'} ${g.opponent} · ${g.result ?? ''} ${g.pts_for ?? '?'}-${g.pts_against ?? '?'}${g.overtimes ? ` (${g.overtimes > 1 ? `${g.overtimes}OT` : 'OT'})` : ''}${!g.game_ok ? ' · not reconciled' : !g.side_complete ? ' · unidentified player' : ''}`;

function Five({ players }) {
    return (
        <div className="rot-five-list">
            {players.map((p) => <PlayerName key={p.player_id} playerId={p.player_id} name={p.player_name ?? `#${p.player_id}`} size={20} />)}
        </div>
    );
}

function GameView({ game, team, onNavigate, games, onPick }) {
    const idx = games.findIndex((g) => g.game_id === game.game_id);
    const prev = idx > 0 ? games[idx - 1] : null;
    const next = idx >= 0 && idx < games.length - 1 ? games[idx + 1] : null;
    const mine = game.home_team === team ? 'home' : 'away';
    const opp = mine === 'home' ? game.away_team : game.home_team;
    const my = game.final[mine];
    const their = game.final[mine === 'home' ? 'away' : 'home'];
    const rows = ['home', 'away'].flatMap((side) => game[side].players.map((p) => ({ ...p, team: game[side].team })));
    return (
        <div>
            <div className="rot-game-head">
                <span className="rot-game-score">
                    <TeamLink abbr={team} season={game.season} /> {my}-{their} {game.home_team === team ? 'vs' : '@'} <TeamLink abbr={opp} season={game.season} />
                </span>
                <span className="page-subtitle" style={{ margin: 0 }}>{game.date}{game.periods > 4 ? ` · ${game.periods - 4 > 1 ? `${game.periods - 4} overtimes` : 'overtime'}` : ''}</span>
                <div className="rot-nav">
                    <button type="button" className="pp-link rp-link" disabled={!prev} onClick={() => prev && onPick(prev.game_id)}>← Previous game</button>
                    <button type="button" className="pp-link rp-link" disabled={!next} onClick={() => next && onPick(next.game_id)}>Next game →</button>
                    <button type="button" className="pp-link rp-link" onClick={() => onNavigate('analytics', 'replay', { game: game.game_id })}>
                        Open in Game Replay →
                    </button>
                </div>
            </div>
            {!game.game_ok && (
                <p className="rot-flag">
                    This game&apos;s play-by-play didn&apos;t reconcile with the real result ({game.reason}), so it is left out of the season
                    heatmap and the closing lineups. The bars still show what the play-by-play says; treat them with care.
                </p>
            )}
            {game.game_ok && !game.tracked_ok && (
                <p className="rot-flag">
                    For part of this game a side had fewer than five identified players ({game.reason}): the dashed
                    &quot;Unidentified&quot; row, almost always a player ESPN gives no id to. Everyone else&apos;s minutes are complete.
                </p>
            )}
            <GameRotationChart game={game} team={team} />
            <p className="page-subtitle lb-summary">
                Bold names started. Bars are stretches on the floor; the numbers on the right are minutes (the same as each player&apos;s Game Log).
                The margin below is {team}&apos;s, taken at every substitution and period break and drawn flat in between. Hover or use the
                arrow keys to see who was on the floor.
            </p>
            <TableExport name={`rotation ${game.away_team} at ${game.home_team} ${game.date}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            <th>Player</th><th>Team</th><th title="In the game's first stint">Started</th>
                            <th className="lb-num">Min</th>
                            <th className="lb-num" title="Points scored minus allowed while on the floor, from the stints (reconciled to the real final)">+/-</th>
                            <th className="lb-num" title="Separate stretches on the floor">Stints</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((p) => (
                            <tr key={p.player_id}>
                                <td><PlayerName playerId={p.player_id} name={p.player_name ?? `#${p.player_id}`} size={22} /></td>
                                <td><TeamLink abbr={p.team} season={game.season} /></td>
                                <td>{p.starter ? 'Yes' : ''}</td>
                                <td className="lb-num">{num(p.minutes)}</td>
                                <td className={`lb-num ${tone(p.plus_minus, 0)}`}>{p.plus_minus == null ? '—' : signed(p.plus_minus, 0)}</td>
                                <td className="lb-num">{p.stretches.length}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

function SeasonView({ data, measure, setMeasure, onPickGame }) {
    const c = data.closing;
    const closeFive = c.most_minutes;
    return (
        <div>
            <p className="rx-verdict">
                <strong>{data.team} {data.season_label}: {data.games_counted} of {data.games} games counted.</strong>{' '}
                {data.excluded.length > 0 && `${data.excluded.length} left out because their play-by-play didn't reconcile with the real result (listed below). `}
                {data.partial_games > 0 && `In ${data.partial_games} counted game${data.partial_games === 1 ? '' : 's'} this team had a player ESPN gives no id to for part of the game: ${num(data.unidentified_minutes, 0)} player-minutes, shown as "Unidentified". `}
                {c.available && (
                    <>
                        Close games (within {5} at 5:00 left in the fourth): {c.close_games}, record {c.record.wins}-{c.record.losses}.
                    </>
                )}
            </p>

            <div className="tab-bar lb-modes rot-toggle" role="tablist" aria-label="Heatmap shade">
                {[['team', "Share of the team's games"], ['own', 'Share of his own games']].map(([k, t]) => (
                    <button key={k} type="button" role="tab" aria-selected={measure === k}
                        className={`tab-btn ${measure === k ? 'tab-btn--active' : ''}`} onClick={() => setMeasure(k)}>{t}</button>
                ))}
            </div>
            <RotationHeatmap data={data} measure={measure} />
            <p className="page-subtitle lb-summary">
                Each cell: how often he was on the floor in that minute of regulation, in seconds, over {measure === 'own' ? 'the games he played' : `all ${data.games_counted} counted games`}.
                Starters light up the first minutes of the first and third quarters; closers the last minutes of the fourth. Overtime isn&apos;t shown.
            </p>

            <div className="rot-fives">
                {data.starting_five && (
                    <div className="rot-five-card">
                        <h3 className="rp-panel-title">Most common starting five</h3>
                        <Five players={data.starting_five.players} />
                        <p className="rot-five-meta">Started {data.starting_five.games} of {data.games_counted} counted games together.</p>
                    </div>
                )}
                {closeFive && (
                    <div className="rot-five-card">
                        <h3 className="rp-panel-title">Most-used closing five</h3>
                        <Five players={closeFive.players} />
                        <p className="rot-five-meta">
                            {num(closeFive.minutes)} of {num(c.stretch_minutes)} closing minutes in {closeFive.games} of {c.close_games} close games,
                            {' '}{signed(closeFive.plus_minus, 0)} ({closeFive.pts_for}-{closeFive.pts_against}); finished {closeFive.finished} games,
                            {' '}{closeFive.finished_wins}-{closeFive.finished_losses}.
                            {closeFive.starting_five ? ' Also a starting five this season.' : ' Never started a game together.'}
                        </p>
                    </div>
                )}
            </div>

            {c.available && (
                <>
                    <h3 className="rp-panel-title">Closing lineups in close games</h3>
                    <p className="page-subtitle" style={{ marginTop: 0 }}>
                        The last five minutes of the fourth and all of overtime in the {c.close_games} games within five points at 5:00 left. Fives with
                        {' '}{c.min_minutes}+ minutes, most games first ({c.lineups_total} fives closed at some point). A handful of minutes is mostly noise:
                        read games and minutes first, the rating last.
                        {c.unidentified_minutes > 0 && ` ${num(c.unidentified_minutes)} closing minutes had an unidentified player and aren't credited to a five.`}
                    </p>
                    {c.lineups.length > 0 ? (
                        <>
                            <TableExport name={`closing lineups ${data.team} ${data.season_label}`} />
                            <div className="table-wrapper">
                                <table className="data-table lb-table">
                                    <thead>
                                        <tr>
                                            <th>Five</th>
                                            <th className="lb-num" title="Close games in which this five was on the floor in the closing stretch">Games</th>
                                            <th className="lb-num">Min</th>
                                            <th className="lb-num" title="Points for and against while on the floor">Score</th>
                                            <th className="lb-num">+/-</th>
                                            <th className="lb-num" title="Points per 100 possessions (both sides averaged); small samples">Net / 100</th>
                                            <th className="lb-num" title="On the floor at the final horn, and those games' record">Finished (W-L)</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {c.lineups.map((l) => (
                                            <tr key={l.players.map((p) => p.player_id).join('-')} className={l.minutes < 15 ? 'sl-short' : undefined}
                                                title={l.minutes < 15 ? 'Under 15 minutes: the rating is noise' : undefined}>
                                                <td className="rot-five-cell">{l.players.map((p) => <PlayerName key={p.player_id} playerId={p.player_id} name={p.player_name ?? `#${p.player_id}`} size={20} />)}</td>
                                                <td className="lb-num">{l.games}</td>
                                                <td className="lb-num">{num(l.minutes)}</td>
                                                <td className="lb-num">{l.pts_for}-{l.pts_against}</td>
                                                <td className={`lb-num ${tone(l.plus_minus, 0)}`}>{signed(l.plus_minus, 0)}</td>
                                                <td className={`lb-num ${tone(l.net_rating)}`}>{signed(l.net_rating)}</td>
                                                <td className="lb-num">{l.finished} ({l.finished_wins}-{l.finished_losses})</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    ) : <p className="empty-message">No five reached {c.min_minutes} closing minutes in close games.</p>}
                </>
            )}

            <h3 className="rp-panel-title" style={{ marginTop: 'var(--space-5)' }}>Starters and closers</h3>
            <TableExport name={`rotation players ${data.team} ${data.season_label}`} />
            <div className="table-wrapper">
                <table className="data-table lb-table">
                    <thead>
                        <tr>
                            <th>Player</th>
                            <th className="lb-num" title="Counted games he played for this team">GP</th>
                            <th className="lb-num" title="Games started">GS</th>
                            <th className="lb-num">Min</th>
                            <th className="lb-num">MPG</th>
                            <th className="lb-num" title="Close games he played in the closing stretch">Close games</th>
                            <th className="lb-num" title="Minutes in the closing stretch of close games">Closing min</th>
                            <th className="lb-num" title="His share of the team's closing minutes in close games (a full-time closer = 100%)">Closing share</th>
                        </tr>
                    </thead>
                    <tbody>
                        {data.players.filter((p) => p.minutes >= 1).map((p) => (
                            <tr key={p.player_id} className={p.games < 10 ? 'sl-short' : undefined}>
                                <td><PlayerName playerId={p.player_id} name={p.player_name ?? `#${p.player_id}`} size={22} /></td>
                                <td className="lb-num">{p.games}</td>
                                <td className="lb-num">{p.starts}</td>
                                <td className="lb-num">{num(p.minutes, 0)}</td>
                                <td className="lb-num">{num(p.mpg)}</td>
                                <td className="lb-num">{p.closing_games}</td>
                                <td className="lb-num">{num(p.closing_minutes)}</td>
                                <td className="lb-num">{pct(p.closing_share)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>

            {data.excluded.length > 0 && (
                <details className="rot-excluded">
                    <summary>{data.excluded.length} game{data.excluded.length === 1 ? '' : 's'} left out (play-by-play didn&apos;t reconcile)</summary>
                    <ul>
                        {data.excluded.map((g) => (
                            <li key={g.game_id}>
                                <button type="button" className="pp-link rp-link" onClick={() => onPickGame(g.game_id)}>{g.date} {g.home ? 'vs' : '@'} {g.opponent}</button>: {g.reason}
                            </li>
                        ))}
                    </ul>
                </details>
            )}
        </div>
    );
}

export default function Rotations({ onNavigate }) {
    const params = useInitialParams();
    const [options, setOptions] = useState(null);
    const [optionsError, setOptionsError] = useState('');
    const [form, setForm] = useState(null);
    const [teamRes, setTeamRes] = useState(null);   // { key, data } | { key, error }
    const [gamesRes, setGamesRes] = useState(null); // { key, data } | { key, error }
    const [gameRes, setGameRes] = useState(null);   // { key, data } | { key, error }

    useEffect(() => {
        fetchRotationOptions()
            .then((o) => {
                setOptions(o);
                const season = parseParam.int(params, 'season', { min: 2000, max: 2100 });
                const s = o.seasons.includes(season) ? season : o.default_season;
                const team = parseParam.str(params, 'team')?.toUpperCase();
                const teams = o.teams[String(s)] ?? [];
                setForm({
                    season: s,
                    team: teams.includes(team) ? team : (teams.includes('BOS') ? 'BOS' : teams[0]),
                    game: parseParam.str(params, 'game'),
                    measure: parseParam.oneOf(params, 'm', ['team', 'own']) ?? 'team',
                    lu: (parseParam.str(params, 'lu') ?? '').split(',').map(Number).filter((n) => Number.isInteger(n) && n > 0),
                });
            })
            .catch(() => setOptionsError('Rotations couldn\'t load. Is the impact API (port 8002) running, and have scripts/build_lineup_stints.py and scripts/build_rotations.py been run?'));
    }, [params]);

    useUrlSync(form && { team: form.team, season: form.season, game: form.game, m: form.measure === 'team' ? null : form.measure,
        lu: !form.game && form.lu?.length === 5 ? form.lu.join(',') : null });

    const teamKey = form ? `${form.team}-${form.season}` : null;
    useEffect(() => {
        if (!form) return undefined;
        let active = true;
        fetchTeamRotation(form.team, form.season)
            .then((d) => { if (active) setTeamRes({ key: teamKey, data: d }); })
            .catch((e) => { if (active) setTeamRes({ key: teamKey, error: e.response?.data?.detail || 'The rotation couldn\'t load.' }); });
        fetchRotationGames(form.team, form.season)
            .then((d) => { if (active) setGamesRes({ key: teamKey, data: d }); })
            .catch((e) => { if (active) setGamesRes({ key: teamKey, error: e.response?.data?.detail || 'The game list couldn\'t load.' }); });
        return () => { active = false; };
    }, [form?.team, form?.season, teamKey]); // eslint-disable-line react-hooks/exhaustive-deps

    const gameId = form?.game ?? null;
    useEffect(() => {
        if (!gameId) return undefined;
        let active = true;
        fetchRotationGame(gameId)
            .then((d) => { if (active) setGameRes({ key: gameId, data: d }); })
            .catch((e) => { if (active) setGameRes({ key: gameId, error: e.response?.data?.detail || 'That game couldn\'t load.' }); });
        return () => { active = false; };
    }, [gameId]);

    const games = useMemo(() => (gamesRes?.key === teamKey ? gamesRes.data.games : []), [gamesRes, teamKey]);

    if (optionsError) return <section className="dashboard-card"><p className="error-message">{optionsError}</p></section>;
    if (!options || !form) return <Loader />;

    const set = (patch) => setForm((f) => ({ ...f, ...patch }));
    const teams = options.teams[String(form.season)] ?? [];
    const team = teamRes?.key === teamKey ? teamRes.data : null;
    const teamError = teamRes?.key === teamKey ? teamRes.error : '';
    const game = gameId && gameRes?.key === gameId ? gameRes.data : null;
    const gameError = gameId && gameRes?.key === gameId ? gameRes.error : '';
    const gameMismatch = game && game.home_team !== form.team && game.away_team !== form.team;
    const seasons = [...options.seasons].reverse();

    return (
        <section className="dashboard-card lb-card oo-card">
            <h2 className="card-title hb-page-title">
                Rotations: who played when
                <InfoTooltip label="How rotations are built" title="Under the hood">{team?.method ?? game?.method}</InfoTooltip>
                <SourceBadge source={team?._source} />
                <CopyLinkButton />
                <SaveViewButton pageId="rotations" />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Every stint of every game since 2020-21, rebuilt from the play-by-play: a team&apos;s whole season as a heatmap of who is on
                the floor at each minute, its starting and closing fives, and any single game as a rotation chart with the score underneath.
            </p>

            <div className="lb-controls rot-controls">
                <label>
                    <span>Season</span>
                    <select className="input-field" value={form.season} onChange={(e) => {
                        const s = Number(e.target.value);
                        const ts = options.teams[String(s)] ?? [];
                        set({ season: s, team: ts.includes(form.team) ? form.team : ts[0], game: null, lu: [] });
                    }}>
                        {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <label>
                    <span>Team</span>
                    <select className="input-field" value={form.team} onChange={(e) => set({ team: e.target.value, game: null, lu: [] })}>
                        {teams.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                </label>
                <label style={{ flex: '2 1 280px' }}>
                    <span>Game</span>
                    <select className="input-field" value={form.game ?? ''} onChange={(e) => set({ game: e.target.value || null })}>
                        <option value="">Whole season (heatmap)</option>
                        {gameId && !games.some((g) => g.game_id === gameId) && <option value={gameId}>{game ? `${game.date} ${game.away_team} @ ${game.home_team}` : gameId}</option>}
                        {games.map((g) => <option key={g.game_id} value={g.game_id}>{gameLabel(g)}</option>)}
                    </select>
                </label>
            </div>

            {gameId && (
                <div className="lb-results" style={{ marginBottom: 'var(--space-5)' }}>
                    {gameError && <p className="error-message">{gameError}</p>}
                    {!game && !gameError && <Loader />}
                    {game && gameMismatch && <p className="error-message">That game isn&apos;t one of {form.team}&apos;s; showing it from {game.home_team}&apos;s side.</p>}
                    {game && (
                        <>
                            <GameView game={game} team={gameMismatch ? game.home_team : form.team} onNavigate={onNavigate} games={games}
                                onPick={(id) => set({ game: id })} />
                            <button type="button" className="pp-link rp-link" style={{ marginTop: 'var(--space-3)' }} onClick={() => set({ game: null })}>
                                ← Back to the {form.team} season view
                            </button>
                        </>
                    )}
                </div>
            )}

            {!gameId && (
                <div className={team ? 'lb-results' : 'lb-results lb-results--stale'} aria-busy={!team}>
                    {teamError && <p className="error-message">{teamError}</p>}
                    {!team && !teamError && <Loader />}
                    {team && <SeasonView data={team} measure={form.measure} setMeasure={(m) => set({ measure: m })}
                        onPickGame={(id) => set({ game: id })} />}
                    {team && games.length > 0 && (
                        <p className="page-subtitle lb-summary">
                            Pick a game above for its full rotation chart, or open the latest:{' '}
                            <button type="button" className="pp-link rp-link" onClick={() => set({ game: games[games.length - 1].game_id })}>
                                {gameLabel(games[games.length - 1])}
                            </button>
                        </p>
                    )}
                    <LineupPredictorPanel key={teamKey} team={form.team} season={form.season} initialIds={form.lu}
                        onIdsChange={(ids) => set({ lu: ids })} />
                </div>
            )}
        </section>
    );
}
