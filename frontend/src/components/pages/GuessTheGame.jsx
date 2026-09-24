import React, { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { fetchGuessTheGameDaily, submitGuessTheGame, fetchGuessTheGameReveal } from '../../services/api';
import { localDateIso } from '../../utils/date';
import { TEAM_NAME_TO_ABBR } from '../../utils/teamAssets';
import TeamLogo from '../common/TeamLogo';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';

const STORAGE_PREFIX = 'nbahub_guess_the_game:';
const TEAMS = Object.entries(TEAM_NAME_TO_ABBR).map(([name, abbr]) => ({ name, abbr })).sort((a, b) => a.name.localeCompare(b.name));

const CHART_W = 640;
const CHART_H = 220;
const PAD_L = 36;
const PAD_R = 12;
const PAD_T = 12;
const PAD_B = 12;
const PLOT_W = CHART_W - PAD_L - PAD_R;
const PLOT_H = CHART_H - PAD_T - PAD_B;

const CLUE_LABEL = {
    season: 'Season',
    final_margin: 'Final Margin',
    one_team: 'One Team In This Game',
};

function clueDisplayValue(clue) {
    if (!clue) return null;
    if (clue.type === 'season') return `${clue.value - 1}-${String(clue.value).slice(-2)}`;
    if (clue.type === 'final_margin') return `${clue.value} points`;
    if (clue.type === 'one_team') return clue.value;
    return String(clue.value);
}

function loadSaved(key) {
    try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : null;
    } catch {
        return null;
    }
}

function saveState(key, data) {
    try {
        localStorage.setItem(key, JSON.stringify(data));
    } catch {
        // Ignore storage errors in private/incognito or quota limits.
    }
}

export default function GuessTheGame() {
    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);
    const puzzleDate = useMemo(() => localDateIso(), []);

    const [daily, setDaily] = useState(null);
    const [loadError, setLoadError] = useState('');
    const [attempts, setAttempts] = useState([]);
    const [status, setStatus] = useState('playing'); // playing | won | lost
    const [reveal, setReveal] = useState(null);
    const [search, setSearch] = useState('');
    const [submitError, setSubmitError] = useState('');
    const [submitting, setSubmitting] = useState(false);

    const storageKey = `${STORAGE_PREFIX}${puzzleDate}`;

    useEffect(() => {
        let active = true;
        (async () => {
            try {
                const data = await fetchGuessTheGameDaily(puzzleDate);
                if (!active) return;
                setDaily(data);
                const saved = loadSaved(storageKey);
                if (saved) {
                    setAttempts(saved.attempts || []);
                    setStatus(saved.status || 'playing');
                    setReveal(saved.reveal || null);
                }
            } catch (e) {
                if (active) setLoadError(e?.response?.data?.detail || "Could not load today's game puzzle.");
            }
        })();
        return () => { active = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [puzzleDate]);

    const maxElapsed = useMemo(() => {
        if (!daily?.points?.length) return 2880;
        return Math.max(2880, ...daily.points.map((p) => p.seconds_elapsed));
    }, [daily]);

    function chartX(secondsElapsed) {
        return PAD_L + (secondsElapsed / maxElapsed) * PLOT_W;
    }
    function chartY(wp) {
        return PAD_T + (1 - wp) * PLOT_H;
    }

    const linePath = useMemo(() => {
        if (!daily?.points?.length) return '';
        return daily.points
            .map((p, i) => `${i === 0 ? 'M' : 'L'} ${chartX(p.seconds_elapsed).toFixed(1)} ${chartY(p.home_wp).toFixed(1)}`)
            .join(' ');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [daily, maxElapsed]);

    const guessedAbbrs = useMemo(() => new Set(attempts.map((a) => a.team)), [attempts]);

    const suggestions = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (q.length < 1) return [];
        return TEAMS.filter((t) =>
            (t.name.toLowerCase().includes(q) || t.abbr.toLowerCase().includes(q)) && !guessedAbbrs.has(t.abbr)
        ).slice(0, 8);
    }, [search, guessedAbbrs]);

    async function submitTeam(abbr) {
        if (!daily || status !== 'playing' || submitting) return;
        setSubmitting(true);
        setSubmitError('');
        try {
            const attemptNumber = attempts.length + 1;
            const result = await submitGuessTheGame(abbr, attemptNumber, puzzleDate);
            const nextAttempts = [...attempts, { team: abbr, ...result }];
            let nextStatus = status;
            let nextReveal = reveal;

            if (result.correct) {
                nextStatus = 'won';
                nextReveal = result.mystery_game;
            } else if (nextAttempts.length >= daily.max_guesses) {
                nextStatus = 'lost';
                try {
                    nextReveal = await fetchGuessTheGameReveal(puzzleDate);
                } catch {
                    nextReveal = null;
                }
            }

            setAttempts(nextAttempts);
            setStatus(nextStatus);
            setReveal(nextReveal);
            setSearch('');
            saveState(storageKey, { attempts: nextAttempts, status: nextStatus, reveal: nextReveal });
        } catch (e) {
            setSubmitError(e?.response?.data?.detail || 'Could not submit that guess.');
        } finally {
            setSubmitting(false);
        }
    }

    function handleSearchKeyDown(e) {
        if (e.key === 'Enter' && suggestions.length === 1) {
            submitTeam(suggestions[0].abbr);
        }
    }

    return (
        <div className="page fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="movie" /></span>
                    Guess the Game
                    <InfoTooltip label="How this works" title="A real game's real win-probability curve">
                        Everyone gets the same daily mystery game — a real completed game, picked from the same
                        real play-by-play data behind the Game Replay tab and Clutch WPA tracker. The curve above
                        is that game's real home-team win probability over time, from the real trained WPA model,
                        with no team names or date shown. You get {daily?.max_guesses ?? 3} guesses at either
                        team in the game; each wrong guess reveals a real clue — season, then final margin, then
                        one of the two real teams.
                    </InfoTooltip>
                </h2>
                <p className="page-subtitle">
                    {daily ? `Real game shape for ${puzzleDate} — no teams or date shown` : 'Loading today\'s puzzle…'}
                </p>
                {loadError && <p className="error-message">{loadError}</p>}

                {daily && (
                    <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} style={{ width: '100%', display: 'block', marginTop: '0.75rem' }}>
                        <rect x="0" y="0" width={CHART_W} height={CHART_H} fill="var(--surface-2)" rx="8" />
                        <line x1={PAD_L} y1={chartY(0.5)} x2={CHART_W - PAD_R} y2={chartY(0.5)} stroke="var(--hairline)" strokeWidth="1" strokeDasharray="4 3" />
                        {[0, 0.5, 1].map((t) => (
                            <text key={t} x={PAD_L - 8} y={chartY(t) + 3} fill="var(--text-3)" fontSize="10" textAnchor="end">{Math.round(t * 100)}%</text>
                        ))}
                        <path d={linePath} fill="none" stroke="var(--accent)" strokeWidth="2.5" />
                    </svg>
                )}
            </div>

            {daily && status === 'playing' && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <div style={{ position: 'relative', maxWidth: 360 }}>
                        <input
                            type="text"
                            className="input-field"
                            placeholder="Type a team name…"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            onKeyDown={handleSearchKeyDown}
                            style={{ width: '100%' }}
                            disabled={submitting}
                        />
                        {suggestions.length > 0 && (
                            <ul className="autocomplete-list" style={{
                                position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
                                background: '#1e293b', border: '1px solid #334155', borderRadius: 6,
                                marginTop: 4, maxHeight: 240, overflowY: 'auto', listStyle: 'none', padding: 0,
                            }}>
                                {suggestions.map((t) => (
                                    <li key={t.abbr}>
                                        <button
                                            type="button"
                                            onClick={() => submitTeam(t.abbr)}
                                            style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left', padding: '0.5rem 0.75rem', background: 'transparent', border: 'none', color: '#e2e8f0', cursor: 'pointer' }}
                                        >
                                            <TeamLogo abbreviation={t.abbr} size={16} />
                                            {t.name}
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                    {submitError && <p className="error-message" style={{ marginTop: '0.5rem' }}>{submitError}</p>}
                    <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>
                        Guess {attempts.length + 1} of {daily.max_guesses}
                    </p>
                </div>
            )}

            {attempts.length > 0 && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <h3 className="section-heading" style={{ marginTop: 0 }}>Guesses & Clues</h3>
                    <AnimatePresence initial={false}>
                        {attempts.map((a, i) => (
                            <motion.div
                                key={`${a.team}-${i}`}
                                initial={isAdvanced ? { opacity: 0, x: -12 } : false}
                                animate={{ opacity: 1, x: 0 }}
                                transition={preset.spring}
                                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0.4rem 0' }}
                            >
                                <TeamLogo abbreviation={a.team} size={20} />
                                <span>{a.team}</span>
                                {a.correct
                                    ? <Icon name="celebration" size="1em" style={{ color: '#34d399' }} />
                                    : <Icon name="close" size="1em" style={{ color: 'var(--text-muted)' }} />}
                                {!a.correct && a.clue && (
                                    <span className="page-subtitle" style={{ margin: 0 }}>
                                        Clue: <strong style={{ color: 'var(--text-primary)' }}>{CLUE_LABEL[a.clue.type]}</strong> — {clueDisplayValue(a.clue)}
                                    </span>
                                )}
                            </motion.div>
                        ))}
                    </AnimatePresence>
                </div>
            )}

            <AnimatePresence>
                {status !== 'playing' && reveal && (
                    <motion.div
                        className="dashboard-card"
                        style={{ marginTop: '1rem', textAlign: 'center' }}
                        initial={isAdvanced ? { opacity: 0, y: -10 } : false}
                        animate={{ opacity: 1, y: 0 }}
                        transition={preset.spring}
                    >
                        <h3 className="section-heading" style={{ marginTop: 0 }}>
                            {status === 'won' ? `Solved in ${attempts.length} guess${attempts.length === 1 ? '' : 'es'}!` : 'Out of guesses'}
                        </h3>
                        <div className="entity-row" style={{ justifyContent: 'center', gap: 12 }}>
                            <TeamLogo abbreviation={reveal.away_team} size={40} />
                            <span style={{ fontWeight: 700 }}>{reveal.away_team} {reveal.final_score.away}</span>
                            <span className="page-subtitle">@</span>
                            <span style={{ fontWeight: 700 }}>{reveal.home_team} {reveal.final_score.home}</span>
                            <TeamLogo abbreviation={reveal.home_team} size={40} />
                        </div>
                        <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
                            {reveal.game_date} · Real season {reveal.season - 1}-{String(reveal.season).slice(-2)}
                        </p>
                        <p className="page-subtitle">Come back tomorrow for a new mystery game.</p>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
