import React, { useState } from 'react';
import { fetchMVPPrediction, fetchDPOYPrediction, fetchROYPrediction, fetchAllNBAPrediction } from '../services/api';
import DataTable from './DataTable';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';
import SourceBadge from './common/SourceBadge';
import SeasonSelect from './common/SeasonSelect';

const AWARDS = {
    mvp: {
        label: 'MVP',
        icon: 'emoji_events',
        fetch: fetchMVPPrediction,
        probKey: 'mvp_chance',
        columns: ['Rank', 'Player', 'Chance to win %', 'PTS', 'TS%', 'Net Rtg'],
        keys: ['rank', 'player', 'probability', 'pts', 'ts_pct', 'net_rating'],
        blurb: `A logistic regression on season stats (points, TS%, usage, off/def rating,
            net rating, win %, minutes, age), ranking the whole league. Its raw outputs
            run far too high (several players at 99%+), so they're calibrated into a
            chance of winning that adds up to 100% across the field, checked on 15
            held-out seasons. Backtest: the favourite won 7 of 15.`,
    },
    dpoy: {
        label: 'DPOY',
        icon: 'shield',
        fetch: fetchDPOYPrediction,
        probKey: 'dpoy_chance',
        columns: ['Rank', 'Player', 'Chance to win %', 'Def Rtg', 'Net Rtg', 'STL', 'BLK', 'REB%'],
        keys: ['rank', 'player', 'probability', 'def_rating', 'net_rating', 'stl', 'blk', 'reb_pct'],
        blurb: `Restricted to players averaging at least 24 minutes across 40+ games
            that season — without this floor the model has to rank hundreds of
            low-minute players it never saw a real DPOY candidate look like, which
            just adds noise near the top. Chances are calibrated to add up to 100%
            across the pool. Backtest (15 seasons): favourite won 7, winner in the top 5
            in 12.`,
    },
    roy: {
        label: 'ROY',
        icon: 'eco',
        fetch: fetchROYPrediction,
        probKey: 'roy_chance',
        columns: ['Rank', 'Player', 'Chance to win %', 'PTS', 'TS%', 'USG%', 'Net Rtg', 'MIN'],
        keys: ['rank', 'player', 'probability', 'pts', 'ts_pct', 'usg_pct', 'net_rating', 'min'],
        blurb: `Restricted to each player's rookie season only (their first NBA season,
            checked against Basketball-Reference) — a 21 PPG rookie year doesn't look special
            next to prime LeBron unless it's compared against other rookies. Chances
            are calibrated to add up to 100% across the rookie pool. Backtest (14
            seasons): favourite won 9, winner always in the top 5.`,
    },
    allnba: {
        label: 'All-NBA',
        icon: 'star',
        fetch: fetchAllNBAPrediction,
        probKey: 'all_nba_chance',
        columns: ['Rank', 'Team', 'Player', 'Chance of a team %', 'PTS', 'REB', 'AST', 'Net Rtg'],
        keys: ['rank', 'predicted_team', 'player', 'probability', 'pts', 'reb', 'ast', 'net_rating'],
        blurb: `Predicts the 15-player All-NBA pool (min>=24, gp>=40), trained on 240
            real historical selections (2009-10 through 2024-25). "Team" (First/Second/
            Third) is reconstructed by simple rank cutoff — top 5/10/15 — not modeled
            per tier, so treat it as an ordering, not real voting behavior. Leave-one-
            season-out backtest: 78% precision@15 (11.7/15 correct on average, 16
            seasons); ROC-AUC 0.98. The real, held-out 2024-25 season: 10/15 correct.
            Chances are calibrated (Platt scaling) so they add up to about 15 a season.`,
    },
};

export default function AwardsRaceSection() {
    const [award, setAward] = useState('mvp');
    const [season, setSeason] = useState(2026);
    const [results, setResults] = useState(null);
    const [poolInfo, setPoolInfo] = useState(null);
    const [source, setSource] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const cfg = AWARDS[award];

    const handlePredict = async () => {
        if (!season) return;
        setLoading(true);
        setError('');
        setResults(null);
        setPoolInfo(null);
        setSource(null);

        try {
            const data = await cfg.fetch(season);
            const predictions = data.results ?? [];
            const ranked = predictions.map((row, i) => ({
                ...row,
                rank: row.rank ?? i + 1,
                player: row.player ?? row.player_name,
                // One decimal as text; DataTable would print a float with three.
                probability: row[cfg.probKey] != null ? (row[cfg.probKey] * 100).toFixed(1) : '—',
            }));
            setResults(ranked);
            setSource(data._source ?? null);
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
        setSource(null);
        setError('');
    };

    return (
        <section className="dashboard-card">
            <h2 className="card-title hb-page-title">
                <span className="card-icon"><Icon name={cfg.icon} /></span>
                Awards Race
                <InfoTooltip label={`How ${cfg.label} Prediction works`} title="Under the hood">
                    {cfg.blurb}
                </InfoTooltip>
                <SourceBadge source={source} />
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
                <SeasonSelect value={season} onChange={setSeason} from={2010} label="Season (the award models cover 2009-10 on)" />
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
