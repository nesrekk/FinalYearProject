import React, { useEffect, useState } from 'react';
import { fetchTeamRotation } from '../../services/api';
import Loader from '../Loader';
import PlayerName from './PlayerName';
import RotationHeatmap from './RotationHeatmap';
import '../../styles/rotations.css';

// The team page's rotation block (GET /rotations/team, 2020-21 on): the
// heatmap of its top players, the most common starting five and the
// most-used closing five, and a link to the full Rotations page.

const num = (v, d = 1) => (v == null ? '—' : Number(v).toFixed(d));
const signed = (v) => (v == null ? '—' : v > 0 ? `+${v}` : v < 0 ? `−${Math.abs(v)}` : '0');
const ROWS = 12;

export default function TeamRotationBlock({ abbr, season, onNavigate }) {
    const key = `${abbr}-${season}`;
    const [res, setRes] = useState(null); // { key, data } | { key, error }

    useEffect(() => {
        let active = true;
        fetchTeamRotation(abbr, season)
            .then((d) => { if (active) setRes({ key, data: d }); })
            .catch((e) => { if (active) setRes({ key, error: e.response?.data?.detail || 'The rotation couldn\'t load.' }); });
        return () => { active = false; };
    }, [abbr, season, key]);

    const d = res?.key === key ? res.data : null;
    const error = res?.key === key ? res.error : '';
    if (error) return <p className="error-message">{error}</p>;
    if (!d) return <Loader />;
    const c = d.closing;
    const cf = c.most_minutes;
    return (
        <>
            <p className="rx-verdict">
                {d.games_counted} of {d.games} games counted{d.excluded.length ? ` (${d.excluded.length} whose play-by-play didn't reconcile left out)` : ''}.
                {d.starting_five && <> Most common starting five: {d.starting_five.games} starts together.</>}
                {c.available && <> Close games (within 5 at 5:00 left in the fourth): {c.close_games}, {c.record.wins}-{c.record.losses}.</>}
            </p>
            <RotationHeatmap data={d} maxRows={ROWS} name={`rotation heatmap ${abbr} ${d.season_label}`} />
            <div className="rot-fives">
                {d.starting_five && (
                    <div className="rot-five-card">
                        <h3 className="rp-panel-title">Starting five</h3>
                        <div className="rot-five-list">
                            {d.starting_five.players.map((p) => <PlayerName key={p.player_id} playerId={p.player_id} name={p.player_name ?? `#${p.player_id}`} size={20} />)}
                        </div>
                        <p className="rot-five-meta">{d.starting_five.games} of {d.games_counted} games.</p>
                    </div>
                )}
                {cf && (
                    <div className="rot-five-card">
                        <h3 className="rp-panel-title">Most-used closing five</h3>
                        <div className="rot-five-list">
                            {cf.players.map((p) => <PlayerName key={p.player_id} playerId={p.player_id} name={p.player_name ?? `#${p.player_id}`} size={20} />)}
                        </div>
                        <p className="rot-five-meta">
                            {num(cf.minutes)} of {num(c.stretch_minutes)} closing minutes in {cf.games} close games, {signed(cf.plus_minus)}.
                        </p>
                    </div>
                )}
            </div>
            <div className="tp-links">
                <button type="button" className="pp-link" onClick={() => onNavigate('rotations', null, { team: abbr, season })}>
                    Open the full rotation page (every player, closing lineups, any game) →
                </button>
            </div>
        </>
    );
}
