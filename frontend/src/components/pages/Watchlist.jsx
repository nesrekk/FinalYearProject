import React, { useEffect, useState, useSyncExternalStore } from 'react';
import { fetchPlayerHistory } from '../../services/api';
import Loader from '../Loader';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import TableExport from '../common/TableExport';
import PlayerName from '../common/PlayerName';
import { getWatchlist, subscribeWatchlist } from '../../utils/watchlist';

function seasonLabel(s) {
    return `${s - 1}-${String(s).slice(-2)}`;
}

function fmt(v, digits = 1) {
    return v == null ? '—' : Number(v).toFixed(digits);
}

function pct(v) {
    return v == null ? '—' : `${(Number(v) * 100).toFixed(1)}%`;
}

// One row per starred player: their most recent season in player_season_stats
// (not necessarily this year — a retired or injured-out player's last real
// season, labeled as such, rather than a blank row).
export default function Watchlist() {
    const starred = useSyncExternalStore(subscribeWatchlist, getWatchlist);
    const [rows, setRows] = useState([]);
    const [failed, setFailed] = useState([]);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        let cancelled = false;

        async function load() {
            setLoading(starred.length > 0);
            const results = await Promise.allSettled(starred.map((p) => fetchPlayerHistory(p.name, Number(p.playerId) > 0 ? p.playerId : undefined)));
            if (cancelled) return;
            const ok = [];
            const bad = [];
            results.forEach((res, i) => {
                if (res.status === 'fulfilled' && res.value.seasons?.length) {
                    const seasons = res.value.seasons;
                    const latest = seasons[seasons.length - 1];
                    ok.push({
                        player_id: res.value.player_id,
                        player_name: res.value.player_name,
                        season: latest.season,
                        age: latest.age,
                        gp: latest.gp,
                        min: latest.min,
                        pts: latest.pts,
                        reb: latest.reb,
                        ast: latest.ast,
                        stl: latest.stl,
                        blk: latest.blk,
                        ts_pct: latest.ts_pct,
                        net_rating: latest.net_rating,
                    });
                } else {
                    bad.push(starred[i].name);
                }
            });
            ok.sort((a, b) => a.player_name.localeCompare(b.player_name));
            setRows(ok);
            setFailed(bad);
            setLoading(false);
        }

        load();
        return () => { cancelled = true; };
    }, [starred]);

    return (
        <div className="page page-watchlist fade-in">
            <div className="dashboard-card">
                <h2 className="card-title hb-page-title">
                    <span className="card-icon"><Icon name="star" fill /></span>
                    Watchlist
                    <InfoTooltip label="How this works" title="Your starred players">
                        Click the star next to any player's name — in any table, chart or list across the
                        app — to add or remove them here. This list lives only in this browser
                        (localStorage), not on the server, so it won't follow you to another device
                        and clearing site data clears it. Each row is that player's most recent
                        season in the database, which may not be the current season if they're
                        retired or haven't played yet this year.
                    </InfoTooltip>
                </h2>

                {starred.length === 0 && (
                    <p className="empty-message">
                        No players starred yet. Click the <Icon name="star" size={14} /> next to a player's name
                        anywhere in the app to add them here.
                    </p>
                )}

                {loading && <Loader />}

                {!loading && failed.length > 0 && (
                    <p className="error-message" style={{ marginTop: '0.5rem' }}>
                        No season data for: {failed.join(', ')}.
                    </p>
                )}

                {!loading && rows.length > 0 && (
                    <>
                        <p className="page-subtitle" style={{ marginTop: '0.75rem', marginBottom: '0.75rem' }}>
                            {rows.length} starred player{rows.length === 1 ? '' : 's'}, each showing their most recent season.
                        </p>
                        <TableExport name="watchlist" />
                        <div className="table-wrapper">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>Player</th><th>Season</th><th>Age</th><th>GP</th><th>MIN</th>
                                        <th>PTS</th><th>REB</th><th>AST</th><th>STL</th><th>BLK</th>
                                        <th>TS%</th><th>Net Rtg</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {rows.map((r) => (
                                        <tr key={r.player_id}>
                                            <td><PlayerName playerId={r.player_id} name={r.player_name} /></td>
                                            <td>{seasonLabel(r.season)}</td>
                                            <td>{r.age ?? '—'}</td>
                                            <td>{r.gp ?? '—'}</td>
                                            <td>{fmt(r.min)}</td>
                                            <td>{fmt(r.pts)}</td>
                                            <td>{fmt(r.reb)}</td>
                                            <td>{fmt(r.ast)}</td>
                                            <td>{fmt(r.stl)}</td>
                                            <td>{fmt(r.blk)}</td>
                                            <td>{pct(r.ts_pct)}</td>
                                            <td>{fmt(r.net_rating)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
