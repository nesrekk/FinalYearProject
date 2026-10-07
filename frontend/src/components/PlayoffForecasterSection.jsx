import React, { useEffect, useRef, useState } from 'react';
import { fetchPlayoffComparison } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import Icon from './common/Icon';
import PlayerHeadshot from './common/PlayerHeadshot';
import AutocompleteDropdown from './common/AutocompleteDropdown';
import NamesakeNote from './common/NamesakeNote';
import usePlayerSuggestions from '../utils/usePlayerSuggestions';
import { STAT_GLOSSARY } from '../utils/statGlossary';
import TableExport from './common/TableExport';
import SeasonSelect from './common/SeasonSelect';
import { latestCompleteSeason } from '../utils/season';

const ROWS = [
    { key: 'ts_pct', label: 'True Shooting %', pct: true },
    { key: 'usg_pct', label: 'Usage %', pct: true },
    { key: 'net_rating', label: 'Net Rating', pct: false },
    { key: 'ast_pct', label: 'Assist %', pct: true },
    { key: 'reb_pct', label: 'Rebound %', pct: true },
];

function fmt(v, pct, digits = 1) {
    if (v == null) return '—';
    return pct ? `${(v * 100).toFixed(digits)}%` : v.toFixed(digits);
}

function deltaColor(v) {
    if (v == null) return 'var(--text-muted)';
    if (v > 0.005) return 'var(--positive)';
    if (v < -0.005) return 'var(--negative)';
    return 'var(--text-secondary)';
}

export default function PlayoffForecasterSection() {
    const searchInputRef = useRef(null);
    const [searchInput, setSearchInput] = useState('Shai Gilgeous-Alexander');
    // { name, id }: the id (from a suggestion) picks the player exactly; a typed name opens the latest of that name.
    const [player, setPlayer] = useState({ name: 'Shai Gilgeous-Alexander', id: null });
    const [season, setSeason] = useState(() => latestCompleteSeason());
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

    const sug = usePlayerSuggestions(searchInput, player.name);

    useEffect(() => {
        let active = true;
        setLoading(true);
        setError('');
        (async () => {
            try {
                const res = await fetchPlayoffComparison(player.name, season, player.id || undefined);
                if (active) setData(res);
            } catch (e) {
                if (active) {
                    setData(null);
                    setError(e?.response?.data?.detail || 'Could not load playoff comparison.');
                }
            } finally {
                if (active) setLoading(false);
            }
        })();
        return () => { active = false; };
    }, [player, season]);

    function pick(next) {
        sug.dismiss();
        setSearchInput(next.name);
        setPlayer(next);
    }

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Playoff Drop-off Forecaster
                    <InfoTooltip label="How this works" title="Real before/after, no predictive model">
                        Regular-season stats come from this project's database; playoff stats are live-fetched
                        from the NBA's own advanced stats (real games, same season). This just shows what
                        actually happened — no regression, no predicted "playoff tax," since a real predictive
                        model would need far more historical playoff data than a season-by-season live fetch can
                        responsibly gather. Playoff sample sizes are often well under 20 games (sometimes as few
                        as 4), so a small-sample warning shows whenever that's the case — don't read too much
                        into a 4-game sweep.
                    </InfoTooltip>
                    <SourceBadge source={data?._source} />
                </h3>
                <div style={{ position: 'relative', display: 'flex', gap: '0.75rem', flexWrap: 'wrap', alignItems: 'center' }}>
                    <div style={{ position: 'relative', flex: 1, minWidth: 220 }}>
                        <input
                            ref={searchInputRef}
                            type="text"
                            className="input-field"
                            placeholder="Player name…"
                            aria-label="Player"
                            value={searchInput}
                            onChange={(e) => setSearchInput(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter' && searchInput.trim()) pick({ name: searchInput.trim(), id: null }); }}
                            style={{ width: '100%' }}
                        />
                        <AutocompleteDropdown anchorRef={searchInputRef} items={sug.labels}
                            onPick={(label) => { const p = sug.pick(label); if (p) pick({ name: p.name, id: p.id }); }} />
                    </div>
                    <SeasonSelect value={season} onChange={setSeason} from={2010} to={latestCompleteSeason()} />
                </div>
                <NamesakeNote name={data?.player_name} id={data?.player_id} onPick={pick} />
                {error && <p className="error-message" style={{ marginTop: '0.5rem' }}>{error}</p>}
            </div>

            {loading && <Loader />}

            {!loading && data && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <div className="entity-row" style={{ marginBottom: '0.75rem' }}>
                        <PlayerHeadshot playerId={data.player_id} playerName={data.player_name} size={48} />
                        <div className="entity-row-text">
                            <span className="entity-row-name">{data.player_name}</span>
                            <span className="entity-row-sub">
                                {data.regular_season.team_abbreviation} · {data.regular_season.gp} regular-season games
                                {data.playoffs && ` · ${data.playoffs.gp} real playoff games`}
                            </span>
                        </div>
                    </div>

                    {!data.playoffs ? (
                        <p className="page-subtitle" style={{ margin: 0 }}>{data.note}</p>
                    ) : (
                        <>
                            {data.small_sample_warning && (
                                <p className="page-subtitle" style={{ color: 'var(--streak)', marginTop: 0 }}>
                                    <Icon name="warning" size="0.9em" style={{ verticalAlign: 'middle', marginRight: 4 }} />
                                    Only {data.playoffs.gp} real playoff games this season — treat this comparison as noisy.
                                </p>
                            )}
                            <TableExport />
                            <div className="hb-table-wrapper table-wrapper">
                                <table className="data-table">
                                    <thead>
                                        <tr>
                                            <th>Stat</th>
                                            <th>Regular Season</th>
                                            <th>Playoffs (real)</th>
                                            <th>Change</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {ROWS.map((r) => {
                                            const def = STAT_GLOSSARY[r.key];
                                            const delta = data.deltas[r.key];
                                            return (
                                                <tr key={r.key}>
                                                    <td>
                                                        {r.label}
                                                        {def && (
                                                            <InfoTooltip label={`What is ${def.title}?`} title={def.title}>
                                                                {def.formula && <><code className="stat-formula">{def.formula}</code><br /></>}
                                                                {def.body}
                                                            </InfoTooltip>
                                                        )}
                                                    </td>
                                                    <td>{fmt(data.regular_season[r.key], r.pct)}</td>
                                                    <td>{fmt(data.playoffs[r.key], r.pct)}</td>
                                                    <td style={{ color: deltaColor(delta), fontWeight: 700 }}>
                                                        {delta != null && (
                                                            <Icon
                                                                name={delta > 0 ? 'arrow_upward' : delta < 0 ? 'arrow_downward' : 'remove'}
                                                                size="0.9em"
                                                                style={{ verticalAlign: 'middle', marginRight: 2 }}
                                                            />
                                                        )}
                                                        {fmt(delta, r.pct)}
                                                    </td>
                                                </tr>
                                            );
                                        })}
                                    </tbody>
                                </table>
                            </div>
                        </>
                    )}
                </div>
            )}
        </div>
    );
}
