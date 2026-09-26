import React, { useEffect, useRef, useState } from 'react';
import Lenis from 'lenis';
import 'lenis/dist/lenis.css';
import ParticleField from '../landing/ParticleField';
import CustomCursor from '../landing/CustomCursor';
import CursorGlow from '../landing/CursorGlow';
import LiveShotHero from '../landing/LiveShotHero';
import WelcomeIntro from '../landing/WelcomeIntro';
import IntroErrorBoundary from '../landing/IntroErrorBoundary';
import FeatureChapters from '../landing/FeatureChapters';
import TeamRibbons from '../landing/TeamRibbons';
import BigStat from '../ui/BigStat';
import Section from '../ui/Section';
import Skeleton from '../ui/Skeleton';
import { fetchSiteStats, fetchGamesByDate, fetchWpaValidation } from '../../services/api';
import { localDateIso } from '../../utils/date';
import TeamLogo from '../common/TeamLogo';
import Footer from '../layout/Footer';

function useHeroCursorScope() {
    return useRef(null);
}

// Scoped to the landing page: destroyed on unmount so the app shell keeps native scroll.
function useSmoothScroll() {
    useEffect(() => {
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;
        const lenis = new Lenis({ autoRaf: true, anchors: true });
        return () => lenis.destroy();
    }, []);
}

const HERO_HEIGHT = 720;

export default function LandingPage({ onOpenToday, onNavigate }) {
    const heroRef = useHeroCursorScope();
    useSmoothScroll();
    const particleFieldRef = useRef(null);
    const [stats, setStats] = useState(null);
    const [statsError, setStatsError] = useState(false);
    const [games, setGames] = useState(null);
    const [introDone, setIntroDone] = useState(false);
    const [wpaValidation, setWpaValidation] = useState(null);
    const ctaRef = useRef(null);

    useEffect(() => {
        let active = true;
        Promise.resolve().then(() => {
            fetchSiteStats().then((d) => { if (active) setStats(d); }).catch(() => { if (active) setStatsError(true); });
            fetchGamesByDate(localDateIso()).then((d) => { if (active) setGames(d?.games || []); }).catch(() => { if (active) setGames([]); });
            fetchWpaValidation().then((d) => {
                if (!active) return;
                const row = d?.scopes?.all_events || Object.values(d?.scopes || {})[0];
                if (row) setWpaValidation(row);
            }).catch(() => {});
        });
        return () => { active = false; };
    }, []);

    function onCtaEnter() {
        const el = ctaRef.current;
        if (!el || !particleFieldRef.current) return;
        const r = el.getBoundingClientRect();
        particleFieldRef.current.setAttractor({
            x: r.left + r.width / 2,
            y: r.top + r.height / 2 + (window.scrollY || 0),
        });
    }
    function onCtaLeave() {
        particleFieldRef.current?.setAttractor(null);
    }

    const seasonLabel = stats ? `${stats.season_min}–${stats.season_max}` : '…';

    return (
        <div className="landing-page">
            <ParticleField ref={particleFieldRef} heroHeight={HERO_HEIGHT} />
            <div className="landing-film-grain" aria-hidden="true" />
            <CustomCursor scopeRef={heroRef} />
            {!introDone && (
                <IntroErrorBoundary onError={() => setIntroDone(true)}>
                    <WelcomeIntro onDone={() => setIntroDone(true)} />
                </IntroErrorBoundary>
            )}

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
                    <LiveShotHero />
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

            <div id="how-it-works" className="chapters-intro">
                <p className="text-eyebrow">How it works</p>
                <h2 className="text-display-lg">
                    Built to be <span className="text-gradient">checked.</span>
                </h2>
                <p className="chapters-intro-subtitle">
                    Six of the roughly thirty tools inside — every one backed by a real Postgres table you can trace to a real upstream source. Scroll, and the particles become the real data.
                </p>
            </div>
            <FeatureChapters onNavigate={onNavigate} />

            <TeamRibbons />

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
                        <p>
                            Every predictive model is backtested against real held-out seasons, with the real accuracy shown alongside the prediction
                            {wpaValidation ? ` — the win-probability model's real held-out ROC-AUC is ${wpaValidation.roc_auc.toFixed(2)}, Brier score ${wpaValidation.brier_score.toFixed(3)}.` : '.'}
                        </p>
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

            <section className="landing-final-cta">
                <button
                    type="button"
                    ref={ctaRef}
                    className="landing-final-cta-btn"
                    data-magnetic
                    onClick={onOpenToday}
                    onMouseEnter={onCtaEnter}
                    onMouseLeave={onCtaLeave}
                >
                    Open NBA Hub
                </button>
            </section>

            <Footer />
        </div>
    );
}
