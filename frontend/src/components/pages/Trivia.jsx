import React, { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { fetchTriviaDaily, submitTriviaGuess } from '../../services/api';
import { localDateIso } from '../../utils/date';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import GameCursorScope from '../games/GameCursorScope';
import ShareResult from '../games/ShareResult';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';

const STORAGE_PREFIX = 'nbahub_trivia:';

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

export default function Trivia() {
    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);
    const puzzleDate = useMemo(() => localDateIso(), []);

    const [daily, setDaily] = useState(null);
    const [loadError, setLoadError] = useState('');
    const [answers, setAnswers] = useState([]); // [{question_id, option_id, correct, correct_option_id}]
    const [pendingResult, setPendingResult] = useState(null); // {option_id, correct, correct_option_id} for the current question
    const [submitting, setSubmitting] = useState(false);
    const [submitError, setSubmitError] = useState('');

    useEffect(() => {
        let active = true;
        (async () => {
            try {
                const data = await fetchTriviaDaily();
                if (!active) return;
                setDaily(data);
                const key = `${STORAGE_PREFIX}${puzzleDate}:${data.season}`;
                const saved = loadSaved(key);
                if (saved) setAnswers(saved.answers || []);
            } catch (e) {
                if (active) setLoadError(e?.response?.data?.detail || 'Could not load today’s trivia.');
            }
        })();
        return () => { active = false; };
    }, [puzzleDate]);

    const storageKey = daily ? `${STORAGE_PREFIX}${puzzleDate}:${daily.season}` : null;
    const currentIndex = answers.length;
    const currentQuestion = daily?.questions?.[currentIndex];
    const done = daily && currentIndex >= daily.questions.length;
    const score = answers.filter((a) => a.correct).length;

    async function pickOption(optionId) {
        if (!daily || !currentQuestion || submitting || pendingResult) return;
        setSubmitting(true);
        setSubmitError('');
        try {
            const result = await submitTriviaGuess(currentQuestion.id, optionId, daily.season, puzzleDate);
            setPendingResult({ option_id: optionId, ...result });
        } catch (e) {
            setSubmitError(e?.response?.data?.detail || 'Could not submit that answer.');
        } finally {
            setSubmitting(false);
        }
    }

    function advance() {
        if (!pendingResult || !currentQuestion) return;
        const nextAnswers = [...answers, {
            question_id: currentQuestion.id,
            option_id: pendingResult.option_id,
            correct: pendingResult.correct,
            correct_option_id: pendingResult.correct_option_id,
        }];
        setAnswers(nextAnswers);
        setPendingResult(null);
        if (storageKey) saveState(storageKey, { answers: nextAnswers });
    }

    return (
        <GameCursorScope>
        <div className="page fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="quiz" /></span>
                    Trivia
                    <InfoTooltip label="How this works" title="Real answers, rotating decoys">
                        Five multiple-choice questions a day, built from this season's qualified player pool
                        (min≥15 mpg, gp≥20). The correct answer is always today's real value — the actual
                        current league leader, or the real most-common statistical archetype — never invented.
                        The three wrong options are always other real players (or real archetype labels) from the
                        same data, just not the right answer for that question; which ones show up rotates daily.
                    </InfoTooltip>
                </h2>
                <p className="page-subtitle">
                    {daily ? `Season ${daily.season} · ${puzzleDate}` : 'Loading today’s trivia…'}
                </p>
                {loadError && <p className="error-message">{loadError}</p>}
                {daily && (
                    <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
                        Question {Math.min(currentIndex + 1, daily.questions.length)} of {daily.questions.length} · Score: {score}
                    </p>
                )}
            </div>

            {daily && !done && currentQuestion && (
                <motion.div
                    key={currentQuestion.id}
                    className="dashboard-card"
                    style={{ marginTop: '1rem' }}
                    initial={isAdvanced ? { opacity: 0, y: 10 } : false}
                    animate={{ opacity: 1, y: 0 }}
                    transition={preset.spring}
                >
                    <h3 className="section-heading" style={{ marginTop: 0 }}>{currentQuestion.question}</h3>
                    {submitError && <p className="error-message">{submitError}</p>}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', marginTop: '0.75rem' }}>
                        {currentQuestion.options.map((opt) => {
                            let style = {};
                            if (pendingResult) {
                                if (opt.id === pendingResult.correct_option_id) {
                                    style = { borderColor: '#34d399', color: 'var(--positive)' };
                                } else if (opt.id === pendingResult.option_id) {
                                    style = { borderColor: '#f87171', color: 'var(--negative)' };
                                }
                            }
                            return (
                                <button
                                    key={opt.id}
                                    type="button"
                                    className="input-field"
                                    style={{ cursor: pendingResult ? 'default' : 'pointer', textAlign: 'left', height: 'auto', padding: '0.75rem 1rem', ...style }}
                                    onClick={() => pickOption(opt.id)}
                                    disabled={!!pendingResult || submitting}
                                >
                                    {pendingResult && opt.id === pendingResult.correct_option_id && (
                                        <Icon name="check" size="1em" style={{ verticalAlign: 'middle', marginRight: 6, color: 'var(--positive)' }} />
                                    )}
                                    {pendingResult && opt.id === pendingResult.option_id && opt.id !== pendingResult.correct_option_id && (
                                        <Icon name="close" size="1em" style={{ verticalAlign: 'middle', marginRight: 6, color: 'var(--negative)' }} />
                                    )}
                                    {opt.label}
                                </button>
                            );
                        })}
                    </div>
                    {pendingResult && (
                        <button type="button" className="input-field" data-magnetic style={{ marginTop: '1rem', cursor: 'pointer', maxWidth: 160 }} onClick={advance}>
                            {currentIndex + 1 >= daily.questions.length ? 'See Results' : 'Next'}
                            <Icon name="arrow_forward" size="1em" style={{ verticalAlign: 'middle', marginLeft: 4 }} />
                        </button>
                    )}
                </motion.div>
            )}

            <AnimatePresence>
                {done && (
                    <motion.div
                        className="dashboard-card"
                        style={{ marginTop: '1rem', textAlign: 'center' }}
                        initial={isAdvanced ? { opacity: 0, y: -10 } : false}
                        animate={{ opacity: 1, y: 0 }}
                        transition={preset.spring}
                    >
                        <h3 className="section-heading" style={{ marginTop: 0 }}>
                            Final Score: {score} / {daily.questions.length}
                        </h3>
                        <p className="page-subtitle">Come back tomorrow for a new set of questions.</p>
                        <ShareResult
                            gameTitle="Trivia"
                            puzzleDate={puzzleDate}
                            outcomes={answers.map((a) => a.correct)}
                            summary={`Score: ${score}/${daily.questions.length}`}
                        />
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
        </GameCursorScope>
    );
}
