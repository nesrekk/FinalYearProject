import React, { useEffect, useState } from 'react';
import { fetchPlayerContractValue } from '../../services/api';

function money(v) {
    if (v == null) return '—';
    const sign = v < 0 ? '−' : '';
    const a = Math.abs(v);
    return a >= 1e6 ? `${sign}$${(a / 1e6).toFixed(1)}M` : `${sign}$${Math.round(a / 1e3).toLocaleString()}K`;
}

// Real contract surplus (Contract Value feature) for the two players in a
// simulated trade, when that season has reliable salary data on file.
export default function TradeContractValue({ season, players }) {
    const [rows, setRows] = useState(null);
    const key = players.map((p) => p.id).join(',');

    useEffect(() => {
        let active = true;
        Promise.all(players.map((p) => fetchPlayerContractValue(p.id, season).catch(() => null)))
            .then((res) => { if (active) setRows(res); });
        return () => { active = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key, season]);

    if (!rows) return null;
    const unavailable = rows.every((r) => !r || !r.available);

    return (
        <div className="dashboard-card" style={{ marginTop: '1rem' }}>
            <h4 className="section-heading" style={{ marginTop: 0 }}>Contract value of the players traded</h4>
            {unavailable ? (
                <p className="page-subtitle" style={{ margin: 0 }}>
                    {rows.find((r) => r && r.reason)?.reason || 'No contract value on file for these players.'}
                </p>
            ) : (
                <div className="table-wrapper">
                    <table className="data-table">
                        <thead>
                            <tr><th>Player</th><th>Real salary</th><th>WAR</th><th>Fair value</th><th>Surplus</th></tr>
                        </thead>
                        <tbody>
                            {rows.map((r, i) => (
                                <tr key={players[i].id}>
                                    <td>{players[i].name}</td>
                                    {r && r.available ? (
                                        <>
                                            <td>{money(r.player.salary)}</td>
                                            <td>{r.player.war?.toFixed(1)}</td>
                                            <td>{money(r.player.fair_value)}</td>
                                            <td style={{ fontWeight: 700, color: r.player.surplus >= 0 ? '#34d399' : '#f87171' }}>
                                                {r.player.surplus > 0 ? '+' : ''}{money(r.player.surplus)}
                                            </td>
                                        </>
                                    ) : (
                                        <td colSpan={4} className="page-subtitle">{r?.reason || 'Not available.'}</td>
                                    )}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <p className="page-subtitle" style={{ fontSize: '0.75rem', marginTop: 6 }}>
                        From the Contract Value tool (Analytics → Teams &amp; Markets): real salary vs. real production priced at that
                        season's real cost per win. See that tool for sources and caveats.
                    </p>
                </div>
            )}
        </div>
    );
}
