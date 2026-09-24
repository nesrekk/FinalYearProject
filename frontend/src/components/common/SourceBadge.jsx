import React from 'react';
import Icon from './Icon';

// Small "where did this come from" chip, reading the `_source` object every
// main Analytics endpoint now returns (api/source_badge.py). Renders nothing
// if the endpoint hasn't been updated yet or the data hasn't loaded —
// additive, never blocks a section that doesn't have it.
export default function SourceBadge({ source }) {
    if (!source) return null;
    const { tables, upstream_api: upstreamApi, live, as_of: asOf } = source;

    const tableList = Array.isArray(tables) && tables.length ? tables.join(', ') : null;
    const asOfLabel = asOf ? new Date(asOf).toLocaleString() : null;
    const titleParts = [
        tableList ? `Table(s): ${tableList}` : null,
        live ? 'Live-fetched on this request' : 'Read from a precomputed table',
        asOfLabel ? `As of: ${asOfLabel}` : null,
    ].filter(Boolean);

    return (
        <span
            className="pill-badge pill-badge--muted"
            style={{ marginLeft: '0.5rem', verticalAlign: 'middle', cursor: 'default' }}
            title={titleParts.join(' · ')}
        >
            <Icon name={live ? 'bolt' : 'table_view'} size="0.85em" />
            {upstreamApi}
        </span>
    );
}
