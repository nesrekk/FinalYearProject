import React, { useEffect, useMemo, useState } from 'react';
import { fetchStatLeaders } from '../../services/api';

const STAT_OPTIONS = [
  { key: 'pts', label: 'Points (PTS)' },
  { key: 'reb', label: 'Rebounds (REB)' },
  { key: 'ast', label: 'Assists (AST)' },
  { key: 'dreb', label: 'Defensive Rebounds (DREB)' },
  { key: 'oreb', label: 'Offensive Rebounds (OREB)' },
  { key: 'stl', label: 'Steals (STL)' },
  { key: 'blk', label: 'Blocks (BLK)' },
  { key: 'tov', label: 'Turnovers (TOV)' },
  { key: 'fg_pct', label: 'FG% (FG_PCT)' },
  { key: 'fg3_pct', label: '3P% (FG3_PCT)' },
  { key: 'ft_pct', label: 'FT% (FT_PCT)' },
  { key: 'fg3m', label: '3PM (FG3M)' },
  { key: 'plus_minus', label: 'Plus/Minus (+/-)' },
];

export default function StatLeaders() {
  const [statKey, setStatKey] = useState('pts');
  const [leaders, setLeaders] = useState([]);
  const [season, setSeason] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const selectedStatLabel = useMemo(
    () => STAT_OPTIONS.find((s) => s.key === statKey)?.label || statKey.toUpperCase(),
    [statKey]
  );

  useEffect(() => {
    let mounted = true;
    (async () => {
      setLoading(true);
      setError('');
      try {
        const data = await fetchStatLeaders(statKey, undefined, 10);
        if (!mounted) return;
        setLeaders(Array.isArray(data?.results) ? data.results : []);
        setSeason(data?.season || null);
      } catch (err) {
        if (!mounted) return;
        setLeaders([]);
        setError(err?.response?.data?.detail || 'Failed to load stat leaders.');
      } finally {
        if (mounted) setLoading(false);
      }
    })();

    return () => {
      mounted = false;
    };
  }, [statKey]);

  return (
    <section className="sl-shell fade-in">
      <div className="sl-top">
        <div className="sl-title-wrap">
          <h2 className="sl-title">Stat Leaders</h2>
          <p className="sl-subtitle">
            Top 10 players by selected stat {season ? `(${season} season)` : ''}.
          </p>
        </div>

        <div className="sl-controls">
          <label className="sl-label" htmlFor="stat-select">
            Stat:
          </label>
          <select
            id="stat-select"
            className="search-input sl-select"
            value={statKey}
            onChange={(e) => setStatKey(e.target.value)}
          >
            {STAT_OPTIONS.map((opt) => (
              <option key={opt.key} value={opt.key}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="sl-chip-row">
        {STAT_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            className={`sl-chip ${opt.key === statKey ? 'sl-chip--active' : ''}`}
            type="button"
            onClick={() => setStatKey(opt.key)}
          >
            {opt.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="error-message">{error}</div>
      )}

      <div className="table-wrapper sl-table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Player</th>
              <th>Team</th>
              <th>{selectedStatLabel}</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={4}>Loading leaders...</td>
              </tr>
            )}
            {!loading && !leaders.length && (
              <tr>
                <td colSpan={4}>No data available for this stat yet.</td>
              </tr>
            )}
            {!loading &&
              leaders.map((row) => (
                <tr key={`${row.player_name}-${row.team_abbr}-${row.rank}`}>
                  <td className="rank-cell">
                    {row.rank <= 3 ? (
                      <span className={`sl-medal sl-medal--${row.rank}`}>{row.rank}</span>
                    ) : (
                      row.rank
                    )}
                  </td>
                  <td className="sl-player-name">{row.player_name}</td>
                  <td>
                    <span className="team-abbr-badge">{row.team_abbr || '-'}</span>
                  </td>
                  <td className="sl-value-cell">{row.value}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
