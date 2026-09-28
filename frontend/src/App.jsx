/**
 * NBA Analytics Dashboard — Main App
 *
 * IMPORTANT: FastAPI backend must enable CORS for frontend requests.
 * Each microservice (ports 8000, 8001, 8002) needs CORSMiddleware.
 */

import React, { Suspense, lazy, useCallback, useEffect, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import TopNav from './components/layout/TopNav';
import Footer from './components/layout/Footer';
import PageHeader from './components/layout/PageHeader';
import DashboardHome from './components/pages/DashboardHome';
import LandingPage from './components/pages/LandingPage';
import Loader from './components/Loader';
import { prefetchCoreData } from './services/api';
import { PAGE_PARAM, currentPageParam, pushPage } from './utils/useUrlState';
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
const TradeImpact = lazy(() => import('./components/pages/TradeImpact'));
const DraftValueGuide = lazy(() => import('./components/pages/DraftValueGuide'));
const HallOfFame = lazy(() => import('./components/pages/HallOfFame'));
const GreatsOfTheGame = lazy(() => import('./components/pages/GreatsOfTheGame'));
const RookieClassTracker = lazy(() => import('./components/pages/RookieClassTracker'));
const GamesHub = lazy(() => import('./components/pages/GamesHub'));
const LearnTheGame = lazy(() => import('./components/pages/LearnTheGame'));
const Methodology = lazy(() => import('./components/pages/Methodology'));
const LeaderboardBuilder = lazy(() => import('./components/pages/LeaderboardBuilder'));
const RegressionExplorer = lazy(() => import('./components/pages/RegressionExplorer'));
const BreakoutDetector = lazy(() => import('./components/pages/BreakoutDetector'));
const RoleFinder = lazy(() => import('./components/pages/RoleFinder'));
const EraTranslator = lazy(() => import('./components/pages/EraTranslator'));
const StatLineFinder = lazy(() => import('./components/pages/StatLineFinder'));

// Every page the app can show, by the id used in navConfig and `?page=`.
const PAGES = {
  dashboard: DashboardHome,
  scores: LiveScores,
  news: NewsSection,
  standings: StandingsSection,
  teams: TeamComparison,
  players: PlayerStats,
  compare: PlayerComparison,
  shotcharts: ShotCharts,
  analytics: AnalyticsSection,
  leaders: StatLeaders,
  trade: TradeAnalyzer,
  tradeimpact: TradeImpact,
  draft: DraftValueGuide,
  hof: HallOfFame,
  greats: GreatsOfTheGame,
  rookies: RookieClassTracker,
  games: GamesHub,
  learn: LearnTheGame,
  methodology: Methodology,
  builder: LeaderboardBuilder,
  regression: RegressionExplorer,
  breakouts: BreakoutDetector,
  rolefinder: RoleFinder,
  era: EraTranslator,
  statline: StatLineFinder,
};

// Which view the URL asks for. No `page` = the landing page, except that an
// old-style Analytics link (`/#wpa`, from before pages were in the URL)
// still opens Analytics on that tab.
function routeFromUrl() {
  const page = currentPageParam();
  if (page) return { landing: false, page: PAGES[page] ? page : 'dashboard' };
  if (window.location.hash.length > 1) return { landing: false, page: 'analytics' };
  return { landing: true, page: 'dashboard' };
}

export default function App() {
  const [route, setRoute] = useState(routeFromUrl);
  const activePage = route.page;
  const showLanding = route.landing;

  useEffect(() => {
    prefetchCoreData();
  }, []);

  // Make the URL say which page is open (e.g. an old `/#wpa` link becomes
  // `?page=analytics#wpa`) without adding a history entry.
  useEffect(() => {
    if (!showLanding && currentPageParam() !== activePage) {
      const url = new URL(window.location.href);
      url.searchParams.set(PAGE_PARAM, activePage);
      window.history.replaceState(window.history.state, '', url.pathname + url.search + url.hash);
    }
  }, [showLanding, activePage]);

  // Back / forward.
  useEffect(() => {
    const onPopState = () => {
      setRoute(routeFromUrl());
      // Entries that differ only in the Analytics tab: let the open
      // AnalyticsSection pick the tab up.
      window.dispatchEvent(new Event('hashchange'));
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  // `hash` is an Analytics tab id (e.g. 'wpa'); `params` are optional tool
  // inputs for the target page (e.g. a trade handed to Trade Impact).
  const navigate = useCallback((page, hash, params) => {
    const target = PAGES[page] ? page : 'dashboard';
    // Re-picking the open page from the menu keeps its inputs and link.
    if (!hash && currentPageParam() === target) return;
    pushPage(target, hash, params);
    setRoute({ landing: false, page: target });
  }, []);

  const goToLanding = useCallback(() => {
    pushPage(null);
    setRoute({ landing: true, page: activePage });
  }, [activePage]);

  const renderPage = () => {
    const Page = PAGES[activePage] || DashboardHome;
    return <Page onNavigate={navigate} />;
  };

  if (showLanding) {
    return <LandingPage onOpenToday={() => navigate('dashboard')} onNavigate={navigate} />;
  }

  return (
    <div className="top-shell">
      <TopNav activePage={activePage} onNavigate={navigate} onGoToLanding={goToLanding} />
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
