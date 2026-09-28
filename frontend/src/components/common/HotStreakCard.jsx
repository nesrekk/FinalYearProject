import React, { useEffect, useState } from 'react';
import { fetchHotStreak } from '../../services/api';
import InfoTooltip from './InfoTooltip';
import { openPage } from '../../utils/useUrlState';

// "Is his last N games real?" inside the profile's Game log
// (GET /games/hot-streak/{id}). Two separate answers: how unusual the run is
// for him (random N-game sets of his own season), and how much of a gap like
// it has carried on historically (hot_streak_persistence).

const day = (iso) => new Date(`${iso}T00:00:00Z`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });

export default function HotStreakCard({ playerId, season, stat, win, dates }) {
    const [asOf, setAsOf] = useState('');
    const [result, setResult] = useState(null); // { data } | { error }
    const key = `${playerId}-${season}-${stat}-${win}-${asOf}`;

    useEffect(() => {
        let active = true;
        fetchHotStreak(playerId, { season, stat, window: win, as_of: asOf || undefined })
            .then((data) => { if (active) setResult({ key, data }); })
            .catch((e) => { if (active) setResult({ key, error: e.response?.data?.detail || 'The check couldn\'t load.' }); });
        return () => { active = false; };
    }, [playerId, season, stat, win, asOf, key]);

    const d = result?.key === key ? result.data : null;
    const error = result?.key === key ? result.error : null;
    const pct = d?.format === 'pct';
    const show = (v) => (v == null ? '—' : pct ? `${(v * 100).toFixed(1)}%` : v.toFixed(1));
    const signedShow = (v) => (v == null ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${show(Math.abs(v))}`);
    const choices = [...dates].reverse();

    return (
        <div className="hs-card" aria-live="polite">
            <div className="hs-head">
                <h3 className="hs-title">
                    Are his last {win} games real?
                    <InfoTooltip label="How the hot-streak check works" title="Two separate questions">
                        How unusual: 4,000 random sets of {win} games from his own season so far; the share at least this
                        hot (or cold) is shown. It says whether the run is more than chance clumping, not whether it lasts.
                        How much carries on: across every player-season 2020-21 to 2025-26, the share of a run&apos;s gap
                        from baseline that showed up again in the next {win} games (Methodology page). Baseline = his games
                        this season before the run, plus part of his previous season.
                    </InfoTooltip>
                </h3>
                <label className="pp-select hs-asof">
                    <span>As of</span>
                    <select className="input-field" value={asOf} onChange={(e) => setAsOf(e.target.value)}>
                        <option value="">His latest game</option>
                        {choices.map((iso) => <option key={iso} value={iso}>{day(iso)}</option>)}
                    </select>
                </label>
            </div>
            {error && <p className="error-message">{error}</p>}
            {!d && !error && <p className="page-subtitle">Checking…</p>}
            {d && !d.qualified && <p className="page-subtitle hs-verdict">{d.verdict}</p>}
            {d && d.qualified && (
                <>
                    <p className={`hs-verdict${d.unusual.p >= 0.05 ? '' : ' hs-verdict--unusual'}`}>{d.verdict}</p>
                    <dl className="hs-tiles">
                        <div>
                            <dt>{d.stat_label}, last {d.window.games}</dt>
                            <dd>{show(d.window.value)}</dd>
                            <span>{day(d.window.from)} to {day(d.window.to)}{pct ? ` · ${Math.round(d.window.sample)} attempts` : ''}</span>
                        </div>
                        <div>
                            <dt>Baseline</dt>
                            <dd>{show(d.baseline.value)}</dd>
                            <span>
                                {d.baseline.games_this_season} earlier games
                                {d.baseline.has_prior ? ` + last season as ${d.baseline.prior_games_weight} games` : ' (no previous season on file)'}
                            </span>
                        </div>
                        <div>
                            <dt>Gap</dt>
                            <dd className={d.gap > 0 ? 'pp-pos' : d.gap < 0 ? 'pp-neg' : ''}>{signedShow(d.gap)}</dd>
                            <span>{d.unusual.p < 0.001 ? '<0.1' : (d.unusual.p * 100).toFixed(1)}% of random sets this {d.direction}; {Math.round(d.unusual.percentile_own_windows * 100)}th percentile of his {d.unusual.own_windows} stretches</span>
                        </div>
                        <div>
                            <dt>Expected next {d.window.games}</dt>
                            <dd>{show(d.persistence.expected_next)}</dd>
                            <span>{Math.round(d.persistence.share * 100)}% of the gap (range {Math.round(d.persistence.share_lo * 100)}-{Math.round(d.persistence.share_hi * 100)}%)</span>
                        </div>
                        {d.what_happened_next && (
                            <div>
                                <dt>What happened next</dt>
                                <dd>{show(d.what_happened_next.value)}</dd>
                                <span>his next {d.what_happened_next.games} game{d.what_happened_next.games === 1 ? '' : 's'}</span>
                            </div>
                        )}
                    </dl>
                    <p className="page-subtitle pp-foot">
                        Minutes: {d.minutes.before} before the run, {d.minutes.window} during it
                        {Math.abs(d.minutes.window - d.minutes.before) >= 4 ? ' (a role change, which tends to last longer than a shooting run)' : ''}.{' '}
                        <button type="button" className="pp-link"
                            onClick={() => openPage('hotstreaks', { season: d.season, stat: d.stat, window: d.window.games, as_of: d.as_of })}>
                            Who else was {d.direction} then
                        </button>
                    </p>
                </>
            )}
        </div>
    );
}
