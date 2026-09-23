import React, { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { fetchStatLeaders, fetchHustleLeaders } from '../../services/api';
import PlayerHeadshot from '../common/PlayerHeadshot';
import TeamLogo from '../common/TeamLogo';
import InfoTooltip from '../common/InfoTooltip';
import Icon from '../common/Icon';
import PlayerDetailModal from '../common/PlayerDetailModal';
import { STAT_GLOSSARY } from '../../utils/statGlossary';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';

const STAT_OPTIONS = [
  { key: 'pts', label: 'Points' },
  { key: 'reb', label: 'Rebounds' },
  { key: 'ast', label: 'Assists' },
  { key: 'dreb', label: 'Def. Rebounds' },
  { key: 'oreb', label: 'Off. Rebounds' },
  { key: 'stl', label: 'Steals' },
  { key: 'blk', label: 'Blocks' },
  { key: 'tov', label: 'Turnovers' },
  { key: 'fg_pct', label: 'FG%' },
  { key: 'fg3_pct', label: '3P%' },
  { key: 'ft_pct', label: 'FT%' },
  { key: 'fg3m', label: '3PM' },
  { key: 'plus_minus', label: 'Plus/Minus' },
];

const HUSTLE_STAT_OPTIONS = [
  { key: 'deflections', label: 'Deflections' },
  { key: 'contested_shots', label: 'Contested Shots' },
  { key: 'screen_assists', label: 'Screen Assists' },
  { key: 'loose_balls_recovered', label: 'Loose Balls' },
  { key: 'charges_drawn', label: 'Charges Drawn' },
  { key: 'box_outs', label: 'Box Outs' },
];
const HUSTLE_KEYS = new Set(HUSTLE_STAT_OPTIONS.map((s) => s.key));

export default function StatLeaders() {
  const [statKey, setStatKey] = useState('pts');
  const [leaders, setLeaders] = useState([]);
  const [season, setSeason] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedPlayer, setSelectedPlayer] = useState(null);

  const { isAdvanced } = useMotionMode();
  const preset = motionPreset(isAdvanced);

  const selectedStatLabel = useMemo(
    () => [...STAT_OPTIONS, ...HUSTLE_STAT_OPTIONS].find((s) => s.key === statKey)?.label || statKey.toUpperCase(),
    [statKey]
  );
  const statDef = STAT_GLOSSARY[statKey];

  useEffect(() => {
    let mounted = true;
    (async () => {
      setLoading(true);
      setError('');
      try {
        const data = HUSTLE_KEYS.has(statKey)
          ? await fetchHustleLeaders(statKey, undefined, 10)
          : await fetchStatLeaders(statKey, undefined, 10);
        if (!mounted) return;
        const normalized = (Array.isArray(data?.results) ? data.results : []).map((r) => ({
          ...r,
          team_abbr: r.team_abbr || r.team_abbreviation,
        }));
        setLeaders(normalized);
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

  const leader = leaders[0] || null;

  return (
    <div className="page fade-in">
      <div className="hb-shell">
        <div className="hb-rail">
          <div className="hb-rail-title">
            <Icon name="leaderboard" />
            Stat Leaders
          </div>
          <p className="page-subtitle" style={{ marginTop: '-0.75rem' }}>
            Top 10 {season ? `· ${season}` : ''}
          </p>

          <div className="hb-rail-group">
            <span className="hb-rail-label">Stat</span>
            <div className="hb-rail-list">
              {STAT_OPTIONS.map((opt) => (
                <button
                  key={opt.key}
                  type="button"
                  className={`hb-rail-item ${opt.key === statKey ? 'hb-rail-item--active' : ''}`}
                  onClick={() => setStatKey(opt.key)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <div className="hb-rail-group">
            <span className="hb-rail-label">Hustle (real effort stats)</span>
            <div className="hb-rail-list">
              {HUSTLE_STAT_OPTIONS.map((opt) => (
                <button
                  key={opt.key}
                  type="button"
                  className={`hb-rail-item ${opt.key === statKey ? 'hb-rail-item--active' : ''}`}
                  onClick={() => setStatKey(opt.key)}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="hb-main">
          {error && <p className="error-message">{error}</p>}
          {loading && <p className="page-subtitle">Loading leaders…</p>}

          {!loading && leader && (
            <motion.div
              key={`hero-${leader.player_id}-${statKey}-${isAdvanced}`}
              className="hb-hero"
              initial={isAdvanced ? { opacity: 0, y: -8 } : false}
              animate={{ opacity: 1, y: 0 }}
              transition={preset.spring}
            >
              <span className="hb-row-avatar">
                <PlayerHeadshot playerId={leader.player_id} playerName={leader.player_name} size={40} />
              </span>
              <div className="hb-hero-text">
                <div className="hb-hero-label">League leader — {selectedStatLabel}</div>
                <div className="hb-hero-name">{leader.player_name}</div>
              </div>
              <div className="hb-hero-value">{leader.value}</div>
            </motion.div>
          )}

          {!loading && (
            <motion.div
              key={`${statKey}-${isAdvanced}`}
              className="hb-table-wrapper table-wrapper"
              initial={isAdvanced ? { opacity: 0, y: 6 } : false}
              animate={{ opacity: 1, y: 0 }}
              transition={preset.tableTransition}
            >
              <table className="data-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Player</th>
                    <th>Team</th>
                    <th>
                      {selectedStatLabel}
                      {statDef && (
                        <InfoTooltip label={`What is ${statDef.title}?`} title={statDef.title}>
                          {statDef.formula && <><code className="stat-formula">{statDef.formula}</code><br /></>}
                          {statDef.body}
                        </InfoTooltip>
                      )}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {!leaders.length && (
                    <tr>
                      <td colSpan={4} className="empty-message">No data available for this stat yet.</td>
                    </tr>
                  )}
                  {leaders.map((row) => (
                    <tr
                      key={`${row.player_name}-${row.team_abbr}-${row.rank}`}
                      className="clickable-row"
                      onClick={() => setSelectedPlayer({ ...row, team_abbreviation: row.team_abbr })}
                    >
                      <td>{row.rank}</td>
                      <td>
                        <span className="entity-row">
                          <span className="hb-row-avatar">
                            <PlayerHeadshot playerId={row.player_id} playerName={row.player_name} size={26} />
                          </span>
                          {row.player_name}
                        </span>
                      </td>
                      <td>
                        <span className="entity-row">
                          <TeamLogo abbreviation={row.team_abbr} size={18} />
                          {row.team_abbr || '-'}
                        </span>
                      </td>
                      <td className="hb-cell-accent">{row.value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </motion.div>
          )}
        </div>
      </div>
      <AnimatePresence>
        {selectedPlayer && (
          <PlayerDetailModal
            key={selectedPlayer.player_id}
            player={selectedPlayer}
            onClose={() => setSelectedPlayer(null)}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
