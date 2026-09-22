import React, { useState } from 'react';
import Icon from '../common/Icon';
import SimilaritySection from '../SimilaritySection';
import AwardsRaceSection from '../AwardsRaceSection';
import ImpactSection from '../ImpactSection';
import ModelValidationSection from '../ModelValidationSection';
import PlayerArchetypesSection from '../PlayerArchetypesSection';
import RadarCompareSection from '../RadarCompareSection';
import TrendAnalysisSection from '../TrendAnalysisSection';

const tabs = [
    { id: 'similarity', label: 'Season Similarity', icon: 'bar_chart' },
    { id: 'mvp', label: 'Awards Race', icon: 'emoji_events' },
    { id: 'impact', label: 'Impact Rankings', icon: 'bolt' },
    { id: 'validation', label: 'Model Validation', icon: 'science' },
    { id: 'archetypes', label: 'Player Archetypes', icon: 'biotech' },
    { id: 'radar', label: 'Radar Compare', icon: 'radar' },
    { id: 'trends', label: 'Trend Analysis', icon: 'trending_up' },
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
            </div>
        </div>
    );
}
