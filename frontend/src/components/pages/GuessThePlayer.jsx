import React, { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { fetchGuessDaily, submitGuessThePlayer, fetchGuessReveal } from '../../services/api';
import { localDateIso } from '../../utils/date';
import PlayerHeadshot from '../common/PlayerHeadshot';
import TeamLogo from '../common/TeamLogo';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import GameCursorScope from '../games/GameCursorScope';
import ShareResult from '../games/ShareResult';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';

const STORAGE_PREFIX = 'nbahub_guess_the_player:';

const FIELD_COLUMNS = [
    { key: 'team', label: 'Team', kind: 'match' },
    { key: 'position', label: 'Position', kind: 'match' },
    { key: 'archetype', label: 'Archetype', kind: 'match' },
    { key: 'age', label: 'Age', kind: 'direction' },
    { key: 'pts', label: 'PTS', kind: 'direction' },
    { key: 'reb', label: 'REB', kind: 'direction' },
    { key: 'ast', label: 'AST', kind: 'direction' },
];

const FIELD_VALUE = {
    team: (g) => g.team_abbreviation,
    position: (g) => g.position,
    archetype: (g) => g.archetype,
    age: (g) => g.age,
    pts: (g) => g.pts,
    reb: (g) => g.reb,
    ast: (g) => g.ast,
};

function fmtStat(key, guess) {
    const value = FIELD_VALUE[key](guess);
    if (value == null) return '—';
    if (key === 'pts' || key === 'reb' || key === 'ast') return Number(value).toFixed(1);
    return value;
}

function DirectionIcon({ direction }) {
    if (direction === 'same') return <Icon name="check" size="1.1em" style={{ color: '#34d399' }} />;
    if (direction === 'higher') return <Icon name="arrow_upward" size="1.1em" style={{ color: '#facc15' }} />;
    if (direction === 'lower') return <Icon name="arrow_downward" size="1.1em" style={{ color: '#facc15' }} />;
    return <span style={{ color: 'var(--text-muted)' }}>—</span>;
}

function MatchIcon({ status }) {
    if (status === 'match') return <Icon name="check" size="1.1em" style={{ color: '#34d399' }} />;
    return <Icon name="close" size="1.1em" style={{ color: 'var(--text-muted)' }} />;
}

function loadSaved(key) {
    try {
        const raw = localStorage.getItem(key);
        if (!raw) return null;
        return JSON.parse(raw);
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

export default function GuessThePlayer() {
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
                const data = await fetchGuessDaily();
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

    const guessedNames = useMemo(() => new Set(guesses.map((g) => g.guess.player_name.toLowerCase())), [guesses]);

    const suggestions = useMemo(() => {
        const q = search.trim().toLowerCase();
        if (!daily || q.length < 1) return [];
        return daily.pool
            .filter((p) => p.player_name.toLowerCase().includes(q) && !guessedNames.has(p.player_name.toLowerCase()))
            .slice(0, 8);
    }, [search, daily, guessedNames]);

    async function submitGuess(name) {
        if (!daily || status !== 'playing' || submitting) return;
        setSubmitting(true);
        setSubmitError('');
        try {
            const result = await submitGuessThePlayer(name, daily.season, puzzleDate);
            const nextGuesses = [...guesses, result];
            let nextStatus = status;
            let nextReveal = reveal;

            if (result.correct) {
                nextStatus = 'won';
                nextReveal = result.mystery_player;
            } else if (nextGuesses.length >= daily.max_guesses) {
                nextStatus = 'lost';
                try {
                    nextReveal = await fetchGuessReveal(daily.season, puzzleDate);
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

    return (
        <GameCursorScope>
        <div className="page fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="sports_esports" /></span>
                    Guess the Player
                    <InfoTooltip label="How this works" title="One mystery player, real clues">
                        Everyone gets the same daily mystery player, picked from this season's qualified pool
                        (min≥15 mpg, gp≥20 — the same pool used by Radar Comparison and Player Comparison).
                        Each guess checks against real data: team and per-game stat direction always come straight
                        from player_season_stats. "Position" is an estimated label derived from this project's own
                        BPM-position model, and "Archetype" is its statistical clustering — both stand in for a
                        real scouted position/role, which this project's data doesn't have. You get {daily?.max_guesses ?? 8} guesses.
                    </InfoTooltip>
                </h2>
                <p className="page-subtitle">
                    {daily ? `Season ${daily.season} · pool of ${daily.pool_size} qualified players · ${puzzleDate}` : 'Loading today’s puzzle…'}
                </p>

                {loadError && <p className="error-message">{loadError}</p>}

                {daily && status === 'playing' && (
                    <div style={{ position: 'relative', maxWidth: 360, marginTop: '0.75rem' }}>
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
                                background: 'var(--surface-2)', border: '1px solid var(--hairline)', borderRadius: 6,
                                marginTop: 4, maxHeight: 240, overflowY: 'auto', listStyle: 'none', padding: 0,
                            }}>
                                {suggestions.map((p) => (
                                    <li key={p.player_id}>
                                        <button
                                            type="button"
                                            onClick={() => submitGuess(p.player_name)}
                                            style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', textAlign: 'left', padding: '0.5rem 0.75rem', background: 'transparent', border: 'none', color: 'var(--text)', cursor: 'pointer' }}
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

                {daily && (
                    <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>
                        Guess {Math.min(guesses.length + (status === 'playing' ? 1 : 0), daily.max_guesses)} of {daily.max_guesses}
                    </p>
                )}
            </div>

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
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '0.4rem' }}>
                            <PlayerHeadshot playerId={reveal.player_id} playerName={reveal.player_name} size={84} />
                            <div className="hb-compare-bio-name">{reveal.player_name}</div>
                            <div className="entity-row" style={{ justifyContent: 'center' }}>
                                <TeamLogo abbreviation={reveal.team_abbreviation} size={18} />
                                <span className="page-subtitle" style={{ margin: 0 }}>{reveal.team_abbreviation}</span>
                            </div>
                            <p className="page-subtitle">Come back tomorrow for a new mystery player.</p>
                            <ShareResult
                                gameTitle="Guess the Player"
                                puzzleDate={puzzleDate}
                                outcomes={guesses.map((g) => g.correct)}
                                summary={status === 'won' ? `Solved in ${guesses.length}/${daily.max_guesses}` : `${daily.max_guesses}/${daily.max_guesses}`}
                            />
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {guesses.length > 0 && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <h3 className="section-heading" style={{ marginTop: 0 }}>Your Guesses</h3>
                    <div className="hb-table-wrapper table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Player</th>
                                    {FIELD_COLUMNS.map((col) => <th key={col.key}>{col.label}</th>)}
                                </tr>
                            </thead>
                            <tbody>
                                <AnimatePresence initial={false}>
                                    {guesses.map((g, i) => (
                                        <motion.tr
                                            key={`${g.guess.player_id}-${i}`}
                                            initial={isAdvanced ? { opacity: 0, x: -12 } : false}
                                            animate={{ opacity: 1, x: 0 }}
                                            transition={{ ...preset.spring, delay: 0 }}
                                        >
                                            <td>
                                                <div className="entity-row">
                                                    <PlayerHeadshot playerId={g.guess.player_id} playerName={g.guess.player_name} size={28} />
                                                    {g.guess.player_name}
                                                    {g.correct && <Icon name="celebration" size="1em" style={{ color: '#34d399', marginLeft: 4 }} />}
                                                </div>
                                            </td>
                                            {FIELD_COLUMNS.map((col) => (
                                                <td key={col.key}>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                                        {col.kind === 'match'
                                                            ? <MatchIcon status={g.feedback[col.key]} />
                                                            : <DirectionIcon direction={g.feedback[col.key]} />}
                                                        <span style={{ fontSize: '0.85em', color: 'var(--text-muted)' }}>
                                                            {fmtStat(col.key, g.guess)}
                                                        </span>
                                                    </div>
                                                </td>
                                            ))}
                                        </motion.tr>
                                    ))}
                                </AnimatePresence>
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
        </GameCursorScope>
    );
}
