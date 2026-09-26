import React from 'react';
import PlayerHeadshot from './PlayerHeadshot';

export default function PlayerName({ playerId, name, size = 28, children }) {
    return (
        <span className="player-name-cell">
            <PlayerHeadshot playerId={playerId} playerName={name} size={size} />
            <span className="player-name-cell-text">
                {name}
                {children}
            </span>
        </span>
    );
}
