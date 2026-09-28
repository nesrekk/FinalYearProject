import React, { useEffect, useState } from 'react';
import { fetchSituationalPlayer } from '../../services/api';
import Loader from '../Loader';
import InfoTooltip from './InfoTooltip';
import TableExport from './TableExport';
import { SHORT_SIDES, fmtGap, fmtR, fmtValue, seasonLabel } from './situationalFormat';
import { openPage } from '../../utils/useUrlState';
import '../../styles/gamelog.css';
import '../../styles/splits.css';

// Situational splits on the player profile (GET /splits/situational/player/{id},
// player_situational_splits, 2020-21 on): home/away, back-to-back/rested, long
// trip/short, top-10/bottom-10 opponents, every stat, against the average
// player's gap that season.

export default function SituationalSplitsBlock({ playerId, seasons }) {
    const have = seasons.map((s) => s.season);
    const [season, setSeason] = useState(have[have.length - 1]);
    const [split, setSplit] = useState('home');
    const [loaded, setLoaded] = useState({});
    const [error, setError] = useState('');
    const data = loaded[season];

    useEffect(() => {
        if (loaded[season]) return undefined;
        let active = true;
        fetchSituationalPlayer(playerId, season)
            .then((d) => { if (active) { setError(''); setLoaded((m) => ({ ...m, [season]: d })); } })
            .catch((e) => { if (active) setError(e.response?.data?.detail || 'Could not load that season.'); });
        return () => { active = false; };
    }, [playerId, season, loaded]);

    const block = data?.splits.find((s) => s.split === split);
    const fewA = block && block.games_a < data.min_games;
    const fewB = block && block.games_b < data.min_games;

    return (
        <section id="pp-splits" className="dashboard-card pp-section">
            <h2 className="card-title pp-section-title">
                Situational splits
                <InfoTooltip label="How situational splits are computed" title="His gap against the average player's">
                    Each side is the ratio of his totals over those games (points per 36 = 36 × points ÷ minutes). The
                    95% interval treats each game as one draw. &ldquo;Average player&rdquo; is the league-wide gap that
                    season; a gap whose interval covers it is within normal noise. Repeats = how well a player&apos;s gap
                    in one season predicts his gap the next, across the league: near zero means the split mostly
                    measures luck.
                </InfoTooltip>
            </h2>
            <p className="page-subtitle pp-meta">
                Regular season, 2020-21 on (game-by-game lines are rebuilt from play-by-play). Full-color rows sit
                outside the average player&apos;s gap by more than chance would allow; greyed rows are within noise or
                under the sample floor.{' '}
                <button type="button" className="pp-link"
                    onClick={() => openPage('splits', { season, split, stat: 'pts' })}>Open Situational Splits</button>
            </p>
            <div className="pp-row gl-controls">
                <label className="pp-select">
                    <span>Season</span>
                    <select className="input-field" value={season} onChange={(e) => { setError(''); setSeason(Number(e.target.value)); }}>
                        {[...have].reverse().map((s) => <option key={s} value={s}>{seasonLabel(s)}</option>)}
                    </select>
                </label>
                <div className="tab-bar sp-tabs" role="tablist" aria-label="Situation">
                    {(data?.splits ?? []).map((s) => (
                        <button key={s.split} type="button" role="tab" aria-selected={split === s.split}
                            className={`tab-btn ${split === s.split ? 'tab-btn--active' : ''}`} onClick={() => setSplit(s.split)}>
                            {s.label}
                        </button>
                    ))}
                </div>
            </div>
            {error && !data && <p className="error-message">{error}</p>}
            {!data && !error && <Loader />}
            {block && (
                <>
                    <p className="gl-summary">
                        {block.a}: <strong>{block.games_a}</strong> games · {block.b}: <strong>{block.games_b}</strong> games
                        {(fewA || fewB) && (
                            <span className="pp-warn gl-gap">
                                Under {data.min_games} games on one side: every row is below the sample floor and shown for
                                reference only.
                            </span>
                        )}
                    </p>
                    <TableExport name={`${data.player_name} ${block.split} splits ${seasonLabel(season)}`} />
                    <div className="table-wrapper pp-scroll">
                        <table className="data-table lb-table pp-table sp-table">
                            <thead>
                                <tr>
                                    <th>Stat</th>
                                    <th className="lb-num" title={block.a}>{SHORT_SIDES[block.split][0]}</th>
                                    <th className="lb-num" title={block.b}>{SHORT_SIDES[block.split][1]}</th>
                                    <th className="lb-num">Gap</th>
                                    <th className="lb-num">95% interval</th>
                                    <th className="lb-num">Average player</th>
                                    <th className="lb-num">vs. average</th>
                                    <th className="lb-num" title="Across the league, 2020-21 to 2025-26: correlation of a player's gap (vs. average) with his gap the next season">Repeats (r)</th>
                                </tr>
                            </thead>
                            <tbody>
                                {block.stats.map((s) => {
                                    const r = s.row;
                                    const stand = r && r.qualified && r.ci_excludes_league;
                                    const cls = !r || !r.qualified ? 'sl-short' : stand ? '' : 'pp-small';
                                    const why = !r ? 'Not enough games' : !r.qualified
                                        ? `Below the floor: ${data.min_games}+ games a side${s.side_floor ? ` and ${s.side_floor} ${s.format === 'pct' ? 'attempts' : 'minutes'} a side` : ''}`
                                        : stand ? 'Outside normal noise around the average player\'s gap' : 'Within normal noise';
                                    return (
                                        <tr key={s.stat} className={cls} title={why}>
                                            <td>{s.label}</td>
                                            <td className="lb-num">{fmtValue(r?.value_a, s.stat, s.format)}</td>
                                            <td className="lb-num">{fmtValue(r?.value_b, s.stat, s.format)}</td>
                                            <td className={`lb-num lb-stat ${stand ? (r.vs_league > 0 ? 'pp-pos' : 'pp-neg') : ''}`}>{fmtGap(r?.diff, s.stat, s.format)}</td>
                                            <td className="lb-num">{r ? `${fmtGap(r.ci_low, s.stat, s.format, { unit: false })} to ${fmtGap(r.ci_high, s.stat, s.format, { unit: false })}` : '—'}</td>
                                            <td className="lb-num">{fmtGap(s.league_diff, s.stat, s.format)}</td>
                                            <td className="lb-num">{r?.qualified ? fmtGap(r.vs_league, s.stat, s.format) : '—'}</td>
                                            <td className="lb-num">{fmtR(s.chance?.yoy_r)}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                    <p className="page-subtitle pp-foot">
                        Gap = {block.a.toLowerCase()} minus {block.b.toLowerCase()}; pp = percentage points. Most full-color
                        rows won&apos;t repeat: league-wide, a player&apos;s split one season barely predicts the next (the
                        Repeats column), so read these as what happened, not as traits.
                    </p>
                </>
            )}
        </section>
    );
}
