import React, { useState } from 'react';
import { fetchMVPPrediction, fetchDPOYPrediction, fetchROYPrediction, fetchAllNBAPrediction } from '../services/api';
import DataTable from './DataTable';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';

const AWARDS = {
    mvp: {
        label: 'MVP',
        icon: 'emoji_events',
        fetch: fetchMVPPrediction,
        probKey: 'mvp_probability',
        columns: ['Rank', 'Player', 'Probability %', 'PTS', 'TS%', 'Net Rtg'],
        keys: ['rank', 'player', 'probability', 'pts', 'ts_pct', 'net_rating'],
        blurb: `MVP probabilities come from a logistic regression model trained on
            historical player-season data (pts, TS%, usage%, off/def rating, net rating,
            win%, minutes, age). No candidate-pool restriction — the model ranks the
            entire league, same as the real MVP conversation does.`,
    },
    dpoy: {
        label: 'DPOY',
        icon: 'shield',
        fetch: fetchDPOYPrediction,
        probKey: 'dpoy_probability',
        columns: ['Rank', 'Player', 'Probability %', 'Def Rtg', 'Net Rtg', 'STL', 'BLK', 'REB%'],
        keys: ['rank', 'player', 'probability', 'def_rating', 'net_rating', 'stl', 'blk', 'reb_pct'],
        blurb: `Restricted to players averaging at least 24 minutes across 40+ games
            that season — without this floor the model has to rank hundreds of
            low-minute players it never saw a real DPOY candidate look like, which
            just adds noise near the top. Backtested top-5 accuracy: 80% (15 seasons).`,
    },
    roy: {
        label: 'ROY',
        icon: 'eco',
        fetch: fetchROYPrediction,
        probKey: 'roy_probability',
        columns: ['Rank', 'Player', 'Probability %', 'PTS', 'TS%', 'USG%', 'Net Rtg', 'MIN'],
        keys: ['rank', 'player', 'probability', 'pts', 'ts_pct', 'usg_pct', 'net_rating', 'min'],
        blurb: `Restricted to each player's rookie season only (their first season
            anywhere in this database) — a 21 PPG rookie year doesn't look special
            next to prime LeBron unless it's compared against other rookies.
            Backtested top-5 accuracy: 100% (14 seasons); top-1: 64%.`,
    },
    allnba: {
        label: 'All-NBA',
        icon: 'star',
        fetch: fetchAllNBAPrediction,
        probKey: 'all_nba_probability',
        columns: ['Rank', 'Team', 'Player', 'Probability %', 'PTS', 'REB', 'AST', 'Net Rtg'],
        keys: ['rank', 'predicted_team', 'player', 'probability', 'pts', 'reb', 'ast', 'net_rating'],
        blurb: `Predicts the 15-player All-NBA pool (min>=24, gp>=40), trained on 240
            real historical selections (2009-10 through 2024-25). "Team" (First/Second/
            Third) is reconstructed by simple rank cutoff — top 5/10/15 — not modeled
            per tier, so treat it as an ordering, not real voting behavior. Leave-one-
            season-out backtest: 78% precision@15 (11.7/15 correct on average, 16
            seasons); ROC-AUC 0.98. The real, held-out 2024-25 season: 10/15 correct.`,
    },
};

export default function AwardsRaceSection() {
    const [award, setAward] = useState('mvp');
    const [season, setSeason] = useState('2025');
    const [results, setResults] = useState(null);
    const [poolInfo, setPoolInfo] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const cfg = AWARDS[award];

    const handlePredict = async () => {
        if (!season) return;
        setLoading(true);
        setError('');
        setResults(null);
        setPoolInfo(null);

        try {
            const data = await cfg.fetch(season);
            const predictions = data.results ?? [];
            const ranked = predictions.map((row, i) => ({
                ...row,
                rank: row.rank ?? i + 1,
                player: row.player ?? row.player_name,
                probability: row[cfg.probKey] != null ? row[cfg.probKey] * 100 : null,
            }));
            setResults(ranked);
            if (data.candidate_pool_rule) {
                setPoolInfo(`${data.candidate_pool_size} eligible candidates (${data.candidate_pool_rule})`);
            }
        } catch (err) {
            setError(err.response?.data?.detail || `Failed to fetch ${cfg.label} predictions.`);
        } finally {
            setLoading(false);
        }
    };

    const switchAward = (id) => {
        setAward(id);
        setResults(null);
        setPoolInfo(null);
        setError('');
    };

    return (
        <section className="dashboard-card">
            <h2 className="card-title">
                <span className="card-icon"><Icon name={cfg.icon} /></span>
                Awards Race
                <InfoTooltip label={`How ${cfg.label} Prediction works`} title="Under the hood">
                    {cfg.blurb}
                </InfoTooltip>
            </h2>

            <div className="tab-bar" style={{ marginBottom: '1rem' }}>
                {Object.entries(AWARDS).map(([id, a]) => (
                    <button
                        key={id}
                        type="button"
                        className={`tab-btn ${award === id ? 'tab-btn--active' : ''}`}
                        onClick={() => switchAward(id)}
                    >
                        <span className="tab-icon"><Icon name={a.icon} /></span> {a.label}
                    </button>
                ))}
            </div>

            <div className="input-row">
                <input
                    type="number"
                    placeholder="Season (e.g. 2025)"
                    value={season}
                    onChange={(e) => setSeason(e.target.value)}
                    className="input-field"
                    min={1980}
                    max={2030}
                />
                <button
                    className="action-btn"
                    onClick={handlePredict}
                    disabled={loading || !season}
                >
                    {loading ? 'Predicting…' : `Predict ${cfg.label}`}
                </button>
            </div>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}
            {poolInfo && !loading && !error && (
                <p className="page-subtitle" style={{ marginTop: '0.5rem', marginBottom: '0.5rem' }}>
                    {poolInfo}
                </p>
            )}
            {results && (
                <DataTable
                    columns={cfg.columns}
                    keys={cfg.keys}
                    rows={results}
                    emptyMessage="No predictions available."
                />
            )}
        </section>
    );
}
