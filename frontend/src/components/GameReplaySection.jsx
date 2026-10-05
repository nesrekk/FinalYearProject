import React, { useEffect, useMemo, useRef, useState } from 'react';
import { fetchWpReplayList, fetchWpReplay, fetchWpReplayWhatIf } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import TeamLogo from './common/TeamLogo';
import TableExport from './common/TableExport';
import ChartExport from './common/ChartExport';
import CopyLinkButton from './common/CopyLinkButton';
import SaveViewButton from './common/SaveViewButton';
import { currentPageParam, parseParam, useInitialParams, useUrlSync } from '../utils/useUrlState';
import { bySign, signed } from '../utils/format';

const CHART_W = 760;
const CHART_H = 300;
const PAD_L = 40;
const PAD_R = 16;
const PAD_T = 16;
const PAD_B = 34;
const PLOT_W = CHART_W - PAD_L - PAD_R;
const PLOT_H = CHART_H - PAD_T - PAD_B;

const PERIOD_SECONDS = 720;
const OT_SECONDS = 300;

function formatClock(secondsElapsed) {
    if (secondsElapsed < 4 * PERIOD_SECONDS) {
        const period = Math.floor(secondsElapsed / PERIOD_SECONDS) + 1;
        const intoPeriod = secondsElapsed - (period - 1) * PERIOD_SECONDS;
        const remaining = PERIOD_SECONDS - intoPeriod;
        const m = Math.floor(remaining / 60);
        const s = Math.floor(remaining % 60);
        return `Q${period} ${m}:${String(s).padStart(2, '0')}`;
    }
    const otElapsed = secondsElapsed - 4 * PERIOD_SECONDS;
    const otPeriod = Math.floor(otElapsed / OT_SECONDS) + 1;
    const intoOt = otElapsed - (otPeriod - 1) * OT_SECONDS;
    const remaining = OT_SECONDS - intoOt;
    const m = Math.floor(remaining / 60);
    const s = Math.floor(remaining % 60);
    return `OT${otPeriod > 1 ? otPeriod : ''} ${m}:${String(s).padStart(2, '0')}`;
}

function wpColor(v) {
    if (v == null) return 'var(--text-3)';
    if (v >= 0.5) return 'var(--positive)';
    return 'var(--negative)';
}

export default function GameReplaySection() {
    const svgRef = useRef(null);
    const [games, setGames] = useState(null);
    const [gamesError, setGamesError] = useState('');
    const [selectedGameId, setSelectedGameId] = useState('');
    // A link to one game (?page=analytics&game=<id>#replay, e.g. from the
    // Rotations page) opens it; `t` (seconds since tip-off) and `ev` (the
    // play's event id), e.g. from the Play Finder, also mark that moment on
    // the chart. Without the parameters nothing changes.
    const params = useInitialParams();
    const [linkedGame] = useState(() => parseParam.str(params, 'game'));
    const [linkedT] = useState(() => parseParam.num(params, 't', { min: 0, max: 6000 }));
    const [linkedEv] = useState(() => parseParam.int(params, 'ev', { min: 1 }));
    // A game picked from the list goes into the link too (round 8, R8-057), so Copy link / Save / reload
    // reopen it; the list's first game stays out of the URL until something else is picked.
    const [picked, setPicked] = useState(false);
    const onLinkedGame = !selectedGameId || selectedGameId === linkedGame;
    useUrlSync(linkedGame || picked ? {
        game: selectedGameId || linkedGame,
        t: onLinkedGame ? linkedT : null,
        ev: onLinkedGame ? linkedEv : null,
    } : null);
    const [page] = useState(currentPageParam);
    useEffect(() => () => {
        const url = new URL(window.location.href);
        if (url.searchParams.get('page') !== page) return;
        ['game', 't', 'ev'].forEach((k) => url.searchParams.delete(k));
        window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
    }, [page]);

    const [replay, setReplay] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const [hovered, setHovered] = useState(null);
    const [whatifEventId, setWhatifEventId] = useState(null);
    const [whatif, setWhatif] = useState(null);
    const [whatifLoading, setWhatifLoading] = useState(false);

    useEffect(() => {
        let active = true;
        fetchWpReplayList(undefined, linkedGame ?? undefined)
            .then((data) => {
                if (!active) return;
                setGames(data);
                const linked = linkedGame && data.games?.find((g) => g.game_id === linkedGame);
                if (linked) setSelectedGameId(linked.game_id);
                else if (data.games?.length) setSelectedGameId(data.games[0].game_id);
            })
            .catch((e) => { if (active) setGamesError(e?.response?.data?.detail || 'Could not load the game list.'); });
        return () => { active = false; };
    }, [linkedGame]);

    useEffect(() => {
        if (!selectedGameId) return;
        let active = true;
        setLoading(true);
        setError('');
        setWhatif(null);
        setWhatifEventId(null);
        setHovered(null);
        fetchWpReplay(selectedGameId)
            .then((data) => { if (active) setReplay(data); })
            .catch((e) => {
                if (!active) return;
                setReplay(null);
                setError(e?.response?.data?.detail || 'Could not load this game\'s replay.');
            })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [selectedGameId]);

    const maxElapsed = useMemo(() => {
        if (!replay?.points?.length) return 2880;
        return Math.max(2880, ...replay.points.map((p) => p.seconds_elapsed));
    }, [replay]);

    function chartX(secondsElapsed) {
        return PAD_L + (secondsElapsed / maxElapsed) * PLOT_W;
    }
    function chartY(wp) {
        return PAD_T + (1 - wp) * PLOT_H;
    }

    const linePath = useMemo(() => {
        if (!replay?.points?.length) return '';
        return replay.points
            .map((p, i) => `${i === 0 ? 'M' : 'L'} ${chartX(p.seconds_elapsed).toFixed(1)} ${chartY(p.home_wp).toFixed(1)}`)
            .join(' ');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [replay, maxElapsed]);

    const whatifPath = useMemo(() => {
        if (!whatif?.points?.length) return '';
        return whatif.points
            .map((p, i) => `${i === 0 ? 'M' : 'L'} ${chartX(p.seconds_elapsed).toFixed(1)} ${chartY(p.home_wp).toFixed(1)}`)
            .join(' ');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [whatif, maxElapsed]);

    const missedShots = useMemo(() => (replay?.points || []).filter((p) => p.is_missed_shot), [replay]);

    // The linked moment: the play with that event id, else the last play at or before `t`.
    const moment = useMemo(() => {
        if (!replay?.points?.length || replay.game_id !== linkedGame || (linkedT == null && linkedEv == null)) return null;
        const exact = linkedEv != null && replay.points.find((p) => p.event_id === linkedEv);
        if (exact) return exact;
        if (linkedT == null) return null;
        const before = replay.points.filter((p) => p.seconds_elapsed <= linkedT + 0.05);
        return before.length ? before[before.length - 1] : replay.points[0];
    }, [replay, linkedGame, linkedT, linkedEv]);

    function toggleWhatIf(eventId) {
        if (whatifEventId === eventId) {
            setWhatifEventId(null);
            setWhatif(null);
            return;
        }
        setWhatifEventId(eventId);
        setWhatif(null);
        setWhatifLoading(true);
        fetchWpReplayWhatIf(selectedGameId, eventId)
            .then((data) => setWhatif(data))
            .catch(() => setWhatif(null))
            .finally(() => setWhatifLoading(false));
    }

    const periodTicks = useMemo(() => {
        const ticks = [0, 720, 1440, 2160, 2880];
        let extra = 2880;
        while (extra + OT_SECONDS <= maxElapsed + 1) {
            extra += OT_SECONDS;
            ticks.push(extra);
        }
        return ticks;
    }, [maxElapsed]);

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Game Win-Probability Replay
                    <InfoTooltip label="How this works" title="The real WPA model, replayed play by play">
                        Every real play-by-play event from this real game, run through the same real trained
                        win-probability model used by the Clutch WPA leaderboard — not a re-implementation, the
                        exact same model. The dashed "what if" line is a clearly-labeled counterfactual: click any
                        missed-shot marker below the chart to see how the game's win probability would have
                        looked had that one shot gone in, assuming everything afterward happened exactly as it
                        really did.
                        {replay?.clock_note && <> {replay.clock_note}</>}
                    </InfoTooltip>
                    <SourceBadge source={replay?._source} />
                    <CopyLinkButton />
                    <SaveViewButton pageId="analytics" />
                </h3>
                {gamesError && <p className="error-message">{gamesError}</p>}
                {games && (
                    <select
                        className="input-field"
                        value={selectedGameId}
                        onChange={(e) => { setPicked(true); setSelectedGameId(e.target.value); }}
                        aria-label="Game"
                        style={{ marginTop: '0.5rem' }}
                    >
                        {games.games.map((g) => (
                            <option key={g.game_id} value={g.game_id}>
                                {g.game_date} — {g.away_team} {g.final_score.away} @ {g.home_team} {g.final_score.home}
                            </option>
                        ))}
                    </select>
                )}
            </div>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}

            {!loading && replay && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: '0.5rem' }}>
                        <TeamLogo abbreviation={replay.away_team} size={24} />
                        <span style={{ fontWeight: 700 }}>{replay.away_team} {replay.final_score.away}</span>
                        <span className="page-subtitle">@</span>
                        <span style={{ fontWeight: 700 }}>{replay.home_team} {replay.final_score.home}</span>
                        <TeamLogo abbreviation={replay.home_team} size={24} />
                        <span className="page-subtitle" style={{ marginLeft: 8 }}>{replay.game_date}</span>
                    </div>

                    <ChartExport svgRef={svgRef} name={`${replay.away_team} at ${replay.home_team} ${replay.game_date} win probability`} />
                    <svg ref={svgRef} viewBox={`0 0 ${CHART_W} ${CHART_H}`} style={{ width: '100%', display: 'block' }} role="img" aria-label={`Line chart of ${replay.home_team}'s real home win probability over the course of the game from ${replay.away_team} ${replay.final_score.away} vs ${replay.home_team} ${replay.final_score.home}, tracked play by play from tip-off through the final buzzer, with markers on the top swing plays`}>
                        <rect x="0" y="0" width={CHART_W} height={CHART_H} fill="var(--surface-2)" rx="8" />
                        {/* 50% reference line */}
                        <line x1={PAD_L} y1={chartY(0.5)} x2={CHART_W - PAD_R} y2={chartY(0.5)} stroke="var(--hairline)" strokeWidth="1" strokeDasharray="4 3" />
                        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
                            <text key={t} x={PAD_L - 8} y={chartY(t) + 3} fill="var(--text-3)" fontSize="10" textAnchor="end">{Math.round(t * 100)}%</text>
                        ))}
                        {periodTicks.map((t) => (
                            <React.Fragment key={t}>
                                <line x1={chartX(t)} y1={PAD_T} x2={chartX(t)} y2={CHART_H - PAD_B} stroke="var(--hairline)" strokeWidth="1" />
                                <text x={chartX(t)} y={CHART_H - PAD_B + 16} fill="var(--text-3)" fontSize="10" textAnchor="middle">
                                    {t <= 2880 ? `Q${t / 720 + (t === 2880 ? 0 : 1)}` : `OT${(t - 2880) / 300}`}
                                </text>
                            </React.Fragment>
                        ))}

                        {/* Home-team win probability line */}
                        <path d={linePath} fill="none" stroke="var(--accent)" strokeWidth="2" />
                        {whatifPath && (
                            <path d={whatifPath} fill="none" stroke="var(--text-3)" strokeWidth="2" strokeDasharray="6 4" />
                        )}

                        {/* Invisible hover targets on every real play (not just the top 5) so hovering
                            anywhere along the line shows that play's real detail, not only at the
                            handful of marked swing plays. */}
                        {replay.points.map((p) => (
                            <circle
                                key={`hit-${p.event_id}`}
                                cx={chartX(p.seconds_elapsed)}
                                cy={chartY(p.home_wp)}
                                r={5}
                                fill="transparent"
                                style={{ cursor: 'pointer', pointerEvents: 'all' }}
                                tabIndex={0}
                                onMouseEnter={() => setHovered(p)}
                                onMouseLeave={() => setHovered((h) => (h?.event_id === p.event_id ? null : h))}
                                onFocus={() => setHovered(p)}
                                onBlur={() => setHovered((h) => (h?.event_id === p.event_id ? null : h))}
                            />
                        ))}

                        {/* Highlighted dot for whichever play is currently hovered (from the full
                            series above, or one of the always-visible top-play markers below). */}
                        {hovered && (
                            <circle
                                cx={chartX(hovered.seconds_elapsed)}
                                cy={chartY(hovered.home_wp)}
                                r={6}
                                fill={wpColor(hovered.home_wp)}
                                stroke="var(--surface)"
                                strokeWidth="1.5"
                                style={{ pointerEvents: 'none' }}
                            />
                        )}

                        {/* The moment a link pointed at (Play Finder): a marked line and ring. */}
                        {moment && (
                            <g style={{ pointerEvents: 'none' }}>
                                <line x1={chartX(moment.seconds_elapsed)} y1={PAD_T} x2={chartX(moment.seconds_elapsed)} y2={CHART_H - PAD_B}
                                    stroke="var(--brand)" strokeWidth="1.5" strokeDasharray="3 3" />
                                <circle cx={chartX(moment.seconds_elapsed)} cy={chartY(moment.home_wp)} r={8}
                                    fill="none" stroke="var(--brand)" strokeWidth="2.5" />
                            </g>
                        )}

                        {/* Top-play markers (always visible, on top of the hover layer) */}
                        {replay.top_plays.map((p) => (
                            <circle
                                key={p.event_id}
                                cx={chartX(p.seconds_elapsed)}
                                cy={chartY(p.home_wp)}
                                r={hovered?.event_id === p.event_id ? 7 : 5}
                                fill={wpColor(p.home_wp)}
                                stroke="var(--surface)"
                                strokeWidth="1.5"
                                style={{ cursor: 'pointer', pointerEvents: 'none' }}
                            />
                        ))}

                        <text x={CHART_W / 2} y={CHART_H - 4} fill="var(--text-2)" fontSize="11" textAnchor="middle">Game Clock</text>
                        <text x="14" y={CHART_H / 2} fill="var(--text-2)" fontSize="11" textAnchor="middle" transform={`rotate(-90 14 ${CHART_H / 2})`}>Home Win Probability</text>
                    </svg>

                    <div style={{ minHeight: 44, marginTop: '0.5rem' }}>
                        {hovered && (
                            <p className="page-subtitle" style={{ margin: 0 }}>
                                <strong style={{ color: 'var(--text-primary)' }}>{formatClock(hovered.seconds_elapsed)}</strong>
                                {' — '}{hovered.description}
                                {' · '}Home WP {Math.round(hovered.home_wp * 100)}%
                                {' ('}{signed(hovered.wpa * 100, 1, '-')}pp{')'}
                            </p>
                        )}
                        {!hovered && !whatif && moment && (
                            <p className="page-subtitle" style={{ margin: 0 }}>
                                <strong style={{ color: 'var(--brand-text)' }}>Linked play, {formatClock(moment.seconds_elapsed)}</strong>
                                {' — '}{moment.description}
                                {' · '}Home WP {Math.round(moment.home_wp * 100)}%
                                {' ('}{signed(moment.wpa * 100, 1, '-')}pp{')'}
                            </p>
                        )}
                        {!hovered && whatif && (
                            <p className="page-subtitle" style={{ margin: 0, color: 'var(--streak)' }}>
                                <Icon name="undo" size="0.9em" style={{ verticalAlign: 'middle', marginRight: 4 }} />
                                {whatif.counterfactual_label} — {whatif.disclaimer}
                            </p>
                        )}
                        {!hovered && !whatif && !whatifLoading && !moment && (
                            <p className="page-subtitle" style={{ margin: 0 }}>
                                Hover a marked play above for details. Click a missed shot below to see a "what if it had gone in" line.
                            </p>
                        )}
                        {whatifLoading && <p className="page-subtitle" style={{ margin: 0 }}>Computing counterfactual…</p>}
                    </div>

                    <h4 className="section-heading" style={{ marginTop: '1rem', marginBottom: '0.4rem', fontSize: '0.85rem' }}>
                        Real Missed Shots ({missedShots.length}) — click one for "what if"
                    </h4>
                    <svg viewBox={`0 0 ${CHART_W} 28`} style={{ width: '100%', display: 'block' }} role="img" aria-label={`Timeline strip of ${missedShots.length} real missed shots during the game; select one to see a counterfactual "what if it had gone in" win-probability line`}>
                        <rect x="0" y="0" width={CHART_W} height={28} fill="var(--surface-2)" rx="6" />
                        {missedShots.map((p) => (
                            <circle
                                key={p.event_id}
                                cx={chartX(p.seconds_elapsed)}
                                cy={14}
                                r={whatifEventId === p.event_id ? 5.5 : 3}
                                fill={whatifEventId === p.event_id ? 'var(--series-4)' : 'var(--text-3)'}
                                style={{ cursor: 'pointer' }}
                                onMouseEnter={() => setHovered(p)}
                                onMouseLeave={() => setHovered(null)}
                                onClick={() => toggleWhatIf(p.event_id)}
                            />
                        ))}
                    </svg>

                    <TableExport />
                    <div className="table-wrapper" style={{ marginTop: '1rem' }}>
                        <table className="data-table">
                            <thead>
                                <tr><th>Time</th><th>Play</th><th>Home WP</th><th>Swing</th></tr>
                            </thead>
                            <tbody>
                                {replay.top_plays.map((p) => (
                                    <tr key={p.event_id}>
                                        <td>{formatClock(p.seconds_elapsed)}</td>
                                        <td>{p.description}</td>
                                        <td>{Math.round(p.home_wp * 100)}%</td>
                                        <td style={{ color: bySign(p.wpa * 100, 1, 'var(--positive)', 'var(--negative)', undefined) }}>
                                            {signed(p.wpa * 100, 1, '-')}pp
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}
