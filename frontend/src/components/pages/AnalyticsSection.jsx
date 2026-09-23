import React, { useEffect, useMemo, useState } from 'react';
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
import HeliocentricitySection from '../HeliocentricitySection';
import ClutchWpaSection from '../ClutchWpaSection';
import LineupChemistrySection from '../LineupChemistrySection';
import GameReplaySection from '../GameReplaySection';

const TAB_GROUPS = [
    {
        name: 'Models',
        tabs: [
            { id: 'mvp', label: 'Awards Race', icon: 'emoji_events' },
            { id: 'impact', label: 'Impact Rankings', icon: 'bolt' },
            { id: 'validation', label: 'Model Validation', icon: 'science' },
        ],
    },
    {
        name: 'Player Analysis',
        tabs: [
            { id: 'similarity', label: 'Season Similarity', icon: 'bar_chart' },
            { id: 'archetypes', label: 'Player Archetypes', icon: 'biotech' },
            { id: 'radar', label: 'Radar Compare', icon: 'radar' },
            { id: 'trends', label: 'Trend Analysis', icon: 'trending_up' },
            { id: 'trajectory', label: 'Career Trajectory', icon: 'timeline' },
            { id: 'helio', label: 'Heliocentricity', icon: 'wb_sunny' },
            { id: 'wpa', label: 'Clutch WPA', icon: 'timer' },
        ],
    },
    {
        name: 'Teams & Markets',
        tabs: [
            { id: 'vegas', label: 'Vegas Scanner', icon: 'currency_exchange' },
            { id: 'playoffs', label: 'Playoff Forecaster', icon: 'military_tech' },
            { id: 'lineups', label: 'Lineup Chemistry', icon: 'diversity_3' },
            { id: 'replay', label: 'Game Replay', icon: 'movie' },
        ],
    },
    {
        name: 'Prospects',
        tabs: [
            { id: 'prospects', label: 'Draft Prospects', icon: 'school' },
        ],
    },
];

const ALL_TABS = TAB_GROUPS.flatMap((g) => g.tabs);
const DEFAULT_TAB = 'similarity';

function groupNameForTab(tabId) {
    const group = TAB_GROUPS.find((g) => g.tabs.some((t) => t.id === tabId));
    return group ? group.name : TAB_GROUPS[0].name;
}

function tabFromHash() {
    const hash = window.location.hash.replace(/^#/, '');
    return ALL_TABS.some((t) => t.id === hash) ? hash : null;
}

export default function AnalyticsSection() {
    const [activeTab, setActiveTab] = useState(() => tabFromHash() || DEFAULT_TAB);
    const [activeGroup, setActiveGroup] = useState(() => groupNameForTab(tabFromHash() || DEFAULT_TAB));

    // Keep the URL hash in sync so a specific tab can be linked/bookmarked
    // directly, without polluting browser back/forward history on every click.
    useEffect(() => {
        const newHash = `#${activeTab}`;
        if (window.location.hash !== newHash) {
            window.history.replaceState(null, '', newHash);
        }
    }, [activeTab]);

    // If the page loads (or the hash changes externally) with a tab id in the
    // URL, honor it — this is what makes a link like #wpa actually work.
    useEffect(() => {
        function onHashChange() {
            const tab = tabFromHash();
            if (tab) {
                setActiveTab(tab);
                setActiveGroup(groupNameForTab(tab));
            }
        }
        window.addEventListener('hashchange', onHashChange);
        return () => window.removeEventListener('hashchange', onHashChange);
    }, []);

    const currentGroupTabs = useMemo(
        () => TAB_GROUPS.find((g) => g.name === activeGroup)?.tabs || TAB_GROUPS[0].tabs,
        [activeGroup]
    );

    function selectGroup(groupName) {
        setActiveGroup(groupName);
        const group = TAB_GROUPS.find((g) => g.name === groupName);
        if (group && !group.tabs.some((t) => t.id === activeTab)) {
            setActiveTab(group.tabs[0].id);
        }
    }

    return (
        <div className="page page-analytics fade-in">
            {/* Group selector */}
            <div className="tab-bar tab-bar--groups">
                {TAB_GROUPS.map((group) => (
                    <button
                        key={group.name}
                        className={`tab-btn tab-btn--group ${activeGroup === group.name ? 'tab-btn--active' : ''}`}
                        onClick={() => selectGroup(group.name)}
                    >
                        {group.name}
                    </button>
                ))}
            </div>

            {/* Tabs within the selected group */}
            <div className="tab-bar tab-bar--sub">
                {currentGroupTabs.map((tab) => (
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
                {activeTab === 'helio' && <HeliocentricitySection />}
                {activeTab === 'wpa' && <ClutchWpaSection />}
                {activeTab === 'lineups' && <LineupChemistrySection />}
                {activeTab === 'replay' && <GameReplaySection />}
            </div>
        </div>
    );
}
