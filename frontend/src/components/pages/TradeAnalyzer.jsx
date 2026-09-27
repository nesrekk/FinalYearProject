import React, { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { fetchTradeTeams, fetchTradeRoster, simulateTrade } from '../../services/api';
import TradeContractValue from '../common/TradeContractValue';
import Loader from '../Loader';
import InfoTooltip from '../common/InfoTooltip';
import Icon from '../common/Icon';
import TeamLogo from '../common/TeamLogo';
import PlayerHeadshot from '../common/PlayerHeadshot';
import { STAT_GLOSSARY } from '../../utils/statGlossary';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';
import TableExport from '../common/TableExport';

function StatLabel({ statKey, children }) {
    const def = STAT_GLOSSARY[statKey];
    if (!def) return children;
    return (
        <>
            {children}
            <InfoTooltip label={`What is ${def.title}?`} title={def.title}>
                {def.formula && <><code className="stat-formula">{def.formula}</code><br /></>}
                {def.body}
            </InfoTooltip>
        </>
    );
}

function fmt(v, digits = 1) {
    return v == null ? '—' : Number(v).toFixed(digits);
}

function DeltaValue({ before, after, digits = 1, higherIsBetter = true }) {
    if (before == null || after == null) return <span>—</span>;
    const delta = after - before;
    const improved = higherIsBetter ? delta > 0 : delta < 0;
    const flat = Math.abs(delta) < 0.005;
    const color = flat ? 'var(--text-3)' : (improved ? 'var(--positive)' : 'var(--negative)');
    const sign = delta >= 0 ? '+' : '';
    return (
        <span className="hb-delta">
            {fmt(after, digits)}{' '}
            <span style={{ color, fontSize: '0.85em' }}>({sign}{fmt(delta, digits)})</span>
        </span>
    );
}

function PlayerCard({ label, player }) {
    if (!player) return null;
    return (
        <div className="hb-trade-card">
            <div className="page-subtitle" style={{ marginBottom: 6 }}>{label}</div>
            <span className="entity-row">
                <PlayerHeadshot playerId={player.player_id} playerName={player.player_name} size={36} />
                <span className="entity-row-text">
                    <span className="hb-trade-name">{player.player_name}</span>
                    <span className="entity-row-sub">
                        {player.archetype || 'Unclustered'} · {fmt(player.pts)} pts · {fmt(player.reb)} reb · {fmt(player.ast)} ast · {fmt(player.min)} mpg
                    </span>
                </span>
            </span>
        </div>
    );
}

function TeamPanel({ side, isAdvanced, preset }) {
    if (!side) return null;
    const { sends, receives, fit_note: fitNote } = side.trade;
    const { before, after } = side.summary;
    return (
        <motion.div
            className="dashboard-card"
            style={{ flex: 1, minWidth: 320 }}
            initial={isAdvanced ? { opacity: 0, y: 12 } : false}
            animate={{ opacity: 1, y: 0 }}
            transition={preset.spring}
        >
            <h3 className="hb-trade-heading">
                <TeamLogo abbreviation={side.trade.team} size={24} />
                {side.trade.team}
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginBottom: '1rem' }}>
                <PlayerCard label="Trading Away" player={sends} />
                <PlayerCard label="Acquiring" player={receives} />
            </div>
            {fitNote && (
                <p className="page-subtitle" style={{ marginBottom: '1rem' }}>
                    Stylistic fit: {receives.player_name}'s incoming profile is most similar to existing
                    teammate <strong>{fitNote.player_name}</strong> ({(fitNote.similarity_score * 100).toFixed(0)}% match).
                </p>
            )}
            {before.predicted_win_pct != null && (
                <div className="hb-hero" style={{ marginBottom: '1rem' }}>
                    <span className="hb-row-avatar">
                        <TeamLogo abbreviation={side.trade.team} size={40} />
                    </span>
                    <div className="hb-hero-text">
                        <div className="hb-hero-label">Predicted Win% — was {(before.predicted_win_pct * 100).toFixed(1)}%</div>
                        <div className="hb-hero-name" style={{ whiteSpace: 'normal', fontSize: '0.78rem', fontFamily: 'inherit', fontWeight: 500, color: 'var(--text-secondary)' }}>
                            model R²=0.92, avg. error ~2.5 wins over 82 games
                        </div>
                    </div>
                    <div className="hb-hero-value" style={{ fontSize: '1.4rem' }}>
                        <DeltaValue before={before.predicted_win_pct * 100} after={after.predicted_win_pct * 100} digits={1} />%
                    </div>
                </div>
            )}
            <TableExport />
            <div className="hb-table-wrapper table-wrapper">
                <table className="data-table">
                    <thead>
                        <tr><th>Roster Metric (min-weighted)</th><th>Before → After</th></tr>
                    </thead>
                    <tbody>
                        <tr><td><StatLabel statKey="net_rating">Net Rating</StatLabel></td><td><DeltaValue before={before.net_rating} after={after.net_rating} /></td></tr>
                        <tr><td><StatLabel statKey="off_rating">Off. Rating</StatLabel></td><td><DeltaValue before={before.off_rating} after={after.off_rating} /></td></tr>
                        <tr><td><StatLabel statKey="def_rating">Def. Rating</StatLabel></td><td><DeltaValue before={before.def_rating} after={after.def_rating} higherIsBetter={false} /></td></tr>
                        <tr><td><StatLabel statKey="ts_pct">TS%</StatLabel></td><td><DeltaValue before={before.ts_pct} after={after.ts_pct} digits={3} /></td></tr>
                        <tr><td><StatLabel statKey="impact_score">Total Impact Score (roster sum)</StatLabel></td><td><DeltaValue before={before.total_impact_raw} after={after.total_impact_raw} digits={2} /></td></tr>
                    </tbody>
                </table>
            </div>
        </motion.div>
    );
}

export default function TradeAnalyzer() {
    const [season, setSeason] = useState(2024);
    const [teams, setTeams] = useState([]);

    const [teamA, setTeamA] = useState('');
    const [teamB, setTeamB] = useState('');
    const [rosterA, setRosterA] = useState([]);
    const [rosterB, setRosterB] = useState([]);
    const [playerAId, setPlayerAId] = useState('');
    const [playerBId, setPlayerBId] = useState('');

    const [result, setResult] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);

    useEffect(() => {
        let active = true;
        fetchTradeTeams(season)
            .then((data) => { if (active) setTeams(data.teams || []); })
            .catch(() => { if (active) setTeams([]); });
        return () => { active = false; };
    }, [season]);

    useEffect(() => {
        setPlayerAId('');
        if (!teamA) { setRosterA([]); return; }
        let active = true;
        fetchTradeRoster(teamA, season)
            .then((data) => { if (active) setRosterA(data.roster || []); })
            .catch(() => { if (active) setRosterA([]); });
        return () => { active = false; };
    }, [teamA, season]);

    useEffect(() => {
        setPlayerBId('');
        if (!teamB) { setRosterB([]); return; }
        let active = true;
        fetchTradeRoster(teamB, season)
            .then((data) => { if (active) setRosterB(data.roster || []); })
            .catch(() => { if (active) setRosterB([]); });
        return () => { active = false; };
    }, [teamB, season]);

    const canSimulate = teamA && teamB && playerAId && playerBId && teamA !== teamB;

    async function handleSimulate() {
        setLoading(true);
        setError('');
        setResult(null);
        try {
            const data = await simulateTrade({
                season, teamA, playerAId: Number(playerAId), teamB, playerBId: Number(playerBId),
            });
            setResult(data);
        } catch (e) {
            setError(e?.response?.data?.detail || 'Failed to simulate trade.');
        } finally {
            setLoading(false);
        }
    }

    const panels = useMemo(() => {
        if (!result) return null;
        return {
            a: { trade: result.trade.team_a, summary: result.team_a_summary },
            b: { trade: result.trade.team_b, summary: result.team_b_summary },
        };
    }, [result]);

    return (
        <div className="page page-trade fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="swap_horiz" /></span>
                    Trade Analyzer
                    <InfoTooltip label="How this works" title="What this simulates">
                        Swaps two players and recomputes each roster's minute-weighted net/offensive/defensive
                        rating, true-shooting %, and total impact score using their own historical stats as a
                        stand-in for their contribution. It does NOT simulate role, usage, or minutes changing
                        in the new context — read it as a roster production balance estimate, not a real
                        on-court projection.
                    </InfoTooltip>
                </h2>
                <p className="page-subtitle">
                    Pick a season and two teams, then a player from each roster to propose a straight
                    1-for-1 trade.
                </p>

                <div className="input-row">
                    <input
                        type="number"
                        className="input-field"
                        value={season}
                        onChange={(e) => setSeason(Number(e.target.value))}
                        min={2010}
                        max={2026}
                    />
                    <select className="input-field" value={teamA} onChange={(e) => setTeamA(e.target.value)}>
                        <option value="">Team A…</option>
                        {teams.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                    <select className="input-field" value={teamB} onChange={(e) => setTeamB(e.target.value)}>
                        <option value="">Team B…</option>
                        {teams.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                </div>

                <div className="input-row">
                    <select
                        className="input-field"
                        value={playerAId}
                        onChange={(e) => setPlayerAId(e.target.value)}
                        disabled={!rosterA.length}
                    >
                        <option value="">{teamA ? `Player from ${teamA}…` : 'Pick Team A first'}</option>
                        {rosterA.map((p) => (
                            <option key={p.player_id} value={p.player_id}>{p.player_name} ({fmt(p.pts)} ppg)</option>
                        ))}
                    </select>
                    <select
                        className="input-field"
                        value={playerBId}
                        onChange={(e) => setPlayerBId(e.target.value)}
                        disabled={!rosterB.length}
                    >
                        <option value="">{teamB ? `Player from ${teamB}…` : 'Pick Team B first'}</option>
                        {rosterB.map((p) => (
                            <option key={p.player_id} value={p.player_id}>{p.player_name} ({fmt(p.pts)} ppg)</option>
                        ))}
                    </select>
                    <button
                        type="button"
                        className="btn-primary"
                        disabled={!canSimulate || loading}
                        onClick={handleSimulate}
                    >
                        {loading ? 'Simulating…' : 'Simulate Trade'}
                    </button>
                </div>
                {teamA && teamA === teamB && (
                    <p className="error-message" style={{ marginTop: '0.5rem' }}>Pick two different teams.</p>
                )}
                {error && <p className="error-message" style={{ marginTop: '0.5rem' }}>{error}</p>}
            </div>

            {loading && <Loader />}

            {panels && (
                <>
                    <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginTop: '1rem' }}>
                        <TeamPanel side={panels.a} isAdvanced={isAdvanced} preset={preset} />
                        <TeamPanel side={panels.b} isAdvanced={isAdvanced} preset={preset} />
                    </div>
                    <p className="page-subtitle" style={{ marginTop: '1rem' }}>{result.caveat}</p>
                    <TradeContractValue
                        season={season}
                        players={[
                            { id: Number(playerAId), name: rosterA.find((p) => p.player_id === Number(playerAId))?.player_name || 'Player A' },
                            { id: Number(playerBId), name: rosterB.find((p) => p.player_id === Number(playerBId))?.player_name || 'Player B' },
                        ]}
                    />
                </>
            )}
        </div>
    );
}
