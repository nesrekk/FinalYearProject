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

export default function VegasScannerSection() {
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);

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
                        with each book's bookmaker margin ("vig") removed via Shin's method (1992) — the same
                        formula the public `shin` package implements, written directly here since that package
                        needs a Rust toolchain that won't build in this environment. proxy_probability is each
                        team's real win percentage this season, normalized to sum to 1 — a naive stand-in for
                        "who is actually good right now," not a trained championship model (this project has no
                        such model). value is the gap between them. This is real data for a real-data comparison,
                        not a betting recommendation, and the free odds API quota is limited so this refreshes at
                        most a few times a day.
                    </InfoTooltip>
                </h3>
                {data && (
                    <p className="page-subtitle" style={{ marginTop: 0 }}>
                        {data.books_used?.length} real sportsbooks · avg Shin z={data.avg_z} · updated {data.last_update ? new Date(data.last_update).toLocaleString() : '—'}
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
                                    <th>Best Odds</th>
                                    <th>Market Prob.</th>
                                    <th>Win%</th>
                                    <th>Proxy Prob.</th>
                                    <th>Value</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.teams.map((t) => (
                                    <tr key={t.team_name}>
                                        <td>
                                            <div className="entity-row">
                                                <TeamLogo abbreviation={t.team_abbreviation} size={20} />
                                                {t.team_name}
                                            </div>
                                        </td>
                                        <td>{t.best_odds != null ? t.best_odds.toFixed(2) : '—'}</td>
                                        <td>{fmtPct(t.market_probability)}</td>
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
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            )}
        </div>
    );
}
