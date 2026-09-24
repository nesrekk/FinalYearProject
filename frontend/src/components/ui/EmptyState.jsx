import React from 'react';
import Icon from '../common/Icon';

export function EmptyState({ icon = 'inbox', message, className = '' }) {
    return (
        <div className={`kit-state ${className}`}>
            <Icon name={icon} size="2em" />
            <p>{message}</p>
        </div>
    );
}

export function ErrorState({ icon = 'error_outline', message, onRetry, className = '' }) {
    return (
        <div className={`kit-state kit-state--error ${className}`}>
            <Icon name={icon} size="2em" />
            <p>{message}</p>
            {onRetry && (
                <button type="button" className="kit-state-retry" onClick={onRetry}>
                    Try again
                </button>
            )}
        </div>
    );
}
