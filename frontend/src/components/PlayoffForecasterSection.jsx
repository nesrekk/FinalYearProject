import React, { useEffect, useState } from 'react';
import { fetchPlayoffComparison, fetchLivePlayerSuggestions } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import PlayerHeadshot from './common/PlayerHeadshot';
import { STAT_GLOSSARY } from '../utils/statGlossary';

const ROWS = [
    { key: 'ts_pct', label: 'True Shooting %', pct: true },
    { key: 'usg_pct', label: 'Usage %', pct: true },
    { key: 'net_rating', label: 'Net Rating', pct: false },
    { key: 'ast_pct', label: 'Assist %', pct: true },
    { key: 'reb_pct', label: 'Rebound %', pct: true },
];

function fmt(v, pct, digits = 1) {
    if (v == null) return '—';
    return pct ? `${(v * 100).toFixed(digits)}%` : v.toFixed(digits);
}

function deltaColor(v) {
    if (v == null) return 'var(--text-muted)';
    if (v > 0.005) return '#34d399';
    if (v < -0.005) return '#f87171';
    return 'var(--text-secondary)';
}

export default function PlayoffForecasterSection() {
    const [searchInput, setSearchInput] = useState('Jayson Tatum');
    const [suggestions, setSuggestions] = useState([]);
    const [playerName, setPlayerName] = useState('Jayson Tatum');
    const [season, setSeason] = useState(2025);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const query = searchInput.trim();
        if (query.length < 2 || query.toLowerCase() === playerName.toLowerCase()) {
            setSuggestions([]);
            return;
        }
        let active = true;
        const timer = setTimeout(async () => {
            try {
                const res = await fetchLivePlayerSuggestions(query, 8);
                if (active) setSuggestions(res?.results ?? []);
            } catch {
                if (active) setSuggestions([]);
            }
        }, 200);
        return () => { active = false; clearTimeout(timer); };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [searchInput]);

    useEffect(() => {
        let active = true;
        setLoading(true);
        setError('');
        (async () => {
            try {
                const res = await fetchPlayoffComparison(playerName, season);
                if (active) setData(res);
            } catch (e) {
                if (active) {
                    setData(null);
                    setError(e?.response?.data?.detail || 'Could not load playoff comparison.');
                }
            } finally {
                if (active) setLoading(false);
            }
        })();
        return () => { active = false; };
    }, [playerName, season]);

    function pick(name) {
        setSearchInput(name);
        setSuggestions([]);
        setPlayerName(name);
    }

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Playoff Drop-off Forecaster
                    <InfoTooltip label="How this works" title="Real before/after, no predictive model">
                        Regular-season stats come from this project's database; playoff stats are live-fetched
                        from the NBA's own advanced stats (real games, same season). This just shows what
                        actually happened — no regression, no predicted "playoff tax," since a real predictive
                        model would need far more historical playoff data than a season-by-season live fetch can
                        responsibly gather. Playoff sample sizes are often well under 20 games (sometimes as few
                        as 4), so a small-sample warning shows whenever that's the case — don't read too much
                        into a 4-game sweep.
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
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
                    />
                </div>
                {error && <p className="error-message" style={{ marginTop: '0.5rem' }}>{error}</p>}
            </div>

            {loading && <Loader />}

            {!loading && data && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <div className="entity-row" style={{ marginBottom: '0.75rem' }}>
                        <PlayerHeadshot playerId={data.player_id} playerName={data.player_name} size={48} />
                        <div className="entity-row-text">
                            <span className="entity-row-name">{data.player_name}</span>
                            <span className="entity-row-sub">
                                {data.regular_season.team_abbreviation} · {data.regular_season.gp} regular-season games
                                {data.playoffs && ` · ${data.playoffs.gp} real playoff games`}
                            </span>
                        </div>
                    </div>

                    {!data.playoffs ? (
                        <p className="page-subtitle" style={{ margin: 0 }}>{data.note}</p>
                    ) : (
                        <>
                            {data.small_sample_warning && (
                                <p className="page-subtitle" style={{ color: '#facc15', marginTop: 0 }}>
                                    <Icon name="warning" size="0.9em" style={{ verticalAlign: 'middle', marginRight: 4 }} />
                                    Only {data.playoffs.gp} real playoff games this season — treat this comparison as noisy.
                                </p>
                            )}
                            <div className="hb-table-wrapper table-wrapper">
                                <table className="data-table">
                                    <thead>
                                        <tr>
                                            <th>Stat</th>
                                            <th>Regular Season</th>
                                            <th>Playoffs (real)</th>
                                            <th>Change</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {ROWS.map((r) => {
                                            const def = STAT_GLOSSARY[r.key];
                                            const delta = data.deltas[r.key];
                                            return (
                                                <tr key={r.key}>
                                                    <td>
                                                        {r.label}
                                                        {def && (
                                                            <InfoTooltip label={`What is ${def.title}?`} title={def.title}>
                                                                {def.formula && <><code className="stat-formula">{def.formula}</code><br /></>}
                                                                {def.body}
                                                            </InfoTooltip>
                                                        )}
                                                    </td>
                                                    <td>{fmt(data.regular_season[r.key], r.pct)}</td>
                                                    <td>{fmt(data.playoffs[r.key], r.pct)}</td>
                                                    <td style={{ color: deltaColor(delta), fontWeight: 700 }}>
                                                        {delta != null && (
                                                            <Icon
                                                                name={delta > 0 ? 'arrow_upward' : delta < 0 ? 'arrow_downward' : 'remove'}
                                                                size="0.9em"
                                                                style={{ verticalAlign: 'middle', marginRight: 2 }}
                                                            />
                                                        )}
                                                        {fmt(delta, r.pct)}
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
}
