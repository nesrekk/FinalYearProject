import React, { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { mockTeamStats, allTeams } from '../../services/mockData';
import { fetchCurrentMeta } from '../../services/api';
import TeamLogo from '../common/TeamLogo';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';

const statLabels = [
    { key: 'ppg', label: 'Points Per Game', max: 130 },
    { key: 'rpg', label: 'Rebounds Per Game', max: 55 },
    { key: 'apg', label: 'Assists Per Game', max: 35 },
    { key: 'fgPct', label: 'FG %', max: 55 },
    { key: 'threePct', label: '3PT %', max: 45 },
    { key: 'ftPct', label: 'FT %', max: 90 },
    { key: 'spg', label: 'Steals Per Game', max: 12 },
    { key: 'bpg', label: 'Blocks Per Game', max: 8 },
];

export default function TeamComparison() {
    const [teamStats, setTeamStats] = useState(mockTeamStats);
    const [teamKeys, setTeamKeys] = useState(allTeams);
    const [teamA, setTeamA] = useState('LAL');
    const [teamB, setTeamB] = useState('BOS');
    const [loading, setLoading] = useState(false);

    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);

    useEffect(() => {
        let active = true;
        async function loadCurrent() {
            setLoading(true);
            try {
                const meta = await fetchCurrentMeta();
                const incoming = meta?.team_stats;
                if (active && incoming && Object.keys(incoming).length > 0) {
                    const keys = Object.keys(incoming).sort();
                    setTeamStats(incoming);
                    setTeamKeys(keys);
                    if (!incoming[teamA]) setTeamA(keys[0]);
                    if (!incoming[teamB]) setTeamB(keys[1] || keys[0]);
                }
            } catch {
                // keep fallback data
            } finally {
                if (active) setLoading(false);
            }
        }
        loadCurrent();
        return () => {
            active = false;
        };
    }, []);

    const statsA = teamStats[teamA];
    const statsB = teamStats[teamB];
    const canRender = useMemo(() => Boolean(statsA && statsB), [statsA, statsB]);
    const matchupKey = `${teamA}-${teamB}-${isAdvanced}`;

    return (
        <div className="page page-teams fade-in">
            {loading && <p className="page-subtitle" style={{ marginBottom: '0.75rem' }}>Loading current season team stats...</p>}
            {/* Team Selectors */}
            <div className="comparison-selectors">
                <div className="selector-group">
                    <select
                        className="team-select"
                        value={teamA}
                        onChange={(e) => setTeamA(e.target.value)}
                    >
                        {teamKeys.map((t) => (
                            <option key={t} value={t}>{teamStats[t].name}</option>
                        ))}
                    </select>
                </div>

                <span className="vs-divider">VS</span>

                <div className="selector-group">
                    <select
                        className="team-select"
                        value={teamB}
                        onChange={(e) => setTeamB(e.target.value)}
                    >
                        {teamKeys.map((t) => (
                            <option key={t} value={t}>{teamStats[t].name}</option>
                        ))}
                    </select>
                </div>
            </div>

            {canRender && (
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '0.75rem' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <TeamLogo abbreviation={statsA.abbr} size={28} />
                        <span className="page-subtitle" style={{ margin: 0 }}>{statsA.name}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span className="page-subtitle" style={{ margin: 0 }}>{statsB.name}</span>
                        <TeamLogo abbreviation={statsB.abbr} size={28} />
                    </div>
                </div>
            )}

            {/* Comparison Bars */}
            {canRender && (
                <div className="comparison-bars" key={matchupKey}>
                    {statLabels.map(({ key, label, max }, i) => {
                        const valA = statsA[key];
                        const valB = statsB[key];
                        const pctA = (valA / max) * 100;
                        const pctB = (valB / max) * 100;
                        const winner = valA > valB ? 'A' : valB > valA ? 'B' : null;
                        const delay = isAdvanced ? i * 0.04 : 0;

                        return (
                            <div key={key} className="comparison-row">
                                <span className={`comp-value comp-value--left ${winner === 'A' ? 'comp-value--winner' : ''}`}>
                                    {valA}
                                </span>
                                <div className="comp-bar-group">
                                    <div className="comp-bar comp-bar--left">
                                        <motion.div
                                            className="comp-bar-fill comp-bar-fill--a"
                                            initial={{ width: 0 }}
                                            animate={{ width: `${pctA}%` }}
                                            transition={{ ...preset.tableTransition, delay }}
                                        />
                                    </div>
                                    <span className="comp-label">{label}</span>
                                    <div className="comp-bar comp-bar--right">
                                        <motion.div
                                            className="comp-bar-fill comp-bar-fill--b"
                                            initial={{ width: 0 }}
                                            animate={{ width: `${pctB}%` }}
                                            transition={{ ...preset.tableTransition, delay }}
                                        />
                                    </div>
                                </div>
                                <span className={`comp-value comp-value--right ${winner === 'B' ? 'comp-value--winner' : ''}`}>
                                    {valB}
                                </span>
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
