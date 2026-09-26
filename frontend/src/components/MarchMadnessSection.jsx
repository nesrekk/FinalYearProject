import React, { useEffect, useMemo, useState } from 'react';
import { fetchMarchMadness } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import SourceBadge from './common/SourceBadge';
import '../styles/college.css';

// Columns of ncaa_bracket_odds shown in the table; index into REACH (1 = won a Round of 64 game).
const ROUNDS = [
    { key: 'p_r32', label: 'Round of 32', short: 'R32', reach: 2 },
    { key: 'p_s16', label: 'Sweet 16', short: 'S16', reach: 3 },
    { key: 'p_e8', label: 'Elite 8', short: 'E8', reach: 4 },
    { key: 'p_f4', label: 'Final Four', short: 'F4', reach: 5 },
    { key: 'p_final', label: 'Title game', short: 'Final', reach: 6 },
    { key: 'p_champ', label: 'Champion', short: 'Title', reach: 7 },
];

const FEATURE_LABEL = {
    seed: 'Seed only',
    elo: 'Elo only',
    margin: 'Adjusted margin only',
    'margin+elo': 'Adjusted margin + Elo',
    'margin+elo+seed': 'Adjusted margin + Elo + seed',
};

function pct(p, digits = 1) {
    if (p == null) return '—';
    if (p > 0 && p < 0.001) return '<0.1%';
    return `${(p * 100).toFixed(digits)}%`;
}

function ordinal(n) {
    const s = ['th', 'st', 'nd', 'rd'];
    const v = n % 100;
    return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
}

function seedRank(rank, tied) {
    return tied > 1 ? `tied ${ordinal(rank)}–${ordinal(rank + tied - 1)}` : ordinal(rank);
}

// Shade strength per theme lives in college.css (--heat-max), so text keeps its contrast.
function heat(p) {
    return { '--p': Math.min(1, Math.max(0, p ?? 0)).toFixed(3) };
}

export default function MarchMadnessSection() {
    const [season, setSeason] = useState(null);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [showAll, setShowAll] = useState(false);

    useEffect(() => {
        let active = true;
        fetchMarchMadness(season)
            .then((d) => { if (active) { setData(d); setError(''); } })
            .catch((e) => { if (active) setError(e?.response?.data?.detail || 'Could not load the March Madness model.'); })
            .finally(() => { if (active) setLoading(false); });
        return () => { active = false; };
    }, [season]);

    function changeSeason(value) {
        setLoading(true);
        setSeason(value);
    }

    const upsets = useMemo(() => {
        if (!data) return [];
        return data.games.filter((g) => g.upset).sort((a, b) => b.favourite_p - a.favourite_p).slice(0, 8);
    }, [data]);

    if (loading && !data) return <Loader />;
    if (error && !data) return <div className="dashboard-card"><p className="error-message">{error}</p></div>;
    if (!data) return null;

    const s = data.season_summary;
    const bt = data.backtest_summary;
    const testSeason = data.seasons.find((x) => x.is_test);
    const teams = showAll ? data.teams : data.teams.slice(0, 16);
    const accuracy = data.variants.find((v) => v.chosen);
    const seedOnly = data.variants.find((v) => v.feature_set === 'seed');

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    March Madness Model
                    <InfoTooltip label="How this works" title="Pre-tournament inputs only, tested on seasons it never saw">
                        Every input is known before the first tip: each team&apos;s Elo going into the tournament
                        (CollegeBasketballData.com), an opponent- and venue-adjusted scoring margin fitted here on every
                        D1-vs-D1 game before the tournament (margins capped at ±25), and seed. A logistic regression on the
                        difference between two teams gives each game&apos;s win probability; the real bracket is then played
                        out 20,000 times. Five input sets were compared by leave-one-season-out backtest on 2013–2025 and the
                        best held-out log loss was kept ({FEATURE_LABEL[data.chosen_feature_set]}). Each backtest season uses a
                        model that never saw it; {testSeason?.season} was held back entirely as the final test. End-of-season
                        team ratings aren&apos;t used because they include the tournament itself.
                    </InfoTooltip>
                    <SourceBadge source={data._source} />
                </h3>

                <div className="cb-toolbar" style={{ marginTop: '0.75rem' }}>
                    <select value={data.season} onChange={(e) => changeSeason(Number(e.target.value))} aria-label="Tournament season">
                        {[...data.seasons].reverse().map((x) => (
                            <option key={x.season} value={x.season}>
                                {x.season} tournament{x.is_test ? ' (final test)' : ' (backtest)'}
                            </option>
                        ))}
                    </select>
                    {loading && <span className="cb-sub">Loading…</span>}
                </div>

                <p className="cb-verdict">
                    {s.champion} won {s.season}. The model had them {ordinal(s.champion_rank)} of {data.teams.length}
                </p>
                <p className="cb-lede">
                    Before tip-off, the model gave {s.champion} a {pct(s.champion_p)} chance of the title
                    ({s.is_test ? 'trained on 2013–2025 only, never on this tournament' : 'from a model that never saw this season'}).
                    Seeds alone would have ranked them {seedRank(s.seed_champion_rank, s.seed_champion_tied)} ({pct(s.seed_champion_p)}).
                    It called {pct(s.game_accuracy, 0)} of {s.n_games} games right, against {pct(s.seed_game_accuracy, 0)} for
                    seeds alone (a same-seed game, a coin flip to seeds, counts as half).
                </p>

                <div className="cb-kpis">
                    <div className="cb-kpi"><b>{bt.champion_top1} / {bt.n_seasons}</b><span>backtest seasons the model&apos;s favourite won the title</span></div>
                    <div className="cb-kpi"><b>{bt.champion_top4} / {bt.n_seasons}</b><span>champion in the model&apos;s top 4 (champion was a 1 seed: {bt.seed_champion_top4})</span></div>
                    <div className="cb-kpi"><b>{pct(accuracy.accuracy, 1)}</b><span>held-out game accuracy, {accuracy.n_games} games (seeds: {pct(seedOnly.accuracy, 1)})</span></div>
                    <div className="cb-kpi"><b>{bt.reach_log_loss.toFixed(3)}</b><span>round-by-round log loss, lower is better (seeds: {bt.seed_reach_log_loss.toFixed(3)})</span></div>
                </div>
                <p className="cb-note">
                    {accuracy.log_loss < seedOnly.log_loss
                        ? 'Game by game, the model’s probabilities are better calibrated than seeds'
                        : 'Game by game, the model’s probabilities are no better calibrated than seeds'}
                    {bt.champion_top4 < bt.seed_champion_top4
                        ? `, but it is not better at finding the champion: the winner was a 1 seed in ${bt.seed_champion_top4} of ${bt.n_seasons} backtest seasons and in the model’s top 4 in ${bt.champion_top4}.`
                        : `, and the champion was in its top 4 in ${bt.champion_top4} of ${bt.n_seasons} backtest seasons, against ${bt.seed_champion_top4} for the 1 seeds.`}
                </p>
            </div>

            <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                <h3 className="section-heading" style={{ marginTop: 0 }}>Pre-tournament odds, {data.season}</h3>
                <div className="hb-table-wrapper table-wrapper">
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>Team</th><th>Seed</th><th>Adj. margin</th><th>Elo</th>
                                {ROUNDS.map((r) => <th key={r.key} className="cb-prob" title={r.label}>{r.short}</th>)}
                            </tr>
                        </thead>
                        <tbody>
                            {teams.map((t) => (
                                <tr key={t.team}>
                                    <td>{t.team}<span className="cb-sub">{t.region || ''}</span></td>
                                    <td className="cb-num">{t.seed}</td>
                                    <td className="cb-num">{t.adj_margin > 0 ? '+' : ''}{t.adj_margin.toFixed(1)}</td>
                                    <td className="cb-num">{t.elo}</td>
                                    {ROUNDS.map((r) => (
                                        <td key={r.key} className={`cb-prob ${t.actual_reach >= r.reach ? 'cb-hit' : ''}`} style={heat(t[r.key])}>
                                            {pct(t[r.key], t[r.key] >= 0.1 ? 0 : 1)}
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <div className="cb-legend">
                    <span><i className="cb-prob" style={{ '--p': 0.8 }} />Shade = model&apos;s chance of reaching the round</span>
                    <span><i style={{ outline: '2px solid var(--line)', outlineOffset: -4 }} />Outlined = the team really got there</span>
                </div>
                <button type="button" className="back-btn" style={{ marginTop: '0.75rem', padding: '6px 12px' }} onClick={() => setShowAll((v) => !v)}>
                    {showAll ? 'Show top 16' : `Show all ${data.teams.length} teams`}
                </button>
            </div>

            <div className="cb-grid">
                <div className="dashboard-card">
                    <h3 className="section-heading" style={{ marginTop: 0 }}>Biggest upsets, by the model</h3>
                    {upsets.length === 0 ? <p className="cb-note">The model&apos;s favourite won every game.</p> : (
                        <div className="hb-table-wrapper table-wrapper">
                            <table className="data-table">
                                <thead><tr><th>Round</th><th>Winner</th><th>Beat</th><th>Favourite&apos;s odds</th></tr></thead>
                                <tbody>
                                    {upsets.map((g) => {
                                        const aWon = g.winner === g.team_a;
                                        const [w, ws, wp, l, ls, lp] = aWon
                                            ? [g.team_a, g.seed_a, g.points_a, g.team_b, g.seed_b, g.points_b]
                                            : [g.team_b, g.seed_b, g.points_b, g.team_a, g.seed_a, g.points_a];
                                        return (
                                            <tr key={`${g.round}-${w}`}>
                                                <td>{g.round_name}</td>
                                                <td>({ws}) {w}<span className="cb-sub cb-num">{wp}–{lp}</span></td>
                                                <td>({ls}) {l}</td>
                                                <td className="cb-num">{pct(g.favourite_p, 0)}</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
                <div className="dashboard-card">
                    <h3 className="section-heading" style={{ marginTop: 0 }}>Which inputs won</h3>
                    <div className="hb-table-wrapper table-wrapper">
                        <table className="data-table">
                            <thead><tr><th>Inputs</th><th>Log loss</th><th>Accuracy</th></tr></thead>
                            <tbody>
                                {data.variants.map((v) => (
                                    <tr key={v.feature_set}>
                                        <td>{FEATURE_LABEL[v.feature_set] || v.feature_set}{v.chosen && <span className="cb-sub">chosen</span>}</td>
                                        <td className="cb-num">{v.log_loss.toFixed(4)}</td>
                                        <td className="cb-num">{pct(v.accuracy, 1)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                    <p className="cb-note">
                        Held-out results on {accuracy.n_games} tournament games, 2013–2025 (each season predicted by a model
                        trained on the others). Lower log loss means better-calibrated probabilities.
                    </p>
                </div>
            </div>

            <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                <h3 className="section-heading" style={{ marginTop: 0 }}>Every season</h3>
                <div className="hb-table-wrapper table-wrapper">
                    <table className="data-table">
                        <thead>
                            <tr><th>Season</th><th>Champion</th><th>Model rank</th><th>Model title odds</th><th>Seed-only rank</th><th>Games called (model / seeds)</th></tr>
                        </thead>
                        <tbody>
                            {[...data.seasons].reverse().map((x) => (
                                <tr key={x.season}>
                                    <td>
                                        <button type="button" className="back-btn" style={{ padding: '4px 10px' }} onClick={() => changeSeason(x.season)}>
                                            {x.season}
                                        </button>
                                        {x.is_test && <span className="cb-sub">final test</span>}
                                    </td>
                                    <td>{x.champion}</td>
                                    <td className="cb-num">{ordinal(x.champion_rank)}</td>
                                    <td className="cb-num">{pct(x.champion_p)}</td>
                                    <td className="cb-num">{seedRank(x.seed_champion_rank, x.seed_champion_tied)}</td>
                                    <td className="cb-num">
                                        {pct(x.game_accuracy, 0)} / {pct(x.seed_game_accuracy, 0)} of {x.n_games}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <p className="cb-note">No 2020 tournament (cancelled). 2017: the data source is missing two First Four games, so those two winners enter at the Round of 64 and the two losers aren&apos;t listed. Seed-only odds use the same simulation with seed as the only input.</p>
            </div>
        </div>
    );
}
