import React, { useState } from 'react';
import { fetchRawImpact, fetchStarImpact, fetchBpmLeaderboard } from '../services/api';
import DataTable from './DataTable';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';
import SourceBadge from './common/SourceBadge';

export default function ImpactSection() {
    const [season, setSeason] = useState('2026');
    const [results, setResults] = useState(null);
    const [source, setSource] = useState(null);
    const [activeTab, setActiveTab] = useState(null); // 'raw' | 'star'
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const FETCHERS = { raw: fetchRawImpact, star: fetchStarImpact, bpm: fetchBpmLeaderboard };

    const fetchData = async (type) => {
        if (!season) return;
        setLoading(true);
        setError('');
        setResults(null);
        setSource(null);
        setActiveTab(type);

        try {
            const data = await FETCHERS[type](season);
            const rows = Array.isArray(data) ? data : data.results ?? data.rankings ?? [];
            const ranked = rows.map((row, i) => ({
                ...row,
                rank: row.rank ?? i + 1,
                player: row.player ?? row.player_name,
                impact_score: row.impact_score ?? row.impact_score_raw ?? row.impact_score_star,
                win_pct: row.win_pct ?? row.w_pct,
            }));
            setResults(ranked);
            setSource(Array.isArray(data) ? null : data._source ?? null);
        } catch (err) {
            setError(err.response?.data?.detail || 'Failed to fetch impact rankings.');
        } finally {
            setLoading(false);
        }
    };

    const TABLE_CONFIG = {
        bpm: {
            columns: ['Rank', 'Player', 'Team', 'PTS', 'BPM', 'OBPM', 'DBPM', 'VORP'],
            keys: ['rank', 'player', 'team_abbreviation', 'pts', 'bpm', 'obpm', 'dbpm', 'vorp'],
        },
        default: {
            columns: ['Rank', 'Player', 'Impact Score', 'PTS', 'Win %'],
            keys: ['rank', 'player', 'impact_score', 'pts', 'win_pct'],
        },
    };
    const table = TABLE_CONFIG[activeTab] ?? TABLE_CONFIG.default;

    return (
        <section className="dashboard-card">
            <h2 className="card-title hb-page-title">
                <span className="card-icon"><Icon name="bolt" /></span>
                Impact Rankings
                <InfoTooltip
                    label="How Impact Rankings work"
                    title="Under the hood"
                >
                    Impact scores are computed from a weighted blend of player stats (two variants: Raw vs Star),
                    then normalized so seasons are comparable. The API mainly reads the precomputed scores from the
                    database and returns the top-ranked players for the selected season.
                    <br /><br />
                    BPM and VORP are Basketball-Reference&apos;s published values (Box Plus/Minus 2.0), linked to
                    each player through this project&apos;s player-id map. An earlier in-house reproduction of the
                    formula ran hot (2025-26 SGA at 22.0 against the published 11.7) and was replaced.
                </InfoTooltip>
                <SourceBadge source={source} />
            </h2>

            <div className="input-row">
                <input
                    type="number"
                    placeholder="Season (e.g. 2026)"
                    value={season}
                    onChange={(e) => setSeason(e.target.value)}
                    className="input-field"
                    min={1980}
                    max={2030}
                />
                <button
                    className={`action-btn ${activeTab === 'raw' ? 'active-tab' : ''}`}
                    onClick={() => fetchData('raw')}
                    disabled={loading || !season}
                >
                    {loading && activeTab === 'raw' ? 'Loading…' : 'Raw Impact'}
                </button>
                <button
                    className={`action-btn ${activeTab === 'star' ? 'active-tab' : ''}`}
                    onClick={() => fetchData('star')}
                    disabled={loading || !season}
                >
                    {loading && activeTab === 'star' ? 'Loading…' : 'Star Impact'}
                </button>
                <button
                    className={`action-btn ${activeTab === 'bpm' ? 'active-tab' : ''}`}
                    onClick={() => fetchData('bpm')}
                    disabled={loading || !season}
                >
                    {loading && activeTab === 'bpm' ? 'Loading…' : 'BPM / VORP'}
                </button>
            </div>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}
            {results && (
                <DataTable
                    columns={table.columns}
                    keys={table.keys}
                    rows={results}
                    emptyMessage="No impact data available."
                />
            )}
        </section>
    );
}
