import React, { useEffect, useState } from 'react';
import { fetchDraftProspectComp, fetchLivePlayerSuggestions } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import PlayerHeadshot from './common/PlayerHeadshot';
import LengthMattersCard from './LengthMattersCard';
import TableExport from './common/TableExport';

function fmt(v, digits = 1) {
    return v == null ? '—' : v.toFixed(digits);
}

function fmtPct(v) {
    return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}

function fmtInches(v) {
    if (v == null) return '—';
    const feet = Math.floor(v / 12);
    const inches = (v % 12).toFixed(1);
    return `${feet}'${inches}" (${v.toFixed(1)}")`;
}

export default function DraftProspectSection() {
    const [searchInput, setSearchInput] = useState('Zion Williamson');
    const [suggestions, setSuggestions] = useState([]);
    const [playerName, setPlayerName] = useState('Zion Williamson');
    const [includeMeasurements, setIncludeMeasurements] = useState(false);
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
                const res = await fetchDraftProspectComp(playerName, null, 5, includeMeasurements);
                if (active) setData(res);
            } catch (e) {
                if (active) {
                    setData(null);
                    setError(e?.response?.data?.detail || 'Could not find college data for this player.');
                }
            } finally {
                if (active) setLoading(false);
            }
        })();
        return () => { active = false; };
    }, [playerName, includeMeasurements]);

    function pick(name) {
        setSearchInput(name);
        setSuggestions([]);
        setPlayerName(name);
    }

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Draft Prospect Comp Finder
                    <InfoTooltip label="How this works" title="Real college comps, real NBA outcomes">
                        Real D1 college season stats (CollegeBasketballData.com, ~105,000 player-seasons,
                        2014-2025), era-normalized within that player's own college season the same way this
                        project normalizes NBA seasons elsewhere. Comps are real players whose real college
                        season can ALSO be matched by name to a real NBA rookie season in this project's own
                        database (~55% of NBA rookies since 2015 — international players and G-League/
                        draft-and-stash players never show up in US college data, a real gap, not a bug). The
                        projected outcome is a similarity-weighted average of those comps' REAL rookie numbers,
                        never a trained or predicted model. College-side metrics (usage, net rating, etc.) use a
                        different methodology than this project's own NBA-side metrics of the same name —
                        they're only ever compared to other college seasons, never mixed with NBA numbers. This
                        method also can't see draft position, physical measurables, or competition level, so an
                        outlier talent's real outcome can look very different from what pure production comps
                        suggest — that's a real limitation, not something this tool papers over.
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
                </h3>
                <div style={{ position: 'relative', maxWidth: 360 }}>
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
                            background: 'var(--surface)', border: '2px solid var(--line)', borderRadius: 0, boxShadow: 'var(--shadow-card)',
                            marginTop: 4, maxHeight: 220, overflowY: 'auto', listStyle: 'none', padding: 0,
                        }}>
                            {suggestions.map((name) => (
                                <li key={name}>
                                    <button
                                        type="button"
                                        onClick={() => pick(name)}
                                        style={{ display: 'block', width: '100%', textAlign: 'left', padding: '0.5rem 0.75rem', background: 'transparent', border: 'none', color: 'var(--text)', cursor: 'pointer' }}
                                    >
                                        {name}
                                    </button>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: '0.75rem', cursor: 'pointer' }}>
                    <input
                        type="checkbox"
                        checked={includeMeasurements}
                        onChange={(e) => setIncludeMeasurements(e.target.checked)}
                    />
                    <span className="page-subtitle" style={{ margin: 0 }}>
                        Also match on real NBA Draft Combine measurements (wingspan, standing reach)
                    </span>
                </label>
                {error && <p className="error-message" style={{ marginTop: '0.5rem' }}>{error}</p>}
            </div>

            {loading && <Loader />}

            {!loading && data && (
                <>
                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <div className="entity-row" style={{ marginBottom: '0.75rem' }}>
                            <PlayerHeadshot playerId={data.prospect.nba_player_id} playerName={data.prospect.name} size={48} />
                            <div className="entity-row-text">
                                <span className="entity-row-name">{data.prospect.name}</span>
                                <span className="entity-row-sub">
                                    {data.prospect.team} · {data.prospect.college_season - 1}-{String(data.prospect.college_season).slice(-2)} · {data.prospect.games} real college games
                                </span>
                            </div>
                        </div>
                        <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
                            <span className="page-subtitle">PPG <strong style={{ color: 'var(--text-primary)' }}>{fmt(data.prospect.ppg)}</strong></span>
                            <span className="page-subtitle">APG <strong style={{ color: 'var(--text-primary)' }}>{fmt(data.prospect.apg)}</strong></span>
                            <span className="page-subtitle">RPG <strong style={{ color: 'var(--text-primary)' }}>{fmt(data.prospect.rpg)}</strong></span>
                            <span className="page-subtitle">Usage <strong style={{ color: 'var(--text-primary)' }}>{fmt(data.prospect.usage)}</strong></span>
                            <span className="page-subtitle">TS% <strong style={{ color: 'var(--text-primary)' }}>{fmtPct(data.prospect.ts_pct)}</strong></span>
                            <span className="page-subtitle">Net Rtg <strong style={{ color: 'var(--text-primary)' }}>{fmt(data.prospect.net_rating)}</strong></span>
                        </div>
                        {data.prospect.combine_measurements ? (
                            <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', marginTop: '0.6rem', paddingTop: '0.6rem', borderTop: '1px solid var(--border)' }}>
                                <span className="page-subtitle">Height (no shoes) <strong style={{ color: 'var(--text-primary)' }}>{fmtInches(data.prospect.combine_measurements.height_wo_shoes)}</strong></span>
                                <span className="page-subtitle">Wingspan <strong style={{ color: 'var(--text-primary)' }}>{fmtInches(data.prospect.combine_measurements.wingspan)}</strong></span>
                                <span className="page-subtitle">Standing Reach <strong style={{ color: 'var(--text-primary)' }}>{fmtInches(data.prospect.combine_measurements.standing_reach)}</strong></span>
                                <span className="page-subtitle">Weight <strong style={{ color: 'var(--text-primary)' }}>{data.prospect.combine_measurements.weight != null ? `${data.prospect.combine_measurements.weight.toFixed(1)} lbs` : '—'}</strong></span>
                            </div>
                        ) : (
                            <p className="page-subtitle" style={{ marginTop: '0.6rem', paddingTop: '0.6rem', borderTop: '1px solid var(--border)' }}>
                                No real NBA Draft Combine measurements on file for this prospect (didn't attend, or this is before combine data was tracked here).
                            </p>
                        )}
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>
                            Projected NBA Rookie Season
                            <span className="page-subtitle" style={{ marginLeft: 8, fontWeight: 400 }}>
                                (comp-weighted average of real outcomes · bridge pool: {data.bridge_pool_size} real players)
                            </span>
                        </h3>
                        <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
                            <span className="page-subtitle">PTS <strong style={{ color: 'var(--text)' }}>{fmt(data.projected_nba_rookie_outcome.pts)}</strong></span>
                            <span className="page-subtitle">TS% <strong style={{ color: 'var(--text)' }}>{fmtPct(data.projected_nba_rookie_outcome.ts_pct)}</strong></span>
                            <span className="page-subtitle">AST% <strong style={{ color: 'var(--text)' }}>{fmtPct(data.projected_nba_rookie_outcome.ast_pct)}</strong></span>
                            <span className="page-subtitle">REB% <strong style={{ color: 'var(--text)' }}>{fmtPct(data.projected_nba_rookie_outcome.reb_pct)}</strong></span>
                            <span className="page-subtitle">Net Rtg <strong style={{ color: 'var(--text)' }}>{fmt(data.projected_nba_rookie_outcome.net_rating)}</strong></span>
                        </div>
                    </div>

                    <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                        <h3 className="section-heading" style={{ marginTop: 0 }}>Real College Comps</h3>
                        <p className="page-subtitle" style={{ marginTop: '-0.5rem', marginBottom: '0.75rem' }}>{data.measurements_note}</p>
                        <TableExport />
                        <div className="hb-table-wrapper table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Player</th>
                                        <th>College Season</th>
                                        <th>Similarity</th>
                                        <th>Real NBA Rookie PTS</th>
                                        <th>Real NBA Rookie TS%</th>
                                        <th>Real NBA Rookie Net Rtg</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.comps.map((c) => (
                                        <tr key={`${c.name}-${c.college_season}`}>
                                            <td>
                                                <div className="entity-row">
                                                    <PlayerHeadshot playerId={c.nba_player_id} playerName={c.name} size={26} />
                                                    {c.name}
                                                </div>
                                            </td>
                                            <td>{c.team} ’{String(c.college_season).slice(-2)}</td>
                                            <td>{(c.similarity * 100).toFixed(1)}%</td>
                                            <td>{fmt(c.nba_rookie_outcome.pts)}</td>
                                            <td>{fmtPct(c.nba_rookie_outcome.ts_pct)}</td>
                                            <td>{fmt(c.nba_rookie_outcome.net_rating)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </>
            )}

            <LengthMattersCard />
        </div>
    );
}
