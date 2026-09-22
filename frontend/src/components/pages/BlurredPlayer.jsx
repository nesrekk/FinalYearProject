import React, { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { fetchBlurredDaily, submitBlurredPlayerGuess, fetchBlurredReveal, getBlurredPlayerImageUrl } from '../../services/api';
import { localDateIso } from '../../utils/date';
import TeamLogo from '../common/TeamLogo';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';

const STORAGE_PREFIX = 'nbahub_blurred_player:';
const MAX_BLUR = 22;
const MIN_BLUR = 3;

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

export default function BlurredPlayer() {
    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);
    const puzzleDate = useMemo(() => localDateIso(), []);

    const [daily, setDaily] = useState(null);
    const [loadError, setLoadError] = useState('');
    const [guesses, setGuesses] = useState([]);
    const [status, setStatus] = useState('playing'); // playing | won | lost
    const [reveal, setReveal] = useState(null);
    const [search, setSearch] = useState('');
    const [submitError, setSubmitError] = useState('');
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => {
        let active = true;
        (async () => {
            try {
                const data = await fetchBlurredDaily();
                if (!active) return;
                setDaily(data);
                const key = `${STORAGE_PREFIX}${puzzleDate}:${data.season}`;
                const saved = loadSaved(key);
                if (saved) {
                    setGuesses(saved.guesses || []);
                    setStatus(saved.status || 'playing');
                    setReveal(saved.reveal || null);
                }
            } catch (e) {
                if (active) setLoadError(e?.response?.data?.detail || 'Could not load today’s puzzle.');
            }
        })();
        return () => { active = false; };
    }, [puzzleDate]);

    const storageKey = daily ? `${STORAGE_PREFIX}${puzzleDate}:${daily.season}` : null;

    const guessedNames = useMemo(() => new Set(guesses.map((g) => g.guess_player_name.toLowerCase())), [guesses]);

    const suggestions = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (!daily || q.length < 1) return [];
        return daily.pool
            .filter((p) => p.player_name.toLowerCase().includes(q) && !guessedNames.has(p.player_name.toLowerCase()))
            .slice(0, 8);
    }, [search, daily, guessedNames]);

    const blurPx = useMemo(() => {
        if (!daily) return MAX_BLUR;
        if (status !== 'playing') return 0;
        const fraction = 1 - guesses.length / daily.max_guesses;
        return Math.max(MIN_BLUR, Math.round(MAX_BLUR * fraction));
    }, [daily, guesses.length, status]);

    async function submitGuess(name) {
        if (!daily || status !== 'playing' || submitting) return;
        setSubmitting(true);
        setSubmitError('');
        try {
            const result = await submitBlurredPlayerGuess(name, daily.season, puzzleDate);
            const nextGuesses = [...guesses, result];
            let nextStatus = status;
            let nextReveal = reveal;

            if (result.correct) {
                nextStatus = 'won';
                nextReveal = result.mystery_player;
            } else if (nextGuesses.length >= daily.max_guesses) {
                nextStatus = 'lost';
                try {
                    nextReveal = await fetchBlurredReveal(daily.season, puzzleDate);
                } catch {
                    nextReveal = null;
                }
            }

            setGuesses(nextGuesses);
            setStatus(nextStatus);
            setReveal(nextReveal);
            setSearch('');
            if (storageKey) saveState(storageKey, { guesses: nextGuesses, status: nextStatus, reveal: nextReveal });
        } catch (e) {
            setSubmitError(e?.response?.data?.detail || 'Could not submit that guess.');
        } finally {
            setSubmitting(false);
        }
    }

    function handleSearchKeyDown(e) {
        if (e.key === 'Enter' && suggestions.length === 1) {
            submitGuess(suggestions[0].player_name);
        }
    }

    const imageUrl = daily ? getBlurredPlayerImageUrl(daily.season, puzzleDate) : null;

    return (
        <div className="page fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="blur_on" /></span>
                    Blurred Player
                    <InfoTooltip label="How this works" title="A real photo, progressively revealed">
                        Today's mystery player is a real headshot, drawn from the same qualified season pool as
                        Guess the Player (min≥15 mpg, gp≥20), but a different daily pick so the two games
                        don't share an answer. The photo starts heavily blurred and sharpens a little with each
                        wrong guess, down to a floor that never fully clears until you win or run out of guesses.
                        You get {daily?.max_guesses ?? 8} guesses.
                    </InfoTooltip>
                </h2>
                <p className="page-subtitle">
                    {daily ? `Season ${daily.season} · pool of ${daily.pool_size} qualified players · ${puzzleDate}` : 'Loading today’s puzzle…'}
                </p>

                {loadError && <p className="error-message">{loadError}</p>}

                {daily && (
                    <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
                        Guess {Math.min(guesses.length + (status === 'playing' ? 1 : 0), daily.max_guesses)} of {daily.max_guesses}
                    </p>
                )}
            </div>

            {imageUrl && (
                <div className="dashboard-card" style={{ marginTop: '1rem', textAlign: 'center' }}>
                    <motion.div
                        animate={{ filter: `blur(${blurPx}px)` }}
                        transition={preset.spring}
                        style={{ display: 'inline-block', borderRadius: 12, overflow: 'hidden', width: 220, height: 161 }}
                    >
                        <img
                            src={imageUrl}
                            alt={status === 'playing' ? 'Mystery player' : (reveal?.player_name || 'Mystery player')}
                            style={{ width: 220, height: 161, objectFit: 'cover', display: 'block' }}
                        />
                    </motion.div>

                    {status === 'playing' && (
                        <div style={{ position: 'relative', maxWidth: 360, margin: '1rem auto 0' }}>
                            <input
                                type="text"
                                className="input-field"
                                placeholder="Type a player name…"
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
                                    marginTop: 4, maxHeight: 240, overflowY: 'auto', listStyle: 'none', padding: 0, textAlign: 'left',
                                }}>
                                    {suggestions.map((p) => (
                                        <li key={p.player_id}>
                                            <button
                                                type="button"
                                                onClick={() => submitGuess(p.player_name)}
                                                style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left', padding: '0.5rem 0.75rem', background: 'transparent', border: 'none', color: '#e2e8f0', cursor: 'pointer' }}
                                            >
                                                <TeamLogo abbreviation={p.team_abbreviation} size={16} />
                                                {p.player_name}
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    )}
                    {submitError && <p className="error-message" style={{ marginTop: '0.5rem' }}>{submitError}</p>}
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
                            {status === 'won' ? `Solved in ${guesses.length} guess${guesses.length === 1 ? '' : 'es'}!` : 'Out of guesses'}
                        </h3>
                        <div className="entity-row" style={{ justifyContent: 'center' }}>
                            <TeamLogo abbreviation={reveal.team_abbreviation} size={18} />
                            <span className="page-subtitle" style={{ margin: 0 }}>{reveal.player_name} · {reveal.team_abbreviation}</span>
                        </div>
                        <p className="page-subtitle">Come back tomorrow for a new mystery player.</p>
                    </motion.div>
                )}
            </AnimatePresence>

            {guesses.length > 0 && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <h3 className="section-heading" style={{ marginTop: 0 }}>Your Guesses</h3>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                        <AnimatePresence initial={false}>
                            {guesses.map((g, i) => (
                                <motion.div
                                    key={`${g.guess_player_name}-${i}`}
                                    initial={isAdvanced ? { opacity: 0, x: -12 } : false}
                                    animate={{ opacity: 1, x: 0 }}
                                    transition={preset.spring}
                                    style={{ display: 'flex', alignItems: 'center', gap: 8 }}
                                >
                                    <Icon name={g.correct ? 'check' : 'close'} size="1.1em" style={{ color: g.correct ? '#34d399' : 'var(--text-muted)' }} />
                                    {g.guess_player_name}
                                </motion.div>
                            ))}
                        </AnimatePresence>
                    </div>
                </div>
            )}
        </div>
    );
}
