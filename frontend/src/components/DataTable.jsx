import React from 'react';
import PlayerName from './common/PlayerName';

const PLAYER_KEYS = new Set(['player', 'player_name']);

/**
 * Reusable data table component.
 *
 * @param {Object}   props
 * @param {string[]} props.columns  - Column header labels
 * @param {string[]} props.keys     - Keys to pull from each row object
 * @param {Object[]} props.rows     - Array of row data objects
 * @param {string}   [props.emptyMessage] - Message when no rows
 */
export default function DataTable({ columns, keys, rows, emptyMessage = 'No data to display.' }) {
    if (!rows || rows.length === 0) {
        return <p className="empty-message">{emptyMessage}</p>;
    }

    return (
        <div className="table-wrapper">
            <table className="data-table">
                <thead>
                    <tr>
                        {columns.map((col, i) => (
                            <th key={i}>{col}</th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row, rowIdx) => (
                        <tr key={rowIdx}>
                            {keys.map((key, colIdx) => (
                                <td key={colIdx}>
                                    {PLAYER_KEYS.has(key) && row.player_id ? (
                                        <PlayerName playerId={row.player_id} name={row[key]} />
                                    ) : typeof row[key] === 'number'
                                        ? Number.isInteger(row[key])
                                            ? row[key]
                                            : row[key].toFixed(3)
                                        : row[key]}
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}
