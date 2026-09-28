import React, { useSyncExternalStore } from 'react';
import PlayerHeadshot from './PlayerHeadshot';
import Icon from './Icon';
import { isWatched, subscribeWatchlist, toggleWatchlist } from '../../utils/watchlist';
import { isPlainClick, openPlayerProfile, playerProfileHref } from '../../utils/useUrlState';
import '../../styles/profile.css';

// Photo + name link to the player's profile page (?page=player&id=…).
// Placeholder ids (draft picks who never played are negative) aren't linked.
function ProfileLink({ playerId, name, children }) {
    if (!(Number(playerId) > 0)) return <span className="player-name-link">{children}</span>;
    const onClick = (e) => {
        e.stopPropagation(); // rows with their own click handler
        if (!isPlainClick(e)) return;
        e.preventDefault();
        openPlayerProfile(playerId);
    };
    return (
        <a className="player-name-link" href={playerProfileHref(playerId)} onClick={onClick}
            title={`Open ${name}'s profile`}>
            {children}
        </a>
    );
}


export default function PlayerName({ playerId, name, size = 28, children }) {
    const watched = useSyncExternalStore(subscribeWatchlist, () => isWatched(playerId));

    return (
        <span className="player-name-cell">
            <ProfileLink playerId={playerId} name={name}>
                <PlayerHeadshot playerId={playerId} playerName={name} size={size} />
                <span className="player-name-cell-text">
                    {name}
                    {children}
                </span>
            </ProfileLink>
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
