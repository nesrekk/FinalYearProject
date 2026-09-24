import React from 'react';

// columns: [{ key, label, align }]. rows: [{ [key]: value }]. Sticky
// header + hairline rows on desktop; below 768px each row becomes a
// labeled card (CSS-driven, via each cell's data-label).
export default function Table({ columns, rows, emptyMessage = 'No data to display.', className = '' }) {
    if (!rows || rows.length === 0) {
        return <p className="kit-table-empty">{emptyMessage}</p>;
    }
    return (
        <div className={`kit-table-wrapper ${className}`}>
            <table className="kit-table">
                <thead>
                    <tr>
                        {columns.map((col) => (
                            <th key={col.key} style={{ textAlign: col.align || 'left' }}>{col.label}</th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, i) => (
                        <tr key={row.id ?? i}>
                            {columns.map((col) => (
                                <td key={col.key} data-label={col.label} style={{ textAlign: col.align || 'left' }}>
                                    {col.render ? col.render(row[col.key], row) : row[col.key] ?? '—'}
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
