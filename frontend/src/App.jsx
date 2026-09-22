/**
 * NBA Analytics Dashboard — Main App
 *
 * IMPORTANT: FastAPI backend must enable CORS for frontend requests.
 * Each microservice (ports 8000, 8001, 8002) needs CORSMiddleware.
 */

import React, { useEffect, useState } from 'react';
import Sidebar from './components/layout/Sidebar';
import PageHeader from './components/layout/PageHeader';
import DashboardHome from './components/pages/DashboardHome';
import LiveScores from './components/pages/LiveScores';
import NewsSection from './components/pages/NewsSection';
import StandingsSection from './components/pages/StandingsSection';
import TeamComparison from './components/pages/TeamComparison';
import PlayerStats from './components/pages/PlayerStats';
import PlayerComparison from './components/pages/PlayerComparison';
import ShotCharts from './components/pages/ShotCharts';
import AnalyticsSection from './components/pages/AnalyticsSection';
import StatLeaders from './components/pages/StatLeaders';
import TradeAnalyzer from './components/pages/TradeAnalyzer';
import DraftValueGuide from './components/pages/DraftValueGuide';
import RookieClassTracker from './components/pages/RookieClassTracker';
import GuessThePlayer from './components/pages/GuessThePlayer';
import { prefetchCoreData } from './services/api';
import './styles/dashboard.css';

export default function App() {
  const [activePage, setActivePage] = useState('dashboard');

  useEffect(() => {
    prefetchCoreData();
  }, []);

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
      case 'rookies': return <RookieClassTracker />;
      case 'guess': return <GuessThePlayer />;
      default: return <DashboardHome onNavigate={setActivePage} />;
    }
  };

  return (
    <div className="app-shell">
      <Sidebar activePage={activePage} onNavigate={setActivePage} />
      <div className="main-area">
        <PageHeader activePage={activePage} />
        <main className="main-content">
          {renderPage()}
        </main>
      </div>
    </div>
  );
}
