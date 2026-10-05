import React, { useEffect, useState } from 'react';
import { fetchTradeTeams, fetchTradeRoster, fetchWithWithoutStar } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import TeamLogo from './common/TeamLogo';
import PlayerHeadshot from './common/PlayerHeadshot';
import { signed } from '../utils/format';
import SeasonSelect from './common/SeasonSelect';

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
    const [season, setSeason] = useState(2026);
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
            // The roster knows the id, so two players with the same name can't be mixed up.
            const picked = roster.find((p) => p.player_name === name);
            const data = await fetchWithWithoutStar(team, season, name, picked?.player_id);
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
                    The team&apos;s record and average final margin split by whether the player had minutes in
                    each game. From 2020-21 this is read from stored data (the final scores and the play-by-play
                    game lines); earlier seasons are fetched live from stats.nba.com, which can take a few
                    seconds or not answer. This is an association, not causation — other players being in or
                    out of the lineup for those same games also affects the result, and this doesn&apos;t control
                    for that. Both sample sizes are always shown; a durable player who rarely sits will have a
                    small, noisy &quot;without&quot; sample.
                </InfoTooltip>
                <SourceBadge source={result?._source} />
            </h2>
            <p className="page-subtitle">Pick a season, a team, then a player from that roster.</p>

            <div className="input-row">
                <SeasonSelect value={season} onChange={setSeason} from={2010} />
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
