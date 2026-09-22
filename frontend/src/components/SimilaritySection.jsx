import React, { useEffect, useState } from 'react';
import { fetchPlayerSuggestions, fetchSeasonSimilarity } from '../services/api';
import DataTable from './DataTable';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';

export default function SimilaritySection() {
    const [player, setPlayer] = useState('');
    const [season, setSeason] = useState('2026');
    const [results, setResults] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [suggestions, setSuggestions] = useState([]);

    useEffect(() => {
        const query = player.trim();
        if (query.length < 2) {
            setSuggestions([]);
            return;
        }

        const timer = setTimeout(async () => {
            try {
                const data = await fetchPlayerSuggestions(query, 10);
                setSuggestions(data?.results ?? []);
            } catch {
                setSuggestions([]);
            }
        }, 200);

        return () => clearTimeout(timer);
    }, [player]);

    const handleSearch = async () => {
        if (!player.trim() || !season) return;
        setLoading(true);
        setError('');
        setResults(null);

        try {
            const data = await fetchSeasonSimilarity(player.trim(), season);
            const rows = Array.isArray(data) ? data : data.results ?? data.similar_seasons ?? [];
            const normalized = rows.map((row) => ({
                ...row,
                player: row.player ?? row.player_name,
            }));
            setResults(normalized);
        } catch (err) {
            setError(err.response?.data?.detail || 'Failed to fetch similarity data.');
        } finally {
            setLoading(false);
        }
    };

    return (
        <section className="dashboard-card">
            <h2 className="card-title">
                <span className="card-icon"><Icon name="bar_chart" /></span>
                Season Similarity
                <InfoTooltip
                    label="How Season Similarity works"
                    title="Under the hood"
                >
                    We represent each player-season as a normalized feature vector (stats like scoring, efficiency,
                    usage, impact signals). Similarity is computed using cosine similarity, which compares the
                    direction of two vectors (stat “profile”) and returns the closest matches.
                </InfoTooltip>
            </h2>

            <div className="input-row">
                <input
                    type="text"
                    placeholder="Player Name"
                    value={player}
                    onChange={(e) => setPlayer(e.target.value)}
                    className="input-field"
                    list="similarity-player-suggestions"
                />
                <datalist id="similarity-player-suggestions">
                    {suggestions.map((name) => (
                        <option key={name} value={name} />
                    ))}
                </datalist>
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
                    className="action-btn"
                    onClick={handleSearch}
                    disabled={loading || !player.trim() || !season}
                >
                    {loading ? 'Searching…' : 'Find Similar Seasons'}
                </button>
            </div>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}
            {results && (
                <DataTable
                    columns={['Player', 'Season', 'Similarity Score']}
                    keys={['player', 'season', 'similarity_score']}
                    rows={results}
                    emptyMessage="No similar seasons found."
                />
            )}
        </section>
    );
}
