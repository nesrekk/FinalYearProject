import React, { useState } from 'react';
import Icon from './Icon';
import { openInWorkbench } from '../../utils/openInWorkbench';

// Next to a page's Copy link / Save buttons: `build()` returns a board
// (utils/openInWorkbench.js builders) from the page's current inputs; it is
// saved in this browser and the Workbench opens on it.
export default function OpenInWorkbenchButton({ build, disabled = false }) {
    const [state, setState] = useState({ busy: false, error: '' });
    const go = async () => {
        setState({ busy: true, error: '' });
        try {
            await openInWorkbench(build());
        } catch (e) {
            setState({ busy: false, error: e?.message || 'The board couldn’t be created.' });
        }
    };
    return (
        <span className="open-workbench">
            <button type="button" className="table-export-btn" onClick={go} disabled={disabled || state.busy}
                aria-label="Open these players and stats in a new Workbench board"
                title="A new Workbench board with what this page shows, saved in this browser">
                <Icon name="dashboard_customize" size={15} />
                Open in Workbench
            </button>
            {state.error && <span className="error-message" role="alert"> {state.error}</span>}
        </span>
    );
}
