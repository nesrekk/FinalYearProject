import React, { useEffect, useState } from 'react';
import { fetchChampionshipOdds } from '../services/api';
import Loader from './Loader';
import InfoTooltip from './common/InfoTooltip';
import Icon from './common/Icon';
import TeamLogo from './common/TeamLogo';

function fmtPct(v) {
    return v == null ? '—' : `${(v * 100).toFixed(1)}%`;
}

function valueColor(v) {
    if (v == null) return 'var(--text-muted)';
    if (v > 0.03) return '#34d399';
    if (v < -0.03) return '#f87171';
    return 'var(--text-secondary)';
}

// Coefficient of variation (spread / mean) rather than raw spread — a
// favorite and a 100-1 longshot can have the same raw stdev between books
// but wildly different relative disagreement, since raw stdev scales with
// the probability's own magnitude. Thresholds below are simple, round,
// disclosed cutoffs picked to spread across this market's real observed
// range (~0.4%-12%+ CV), not fitted to anything.
function consensusInfo(spread, marketProb) {
    if (spread == null || !marketProb) return { label: 'N/A', color: 'var(--text-muted)' };
    const cv = spread / marketProb;
    if (cv < 0.05) return { label: 'Strong Consensus', color: '#34d399', cv };
    if (cv < 0.15) return { label: 'Some Disagreement', color: '#facc15', cv };
    return { label: 'Books Disagree', color: '#f87171', cv };
}

function BookBreakdown({ books }) {
    if (!books?.length) return null;
    return (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', padding: '0.6rem 0.75rem', background: 'rgba(148,163,184,0.06)', borderRadius: 6 }}>
            {books.map((b) => (
                <span key={b.book} className="page-subtitle" style={{ margin: 0, fontSize: '0.75rem' }}>
                    <strong style={{ color: 'var(--text-secondary)' }}>{b.book}:</strong> {b.odds.toFixed(2)} ({fmtPct(b.probability)})
                </span>
            ))}
        </div>
    );
}

export default function VegasScannerSection() {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [expanded, setExpanded] = useState(null);

    useEffect(() => {
        let active = true;
        (async () => {
            try {
                const res = await fetchChampionshipOdds();
                if (active) setData(res);
            } catch (e) {
                if (active) setError(e?.response?.data?.detail || 'Could not load live championship odds.');
            } finally {
                if (active) setLoading(false);
            }
        })();
        return () => { active = false; };
    }, []);

    return (
        <div className="fade-in">
            <div className="dashboard-card">
                <h3 className="section-heading" style={{ marginTop: 0 }}>
                    Vegas vs. Machine: Championship Odds Scanner
                    <InfoTooltip label="How this works" title="Real market data, a naive proxy, no betting advice">
                        market_probability is real, live NBA championship-winner odds (multiple real sportsbooks),
                        with each book's own bookmaker margin ("vig") removed independently via Shin's method
                        (1992) before averaging — the same formula the public `shin` package implements, written
                        directly here since that package needs a Rust toolchain that won't build in this
                        environment. proxy_probability is each team's real win percentage this season, normalized
                        to sum to 1 — a naive stand-in for "who is actually good right now," not a trained
                        championship model (this project has no such model). Consensus is the coefficient of
                        variation across books' individual devigged probabilities for that team (spread relative
                        to the probability itself, not raw spread, so a longshot and a favorite are judged
                        fairly) — click a row to see every book's individual line. This is real data for a
                        real-data comparison, not a betting recommendation, and the free odds API quota is
                        limited so this refreshes at most a few times a day.
                    </InfoTooltip>
                </h3>
                {data && (
                    <p className="page-subtitle" style={{ marginTop: 0 }}>
                        {data.books_used?.length} real sportsbooks ({data.books_used?.join(', ')}) · avg Shin z={data.avg_z} · updated {data.last_update ? new Date(data.last_update).toLocaleString() : '—'}
                    </p>
                )}
                {error && <p className="error-message">{error}</p>}
            </div>

            {loading && <Loader />}

            {!loading && data && (
                <div className="dashboard-card" style={{ marginTop: '1rem' }}>
                    <div className="hb-table-wrapper table-wrapper">
                        <table className="data-table">
                            <thead>
                                <tr>
                                    <th>Team</th>
                                    <th>Odds Range</th>
                                    <th>Market Prob.</th>
                                    <th>Consensus</th>
                                    <th>Win%</th>
                                    <th>Proxy Prob.</th>
                                    <th>Value</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.teams.map((t) => {
                                    const consensus = consensusInfo(t.probability_spread, t.market_probability);
                                    const isOpen = expanded === t.team_name;
                                    return (
                                        <React.Fragment key={t.team_name}>
                                            <tr
                                                onClick={() => setExpanded(isOpen ? null : t.team_name)}
                                                style={{ cursor: 'pointer' }}
                                            >
                                                <td>
                                                    <div className="entity-row">
                                                        <Icon name={isOpen ? 'expand_more' : 'chevron_right'} size="1em" style={{ color: 'var(--text-muted)' }} />
                                                        <TeamLogo abbreviation={t.team_abbreviation} size={20} />
                                                        {t.team_name}
                                                    </div>
                                                </td>
                                                <td>
                                                    {t.worst_odds != null && t.best_odds != null
                                                        ? `${t.worst_odds.toFixed(2)} – ${t.best_odds.toFixed(2)}`
                                                        : '—'}
                                                </td>
                                                <td>{fmtPct(t.market_probability)}</td>
                                                <td style={{ color: consensus.color, fontSize: '0.78em', fontWeight: 600 }}>
                                                    {consensus.label}
                                                </td>
                                                <td>{t.win_pct != null ? t.win_pct.toFixed(3) : '—'}</td>
                                                <td>{fmtPct(t.proxy_probability)}</td>
                                                <td style={{ color: valueColor(t.value), fontWeight: 700 }}>
                                                    {t.value != null && (
                                                        <Icon
                                                            name={t.value > 0 ? 'arrow_upward' : t.value < 0 ? 'arrow_downward' : 'remove'}
                                                            size="0.9em"
                                                            style={{ verticalAlign: 'middle', marginRight: 2 }}
                                                        />
                                                    )}
                                                    {t.value != null ? `${t.value > 0 ? '+' : ''}${(t.value * 100).toFixed(1)}pp` : '—'}
                                                </td>
                                            </tr>
                                            {isOpen && (
                                                <tr>
                                                    <td colSpan={7} style={{ padding: 0 }}>
                                                        <BookBreakdown books={t.books} />
                                                    </td>
                                                </tr>
                                            )}
                                        </React.Fragment>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}
