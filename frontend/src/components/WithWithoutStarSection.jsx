import React, { useEffect, useState } from 'react';
import { fetchTradeTeams, fetchTradeRoster, fetchWithWithoutStar } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import TeamLogo from './common/TeamLogo';
import PlayerHeadshot from './common/PlayerHeadshot';
import { signed } from '../utils/format';

function fmt(v, digits = 1) {
    return v == null ? '—' : Number(v).toFixed(digits);
}

function SplitCard({ label, split, color }) {
    return (
        <div className="stat-card" style={{ borderColor: color }}>
            <div className="stat-card-label">{label}</div>
            <div className="stat-card-value" style={{ fontSize: '1.3rem' }}>
                {split.n === 0 ? '—' : `${split.wins}-${split.losses}`}
            </div>
            <div className="page-subtitle" style={{ marginTop: 4, fontSize: '0.78rem' }}>
                {split.n === 0
                    ? 'No real games in this split'
                    : `${split.n} real games · ${(split.win_pct * 100).toFixed(1)}% win rate · ${signed(split.avg_point_diff, 1, '-')} avg point diff`}
            </div>
        </div>
    );
}

export default function WithWithoutStarSection() {
    const [season, setSeason] = useState(2025);
    const [teams, setTeams] = useState([]);
    const [team, setTeam] = useState('');
    const [roster, setRoster] = useState([]);
    const [playerName, setPlayerName] = useState('');

    const [result, setResult] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    useEffect(() => {
        let active = true;
        fetchTradeTeams(season)
            .then((data) => { if (active) setTeams(data.teams || []); })
            .catch(() => { if (active) setTeams([]); });
        return () => { active = false; };
    }, [season]);

    useEffect(() => {
        setPlayerName('');
        setResult(null);
        if (!team) { setRoster([]); return; }
        let active = true;
        fetchTradeRoster(team, season)
            .then((data) => { if (active) setRoster(data.roster || []); })
            .catch(() => { if (active) setRoster([]); });
        return () => { active = false; };
    }, [team, season]);

    async function loadSplit(name) {
        setPlayerName(name);
        setLoading(true);
        setError('');
        setResult(null);
        try {
            const data = await fetchWithWithoutStar(team, season, name);
            setResult(data);
        } catch (e) {
            setError(e?.response?.data?.detail || 'Could not load this split.');
        } finally {
            setLoading(false);
        }
    }

    return (
        <section className="dashboard-card">
            <h2 className="card-title hb-page-title">
                <span className="card-icon"><Icon name="person_off" /></span>
                With vs. Without a Star
                <InfoTooltip label="How this works" title="A real association, not a causal claim">
                    Real team record and real point differential split by whether a real player actually
                    played in each real game, live-fetched from the NBA's own real per-game data for the whole
                    season (not a model or a sample). This is an association, not causation — other players
                    being in or out of the lineup for those same real games also affects the real result, and
                    this doesn't control for that. Real sample sizes for both splits are always shown; a
                    durable player who rarely sits will have a small, noisy "without" sample.
                </InfoTooltip>
                <SourceBadge source={result?._source} />
            </h2>
            <p className="page-subtitle">Pick a season, a team, then a player from that roster.</p>

            <div className="input-row">
                <input
                    type="number"
                    className="input-field"
                    value={season}
                    onChange={(e) => setSeason(Number(e.target.value))}
                    min={2010}
                    max={2026}
                />
                <select className="input-field" value={team} onChange={(e) => setTeam(e.target.value)}>
                    <option value="">Team…</option>
                    {teams.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
                <select
                    className="input-field"
                    value={playerName}
                    onChange={(e) => loadSplit(e.target.value)}
                    disabled={!roster.length}
                >
                    <option value="">{team ? `Player from ${team}…` : 'Pick a team first'}</option>
                    {roster.map((p) => (
                        <option key={p.player_id} value={p.player_name}>{p.player_name} ({fmt(p.pts)} ppg)</option>
                    ))}
                </select>
            </div>

            {error && <p className="error-message" style={{ marginTop: '0.5rem' }}>{error}</p>}
            {loading && <Loader />}

            {result && (
                <>
                    <div className="entity-row" style={{ marginTop: '1rem', marginBottom: '0.75rem' }}>
                        <PlayerHeadshot playerId={result.player_id} playerName={result.player_name} size={40} />
                        <TeamLogo abbreviation={result.team_abbreviation} size={24} />
                        <span style={{ fontWeight: 700 }}>{result.player_name}</span>
                        <span className="page-subtitle" style={{ margin: 0 }}>
                            {result.team_abbreviation} · {result.season - 1}-{String(result.season).slice(-2)}
                        </span>
                    </div>
                    <div className="stat-cards-row">
                        <SplitCard label="With Him" split={result.with_player} color="rgba(52,211,153,0.4)" />
                        <SplitCard label="Without Him" split={result.without_player} color="rgba(248,113,113,0.4)" />
                    </div>
                    <p className="page-subtitle" style={{ marginTop: '0.75rem' }}>{result.methodology}</p>
                </>
            )}
        </section>
    );
}
