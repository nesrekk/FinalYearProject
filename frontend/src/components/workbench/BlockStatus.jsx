import React, { useEffect } from 'react';
import Icon from '../common/Icon';
import { studyEvent } from '../../utils/studyLog';

// A block's loading and error lines, the same in every block. A failed
// request is never kept in the query cache (services/api.js), so "Try again"
// sends it afresh.

export function Loading({ what = '' }) {
    return (
        <p className="wb-meta wb-loading" role="status">
            <span className="wb-spinner" aria-hidden="true" />
            {what ? `Loading ${what}…` : 'Loading…'}
        </p>
    );
}

export function BlockError({ message, onRetry }) {
    // Counted during a usability session (utils/studyLog.js; a no-op otherwise).
    useEffect(() => { studyEvent('block_error', { message: String(message || '') }); }, [message]);
    return (
        <div className="wb-error wb-error-box" role="alert">
            <p>{message}</p>
            {onRetry && (
                <button type="button" className="table-export-btn" onClick={onRetry}>
                    <Icon name="refresh" size={15} /> Try again
                </button>
            )}
        </div>
    );
}
