import React, { useState } from 'react';
import Icon from '../common/Icon';
import SimilaritySection from '../SimilaritySection';
import AwardsRaceSection from '../AwardsRaceSection';
import ImpactSection from '../ImpactSection';
import ModelValidationSection from '../ModelValidationSection';
import PlayerArchetypesSection from '../PlayerArchetypesSection';
import RadarCompareSection from '../RadarCompareSection';
import TrendAnalysisSection from '../TrendAnalysisSection';
import TrajectoryForecasterSection from '../TrajectoryForecasterSection';
import VegasScannerSection from '../VegasScannerSection';
import PlayoffForecasterSection from '../PlayoffForecasterSection';
import DraftProspectSection from '../DraftProspectSection';

const tabs = [
    { id: 'similarity', label: 'Season Similarity', icon: 'bar_chart' },
    { id: 'mvp', label: 'Awards Race', icon: 'emoji_events' },
    { id: 'impact', label: 'Impact Rankings', icon: 'bolt' },
    { id: 'validation', label: 'Model Validation', icon: 'science' },
    { id: 'archetypes', label: 'Player Archetypes', icon: 'biotech' },
    { id: 'radar', label: 'Radar Compare', icon: 'radar' },
    { id: 'trends', label: 'Trend Analysis', icon: 'trending_up' },
    { id: 'trajectory', label: 'Career Trajectory', icon: 'timeline' },
    { id: 'vegas', label: 'Vegas Scanner', icon: 'currency_exchange' },
    { id: 'playoffs', label: 'Playoff Forecaster', icon: 'military_tech' },
    { id: 'prospects', label: 'Draft Prospects', icon: 'school' },
];

export default function AnalyticsSection() {
    const [activeTab, setActiveTab] = useState('similarity');

    return (
        <div className="page page-analytics fade-in">
            {/* Analytics tabs */}
            <div className="tab-bar">
                {tabs.map((tab) => (
                    <button
                        key={tab.id}
                        className={`tab-btn ${activeTab === tab.id ? 'tab-btn--active' : ''}`}
                        onClick={() => setActiveTab(tab.id)}
                    >
                        <span className="tab-icon"><Icon name={tab.icon} /></span> {tab.label}
                    </button>
                ))}
            </div>

            {/* Tab content */}
            <div className="analytics-content fade-in">
                {activeTab === 'similarity' && <SimilaritySection />}
                {activeTab === 'mvp' && <AwardsRaceSection />}
                {activeTab === 'impact' && <ImpactSection />}
                {activeTab === 'validation' && <ModelValidationSection />}
                {activeTab === 'archetypes' && <PlayerArchetypesSection />}
                {activeTab === 'radar' && <RadarCompareSection />}
                {activeTab === 'trends' && <TrendAnalysisSection />}
                {activeTab === 'trajectory' && <TrajectoryForecasterSection />}
                {activeTab === 'vegas' && <VegasScannerSection />}
                {activeTab === 'playoffs' && <PlayoffForecasterSection />}
                {activeTab === 'prospects' && <DraftProspectSection />}
            </div>
        </div>
    );
}
