import React, { useEffect, useState } from 'react';
import { fetchTeamAssists } from '../../services/api';
import Loader from '../Loader';
import AssistNetwork from './AssistNetwork';
import { pickNetwork } from '../../utils/assistNetwork';
import PlayerName from './PlayerName';
import '../../styles/assists.css';

// The team page's assist block (GET /assists/team, 2020-21 on): the
// network of its top eight players by minutes, its top five pairs and a
// link to the full Assist Network page.

const pct = (v, d = 1) => (v == null ? '—' : `${(v * 100).toFixed(d)}%`);
const NODES = 8;
const PAIRS = 5;

export default function TeamAssistBlock({ abbr, season, onNavigate }) {
    const key = `${abbr}-${season}`;
    const [res, setRes] = useState(null); // { key, data } | { key, error }

    useEffect(() => {
        let active = true;
        fetchTeamAssists(abbr, season)
            .then((d) => { if (active) setRes({ key, data: d }); })
            .catch((e) => { if (active) setRes({ key, error: e.response?.data?.detail || 'The assist network couldn\'t load.' }); });
        return () => { active = false; };
    }, [abbr, season, key]);

    const d = res?.key === key ? res.data : null;
    const error = res?.key === key ? res.error : '';
    if (error) return <p className="error-message">{error}</p>;
    if (!d) return <Loader />;
    const t = d.totals;
    const { lines } = pickNetwork(d.players, d.edges, NODES, 1);
    const minEdge = [5, 10, 15, 20, 30, 50].find((m) => lines.filter((e) => e.ast >= m).length <= 24) ?? 50;
    return (
        <>
            <p className="rx-verdict">
                {t.assisted.toLocaleString()} of {t.fgm.toLocaleString()} made shots assisted ({pct(t.share)}){t.rank ? `, #${t.rank} of ${t.n_teams}` : ''};
                {' '}league {pct(d.league.assisted_share)}.
            </p>
            <AssistNetwork players={d.players} edges={d.edges} nPlayers={NODES} minEdge={minEdge}
                name={`assist network ${d.team} ${d.season_label}`} />
            <p className="page-subtitle pp-foot">Top {NODES} players by minutes; lines for pairs with {minEdge}+ assists.</p>
            <h3 className="rp-panel-title">Top pairs</h3>
            <ul className="an-list">
                {d.edges.filter((e) => e.scorer_id !== 0).slice(0, PAIRS).map((e) => (
                    <li key={`${e.passer_id}-${e.scorer_id}`}>
                        <span className="an-pair">
                            <PlayerName playerId={e.passer_id} name={e.passer_name} size={20} />
                            <span aria-label="to">→</span>
                            <PlayerName playerId={e.scorer_id} name={e.scorer_name} size={20} />
                        </span>
                        <span className="an-list-n">{e.ast} ast · {e.games} games · {e.kinds.three} 3s</span>
                    </li>
                ))}
            </ul>
            <button type="button" className="pp-link" style={{ marginTop: 'var(--space-3)' }}
                onClick={() => onNavigate('assists', null, { team: d.team, season: d.season })}>
                Open the full assist network →
            </button>
        </>
    );
}
