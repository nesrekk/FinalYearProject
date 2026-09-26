/**
 * NBA Analytics Dashboard — Main App
 *
 * IMPORTANT: FastAPI backend must enable CORS for frontend requests.
 * Each microservice (ports 8000, 8001, 8002) needs CORSMiddleware.
 */

import React, { Suspense, lazy, useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import TopNav from './components/layout/TopNav';
import Footer from './components/layout/Footer';
import PageHeader from './components/layout/PageHeader';
import DashboardHome from './components/pages/DashboardHome';
import LandingPage from './components/pages/LandingPage';
import Loader from './components/Loader';
import { prefetchCoreData } from './services/api';
import './styles/dashboard.css';
import './styles/theme.css';

// Code-split: only the landing page + Today dashboard (the two views
// everyone hits first) are in the main bundle. Every other page is its
// own chunk, fetched the first time it's actually opened — this is what
// keeps the single-bundle warning from `vite build` from just growing
// forever as pages accumulate.
const LiveScores = lazy(() => import('./components/pages/LiveScores'));
const NewsSection = lazy(() => import('./components/pages/NewsSection'));
const StandingsSection = lazy(() => import('./components/pages/StandingsSection'));
const TeamComparison = lazy(() => import('./components/pages/TeamComparison'));
const PlayerStats = lazy(() => import('./components/pages/PlayerStats'));
const PlayerComparison = lazy(() => import('./components/pages/PlayerComparison'));
const ShotCharts = lazy(() => import('./components/pages/ShotCharts'));
const AnalyticsSection = lazy(() => import('./components/pages/AnalyticsSection'));
const StatLeaders = lazy(() => import('./components/pages/StatLeaders'));
const TradeAnalyzer = lazy(() => import('./components/pages/TradeAnalyzer'));
const DraftValueGuide = lazy(() => import('./components/pages/DraftValueGuide'));
const HallOfFame = lazy(() => import('./components/pages/HallOfFame'));
const RookieClassTracker = lazy(() => import('./components/pages/RookieClassTracker'));
const GamesHub = lazy(() => import('./components/pages/GamesHub'));
const LearnTheGame = lazy(() => import('./components/pages/LearnTheGame'));

export default function App() {
  const [activePage, setActivePage] = useState('dashboard');
  const [showLanding, setShowLanding] = useState(true);

  useEffect(() => {
    prefetchCoreData();
  }, []);

  const enterApp = (page, hash) => {
    setShowLanding(false);
    setActivePage(page || 'dashboard');
    if (hash) requestAnimationFrame(() => { window.location.hash = hash; });
  };

  const renderPage = () => {
    switch (activePage) {
      case 'dashboard': return <DashboardHome onNavigate={setActivePage} />;
      case 'scores': return <LiveScores />;
      case 'news': return <NewsSection />;
      case 'standings': return <StandingsSection />;
      case 'teams': return <TeamComparison />;
      case 'players': return <PlayerStats />;
      case 'compare': return <PlayerComparison />;
      case 'shotcharts': return <ShotCharts />;
      case 'analytics': return <AnalyticsSection />;
      case 'leaders': return <StatLeaders />;
      case 'trade': return <TradeAnalyzer />;
      case 'draft': return <DraftValueGuide />;
      case 'hof': return <HallOfFame />;
      case 'rookies': return <RookieClassTracker />;
      case 'games': return <GamesHub />;
      case 'learn': return <LearnTheGame onNavigate={setActivePage} />;
      default: return <DashboardHome onNavigate={setActivePage} />;
    }
  };

  if (showLanding) {
    return <LandingPage onOpenToday={() => enterApp('dashboard')} onNavigate={enterApp} />;
  }

  return (
    <div className="top-shell">
      <TopNav activePage={activePage} onNavigate={setActivePage} onGoToLanding={() => setShowLanding(true)} />
      <main className="top-shell-main">
        <PageHeader activePage={activePage} />
        <AnimatePresence mode="wait">
          <motion.div
            key={activePage}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
          >
            <Suspense fallback={<Loader />}>
              {renderPage()}
            </Suspense>
          </motion.div>
        </AnimatePresence>
      </main>
      <Footer />
    </div>
  );
}
