import React, { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import TeamLogo from '../common/TeamLogo';
import TeamLink from '../common/TeamLink';
import PlayerHeadshot from '../common/PlayerHeadshot';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import Section from '../ui/Section';
import { BentoGrid, Tile } from '../ui/BentoGrid';
import BigStat from '../ui/BigStat';
import Skeleton, { SkeletonGroup } from '../ui/Skeleton';
import { EmptyState } from '../ui/EmptyState';
import { abbrFromTeamName } from '../../utils/teamAssets';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';
import {
    fetchCurrentMeta,
    fetchCurrentNews,
    fetchGamesByDate,
    fetchMVPPrediction,
    fetchDPOYPrediction,
    fetchROYPrediction,
    fetchAllNBAPrediction,
    fetchWpReplayList,
} from '../../services/api';
import { localDateIso } from '../../utils/date';

const AWARD_META = {
    mvp: { icon: 'emoji_events', label: 'MVP' },
    dpoy: { icon: 'shield', label: 'DPOY' },
    roy: { icon: 'eco', label: 'ROY' },
    allnba: { icon: 'star', label: 'All-NBA' },
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

const GRID_VARIANTS = {
    hidden: {},
    show: (stagger) => ({ transition: { staggerChildren: stagger } }),
};
const CARD_VARIANTS = {
    hidden: { opacity: 0, y: 10 },
    show: { opacity: 1, y: 0 },
};

export default function DashboardHome({ onNavigate }) {
    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);
    const [games, setGames] = useState([]);
    const [gamesState, setGamesState] = useState('loading');
    const [recentFinals, setRecentFinals] = useState(null);
    const [news, setNews] = useState([]);
    const [meta, setMeta] = useState(null);
    const [awardsRace, setAwardsRace] = useState({ season: null, mvp: null, dpoy: null, roy: null, allnba: null });
    const [awardsLoading, setAwardsLoading] = useState(true);

    useEffect(() => {
        let active = true;
        const today = localDateIso();

        async function loadGames() {
            try {
                const g = await fetchGamesByDate(today);
                if (!active) return;
                const list = Array.isArray(g?.games) ? g.games : [];
                setGames(list);
                setGamesState('done');
                if (list.length === 0) {
                    const replay = await fetchWpReplayList().catch(() => null);
                    const all = replay?.games || [];
                    const last = all.reduce((m, x) => (x.game_date > m ? x.game_date : m), '');
                    if (active && last) setRecentFinals({ date: last, games: all.filter((x) => x.game_date === last).slice(0, 4) });
                }
            } catch {
                if (active) setGamesState('error');
            }
        }

        async function loadDashboard() {
            try {
                const [n, m] = await Promise.all([
                    fetchCurrentNews(today, 10),
                    fetchCurrentMeta(),
                ]);
                if (!active) return;
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
                // news, meta and awards simply stay empty
            } finally {
                if (active) setAwardsLoading(false);
            }
        }

        loadGames();
        loadDashboard();
        return () => {
            active = false;
        };
    }, []);

    function seasonLabel(season) {
        return season ? `${season - 1}-${String(season).slice(-2)}` : '';
    }

    // Real, informational-only context for a predicted contender — never
    // fed back into the model's probability, just shown alongside it since
    // a human voter's actual ballot is also shaped by "have they already
    // won this?" and "how good is their team's seed?" (the real dynamic
    // behind cases like Rose over James in 2011). seed comes straight from
    // this season's real standings (already fetched into `meta` above);
    // win streak comes from the real award_winners history the backend
    // now tracks (see get_recent_win_streak() in mvp_api.py).
    function findTeamSeed(teamAbbr) {
        if (!meta || !teamAbbr) return null;
        for (const [confKey, confLabel] of [['eastern', 'East'], ['western', 'West']]) {
            const list = meta?.standings?.[confKey] || [];
            // Standings entries' `abbr` field is often blank in the real feed
            // (same gap the Team Form Tracker table already works around) —
            // fall back to deriving the abbreviation from the full team name.
            const team = list.find((t) => (t.abbr || abbrFromTeamName(t.team)) === teamAbbr);
            if (team) return { seed: team.rank, conference: confLabel };
        }
        return null;
    }

    function awardCardLine(award, row) {
        if (!row) return 'No prediction available';
        if (award === 'allnba') {
            const c = row.all_nba_chance;
            return c != null ? `${row.predicted_team} · ${(c * 100).toFixed(1)}% chance of a team` : row.predicted_team;
        }
        // Calibrated chance (adds up to 100% across the field); the raw model
        // probabilities saturate near 100% and are not shown.
        const chance = row[`${award}_chance`];
        return chance != null ? `${(chance * 100).toFixed(1)}% chance to win` : 'Top model pick';
    }

    const liveGames = useMemo(() => games.filter((g) => g.status === 'LIVE').length, [games]);
    const finalGames = useMemo(() => games.filter((g) => g.status === 'FINAL').length, [games]);
    const scheduledGames = useMemo(() => games.filter((g) => g.status !== 'LIVE' && g.status !== 'FINAL').length, [games]);
    // The league's best record, from both conferences (it used to be the West's first row only), and
    // none before the season's first game, when every team is 0-0 (round 8 R8-004).
    const topSeed = useMemo(() => {
        const all = [...(meta?.standings?.eastern || []), ...(meta?.standings?.western || [])];
        const played = all.filter((t) => (t.w || 0) + (t.l || 0) > 0);
        if (!played.length) return all.length ? { notStarted: true } : null;
        return played.reduce((best, t) => (t.w / (t.w + t.l) > best.w / (best.w + best.l) ? t : best));
    }, [meta]);

    const standingsPreview = useMemo(() => {
        const east = (meta?.standings?.eastern || []).slice(0, 3);
        const west = (meta?.standings?.western || []).slice(0, 3);
        return { east, west };
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

            {/* Bento overview */}
            <BentoGrid className="dashboard-bento">
                <Tile span={8} className="dashboard-tile dashboard-tile--games" onClick={() => onNavigate('scores')}>
                    <div className="dashboard-tile-header">
                        <p className="text-eyebrow">Today&apos;s Games</p>
                        <span className="dashboard-tile-meta">{liveGames} live · {finalGames} final · {scheduledGames} scheduled</span>
                    </div>
                    {gamesState === 'loading' ? (
                        <SkeletonGroup lines={3} />
                    ) : gamesState === 'error' ? (
                        <EmptyState icon="sports_basketball" message="Today's scores couldn't load right now." />
                    ) : games.length === 0 ? (
                        <div className="dashboard-no-games">
                            <p className="dashboard-no-games-title">No NBA games today.</p>
                            {recentFinals && (
                                <>
                                    <p className="text-eyebrow">Most recent real finals · {recentFinals.date}</p>
                                    <div className="dashboard-game-list">
                                        {recentFinals.games.map((g) => (
                                            <div key={g.game_id} className="dashboard-game-row">
                                                <span className="dashboard-game-team">
                                                    <TeamLogo abbreviation={g.away_team} size={22} />
                                                    {g.away_team}
                                                </span>
                                                <span className="dashboard-game-score">
                                                    {g.final_score?.away} <em>–</em> {g.final_score?.home}
                                                </span>
                                                <span className="dashboard-game-team dashboard-game-team--home">
                                                    {g.home_team}
                                                    <TeamLogo abbreviation={g.home_team} size={22} />
                                                </span>
                                                <span className="dashboard-game-status">Final</span>
                                            </div>
                                        ))}
                                    </div>
                                </>
                            )}
                        </div>
                    ) : (
                        <div className="dashboard-game-list">
                            {games.slice(0, 4).map((g) => (
                                <div key={g.id} className={`dashboard-game-row${g.status === 'LIVE' ? ' dashboard-game-row--live' : ''}`}>
                                    <span className="dashboard-game-team">
                                        <TeamLogo abbreviation={g.away?.abbr} size={22} />
                                        {g.away?.abbr}
                                    </span>
                                    <span className="dashboard-game-score">
                                        {g.away?.score ?? '–'} <em>–</em> {g.home?.score ?? '–'}
                                    </span>
                                    <span className="dashboard-game-team dashboard-game-team--home">
                                        {g.home?.abbr}
                                        <TeamLogo abbreviation={g.home?.abbr} size={22} />
                                    </span>
                                    <span className={`dashboard-game-status${g.status === 'LIVE' ? ' dashboard-game-status--live' : ''}`}>
                                        {g.status === 'LIVE' ? `● ${g.quarter || 'LIVE'}` : (g.status_text || g.status)}
                                    </span>
                                </div>
                            ))}
                        </div>
                    )}
                </Tile>

                <Tile span={4} className="dashboard-tile dashboard-tile--mvp" onClick={() => onNavigate('analytics')}>
                    <p className="text-eyebrow">
                        MVP Favorite {awardsRace.season ? `· ${seasonLabel(awardsRace.season)}` : ''}
                    </p>
                    {awardsLoading ? (
                        <SkeletonGroup lines={3} />
                    ) : awardsRace.mvp ? (
                        <>
                            <div className="entity-row" style={{ margin: '0.75rem 0' }}>
                                <PlayerHeadshot playerId={awardsRace.mvp.player_id} playerName={awardsRace.mvp.player_name} size={48} />
                                <div className="entity-row-text">
                                    <span className="entity-row-name">{awardsRace.mvp.player_name}</span>
                                    <span className="entity-row-sub">
                                        <TeamLogo abbreviation={awardsRace.mvp.team_abbreviation} size={14} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                                        {awardsRace.mvp.team_abbreviation}
                                    </span>
                                </div>
                            </div>
                            <BigStat
                                label="Chance to win"
                                value={awardsRace.mvp.mvp_chance != null ? awardsRace.mvp.mvp_chance * 100 : null}
                                digits={1}
                                className="dashboard-mvp-stat"
                            />
                            <div className="probability-bar">
                                <div
                                    className="probability-bar-fill"
                                    style={{ width: `${Math.min(100, (awardsRace.mvp.mvp_chance || 0) * 100)}%` }}
                                />
                            </div>
                        </>
                    ) : (
                        <EmptyState icon="emoji_events" message="No MVP prediction available." />
                    )}
                </Tile>

                <Tile span={4} className="dashboard-tile" onClick={() => onNavigate('leaders')}>
                    <p className="text-eyebrow">Top Scorer</p>
                    {meta?.top_scorer ? (
                        <BigStat
                            label={meta.top_scorer.player_name}
                            value={meta.top_scorer.ppg}
                            digits={1}
                        />
                    ) : (
                        <Skeleton variant="text" width="6rem" height="2.5rem" />
                    )}
                </Tile>

                <Tile span={4} className="dashboard-tile" onClick={() => onNavigate('standings')}>
                    <p className="text-eyebrow">Best record</p>
                    {topSeed?.notStarted ? (
                        <span className="dashboard-tile-sub">
                            {meta?.season ? `${meta.season - 1}-${String(meta.season).slice(-2)}: ` : ''}no games played yet.
                        </span>
                    ) : topSeed ? (
                        <>
                            <p className="text-stat dashboard-seed-value">{topSeed.w}-{topSeed.l}</p>
                            <span className="dashboard-tile-sub">{topSeed.team} · {topSeed.pct}</span>
                        </>
                    ) : (
                        <Skeleton variant="text" width="6rem" height="2.5rem" />
                    )}
                </Tile>

                <Tile span={4} className="dashboard-tile" onClick={() => onNavigate('standings')}>
                    <p className="text-eyebrow">Standings</p>
                    {standingsPreview.east.length === 0 && standingsPreview.west.length === 0 ? (
                        <Skeleton variant="text" width="100%" height="4rem" />
                    ) : (
                        <div className="dashboard-standings-preview">
                            <div>
                                <span className="dashboard-standings-conf">East</span>
                                {standingsPreview.east.map((t, i) => (
                                    <span key={t.team} className="dashboard-standings-row">{i + 1}. {t.team}</span>
                                ))}
                            </div>
                            <div>
                                <span className="dashboard-standings-conf">West</span>
                                {standingsPreview.west.map((t, i) => (
                                    <span key={t.team} className="dashboard-standings-row">{i + 1}. {t.team}</span>
                                ))}
                            </div>
                        </div>
                    )}
                </Tile>
            </BentoGrid>

            {/* Quick Links */}
            <Section className="dashboard-section" eyebrow="Explore" title="Jump straight in.">
                <motion.div
                    key={`links-${isAdvanced}`}
                    className="quick-links-grid"
                    variants={GRID_VARIANTS}
                    custom={preset.stagger}
                    initial="hidden"
                    animate="show"
                >
                    {[
                        { id: 'scores', icon: 'sports_basketball', title: 'Live Scores', desc: 'Real-time game updates' },
                        { id: 'standings', icon: 'emoji_events', title: 'Standings', desc: 'Conference rankings' },
                        { id: 'teams', icon: 'swords', title: 'Team Comparison', desc: 'Head-to-head stats' },
                        { id: 'players', icon: 'person', title: 'Player Stats', desc: 'Browse player data' },
                        { id: 'shotcharts', icon: 'adjust', title: 'Shot Charts', desc: 'Shooting visualizations' },
                        { id: 'analytics', icon: 'insights', title: 'Analytics', desc: 'Similarity & predictions' },
                    ].map((link) => (
                        <motion.button
                            key={link.id}
                            className="quick-link-card"
                            onClick={() => onNavigate(link.id)}
                            variants={CARD_VARIANTS}
                            transition={preset.fieldSpring}
                            whileHover={isAdvanced ? { y: -2 } : undefined}
                        >
                            <span className="quick-link-icon"><Icon name={link.icon} /></span>
                            <span className="quick-link-title">{link.title}</span>
                            <span className="quick-link-desc">{link.desc}</span>
                        </motion.button>
                    ))}
                </motion.div>
            </Section>

            {/* Awards Race Snapshot */}
            <Section
                className="dashboard-section"
                eyebrow="Predictions"
                title={<>Awards Race {awardsRace.season ? `· ${seasonLabel(awardsRace.season)}` : ''}</>}
            >
                <InfoTooltip label="What are the seed/streak badges?" title="Real context, not a hidden score">
                    The seed and "won it before" badges are real data (this season's actual standings, and this
                    project's real award-winner history) shown for context — they're never applied to the
                    probability above them. A real voter's ballot is shaped by things like team success and
                    "haven't they already won this?" (see: Derrick Rose over LeBron James in 2011), but baking an
                    invented penalty into the model's own number would misrepresent it as validated rather than
                    a guess, so instead the raw signal is just shown alongside the model's real output.
                </InfoTooltip>
                <motion.div
                    key={`awards-${isAdvanced}`}
                    className="award-cards-grid"
                    variants={GRID_VARIANTS}
                    custom={preset.stagger}
                    initial="hidden"
                    animate="show"
                >
                    {['mvp', 'dpoy', 'roy', 'allnba'].map((award) => {
                        const row = awardsRace[award];
                        const prob = row
                            ? (award === 'allnba' ? row.all_nba_chance : row[`${award}_chance`])
                            : null;
                        return (
                            <motion.button
                                key={award}
                                className="award-card"
                                onClick={() => onNavigate('analytics')}
                                variants={CARD_VARIANTS}
                                transition={preset.fieldSpring}
                                whileHover={isAdvanced ? { y: -2 } : undefined}
                            >
                                <div className="award-card-top">
                                    <span className="pill-badge"><Icon name={AWARD_META[award].icon} size="0.9em" /> {AWARD_META[award].label}</span>
                                    {prob != null && <span className="award-card-prob">{(prob * 100).toFixed(1)}%</span>}
                                </div>
                                {awardsLoading ? (
                                    <p className="award-card-name">Loading…</p>
                                ) : row ? (
                                    <>
                                        <div className="entity-row" style={{ margin: '0.6rem 0' }}>
                                            <PlayerHeadshot playerId={row.player_id} playerName={row.player_name} size={44} />
                                            <div className="entity-row-text">
                                                <span className="entity-row-name">{row.player_name}</span>
                                                <span className="entity-row-sub">
                                                    <TeamLogo abbreviation={row.team_abbreviation} size={14} style={{ verticalAlign: 'middle', marginRight: 4 }} />
                                                    {row.team_abbreviation}
                                                </span>
                                            </div>
                                        </div>
                                        {prob != null && (
                                            <div className="probability-bar">
                                                <div className="probability-bar-fill" style={{ width: `${Math.min(100, prob * 100)}%` }} />
                                            </div>
                                        )}
                                        <p className="award-card-desc">{awardCardLine(award, row)}</p>
                                        <div className="award-card-badges">
                                            {(() => {
                                                const seed = findTeamSeed(row.team_abbreviation);
                                                return seed && (
                                                    <span className="voter-badge">
                                                        <Icon name="format_list_numbered" size="0.85em" /> #{seed.seed} {seed.conference}
                                                    </span>
                                                );
                                            })()}
                                            {row.recent_win_streak > 0 && (
                                                <span className="voter-badge voter-badge--streak">
                                                    <Icon name="history" size="0.85em" />
                                                    Won last {row.recent_win_streak === 1 ? 'year' : `${row.recent_win_streak} years`}
                                                </span>
                                            )}
                                        </div>
                                    </>
                                ) : (
                                    <p className="award-card-name">No prediction available</p>
                                )}
                            </motion.button>
                        );
                    })}
                </motion.div>
            </Section>

            {/* Latest Headlines */}
            <Section className="dashboard-section" eyebrow="News" title="Latest headlines.">
                <div className="mini-news-list">
                    {news.slice(0, 3).map((item) => (
                        <div key={item.id} className="mini-news-item" onClick={() => onNavigate('news')}>
                            <span className="mini-news-category">{item.category}</span>
                            <p className="mini-news-headline">{item.headline}</p>
                            <span className="mini-news-meta">{item.source} · {item.date}</span>
                        </div>
                    ))}
                    {news.length === 0 && (
                        <EmptyState icon="newspaper" message="No live headlines loaded yet." />
                    )}
                </div>
            </Section>

            <Section className="dashboard-section" eyebrow="Live" title="Team form tracker.">
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
                                        <TeamLink abbr={team.abbr || abbrFromTeamName(team.team)} className="entity-row">
                                            <TeamLogo abbreviation={team.abbr || abbrFromTeamName(team.team)} size={24} />
                                            <span>{team.team}</span>
                                        </TeamLink>
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
            </Section>
        </div>
    );
}
