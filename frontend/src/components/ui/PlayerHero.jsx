import React from 'react';
import PlayerHeadshot from '../common/PlayerHeadshot';
import TeamLogo from '../common/TeamLogo';
import BigStat from './BigStat';
import { TEAM_COLORS } from '../../utils/teamAssets';

export default function PlayerHero({ playerId, playerName, teamAbbreviation, subtitle, stats = [], className = '' }) {
    const teamColor = TEAM_COLORS[teamAbbreviation?.toUpperCase()] || 'var(--accent)';

    return (
        <div
            className={`player-hero ${className}`}
            style={{ '--player-hero-wash': teamColor }}
        >
            <div className="player-hero-top">
                <PlayerHeadshot playerId={playerId} playerName={playerName} size={96} />
                <div className="player-hero-text">
                    <h2 className="text-display player-hero-name">{playerName}</h2>
                    <p className="player-hero-subtitle">
                        {teamAbbreviation && <TeamLogo abbreviation={teamAbbreviation} size={18} />}
                        {subtitle}
                    </p>
                </div>
            </div>
            {stats.length > 0 && (
                <div className="player-hero-stats">
                    {stats.slice(0, 3).map((s) => (
                        <BigStat key={s.label} {...s} />
                    ))}
                </div>
            )}
        </div>
    );
}
