import React, { useEffect, useMemo, useState } from 'react';
import { mockTeamStats, allTeams } from '../../services/mockData';
import { fetchCurrentMeta } from '../../services/api';

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
                        {statsA.logo && <img src={statsA.logo} alt={statsA.abbr} style={{ width: '28px', height: '28px', objectFit: 'contain' }} />}
                        <span className="page-subtitle" style={{ margin: 0 }}>{statsA.name}</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span className="page-subtitle" style={{ margin: 0 }}>{statsB.name}</span>
                        {statsB.logo && <img src={statsB.logo} alt={statsB.abbr} style={{ width: '28px', height: '28px', objectFit: 'contain' }} />}
                    </div>
                </div>
            )}

            {/* Comparison Bars */}
            {canRender && (
                <div className="comparison-bars">
                    {statLabels.map(({ key, label, max }) => {
                        const valA = statsA[key];
                        const valB = statsB[key];
                        const pctA = (valA / max) * 100;
                        const pctB = (valB / max) * 100;
                        const winner = valA > valB ? 'A' : valB > valA ? 'B' : null;

                        return (
                            <div key={key} className="comparison-row">
                                <span className={`comp-value comp-value--left ${winner === 'A' ? 'comp-value--winner' : ''}`}>
                                    {valA}
                                </span>
                                <div className="comp-bar-group">
                                    <div className="comp-bar comp-bar--left">
                                        <div className="comp-bar-fill comp-bar-fill--a" style={{ width: `${pctA}%` }}></div>
                                    </div>
                                    <span className="comp-label">{label}</span>
                                    <div className="comp-bar comp-bar--right">
                                        <div className="comp-bar-fill comp-bar-fill--b" style={{ width: `${pctB}%` }}></div>
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
