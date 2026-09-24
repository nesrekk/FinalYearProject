import React, { useState } from 'react';
import Icon from '../common/Icon';

// Wordle-style shareable result: a row of squares built from each attempt's
// own real correct/wrong outcome (never invented), copied as plain text so
// it can be pasted anywhere without revealing the answer.
export default function ShareResult({ gameTitle, puzzleDate, outcomes, summary }) {
    const [copied, setCopied] = useState(false);

    const grid = outcomes.map((won) => (won ? '\u{1F7E9}' : '\u{1F7E5}')).join('');
    const text = `NBA Hub — ${gameTitle}\n${puzzleDate}\n${grid}${summary ? `\n${summary}` : ''}`;

    async function handleShare() {
        try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 1800);
        } catch {
            // Clipboard access can be denied (permissions, non-secure
            // context) — the grid is still visible on screen either way.
        }
    }

    return (
        <div className="share-result">
            <div className="share-result-grid" aria-hidden="true">{grid}</div>
            <button type="button" className="share-result-btn" data-magnetic onClick={handleShare}>
                <Icon name={copied ? 'check' : 'ios_share'} size="1em" />
                {copied ? 'Copied!' : 'Share Result'}
            </button>
        </div>
    );
}
