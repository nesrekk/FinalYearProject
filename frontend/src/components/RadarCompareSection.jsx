import React, { useEffect, useMemo, useState } from 'react';
import { fetchRadarProfile, fetchLivePlayerSuggestions } from '../services/api';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';

// Debounced suggestion fetch for one search slot, with a cancellation guard
// so an earlier keystroke's response can't resolve after a later one and
// clobber it (out-of-order async race — caught live: typing "Nikola Jokic"
// showed no dropdown because an earlier partial query's response sometimes
// resolved last and overwrote the correct one).
function useSlotSuggestions(query, resolvedName, setSuggestions) {
    useEffect(() => {
        const q = (query || '').trim();
        // Skip re-searching for a name that's already been picked for this
        // slot — otherwise selecting a suggestion re-triggers this effect
        // (searchValues[index] changes to the full name) and reopens a
        // stray one-item dropdown right after picking.
        if (q.length < 2 || q.toLowerCase() === (resolvedName || '').toLowerCase()) {
            setSuggestions([]);
            return;
        }
        let active = true;
        const timer = setTimeout(async () => {
            try {
                const data = await fetchLivePlayerSuggestions(q, 6);
                if (active) setSuggestions(data?.results ?? []);
            } catch {
                if (active) setSuggestions([]);
            }
        }, 200);
        return () => {
            active = false;
            clearTimeout(timer);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [query, resolvedName]);
}

const PALETTE = ['#38bdf8', '#f87171', '#facc15'];
const STAT_LABELS = {
    pts: 'PTS', reb: 'REB', ast: 'AST', stl: 'STL', blk: 'BLK', ts_pct: 'TS%', usg_pct: 'USG%',
    shooting_proficiency: 'Shot Proficiency', spacing: 'Spacing',
    defensive_impact: 'Defensive Impact', rad_per_game: 'Rim Attempts Defended/g',
};
const AXES = Object.keys(STAT_LABELS);

const SIZE = 420;
const CENTER = SIZE / 2;
const MAX_RADIUS = SIZE / 2 - 60;

// total = how many axes are actually being drawn right now — dynamic, not
// AXES.length, so an axis with no data for any compared player (e.g.
// defensive_impact/rad_per_game before scripts/build_defense_tracking_stats.py
// has been run) can be left out entirely instead of defaulting to 0, which
// would misleadingly render as "0th percentile" rather than "no data yet".
function axisAngle(i, total) {
    return -Math.PI / 2 + (i * 2 * Math.PI) / total;
}

function axisPoint(i, total, value0to100) {
    const angle = axisAngle(i, total);
    const r = (Math.max(0, Math.min(100, value0to100)) / 100) * MAX_RADIUS;
    return { x: CENTER + r * Math.cos(angle), y: CENTER + r * Math.sin(angle) };
}

function ringPolygonPoints(total, fraction) {
    return Array.from({ length: total }, (_, i) => {
        const p = axisPoint(i, total, fraction * 100);
        return `${p.x},${p.y}`;
    }).join(' ');
}

function PlayerSlot({ index, color, entry, onSearch, onPick, onRemove, suggestions, searchValue }) {
    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flex: 1, minWidth: 180 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, flexShrink: 0 }} />
                <div style={{ position: 'relative', flex: 1 }}>
                    <input
                        type="text"
                        className="input-field"
                        placeholder={`Player ${index + 1}…`}
                        value={searchValue}
                        onChange={(e) => onSearch(e.target.value)}
                    />
                    {suggestions?.length > 0 && (
                        <ul className="autocomplete-list" style={{
                            position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 10,
                            background: '#1e293b', border: '1px solid #334155', borderRadius: 6,
                            marginTop: 4, maxHeight: 200, overflowY: 'auto', listStyle: 'none', padding: 0,
                        }}>
                            {suggestions.map((name) => (
                                <li key={name}>
                                    <button
                                        type="button"
                                        onClick={() => onPick(name)}
                                        style={{ display: 'block', width: '100%', textAlign: 'left', padding: '0.4rem 0.7rem', background: 'transparent', border: 'none', color: '#e2e8f0', cursor: 'pointer' }}
                                    >
                                        {name}
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
                {entry && (
                    <button type="button" onClick={onRemove} className="page-subtitle" style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: '1rem' }}>✕</button>
                )}
            </div>
            {entry?.error && <p className="error-message" style={{ fontSize: '0.75rem', margin: 0 }}>{entry.error}</p>}
        </div>
    );
}

export default function RadarCompareSection() {
    const [season, setSeason] = useState(2025);
    const [slots, setSlots] = useState([null, null, null]); // {playerName, data, error}
    const [searchValues, setSearchValues] = useState(['', '', '']);
    const [suggestionsBySlot, setSuggestionsBySlot] = useState([[], [], []]);

    function handleSearch(index, value) {
        setSearchValues((prev) => { const next = [...prev]; next[index] = value; return next; });
    }

    function setSlotSuggestions(index, results) {
        setSuggestionsBySlot((prev) => { const next = [...prev]; next[index] = results; return next; });
    }
    // Three fixed slots -> three hook calls (unconditional, same order every
    // render, satisfies the Rules of Hooks).
    useSlotSuggestions(searchValues[0], slots[0]?.playerName, (results) => setSlotSuggestions(0, results));
    useSlotSuggestions(searchValues[1], slots[1]?.playerName, (results) => setSlotSuggestions(1, results));
    useSlotSuggestions(searchValues[2], slots[2]?.playerName, (results) => setSlotSuggestions(2, results));

    async function handlePick(index, playerName) {
        setSearchValues((prev) => { const next = [...prev]; next[index] = playerName; return next; });
        setSuggestionsBySlot((prev) => { const next = [...prev]; next[index] = []; return next; });
        try {
            const data = await fetchRadarProfile(playerName, season);
            setSlots((prev) => { const next = [...prev]; next[index] = { playerName: data.player_name, data }; return next; });
        } catch (e) {
            setSlots((prev) => {
                const next = [...prev];
                next[index] = { playerName, error: e?.response?.data?.detail || 'No data for this player/season.' };
                return next;
            });
        }
    }

    function handleRemove(index) {
        setSlots((prev) => { const next = [...prev]; next[index] = null; return next; });
        setSearchValues((prev) => { const next = [...prev]; next[index] = ''; return next; });
    }

    // Re-fetch every already-picked slot when the season changes — without
    // this, changing the season input silently did nothing for players
    // already added (handlePick only fires on selection, capturing the
    // season at that moment; nothing re-ran it afterward). Caught live:
    // typing a new season kept showing the old season's chart.
    useEffect(() => {
        slots.forEach((slot, index) => {
            if (!slot?.playerName) return;
            fetchRadarProfile(slot.playerName, season)
                .then((data) => {
                    setSlots((prev) => { const next = [...prev]; next[index] = { playerName: data.player_name, data }; return next; });
                })
                .catch((e) => {
                    setSlots((prev) => {
                        const next = [...prev];
                        next[index] = { playerName: slot.playerName, error: e?.response?.data?.detail || 'No data for this player/season.' };
                        return next;
                    });
                });
        });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [season]);

    const activeSlots = useMemo(() => slots.map((s, i) => ({ ...s, color: PALETTE[i] })).filter((s) => s?.data), [slots]);

    // Only draw axes that have real data for at least one compared player —
    // an axis nobody has data for (e.g. defensive tracking stats before
    // that batch script has been run) would otherwise default every player
    // to 0 on that axis, visually implying "0th percentile" rather than
    // "no data yet".
    const visibleAxes = useMemo(() => {
        return AXES.filter((axis) =>
            activeSlots.some((slot) => slot.data.stats.find((s) => s.stat === axis)?.percentile != null)
        );
    }, [activeSlots]);

    return (
        <section className="dashboard-card">
            <h2 className="card-title hb-page-title">
                <span className="card-icon"><Icon name="radar" /></span>
                Radar Comparison
                <InfoTooltip label="How this works" title="Percentile-based radar chart">
                    Each axis is the player's percentile rank (0-100) on that stat among all qualified players
                    (min≥15 mpg, gp≥20) that season — not the raw number. Percentiles put scoring, shooting %,
                    and everything else on the same 0-100 scale so the shapes are directly comparable; a raw-value
                    radar would be dominated by whichever stat happens to have the largest numbers.
                    <br /><br />
                    Shot Proficiency and Spacing are CraftedNBA-style metrics with disclosed formulas: Shot
                    Proficiency rewards 3-point volume AND accuracy together (so one hot streak on 1 attempt
                    can't outrank a real high-volume shooter), Spacing is an index of how much a player's
                    outside shooting pulls defenders away from the paint.
                </InfoTooltip>
                <SourceBadge source={activeSlots[0]?.data?._source} />
            </h2>
            <p className="page-subtitle">Compare up to 3 players' statistical profiles for one season.</p>

            <div className="input-row">
                <input
                    type="number"
                    className="input-field"
                    value={season}
                    onChange={(e) => setSeason(Number(e.target.value))}
                    min={2010}
                    max={2026}
                    style={{ maxWidth: 120 }}
                />
            </div>

            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginTop: '0.75rem', marginBottom: '1.5rem' }}>
                {[0, 1, 2].map((i) => (
                    <PlayerSlot
                        key={i}
                        index={i}
                        color={PALETTE[i]}
                        entry={slots[i]}
                        searchValue={searchValues[i]}
                        suggestions={suggestionsBySlot[i]}
                        onSearch={(v) => handleSearch(i, v)}
                        onPick={(name) => handlePick(i, name)}
                        onRemove={() => handleRemove(i)}
                    />
                ))}
            </div>

            {activeSlots.length > 0 && (
                <>
                    <div className="court-container">
                        <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="court-svg" style={{ maxHeight: 460 }}>
                            <rect x="0" y="0" width={SIZE} height={SIZE} fill="var(--surface-2)" rx="8" />
                            {[0.25, 0.5, 0.75, 1].map((f) => (
                                <polygon key={f} points={ringPolygonPoints(visibleAxes.length, f)} fill="none" stroke="var(--hairline)" strokeWidth="1" />
                            ))}
                            {visibleAxes.map((axis, i) => {
                                const outer = axisPoint(i, visibleAxes.length, 100);
                                const labelPt = axisPoint(i, visibleAxes.length, 118);
                                return (
                                    <React.Fragment key={axis}>
                                        <line x1={CENTER} y1={CENTER} x2={outer.x} y2={outer.y} stroke="var(--hairline)" strokeWidth="1" />
                                        <text
                                            x={labelPt.x} y={labelPt.y}
                                            fill="var(--text-2)" fontSize="12" textAnchor="middle" dominantBaseline="middle"
                                        >
                                            {STAT_LABELS[axis]}
                                        </text>
                                    </React.Fragment>
                                );
                            })}
                            {activeSlots.map((slot) => {
                                const byStat = Object.fromEntries(slot.data.stats.map((s) => [s.stat, s]));
                                const points = visibleAxes.map((axis, i) => {
                                    const p = axisPoint(i, visibleAxes.length, byStat[axis]?.percentile ?? 0);
                                    return `${p.x},${p.y}`;
                                }).join(' ');
                                return (
                                    <polygon
                                        key={slot.playerName}
                                        points={points}
                                        fill={slot.color}
                                        fillOpacity={0.18}
                                        stroke={slot.color}
                                        strokeWidth="2"
                                    />
                                );
                            })}
                        </svg>
                    </div>

                    <div className="table-wrapper" style={{ marginTop: '1rem' }}>
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Stat</th>
                                    {activeSlots.map((slot) => (
                                        <th key={slot.playerName} style={{ color: slot.color }}>{slot.playerName}</th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {AXES.map((axis) => (
                                    <tr key={axis}>
                                        <td>{STAT_LABELS[axis]}</td>
                                        {activeSlots.map((slot) => {
                                            const s = slot.data.stats.find((x) => x.stat === axis);
                                            return (
                                                <td key={slot.playerName}>
                                                    {s?.value != null ? s.value : '—'}
                                                    <span className="page-subtitle" style={{ marginLeft: 6 }}>
                                                        ({s?.percentile != null ? `${s.percentile}th` : '—'})
                                                    </span>
                                                </td>
                                            );
                                        })}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="page-subtitle" style={{ marginTop: '0.5rem' }}>
                        Pool size: {activeSlots[0]?.data.pool_size} qualified players that season.
                    </p>
                </>
            )}
        </section>
    );
}
