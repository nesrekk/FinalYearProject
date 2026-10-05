import React, { useEffect, useRef, useState } from 'react';
import Icon from './Icon';
import { NAV_GROUPS } from '../layout/navConfig';
import { ANALYTICS_TABS } from '../analytics/analyticsTabs';
import { addSavedView, findSavedViewByUrl } from '../../utils/savedViews';
import { PAGE_PARAM } from '../../utils/useUrlState';

const PAGE_LABELS = Object.fromEntries(
    NAV_GROUPS.flatMap((g) => g.items).map((item) => [item.id, item.label]),
);

// The page's nav label plus its current inputs (everything in the query
// string besides `page` — the tool's own inputs, per utils/useUrlState.js),
// so a saved title tells the two apart without the user typing anything:
// "Leaderboard Builder (season: 2025, stat: pts)".
// Analytics tools share ?page=analytics, and its four nav entries share the id: name the tool from the
// hash ("Analytics › Rim Deterrence"), not the last nav entry ("College & Draft", R8-058).
const ANALYTICS_LABELS = Object.fromEntries(ANALYTICS_TABS.map((t) => [t.id, t.label]));

function autoTitle(pageId) {
    const hash = window.location.hash.replace('#', '');
    const tool = pageId === 'analytics' ? ANALYTICS_LABELS[hash] : null;
    const label = tool ? `Analytics › ${tool}` : PAGE_LABELS[pageId] || pageId;
    const params = new URLSearchParams(window.location.search);
    params.delete(PAGE_PARAM);
    const bits = [...params.entries()].map(([k, v]) => `${k}: ${v}`);
    if (hash && !tool) bits.unshift(hash);
    return bits.length ? `${label} (${bits.join(', ')})` : label;
}

// Place directly after a <CopyLinkButton /> in a tool's title row. Saves
// the current URL — page, tab and every input already in the query string —
// to the Saved analyses page (utils/savedViews.js), nothing server-side.
// `title` overrides the auto-generated one (e.g. the player profile passes
// the player's name, since "player" carries no nav label of its own).
export default function SaveViewButton({ pageId, title }) {
    const [status, setStatus] = useState('idle'); // 'idle' | 'saved' | 'already'
    const timer = useRef(null);

    useEffect(() => () => clearTimeout(timer.current), []);

    const save = () => {
        const url = window.location.pathname + window.location.search + window.location.hash;
        const already = findSavedViewByUrl(url);
        if (already) {
            setStatus('already');
        } else {
            addSavedView({ url, page: pageId, title: title || autoTitle(pageId) });
            setStatus('saved');
        }
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setStatus('idle'), 2500);
    };

    return (
        <span className="save-view">
            <button type="button" className="table-export-btn" onClick={save}
                aria-label="Save this exact view to Saved analyses">
                <Icon name={status === 'idle' ? 'bookmark_add' : 'bookmark'} size={15} />
                {status === 'idle' ? 'Save' : status === 'saved' ? 'Saved' : 'Already saved'}
            </button>
        </span>
    );
}
