import React, { useEffect, useState } from 'react';
import { fetchPlayerTrajectory, fetchPlayerSuggestions } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';
import PlayerHeadshot from './common/PlayerHeadshot';

const CHART_W = 720, CHART_H = 320, PAD_L = 56, PAD_R = 20, PAD_T = 20, PAD_B = 36;

function buildChartPoints(data) {
    const actual = data.career.map((c) => ({ age: c.age, pts: c.pts, kind: 'actual' }));
    const projected = data.projection.map((p) => ({
        age: p.age, pts: p.projected_pts, ceiling: p.ceiling_pts, floor: p.floor_pts, kind: 'projected',
    }));
    const overlay = data.projection
        .filter((p) => p.actual_pts != null)
        .map((p) => ({ age: p.age, pts: p.actual_pts, kind: 'overlay' }));
    return { actual, projected, overlay };
}

function TrajectoryChart({ data }) {
    const { actual, projected, overlay } = buildChartPoints(data);
    const allAges = [...actual.map((p) => p.age), ...projected.map((p) => p.age)];
    const allValues = [
        ...actual.map((p) => p.pts),
        ...projected.flatMap((p) => [p.pts, p.ceiling, p.floor]),
        ...overlay.map((p) => p.pts),
    ].filter((v) => v != null);

    if (allAges.length < 2 || allValues.length === 0) {
        return <p className="empty-message">Not enough data to chart a trajectory.</p>;
    }

    const ageMin = Math.min(...allAges), ageMax = Math.max(...allAges);
    const valMin = Math.min(...allValues), valMax = Math.max(...allValues);
    const span = valMax - valMin || 1;
    const yMin = Math.max(0, valMin - span * 0.15);
    const yMax = valMax + span * 0.15;

    const plotW = CHART_W - PAD_L - PAD_R;
    const plotH = CHART_H - PAD_T - PAD_B;
    const xFor = (age) => PAD_L + ((age - ageMin) / (ageMax - ageMin || 1)) * plotW;
    const yFor = (v) => PAD_T + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

    const actualPath = actual.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xFor(p.age).toFixed(1)} ${yFor(p.pts).toFixed(1)}`).join(' ');

    // Cone band: connect the real last-actual point to the projected ceiling/floor series.
    const lastActual = actual[actual.length - 1];
    const bandTop = [lastActual, ...projected].map((p) => `${xFor(p.age).toFixed(1)},${yFor(p.ceiling ?? p.pts).toFixed(1)}`);
    const bandBottom = [lastActual, ...projected].map((p) => `${xFor(p.age).toFixed(1)},${yFor(p.floor ?? p.pts).toFixed(1)}`).reverse();
    const bandPath = `M ${bandTop.join(' L ')} L ${bandBottom.join(' L ')} Z`;

    const projPath = [lastActual, ...projected]
        .map((p, i) => `${i === 0 ? 'M' : 'L'} ${xFor(p.age).toFixed(1)} ${yFor(p.pts).toFixed(1)}`)
        .join(' ');

    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((t) => yMin + t * (yMax - yMin));

    return (
        <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} style={{ width: '100%', height: 'auto', display: 'block' }}>
            <rect x="0" y="0" width={CHART_W} height={CHART_H} fill="#1a2332" rx="8" />
            {yTicks.map((t, idx) => (
                <React.Fragment key={idx}>
                    <line x1={PAD_L} y1={yFor(t)} x2={CHART_W - PAD_R} y2={yFor(t)} stroke="#26344a" strokeWidth="1" />
                    <text x={PAD_L - 8} y={yFor(t) + 3} fill="#64748b" fontSize="10" textAnchor="end">{t.toFixed(0)}</text>
                </React.Fragment>
            ))}
            {[...new Set(allAges)].sort((a, b) => a - b).map((age) => (
                <text key={age} x={xFor(age)} y={CHART_H - PAD_B + 16} fill="#64748b" fontSize="9" textAnchor="middle">
                    Age {age}
                </text>
            ))}

            <path d={bandPath} fill="#a78bfa" fillOpacity={0.15} stroke="none" />
            <path d={actualPath} fill="none" stroke="#38bdf8" strokeWidth="2.5" />
            <path d={projPath} fill="none" stroke="#a78bfa" strokeWidth="2" strokeDasharray="5 4" />

            {actual.map((p) => (
                <circle key={`actual-${p.age}`} cx={xFor(p.age)} cy={yFor(p.pts)} r={3} fill="#38bdf8">
                    <title>Age {p.age} (real): {p.pts.toFixed(1)} pts</title>
                </circle>
            ))}
            {projected.map((p) => p.pts != null && (
                <circle key={`proj-${p.age}`} cx={xFor(p.age)} cy={yFor(p.pts)} r={3.5} fill="#a78bfa">
                    <title>Age {p.age} (projected): {p.pts.toFixed(1)} pts (comp range {p.floor?.toFixed(1)}-{p.ceiling?.toFixed(1)})</title>
                </circle>
            ))}
            {overlay.map((p) => (
                <circle key={`overlay-${p.age}`} cx={xFor(p.age)} cy={yFor(p.pts)} r={4} fill="none" stroke="#facc15" strokeWidth="2">
                    <title>Age {p.age} (what actually happened): {p.pts.toFixed(1)} pts</title>
                </circle>
            ))}
        </svg>
    );
}

export default function TrajectoryForecasterSection() {
    const [searchInput, setSearchInput] = useState('Anthony Edwards');
    const [suggestions, setSuggestions] = useState([]);
    const [playerName, setPlayerName] = useState('Anthony Edwards');
    const [season, setSeason] = useState(2022);
    const [projectYears, setProjectYears] = useState(3);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        const query = searchInput.trim();
        if (query.length < 2 || query.toLowerCase() === playerName.toLowerCase()) {
            setSuggestions([]);
            return;
        }
        let active = true;
        const timer = setTimeout(async () => {
            try {
                const res = await fetchPlayerSuggestions(query, 8);
                if (active) setSuggestions(res?.results ?? []);
            } catch {
                if (active) setSuggestions([]);
            }
        }, 200);
        return () => { active = false; clearTimeout(timer); };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [searchInput]);

    async function load(name, szn, years) {
        setLoading(true);
        setError('');
        try {
            const res = await fetchPlayerTrajectory(name, szn, 5, years);
            setData(res);
        } catch (e) {
            setData(null);
            setError(e?.response?.data?.detail || 'Could not build a trajectory for this player/season.');
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        load(playerName, season, projectYears);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [playerName, season, projectYears]);

    function pick(name) {
        setSearchInput(name);
        setSuggestions([]);
        setPlayerName(name);
    }

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Career Trajectory Forecaster
                    <InfoTooltip label="How this works" title="Real comps, real outcomes, no invented math">
                        Finds this player's closest real statistical matches at the same age (the same
                        era-normalized similarity engine Season Similarity uses), then plots what those REAL
                        comps actually did in their own following seasons — points-per-game weighted by how
                        similar each comp was. The shaded band is the real range between the best and worst
                        outcome among the comps, not a statistical confidence interval. If the player already
                        has real data for a projected age, that real outcome is overlaid (gold ring) so you can
                        see how the projection would have done. Every comp used is listed below — nothing here
                        is a black box.
                    </InfoTooltip>
                </h3>
                <div style={{ position: 'relative', display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'center' }}>
                    <div style={{ position: 'relative', flex: 1, minWidth: 220 }}>
                        <input
                            type="text"
                            className="input-field"
                            placeholder="Player name…"
                            value={searchInput}
                            onChange={(e) => setSearchInput(e.target.value)}
                            style={{ width: '100%' }}
                        />
                        {suggestions.length > 0 && (
                            <ul className="autocomplete-list" style={{
                                position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
                                background: '#1e293b', border: '1px solid #334155', borderRadius: 6,
                                marginTop: 4, maxHeight: 220, overflowY: 'auto', listStyle: 'none', padding: 0,
                            }}>
                                {suggestions.map((name) => (
                                    <li key={name}>
                                        <button
                                            type="button"
                                            onClick={() => pick(name)}
                                            style={{ display: 'block', width: '100%', textAlign: 'left', padding: '0.5rem 0.75rem', background: 'transparent', border: 'none', color: '#e2e8f0', cursor: 'pointer' }}
                                        >
                                            {name}
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                    </div>
                    <input
                        type="number"
                        className="input-field"
                        value={season}
                        onChange={(e) => setSeason(Number(e.target.value))}
                        min={2010}
                        max={2026}
                        style={{ maxWidth: 110 }}
                        title="Season"
                    />
                    <select
                        className="input-field"
                        value={projectYears}
                        onChange={(e) => setProjectYears(Number(e.target.value))}
                        style={{ maxWidth: 160 }}
                    >
                        <option value={1}>Project 1 year</option>
                        <option value={2}>Project 2 years</option>
                        <option value={3}>Project 3 years</option>
                        <option value={5}>Project 5 years</option>
                    </select>
                </div>
                {error && <p className="error-message" style={{ marginTop: '0.5rem' }}>{error}</p>}
            </div>

            {loading && <Loader />}

            {!loading && data && (
                <>
                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <div className="entity-row" style={{ marginBottom: '0.75rem' }}>
                            <PlayerHeadshot playerId={data.player_id} playerName={data.player_name} size={48} />
                            <div className="entity-row-text">
                                <span className="entity-row-name">{data.player_name}</span>
                                <span className="entity-row-sub">Age {data.current_age} in season {data.season} · projecting forward</span>
                            </div>
                        </div>
                        <TrajectoryChart data={data} />
                        <div style={{ display: 'flex', gap: '1.25rem', flexWrap: 'wrap', marginTop: '0.75rem', fontSize: '0.78rem' }}>
                            <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: '#38bdf8', marginRight: 5 }} /> Real career</span>
                            <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: '#a78bfa', marginRight: 5 }} /> Projected (comp-weighted)</span>
                            <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', border: '2px solid #facc15', marginRight: 5 }} /> What actually happened</span>
                        </div>
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>Comps Used</h3>
                        <div className="hb-table-wrapper table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Player</th>
                                        <th>Season</th>
                                        <th>Age</th>
                                        <th>Similarity</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.comps.map((c, i) => (
                                        <tr key={`${c.player_id}-${c.season}-${i}`}>
                                            <td>
                                                <div className="entity-row">
                                                    <PlayerHeadshot playerId={c.player_id} playerName={c.player_name} size={26} />
                                                    {c.player_name}
                                                </div>
                                            </td>
                                            <td>{c.season - 1}-{String(c.season).slice(-2)}</td>
                                            <td>{c.age}</td>
                                            <td>{(c.similarity * 100).toFixed(1)}%</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </>
            )}
        </div>
    );
}
