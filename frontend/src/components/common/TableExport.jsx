import React, { useRef, useState } from 'react';
import Icon from './Icon';
import { downloadText, slugify, tableToRows, toCsv, toJson } from '../../utils/tableExport';

// Place directly before a table (or its .table-wrapper). Exports the table
// exactly as rendered, so whatever filters, season or sort the reader chose
// is what they get. `name` sets the file name; otherwise the nearest heading
// above it is used.
export default function TableExport({ name }) {
    const ref = useRef(null);
    const [error, setError] = useState('');

    const findTable = () => {
        const next = ref.current?.nextElementSibling;
        if (!next) return null;
        return next.tagName === 'TABLE' ? next : next.querySelector('table');
    };

    const baseName = () => {
        if (name) return slugify(name);
        // The last heading before this toolbar in document order.
        const here = ref.current;
        const heading = here && [...document.querySelectorAll('h1, h2, h3, h4')]
            .filter((h) => h.compareDocumentPosition(here) & Node.DOCUMENT_POSITION_FOLLOWING)
            .pop();
        if (heading) {
            const copy = heading.cloneNode(true);
            copy.querySelectorAll('[aria-hidden="true"], button, [role="tooltip"], .it-wrap, .pill-badge, .copy-link').forEach((n) => n.remove());
            return slugify(copy.textContent);
        }
        return 'nba-hub-table';
    };

    const run = (format) => {
        const table = findTable();
        const data = table && tableToRows(table);
        if (!data || data.rows.length === 0) {
            setError('Nothing to export yet.');
            return;
        }
        setError('');
        const stamp = new Date().toISOString().slice(0, 10);
        const file = `${baseName()}-${stamp}.${format}`;
        if (format === 'csv') downloadText(toCsv(data), file, 'text/csv');
        else downloadText(toJson(data), file, 'application/json');
    };

    return (
        <div className="table-export" ref={ref} data-export-skip>
            {error && <span className="table-export-error" role="status">{error}</span>}
            <span className="table-export-label">Export</span>
            <button type="button" className="table-export-btn" onClick={() => run('csv')} aria-label="Download this table as CSV">
                <Icon name="download" size={15} /> CSV
            </button>
            <button type="button" className="table-export-btn" onClick={() => run('json')} aria-label="Download this table as JSON">
                <Icon name="data_object" size={15} /> JSON
            </button>
        </div>
    );
}
