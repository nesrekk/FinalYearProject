import React, { useEffect, useMemo, useState } from 'react';
import StatCard from '../common/StatCard';
import { mockLiveScores } from '../../services/mockData';
import {
    fetchCurrentMeta,
    fetchCurrentNews,
    fetchGamesByDate,
    fetchMVPPrediction,
    fetchDPOYPrediction,
    fetchROYPrediction,
    fetchAllNBAPrediction,
} from '../../services/api';
import { localDateIso } from '../../utils/date';

const AWARD_META = {
    mvp: { icon: '🏆', label: 'MVP' },
    dpoy: { icon: '🛡️', label: 'DPOY' },
    roy: { icon: '🌱', label: 'ROY' },
    allnba: { icon: '⭐', label: 'All-NBA' },
};

// /meta/current reports next-season-if-October-or-later, which usually has
// no player_season_stats rows yet (this project's local data pipeline is a
// season behind live) — so award predictions for that season 404. Walk
// backwards to the most recent season the models can actually predict on,
// instead of hardcoding a season number that goes stale every year.
async function resolveSeasonWithData(startSeason) {
    for (let season = startSeason; season >= startSeason - 3; season--) {
        try {
            const data = await fetchMVPPrediction(season);
            if (data?.results?.length > 0) return season;
        } catch {
            // try the previous season
        }
    }
    return null;
}

export default function DashboardHome({ onNavigate }) {
    const [games, setGames] = useState(mockLiveScores);
    const [news, setNews] = useState([]);
    const [meta, setMeta] = useState(null);
    const [awardsRace, setAwardsRace] = useState({ season: null, mvp: null, dpoy: null, roy: null, allnba: null });
    const [awardsLoading, setAwardsLoading] = useState(true);

    useEffect(() => {
        let active = true;
        const today = localDateIso();

        async function loadDashboard() {
            try {
                const [g, n, m] = await Promise.all([
                    fetchGamesByDate(today),
                    fetchCurrentNews(today, 10),
                    fetchCurrentMeta(),
                ]);
                if (!active) return;
                if (Array.isArray(g?.games) && g.games.length > 0) setGames(g.games);
                if (Array.isArray(n?.items) && n.items.length > 0) {
                    setNews(
                        n.items.map((item, idx) => ({
                            id: `dash-news-${idx}`,
                            category: 'News',
                            headline: item.headline,
                            source: item.source || 'Source',
                            date: item.published_at || today,
                        }))
                    );
                }
                if (m) setMeta(m);
                if (m?.season) {
                    const resolvedSeason = await resolveSeasonWithData(m.season);
                    if (active && resolvedSeason) {
                        const [mvp, dpoy, roy, allnba] = await Promise.allSettled([
                            fetchMVPPrediction(resolvedSeason),
                            fetchDPOYPrediction(resolvedSeason),
                            fetchROYPrediction(resolvedSeason),
                            fetchAllNBAPrediction(resolvedSeason),
                        ]);
                        if (active) {
                            setAwardsRace({
                                season: resolvedSeason,
                                mvp: mvp.status === 'fulfilled' ? mvp.value?.results?.[0] : null,
                                dpoy: dpoy.status === 'fulfilled' ? dpoy.value?.results?.[0] : null,
                                roy: roy.status === 'fulfilled' ? roy.value?.results?.[0] : null,
                                allnba: allnba.status === 'fulfilled' ? allnba.value?.results?.[0] : null,
                            });
                        }
                    }
                }
            } catch {
                // keep existing mock fallback values
            } finally {
                if (active) setAwardsLoading(false);
            }
        }

        loadDashboard();
        return () => {
            active = false;
        };
    }, []);

    function seasonLabel(season) {
        return season ? `${season - 1}-${String(season).slice(-2)}` : '';
    }

    function awardCardLine(award, row) {
        if (!row) return 'No prediction available';
        if (award === 'allnba') {
            return `${row.predicted_team} · ${(row.all_nba_probability * 100).toFixed(1)}%`;
        }
        const probKey = `${award}_probability`;
        const prob = row[probKey];
        return prob != null ? `${(prob * 100).toFixed(1)}% probability` : 'Top model pick';
    }

    const liveGames = useMemo(() => games.filter((g) => g.status === 'LIVE').length, [games]);
    const finalGames = useMemo(() => games.filter((g) => g.status === 'FINAL').length, [games]);
    const scheduledGames = useMemo(() => games.filter((g) => g.status !== 'LIVE' && g.status !== 'FINAL').length, [games]);
    const topSeed = useMemo(() => {
        const west = meta?.standings?.western || [];
        return west.length > 0 ? west[0] : null;
    }, [meta]);

    const formLeaders = useMemo(() => {
        const east = meta?.standings?.eastern || [];
        const west = meta?.standings?.western || [];
        const combined = [...east, ...west];
        return combined
            .filter((t) => t && t.team && t.last10)
            .sort((a, b) => {
                const aPct = Number(String(a.pct || '0').replace('.', '0.'));
                const bPct = Number(String(b.pct || '0').replace('.', '0.'));
                return bPct - aPct;
            })
            .slice(0, 10);
    }, [meta]);

    return (
        <div className="page page-dashboard fade-in">
            {/* Hero */}
            <div className="dashboard-hero">
                <div className="dashboard-hero-content">
                    <h2 className="hero-title">Welcome to <span className="text-accent">NBA Hub</span></h2>
                    <p className="hero-subtitle">Your all-in-one NBA analytics command center. Track live games, explore player stats, compare teams, and predict awards.</p>
                </div>
                <div className="hero-glow"></div>
            </div>

            {/* Quick Stats */}
            <div className="stat-cards-row">
                <StatCard
                    icon="🏀"
                    label="Games Today"
                    value={games.length}
                    sub={`${liveGames} live · ${finalGames} final · ${scheduledGames} scheduled`}
                />
                <StatCard
                    icon="🔥"
                    label="Top Scorer"
                    value={meta?.top_scorer?.player_name || 'Scoring Leader'}
                    sub={meta?.top_scorer?.ppg != null ? `${meta.top_scorer.ppg} PPG` : 'Current season'}
                />
                <StatCard
                    icon="🏆"
                    label="#1 Seed"
                    value={topSeed ? topSeed.team.replace('Oklahoma City ', '') : 'Thunder'}
                    sub={topSeed ? `${topSeed.w}-${topSeed.l} (${topSeed.pct})` : '47-13 (.783)'}
                />
                <StatCard
                    icon="📈"
                    label="MVP Favorite"
                    value={awardsRace.mvp?.player_name || (awardsLoading ? 'Loading…' : 'No prediction')}
                    sub={awardsRace.mvp ? awardCardLine('mvp', awardsRace.mvp) : (awardsRace.season ? seasonLabel(awardsRace.season) : '')}
                />
            </div>

            {/* Quick Links */}
            <h3 className="section-heading">Explore</h3>
            <div className="quick-links-grid">
                {[
                    { id: 'scores', icon: '🏀', title: 'Live Scores', desc: 'Real-time game updates' },
                    { id: 'standings', icon: '🏆', title: 'Standings', desc: 'Conference rankings' },
                    { id: 'teams', icon: '⚔️', title: 'Team Comparison', desc: 'Head-to-head stats' },
                    { id: 'players', icon: '👤', title: 'Player Stats', desc: 'Browse player data' },
                    { id: 'shotcharts', icon: '🎯', title: 'Shot Charts', desc: 'Shooting visualizations' },
                    { id: 'analytics', icon: '📊', title: 'Analytics', desc: 'Similarity & predictions' },
                ].map((link) => (
                    <button
                        key={link.id}
                        className="quick-link-card"
                        onClick={() => onNavigate(link.id)}
                    >
                        <span className="quick-link-icon">{link.icon}</span>
                        <span className="quick-link-title">{link.title}</span>
                        <span className="quick-link-desc">{link.desc}</span>
                    </button>
                ))}
            </div>

            {/* Awards Race Snapshot */}
            <h3 className="section-heading">
                Awards Race {awardsRace.season ? `· ${seasonLabel(awardsRace.season)}` : ''}
            </h3>
            <div className="quick-links-grid">
                {['mvp', 'dpoy', 'roy', 'allnba'].map((award) => {
                    const row = awardsRace[award];
                    return (
                        <button
                            key={award}
                            className="quick-link-card"
                            onClick={() => onNavigate('analytics')}
                        >
                            <span className="quick-link-icon">{AWARD_META[award].icon}</span>
                            <span className="quick-link-title">
                                {awardsLoading ? 'Loading…' : (row?.player_name || 'No prediction')}
                            </span>
                            <span className="quick-link-desc">
                                {AWARD_META[award].label} · {awardsLoading ? '' : awardCardLine(award, row)}
                            </span>
                        </button>
                    );
                })}
            </div>

            {/* Latest Headlines */}
            <h3 className="section-heading">Latest Headlines</h3>
            <div className="mini-news-list">
                {news.slice(0, 3).map((item) => (
                    <div key={item.id} className="mini-news-item" onClick={() => onNavigate('news')}>
                        <span className="mini-news-category">{item.category}</span>
                        <p className="mini-news-headline">{item.headline}</p>
                        <span className="mini-news-meta">{item.source} · {item.date}</span>
                    </div>
                ))}
                {news.length === 0 && (
                    <p className="empty-message">No live headlines loaded yet.</p>
                )}
            </div>

            <h3 className="section-heading" style={{ marginTop: '1.5rem' }}>Team Form Tracker (Live)</h3>
            <div className="table-wrapper">
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Team</th>
                            <th>Record</th>
                            <th>PCT</th>
                            <th>L10</th>
                            <th>Streak</th>
                        </tr>
                    </thead>
                    <tbody>
                        {formLeaders.map((team, idx) => (
                            <tr key={`${team.abbr}-${idx}`}>
                                <td className="rank-cell">{idx + 1}</td>
                                <td className="team-cell">
                                    {team.logo ? (
                                        <img
                                            src={team.logo}
                                            alt={team.abbr}
                                            style={{ width: '24px', height: '24px', objectFit: 'contain' }}
                                        />
                                    ) : (
                                        <span className="team-abbr-badge">{team.abbr}</span>
                                    )}
                                    {team.team}
                                </td>
                                <td>{team.w}-{team.l}</td>
                                <td className="text-accent">{team.pct}</td>
                                <td>{team.last10 || '-'}</td>
                                <td>
                                    <span className={`streak-badge ${String(team.streak || '').startsWith('W') ? 'streak--win' : 'streak--loss'}`}>
                                        {team.streak || '-'}
                                    </span>
                                </td>
                            </tr>
                        ))}
                        {formLeaders.length === 0 && (
                            <tr>
                                <td colSpan={6} className="empty-message">Live team form data is loading...</td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
