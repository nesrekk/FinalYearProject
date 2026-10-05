import React, { Suspense, lazy, useEffect, useRef, useState } from 'react';
import Lenis from 'lenis';
import 'lenis/dist/lenis.css';
import DitherRibbon from '../landing/DitherRibbon';
import DitherBall from '../landing/DitherBall';
import Footer from '../layout/Footer';
import {
    fetchSiteStats, fetchLeagueShotSample, fetchGamesByDate, fetchWpReplayList,
    fetchCurrentMeta, fetchMVPPrediction, fetchBacktestOverview, fetchWpaValidation,
} from '../../services/api';
import { nbaDateIso } from '../../utils/date';

const ShotCourtFlight = lazy(() => import('../landing/ShotCourtFlight'));

const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString());
const pct = (v, digits = 0) => (v == null ? null : `${(v * 100).toFixed(digits)}%`);
const seasonLabel = (s) => `${s - 1}-${String(s).slice(-2)}`;

// Scoped to the landing page: destroyed on unmount so the app shell keeps native scroll.
function useSmoothScroll() {
    useEffect(() => {
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return undefined;
        const lenis = new Lenis({ autoRaf: true, anchors: true });
        return () => lenis.destroy();
    }, []);
}

function useLandingData() {
    const [data, setData] = useState({});
    useEffect(() => {
        let active = true;
        const patch = (fields) => { if (active) setData((d) => ({ ...d, ...fields })); };

        fetchSiteStats().then((stats) => patch({ stats })).catch(() => patch({ statsError: true }));
        fetchLeagueShotSample().then((shots) => patch({ shots })).catch(() => {});
        fetchWpaValidation().then((res) => {
            const scopes = res?.scopes || {};
            patch({ wpa: scopes.all_events || Object.values(scopes)[0] || null });
        }).catch(() => {});
        fetchBacktestOverview().then((bt) => {
            const row = bt?.awards?.find((a) => a.award?.toLowerCase() === 'mvp' && a.model_type === 'logreg');
            if (row) patch({ mvpBacktest: { hits: Math.round(row.top1_accuracy * row.n_seasons_evaluated), n: row.n_seasons_evaluated } });
        }).catch(() => {});

        (async () => {
            const meta = await fetchCurrentMeta().catch(() => null);
            // The latest stored season (the award models' newest), not the league year in progress:
            // asking for the season in progress was a 404 on every load (round 8 R8-049).
            const start = meta?.stored_season ?? meta?.season ?? new Date().getFullYear();
            for (let s = start; s >= start - 3; s--) {
                const res = await fetchMVPPrediction(s).catch(() => null);
                if (res?.results?.length) { patch({ mvpSeason: s, mvpFavorite: res.results[0].player_name }); break; }
            }
        })();

        (async () => {
            const today = await fetchGamesByDate(nbaDateIso()).catch(() => null);
            const live = (today?.games || []).map((g) => ({
                key: g.game_id || `${g.away?.abbr}-${g.home?.abbr}`,
                away: g.away?.abbr, home: g.home?.abbr,
                awayScore: g.away?.score, homeScore: g.home?.score,
                status: g.status_text || g.status,
            }));
            if (live.length) { patch({ ticker: { label: 'Today', games: live } }); return; }
            const list = await fetchWpReplayList().catch(() => null);
            const games = list?.games || [];
            const lastDate = games.reduce((m, g) => (g.game_date > m ? g.game_date : m), '');
            const finals = games.filter((g) => g.game_date === lastDate).map((g) => ({
                key: g.game_id, away: g.away_team, home: g.home_team,
                awayScore: g.final_score?.away, homeScore: g.final_score?.home, status: 'Final',
            }));
            if (finals.length) patch({ ticker: { label: `Real finals · ${lastDate}`, games: finals } });
        })();

        return () => { active = false; };
    }, []);
    return data;
}

function Sticker({ label, value, className = '' }) {
    return (
        <div className={`lp-sticker ${className}`}>
            <span className="lp-mono">{label}</span>
            <b>{value}</b>
        </div>
    );
}

function Ticker({ ticker }) {
    if (!ticker) return <div className="lp-ticker lp-ticker--empty" />;
    const items = ticker.games.map((g) => {
        const homeWon = g.homeScore != null && g.awayScore != null && g.homeScore > g.awayScore;
        return (
            <span key={g.key} className="lp-ticker-item">
                {g.away} {homeWon ? g.awayScore : <em>{g.awayScore}</em>}
                {' — '}
                {homeWon ? <em>{g.homeScore}</em> : g.homeScore} {g.home}
                <small>{g.status}</small>
            </span>
        );
    });
    return (
        <div className="lp-ticker" aria-label={`${ticker.label} scores`}>
            <div className="lp-ticker-track">
                <span className="lp-ticker-item lp-ticker-label">{ticker.label}</span>
                {items}
                <span className="lp-ticker-item lp-ticker-label" aria-hidden="true">{ticker.label}</span>
                {items}
            </div>
        </div>
    );
}

function Chapter({ index, eyebrow, title, copy, action, onAction, side }) {
    return (
        <div className={`lp-chapter lp-chapter--${side}`}>
            <div className="lp-chapter-card">
                <span className="lp-mono">{String(index).padStart(2, '0')} / {eyebrow}</span>
                <h2>{title}</h2>
                <p>{copy}</p>
                {onAction && (
                    <button type="button" className="lp-link" onClick={onAction}>{action} →</button>
                )}
            </div>
        </div>
    );
}

export default function LandingPage({ onOpenToday, onNavigate }) {
    useSmoothScroll();
    const d = useLandingData();
    const flightRef = useRef(null);
    const stats = d.stats;
    const shots = d.shots;

    const range = stats ? `${stats.season_min}–${stats.season_max}` : '';

    return (
        <div className="lp">
            <div className="lp-top">
                <nav className="lp-nav">
                    <span className="lp-logo">NBA HUB®</span>
                    <span className="lp-nav-links lp-mono">
                        <button type="button" onClick={() => onNavigate('analytics', 'mvp')}>Awards</button>
                        <button type="button" onClick={() => onNavigate('shotcharts')}>Shots</button>
                        <button type="button" onClick={() => onNavigate('analytics', 'wpa')}>Clutch</button>
                        <button type="button" onClick={() => onNavigate('games')}>Games</button>
                        <button type="button" onClick={() => onNavigate('learn')}>Learn</button>
                    </span>
                    <button type="button" className="lp-cta" onClick={onOpenToday}>OPEN TODAY ↗</button>
                </nav>

                <header className="lp-hero">
                    <DitherRibbon className="lp-hero-ribbon" />
                    <DitherBall className="lp-hero-ball" />
                    <div className="lp-stickers">
                        <Sticker className="lp-sticker--a" label="Seasons" value={d.statsError ? '—' : fmt(stats?.n_seasons)} />
                        <Sticker className="lp-sticker--b" label="Player-seasons" value={d.statsError ? '—' : fmt(stats?.n_player_seasons)} />
                        <Sticker className="lp-sticker--c" label="College seasons" value={d.statsError ? '—' : fmt(stats?.n_college_seasons)} />
                    </div>
                    <h1 className="lp-title">
                        <span>Every</span>
                        <span className="lp-outline">number.</span>
                        <span>Real.</span>
                    </h1>
                    <div className="lp-side lp-mono">{range ? `${range} · ` : ''}Nothing made up</div>
                </header>
            </div>

            <Ticker ticker={d.ticker} />

            <section className="lp-flight" ref={flightRef}>
                <div className="lp-flight-stage">
                    {shots?.points?.length > 0 && (
                        <Suspense fallback={null}>
                            <ShotCourtFlight points={shots.points} sectionRef={flightRef} />
                        </Suspense>
                    )}
                    <div className="lp-flight-legend lp-mono">
                        <span><i className="lp-dot lp-dot--made" />Made</span>
                        <span><i className="lp-dot lp-dot--missed" />Missed</span>
                        {shots && <span>{fmt(shots.n_sample)} real {shots.season} shots</span>}
                    </div>
                </div>
                <div className="lp-flight-chapters">
                    <Chapter
                        index={1}
                        side="left"
                        eyebrow="Shots"
                        title="Sees every shot."
                        copy={shots
                            ? `${fmt(shots.n_sample)} real ${shots.season} shots, a fixed random sample of all ${fmt(shots.n_season_shots)} taken that season, placed where they were taken. The league shot ${pct(shots.season_fg_pct, 1)} from the field.`
                            : 'Real shots from the latest season, each placed where it was taken. Made in black, missed in cream.'}
                        action="Shot charts"
                        onAction={() => onNavigate('shotcharts')}
                    />
                    <Chapter
                        index={2}
                        side="right"
                        eyebrow="Models"
                        title="Predicts the awards."
                        copy={d.mvpFavorite
                            ? `The MVP model's ${seasonLabel(d.mvpSeason)} favourite is ${d.mvpFavorite}.${d.mvpBacktest ? ` Backtested on real past seasons, its top pick was the actual winner in ${d.mvpBacktest.hits} of ${d.mvpBacktest.n}.` : ''}`
                            : 'Logistic-regression award models, backtested against every real past season, with the accuracy shown next to every pick.'}
                        action="Awards race"
                        onAction={() => onNavigate('analytics', 'mvp')}
                    />
                    <Chapter
                        index={3}
                        side="left"
                        eyebrow="Clutch"
                        title="Feels the pressure."
                        copy={d.wpa
                            ? `Every play of a real game run through a win-probability model, checked on ${fmt(d.wpa.n_events)} held-out real plays: ROC-AUC ${d.wpa.roc_auc?.toFixed(2)}.`
                            : 'Every play of a real game run through a validated win-probability model.'}
                        action="Game replay"
                        onAction={() => onNavigate('analytics', 'replay')}
                    />
                </div>
            </section>

            <section className="lp-grid">
                <div className="lp-cell">
                    <span className="lp-mono">Rule 01</span>
                    <h3>Real sources.</h3>
                    <p>Every endpoint names the Postgres table and upstream source behind it, shown as a chip on every main page.</p>
                </div>
                <div className="lp-cell lp-cell--acid">
                    <span className="lp-mono">Rule 02</span>
                    <h3>Gaps disclosed.</h3>
                    <p>Where real data doesn&apos;t exist, the page says so instead of filling it with a guess.</p>
                </div>
                <div className="lp-cell">
                    <span className="lp-mono">Rule 03</span>
                    <h3>Models graded.</h3>
                    <p>Every predictive model is backtested on real held-out data, and its accuracy is shown alongside it.</p>
                </div>
            </section>

            <button type="button" className="lp-learn" onClick={() => onNavigate('learn')}>
                <span className="lp-mono">New to basketball?</span>
                <span className="lp-learn-title">Learn the game →</span>
                <span className="lp-learn-sub">The court, the scoring and the math behind every shot, in five minutes.</span>
            </button>

            <section className="lp-play">
                <span className="lp-mono">Daily games · real stats</span>
                <div className="lp-play-tiles">
                    {['Guess the Player', 'Blurred Player', 'Higher or Lower', 'Guess the Game'].map((g) => (
                        <button type="button" key={g} className="lp-play-tile" onClick={() => onNavigate('games')}>{g}</button>
                    ))}
                </div>
            </section>

            <button type="button" className="lp-final" onClick={onOpenToday}>
                <span>Open NBA Hub</span><span aria-hidden="true">↗</span>
            </button>

            <Footer />
        </div>
    );
}
