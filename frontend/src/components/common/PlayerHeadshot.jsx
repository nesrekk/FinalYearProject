import React, { useState } from 'react';
import { getPlayerHeadshotUrl, initials } from '../../utils/teamAssets';

export default function PlayerHeadshot({ playerId, playerName, size = 48, className = '', style = {} }) {
    const [failed, setFailed] = useState(false);
    const url = getPlayerHeadshotUrl(playerId);

    // Not every player (especially older/low-usage ones) has a CDN photo —
    // this is a real, expected gap, not an error, so the fallback is a
    // plain initials avatar rather than a broken-image icon.
    if (!url || failed) {
        return (
            <span
                className={`player-headshot-fallback ${className}`}
                style={{ width: size, height: size, fontSize: size * 0.36, ...style }}
            >
                {initials(playerName)}
            </span>
        );
    }

    return (
        <img
            src={url}
            alt={playerName || 'Player'}
            className={`player-headshot-img ${className}`}
            style={{ width: size, height: size, ...style }}
            onError={() => setFailed(true)}
            loading="lazy"
        />
    );
}
