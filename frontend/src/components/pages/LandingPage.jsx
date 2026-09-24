import React, { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import ParticleField from '../landing/ParticleField';
import CustomCursor from '../landing/CustomCursor';
import CursorGlow from '../landing/CursorGlow';
import BigStat from '../ui/BigStat';
import Section from '../ui/Section';
import Skeleton from '../ui/Skeleton';
import { fetchSiteStats, fetchGamesByDate } from '../../services/api';
import { localDateIso } from '../../utils/date';
import TeamLogo from '../common/TeamLogo';
import Footer from '../layout/Footer';

const FEATURES = [
    {
        id: 'analytics',
        hash: 'mvp',
        eyebrow: 'Models',
        title: 'Awards Race',
        copy: 'Real logistic-regression probability for MVP, DPOY, ROY and All-NBA — validated against every real past season, not eyeballed.',
    },
    {
        id: 'analytics',
        hash: 'trajectory',
        eyebrow: 'Player Analysis',
        title: 'Career Trajectory',
        copy: 'Real similar-player comps project a real aging curve, shown as an honest uncertainty cone rather than a single confident line.',
    },
    {
        id: 'analytics',
        hash: 'wpa',
        eyebrow: 'Player Analysis',
        title: 'Win-Probability Replay',
        copy: 'Every real play of a real game, replayed through the same real model that trained on 7,652 real games — click any missed shot for a real counterfactual.',
    },
    {
        id: 'analytics',
        hash: 'prospects',
        eyebrow: 'Prospects',
        title: 'Draft Prospect Comps',
        copy: 'Real D1 college production matched to real NBA rookie outcomes across ~105,000 real college player-seasons.',
    },
];

function useHeroCursorScope() {
    return useRef(null);
}

const HERO_HEIGHT = 720;

export default function LandingPage({ onOpenToday, onNavigate }) {
    const heroRef = useHeroCursorScope();
    const [stats, setStats] = useState(null);
    const [statsError, setStatsError] = useState(false);
    const [games, setGames] = useState(null);

    useEffect(() => {
        let active = true;
        Promise.resolve().then(() => {
            fetchSiteStats().then((d) => { if (active) setStats(d); }).catch(() => { if (active) setStatsError(true); });
            fetchGamesByDate(localDateIso()).then((d) => { if (active) setGames(d?.games || []); }).catch(() => { if (active) setGames([]); });
        });
        return () => { active = false; };
    }, []);

    const seasonLabel = stats ? `${stats.season_min}–${stats.season_max}` : '…';

    return (
        <div className="landing-page">
            <ParticleField heroHeight={HERO_HEIGHT} />
            <div className="landing-film-grain" aria-hidden="true" />
            <CustomCursor scopeRef={heroRef} />

            <section className="landing-hero-wrap">
                <div className="landing-hero" ref={heroRef}>
                    <CursorGlow scopeRef={heroRef} />
                    <button
                        type="button"
                        className="landing-hero-menu"
                        onClick={onOpenToday}
                        aria-label="More"
                    >
                        <span className="material-symbols-outlined icon">more_horiz</span>
                    </button>
                    <div className="landing-hero-content">
                        <p className="text-eyebrow">NBA HUB &middot; {seasonLabel}</p>
                        <h1 className="text-display-xl landing-hero-title">
                            Every number. <span className="text-gradient">Real.</span>
                        </h1>
                        <p className="landing-hero-subtitle">
                            Models, comps, and forecasts built only on data you can trace back to its source.
                        </p>
                        <div className="landing-hero-actions">
                            <button type="button" className="landing-btn landing-btn--primary" data-magnetic onClick={onOpenToday}>
                                Open Today
                            </button>
                            <a href="#how-it-works" className="landing-btn landing-btn--secondary" data-magnetic>
                                See how it works
                            </a>
                        </div>
                    </div>
                </div>

                <div className="landing-stats-row">
                    <div className="landing-stat-tile">
                        <p className="text-eyebrow">Seasons</p>
                        {!stats && !statsError ? (
                            <Skeleton variant="text" width="4rem" height="2.5rem" className="landing-stat-skeleton" />
                        ) : (
                            <BigStat label="" value={statsError ? '—' : stats.n_seasons} className="landing-stat-value landing-stat-value--brand" />
                        )}
                    </div>
                    <div className="landing-stat-tile">
                        <p className="text-eyebrow">Player-Seasons</p>
                        {!stats && !statsError ? (
                            <Skeleton variant="text" width="6rem" height="2.5rem" className="landing-stat-skeleton" />
                        ) : (
                            <BigStat label="" value={statsError ? '—' : stats.n_player_seasons} compact className="landing-stat-value" />
                        )}
                    </div>
                    <div className="landing-stat-tile landing-stat-tile--gradient-border">
                        <p className="text-eyebrow">College Seasons</p>
                        {!stats && !statsError ? (
                            <Skeleton variant="text" width="6rem" height="2.5rem" className="landing-stat-skeleton" />
                        ) : (
                            <BigStat label="" value={statsError ? '—' : stats.n_college_seasons} compact className="landing-stat-value" />
                        )}
                    </div>
                </div>
            </section>

            {games && games.length > 0 && (
                <div className="landing-live-strip">
                    <div className="landing-live-strip-track">
                        {[...games, ...games].map((g, i) => (
                            <span key={i} className="landing-live-strip-item">
                                <TeamLogo abbreviation={g.away?.abbr} size={18} />
                                {g.away?.abbr} {g.away?.score ?? ''} — {g.home?.score ?? ''} {g.home?.abbr}
                                <TeamLogo abbreviation={g.home?.abbr} size={18} />
                                <span className="landing-live-strip-status">{g.status_text || g.status}</span>
                            </span>
                        ))}
                    </div>
                </div>
            )}

            <Section
                className="landing-section"
                eyebrow="How it works"
                title={<span id="how-it-works">Built to be <span className="text-gradient">checked</span>.</span>}
                subtitle="Four of the roughly thirty tools inside — every one backed by a real Postgres table you can trace to a real upstream source."
            >
                <div className="landing-features">
                    {FEATURES.map((f) => (
                        <motion.button
                            type="button"
                            key={f.title}
                            className="landing-feature"
                            onClick={() => onNavigate(f.id, f.hash)}
                            initial={{ opacity: 0, y: 16 }}
                            whileInView={{ opacity: 1, y: 0 }}
                            viewport={{ once: true, margin: '-60px' }}
                            transition={{ type: 'spring', stiffness: 260, damping: 30 }}
                        >
                            <p className="text-eyebrow">{f.eyebrow}</p>
                            <h3 className="text-headline landing-feature-title">{f.title}</h3>
                            <p className="landing-feature-copy">{f.copy}</p>
                            <span className="landing-feature-link">Explore &rarr;</span>
                        </motion.button>
                    ))}
                </div>
            </Section>

            <Section
                className="landing-section"
                title="Nothing is made up."
                subtitle="The guiding rule behind every feature on this site."
            >
                <div className="landing-trust-grid">
                    <div className="landing-trust-item">
                        <span className="material-symbols-outlined icon">verified</span>
                        <h3 className="text-headline">Real sources</h3>
                        <p>Every endpoint discloses which real Postgres table and which real upstream API or dataset backed it — a small chip on every main page.</p>
                    </div>
                    <div className="landing-trust-item">
                        <span className="material-symbols-outlined icon">visibility</span>
                        <h3 className="text-headline">Disclosed gaps</h3>
                        <p>Where real data doesn&apos;t exist for something, that gap is stated outright instead of filled in with a guess.</p>
                    </div>
                    <div className="landing-trust-item">
                        <span className="material-symbols-outlined icon">science</span>
                        <h3 className="text-headline">Validated models</h3>
                        <p>Every predictive model is backtested against real held-out seasons, with the real accuracy shown alongside the prediction.</p>
                    </div>
                </div>
            </Section>

            <Section className="landing-section" eyebrow="Play" title="Daily games, real stats.">
                <div className="landing-games-teaser">
                    {['Guess the Player', 'Blurred Player', 'Higher or Lower', 'Guess the Game'].map((g) => (
                        <button type="button" key={g} className="landing-game-tile" onClick={() => onNavigate('games')}>
                            {g}
                        </button>
                    ))}
                </div>
            </Section>

            <Footer />
        </div>
    );
}
