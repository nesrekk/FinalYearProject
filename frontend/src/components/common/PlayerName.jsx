import React, { useSyncExternalStore } from 'react';
import PlayerHeadshot from './PlayerHeadshot';
import Icon from './Icon';
import { isWatched, subscribeWatchlist, toggleWatchlist } from '../../utils/watchlist';

export default function PlayerName({ playerId, name, size = 28, children }) {
    const watched = useSyncExternalStore(subscribeWatchlist, () => isWatched(playerId));

    return (
        <span className="player-name-cell">
            <PlayerHeadshot playerId={playerId} playerName={name} size={size} />
            <span className="player-name-cell-text">
                {name}
                {children}
            </span>
            {playerId != null && (
                <button
                    type="button"
                    className={`watch-star${watched ? ' watch-star--active' : ''}`}
                    onClick={(e) => { e.stopPropagation(); toggleWatchlist(playerId, name); }}
                    aria-label={watched ? `Remove ${name} from watchlist` : `Add ${name} to watchlist`}
                    aria-pressed={watched}
                    title={watched ? 'On your watchlist' : 'Add to watchlist'}
                >
                    <Icon name="star" size={16} fill={watched} />
                </button>
            )}
        </span>
    );
}
