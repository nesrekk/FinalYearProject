import React, { useCallback, useEffect, useRef, useState } from 'react';
import { fetchSeasonSimilarityProfile } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';
import SourceBadge from './common/SourceBadge';
import PlayerName from './common/PlayerName';
import TableExport from './common/TableExport';
import AutocompleteDropdown from './common/AutocompleteDropdown';
import NamesakeNote from './common/NamesakeNote';
import usePlayerSuggestions from '../utils/usePlayerSuggestions';
import { signed as signedNum } from '../utils/format';

// Similarity inputs exist from 2009-10 on (usage, net rating, AST%/REB%).
// The last season updates from the API's pool after the first search.
const FIRST_SEASON = 2010;
const DEFAULT_LAST_SEASON = 2026;

const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;
const pct = (v) => `${(v * 100).toFixed(1)}%`;
const signed = (v) => signedNum(v, 1, '-'); // sign of the shown value; hyphen minus as before

const STAT_COLUMNS = [
    { key: 'pts', label: 'PTS', fmt: (v) => v.toFixed(1) },
    { key: 'ts_pct', label: 'TS%', fmt: pct },
    { key: 'usg_pct', label: 'USG%', fmt: pct },
    { key: 'net_rating', label: 'Net', fmt: signed },
    { key: 'ast_pct', label: 'AST%', fmt: pct },
    { key: 'reb_pct', label: 'REB%', fmt: pct },
    { key: 'age', label: 'Age', fmt: (v) => v.toFixed(0) },
    { key: 'min', label: 'MIN', fmt: (v) => v.toFixed(1) },
];

const WORDS = {
    pts: 'scoring', ts_pct: 'true shooting', usg_pct: 'usage', net_rating: 'net rating',
    ast_pct: 'assist %', reb_pct: 'rebound %', age: 'age', min: 'minutes',
};

function why(row) {
    const d = row.differs_most;
    const diff = d.feature === 'age'
        ? (d.direction === 'higher' ? 'older' : 'younger')
        : `${d.direction} ${WORDS[d.feature]}`;
    return `Closest on ${row.closest_on.map((f) => WORDS[f]).join(' and ')}; differs most: ${diff}`;
}

function StatCells({ stats }) {
    return STAT_COLUMNS.map((c) => (
        <td key={c.key} className="sim-num">{c.fmt(stats[c.key])}</td>
    ));
}

export default function SimilaritySection() {
    const [player, setPlayer] = useState('');
    const [season, setSeason] = useState(String(DEFAULT_LAST_SEASON));
    const [lastSeason, setLastSeason] = useState(DEFAULT_LAST_SEASON);
    const [excludeSelf, setExcludeSelf] = useState(true);
    const [minGp, setMinGp] = useState(20);
    const [onePerPlayer, setOnePerPlayer] = useState(false);
    const [topN, setTopN] = useState(10);
    const [searched, setSearched] = useState(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [picked, setPicked] = useState(null); // { name, id } from a suggestion

    // A suggestion carries the NBA id (two players can share a name); typed text opens the latest of that name.
    const sug = usePlayerSuggestions(player, picked?.name);
    const inputRef = useRef(null);

    const load = useCallback(async (target) => {
        setLoading(true);
        setError('');
        try {
            const res = await fetchSeasonSimilarityProfile(target.player, target.season,
                { topN, excludeSelf, minGp, onePerPlayer, playerId: target.id || undefined });
            setData(res);
            if (res?.pool?.to) setLastSeason(res.pool.to);
        } catch (err) {
            setData(null);
            setError(err.response?.data?.detail || 'Failed to fetch similarity data.');
        } finally {
            setLoading(false);
        }
    }, [topN, excludeSelf, minGp, onePerPlayer]);

    // Changing a filter re-runs the last search.
    useEffect(() => {
        if (searched) load(searched);
    }, [searched, load]);

    const handleSearch = (e) => {
        e.preventDefault();
        if (!player.trim() || !season) return;
        sug.dismiss();
        const id = picked && picked.name === player.trim() ? picked.id : null;
        setSearched({ player: player.trim(), season: Number(season), id });
    };

    const seasons = [];
    for (let s = lastSeason; s >= FIRST_SEASON; s -= 1) seasons.push(s);
    const q = data?.query;

    return (
        <section className="dashboard-card">
            <h2 className="card-title hb-page-title">
                <span className="card-icon"><Icon name="bar_chart" /></span>
                Season Similarity
                <InfoTooltip label="How Season Similarity works" title="Under the hood">
                    {data?.methodology ?? (
                        'Each season is described by eight numbers: points, true shooting, usage, net rating, assist % and '
                        + 'rebound % (each z-scored within its own season, so eras compare fairly), plus age and minutes. '
                        + 'Seasons are compared by the cosine of those vectors (1 = identical shape).'
                    )}
                </InfoTooltip>
                <SourceBadge source={data?._source} />
            </h2>
            <p className="page-subtitle" style={{ marginTop: '0.25rem' }}>
                Pick a player-season to find the seasons that look most like it, and see why they match.
            </p>

            <form className="input-row" onSubmit={handleSearch}>
                <input
                    ref={inputRef}
                    type="text"
                    placeholder="Player name"
                    aria-label="Player name"
                    value={player}
                    onChange={(e) => setPlayer(e.target.value)}
                    className="input-field"
                />
                <AutocompleteDropdown anchorRef={inputRef} items={sug.labels}
                    onPick={(label) => {
                        const p = sug.pick(label);
                        if (!p) return;
                        setPlayer(p.name);
                        setPicked({ name: p.name, id: p.id });
                        sug.dismiss();
                    }} />
                <select
                    className="input-field"
                    aria-label="Season"
                    value={season}
                    onChange={(e) => setSeason(e.target.value)}
                >
                    {seasons.map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                </select>
                <button type="submit" className="action-btn" disabled={loading || !player.trim()}>
                    {loading ? 'Searching…' : 'Find Similar Seasons'}
                </button>
            </form>

            <NamesakeNote name={q?.player_name} id={q?.player_id}
                onPick={(p) => { setPlayer(p.name); setPicked(p); setSearched({ player: p.name, season: Number(season), id: p.id }); }} />

            <div className="sim-filters">
                <label>
                    <input type="checkbox" checked={excludeSelf} onChange={(e) => setExcludeSelf(e.target.checked)} />
                    Other players only
                </label>
                <label>
                    <input type="checkbox" checked={onePerPlayer} onChange={(e) => setOnePerPlayer(e.target.checked)} />
                    One season per player
                </label>
                <label>
                    Min. games{' '}
                    <select value={minGp} onChange={(e) => setMinGp(Number(e.target.value))}>
                        {[0, 10, 20, 40, 60].map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                </label>
                <label>
                    Show{' '}
                    <select value={topN} onChange={(e) => setTopN(Number(e.target.value))}>
                        {[10, 25].map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                </label>
            </div>

            {loading && <Loader />}
            {error && <p className="error-message">{error}</p>}
            {!loading && q && (
                <>
                    <p className="page-subtitle sim-summary">
                        {data.results.length} closest of {data.pool.seasons.toLocaleString()} player-seasons
                        ({seasonLabel(data.pool.from)} to {seasonLabel(data.pool.to)})
                        {data.filters.exclude_self ? ', other players only' : ''}
                        {data.filters.one_per_player ? ', one season per player' : ''}
                        {data.filters.min_gp ? `, ${data.filters.min_gp}+ games` : ''}.
                        Similarity 1.000 = identical shape.
                    </p>
                    <TableExport name={`seasons like ${q.player_name} ${seasonLabel(q.season)}`} />
                    <div className="table-wrapper">
                        <table className="data-table sim-table">
                            <thead>
                                <tr>
                                    <th>#</th>
                                    <th>Player</th>
                                    <th>Season</th>
                                    <th>Team</th>
                                    <th className="sim-num">GP</th>
                                    <th className="sim-num">Similarity</th>
                                    {STAT_COLUMNS.map((c) => <th key={c.key} className="sim-num">{c.label}</th>)}
                                    <th>Why</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr className="sim-query-row">
                                    <td>Searched</td>
                                    <td><PlayerName playerId={q.player_id} name={q.player_name} /></td>
                                    <td>{seasonLabel(q.season)}</td>
                                    <td>{q.team}</td>
                                    <td className="sim-num">{q.gp}</td>
                                    <td className="sim-num">—</td>
                                    <StatCells stats={q.stats} />
                                    <td>—</td>
                                </tr>
                                {data.results.map((r) => (
                                    <tr key={`${r.player_id}-${r.season}`}>
                                        <td>{r.rank}</td>
                                        <td><PlayerName playerId={r.player_id} name={r.player_name} /></td>
                                        <td>{seasonLabel(r.season)}</td>
                                        <td>{r.team}</td>
                                        <td className="sim-num">{r.gp}</td>
                                        <td className="sim-num">{r.similarity_score.toFixed(3)}</td>
                                        <StatCells stats={r.stats} />
                                        <td className="sim-why">{why(r)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    {data.results.length === 0 && (
                        <p className="empty-message">No seasons pass these filters.</p>
                    )}
                </>
            )}
        </section>
    );
}
