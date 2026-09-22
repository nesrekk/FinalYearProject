import React, { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { fetchHigherLowerPool } from '../../services/api';
import PlayerHeadshot from '../common/PlayerHeadshot';
import TeamLogo from '../common/TeamLogo';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';

const BEST_STREAK_KEY = 'nbahub_higher_lower_best_streak';

function randomOf(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
}

function pickOpponent(pool, excludeId) {
    let candidate = randomOf(pool);
    let guardCount = 0;
    while (candidate.player_id === excludeId && guardCount < 20) {
        candidate = randomOf(pool);
        guardCount += 1;
    }
    return candidate;
}

function fmtValue(value) {
    if (value == null) return '—';
    return value.toLocaleString();
}

function loadBestStreak() {
    try {
        return Number(localStorage.getItem(BEST_STREAK_KEY)) || 0;
    } catch {
        return 0;
    }
}

function saveBestStreak(value) {
    try {
        localStorage.setItem(BEST_STREAK_KEY, String(value));
    } catch {
        // Ignore storage errors in private/incognito or quota limits.
    }
}

function PlayerCard({ player, statLabel, value, hidden, color }) {
    return (
        <div className="hb-hl-card" style={{ borderColor: color }}>
            <PlayerHeadshot playerId={player.player_id} playerName={player.player_name} size={72} />
            <div className="hb-compare-bio-name" style={{ fontSize: '1rem' }}>{player.player_name}</div>
            <div className="entity-row" style={{ justifyContent: 'center' }}>
                <TeamLogo abbreviation={player.team_abbreviation} size={16} />
                <span className="page-subtitle" style={{ margin: 0 }}>{player.team_abbreviation}</span>
            </div>
            <div className="hb-hl-value">
                {hidden ? '?' : fmtValue(value)}
            </div>
            <div className="page-subtitle" style={{ marginTop: '-0.3rem' }}>{statLabel}</div>
        </div>
    );
}

export default function HigherOrLower() {
    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);

    const [pool, setPool] = useState(null);
    const [statOptions, setStatOptions] = useState([]);
    const [loadError, setLoadError] = useState('');

    const [current, setCurrent] = useState(null);
    const [next, setNext] = useState(null);
    const [statKey, setStatKey] = useState(null);
    const [streak, setStreak] = useState(0);
    const [bestStreak, setBestStreak] = useState(loadBestStreak);
    const [status, setStatus] = useState('idle'); // idle | playing | revealed | gameover
    const [lastCorrect, setLastCorrect] = useState(null);

    useEffect(() => {
        let active = true;
        (async () => {
            try {
                const data = await fetchHigherLowerPool();
                if (!active) return;
                setPool(data.players);
                setStatOptions(data.stat_options);
            } catch (e) {
                if (active) setLoadError(e?.response?.data?.detail || 'Could not load the player pool.');
            }
        })();
        return () => { active = false; };
    }, []);

    const statLabel = useMemo(
        () => statOptions.find((s) => s.key === statKey)?.label || '',
        [statOptions, statKey]
    );

    function startGame() {
        const first = randomOf(pool);
        const second = pickOpponent(pool, first.player_id);
        setCurrent(first);
        setNext(second);
        setStatKey(randomOf(statOptions).key);
        setStreak(0);
        setStatus('playing');
        setLastCorrect(null);
    }

    function guess(direction) {
        if (status !== 'playing') return;
        const currentVal = current[statKey];
        const nextVal = next[statKey];
        const correct = nextVal === currentVal || (direction === 'higher' ? nextVal > currentVal : nextVal < currentVal);
        setLastCorrect(correct);
        setStatus('revealed');

        if (correct) {
            const newStreak = streak + 1;
            setStreak(newStreak);
            if (newStreak > bestStreak) {
                setBestStreak(newStreak);
                saveBestStreak(newStreak);
            }
        }
    }

    function advance() {
        const newOpponent = pickOpponent(pool, next.player_id);
        setCurrent(next);
        setNext(newOpponent);
        setStatKey(randomOf(statOptions).key);
        setStatus('playing');
        setLastCorrect(null);
    }

    function endRun() {
        setStatus('gameover');
    }

    useEffect(() => {
        if (status === 'revealed' && lastCorrect === false) {
            endRun();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [status, lastCorrect]);

    return (
        <div className="page fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="swap_vert" /></span>
                    Higher or Lower
                    <InfoTooltip label="How this works" title="Career totals, honestly labeled">
                        Each round rotates a random career stat (points, rebounds, assists, or games played) and asks
                        whether the next player's total is higher or lower than the current one's. These are
                        estimated career totals (season PPG/RPG/APG × games played, summed) across every season
                        this project's database actually has (2010–present) — not an official full-career number
                        the way basketball-reference would report for an older player, since this project's data
                        pipeline doesn't go back that far. Pool: players with ≥150 career games in that window.
                    </InfoTooltip>
                </h2>
                <p className="page-subtitle">Chain correct guesses to build a streak. One wrong guess ends the run.</p>

                {loadError && <p className="error-message">{loadError}</p>}

                <div style={{ display: 'flex', gap: '1.5rem', marginTop: '0.75rem', flexWrap: 'wrap' }}>
                    <div className="page-subtitle" style={{ margin: 0 }}>
                        Streak: <span className="text-accent" style={{ fontWeight: 700 }}>{streak}</span>
                    </div>
                    <div className="page-subtitle" style={{ margin: 0 }}>
                        Best: <span className="text-accent" style={{ fontWeight: 700 }}>{bestStreak}</span>
                    </div>
                </div>

                {pool && (status === 'idle' || status === 'gameover') && (
                    <button type="button" className="input-field" style={{ marginTop: '1rem', cursor: 'pointer', maxWidth: 220 }} onClick={startGame}>
                        <Icon name="play_arrow" size="1em" style={{ verticalAlign: 'middle', marginRight: 4 }} />
                        {status === 'gameover' ? 'Play Again' : 'Start'}
                    </button>
                )}
            </div>

            <AnimatePresence mode="wait">
                {status === 'gameover' && (
                    <motion.div
                        key="gameover"
                        className="dashboard-card"
                        style={{ marginTop: '1rem', textAlign: 'center' }}
                        initial={isAdvanced ? { opacity: 0, y: -10 } : false}
                        animate={{ opacity: 1, y: 0 }}
                        transition={preset.spring}
                    >
                        <h3 className="section-heading" style={{ marginTop: 0 }}>
                            Run over — final streak: {streak}
                        </h3>
                        <p className="page-subtitle">
                            {next.player_name}'s {statLabel.toLowerCase()} was {fmtValue(next[statKey])} vs {current.player_name}'s {fmtValue(current[statKey])}.
                        </p>
                    </motion.div>
                )}
            </AnimatePresence>

            {current && next && (status === 'playing' || status === 'revealed') && (
                <motion.div
                    key={`${current.player_id}-${next.player_id}-${statKey}`}
                    className="dashboard-card"
                    style={{ marginTop: '1rem' }}
                    initial={isAdvanced ? { opacity: 0, y: 10 } : false}
                    animate={{ opacity: 1, y: 0 }}
                    transition={preset.spring}
                >
                    <div style={{ display: 'flex', gap: '1.5rem', justifyContent: 'center', alignItems: 'center', flexWrap: 'wrap' }}>
                        <PlayerCard player={current} statLabel={statLabel} value={current[statKey]} hidden={false} color="#38bdf8" />
                        <div style={{ textAlign: 'center' }}>
                            <div style={{ fontFamily: "'Fraunces', serif", fontWeight: 700, fontSize: '1.1rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>VS</div>
                            {status === 'playing' ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                                    <button type="button" className="input-field" style={{ cursor: 'pointer' }} onClick={() => guess('higher')}>
                                        <Icon name="arrow_upward" size="1em" style={{ verticalAlign: 'middle', marginRight: 4, color: '#facc15' }} /> Higher
                                    </button>
                                    <button type="button" className="input-field" style={{ cursor: 'pointer' }} onClick={() => guess('lower')}>
                                        <Icon name="arrow_downward" size="1em" style={{ verticalAlign: 'middle', marginRight: 4, color: '#facc15' }} /> Lower
                                    </button>
                                </div>
                            ) : (
                                lastCorrect && (
                                    <button type="button" className="input-field" style={{ cursor: 'pointer' }} onClick={advance}>
                                        <Icon name="check" size="1em" style={{ verticalAlign: 'middle', marginRight: 4, color: '#34d399' }} /> Next
                                    </button>
                                )
                            )}
                        </div>
                        <PlayerCard player={next} statLabel={statLabel} value={next[statKey]} hidden={status === 'playing'} color="#f87171" />
                    </div>
                </motion.div>
            )}
        </div>
    );
}
