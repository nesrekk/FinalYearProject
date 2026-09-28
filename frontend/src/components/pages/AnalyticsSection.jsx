import React, { useEffect, useMemo, useState } from 'react';
import Icon from '../common/Icon';
import AnalyticsIndex from '../analytics/AnalyticsIndex';
import AboutModelDrawer from '../ui/AboutModelDrawer';
import { TOOL_META } from '../analytics/toolMeta';
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
import PairChemistrySection from '../PairChemistrySection';
import OnOffSection from '../OnOffSection';
import LuckScheduleSection from '../LuckScheduleSection';
import GameReplaySection from '../GameReplaySection';
import PredictionLedgerSection from '../PredictionLedgerSection';
import WithWithoutStarSection from '../WithWithoutStarSection';
import ScheduleFatigueSection from '../ScheduleFatigueSection';
import MatchupFinderSection from '../MatchupFinderSection';
import RefereeTendenciesSection from '../RefereeTendenciesSection';
import GarbageTimeSection from '../GarbageTimeSection';
import DadIndexSection from '../DadIndexSection';
import SpacingLabSection from '../SpacingLabSection';
import ContractValueSection from '../ContractValueSection';
import CollegePipelineSection from '../CollegePipelineSection';
import MarchMadnessSection from '../MarchMadnessSection';

const TAB_GROUPS = [
    {
        name: 'Models',
        tabs: [
            { id: 'mvp', label: 'Awards Race', icon: 'emoji_events' },
            { id: 'impact', label: 'Impact Rankings', icon: 'bolt' },
            { id: 'validation', label: 'Model Validation', icon: 'science' },
            { id: 'ledger', label: 'Prediction Ledger', icon: 'timeline' },
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
            { id: 'matchups', label: 'Matchup Finder', icon: 'swords' },
            { id: 'garbage', label: 'Garbage-Time Deflator', icon: 'delete_sweep' },
            { id: 'dad', label: 'DAD Index', icon: 'shield' },
        ],
    },
    {
        name: 'Teams & Markets',
        tabs: [
            { id: 'vegas', label: 'Vegas Scanner', icon: 'currency_exchange' },
            { id: 'playoffs', label: 'Playoff Forecaster', icon: 'military_tech' },
            { id: 'lineups', label: 'Lineup Chemistry', icon: 'diversity_3' },
            { id: 'pairs', label: 'Pair Chemistry', icon: 'grid_on' },
            { id: 'onoff', label: 'On/Off', icon: 'swap_horiz' },
            { id: 'luck', label: 'Luck & Schedule', icon: 'casino' },
            { id: 'spacing', label: 'Spacing Lab', icon: 'open_with' },
            { id: 'contracts', label: 'Contract Value', icon: 'payments' },
            { id: 'replay', label: 'Game Replay', icon: 'movie' },
            { id: 'withwithout', label: 'With/Without a Star', icon: 'person_off' },
            { id: 'fatigue', label: 'Schedule Fatigue', icon: 'flight' },
            { id: 'referees', label: 'Referee Tendencies', icon: 'sports_score' },
        ],
    },
    {
        name: 'College & Draft',
        tabs: [
            { id: 'prospects', label: 'Draft Prospects', icon: 'school' },
            { id: 'pipeline', label: 'College → NBA', icon: 'route' },
            { id: 'madness', label: 'March Madness', icon: 'account_tree' },
        ],
    },
];

const ALL_TABS = TAB_GROUPS.flatMap((g) => g.tabs);

function groupNameForTab(tabId) {
    const group = TAB_GROUPS.find((g) => g.tabs.some((t) => t.id === tabId));
    return group ? group.name : TAB_GROUPS[0].name;
}

function tabFromHash() {
    const hash = window.location.hash.replace(/^#/, '');
    return ALL_TABS.some((t) => t.id === hash) ? hash : null;
}

export default function AnalyticsSection() {
    const [activeTab, setActiveTab] = useState(() => tabFromHash());
    const [activeGroup, setActiveGroup] = useState(() => {
        const tab = tabFromHash();
        return tab ? groupNameForTab(tab) : null;
    });

    // Keep the URL hash in sync so a specific tab can be linked/bookmarked
    // directly, without polluting browser back/forward history on every click.
    useEffect(() => {
        const newHash = activeTab ? `#${activeTab}` : '';
        if (window.location.hash !== newHash) {
            if (newHash) window.history.replaceState(null, '', newHash);
            else window.history.replaceState(null, '', window.location.pathname + window.location.search);
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

    function openTool(tabId) {
        setActiveTab(tabId);
        setActiveGroup(groupNameForTab(tabId));
    }

    function backToIndex() {
        setActiveTab(null);
        setActiveGroup(null);
    }

    if (!activeTab) {
        return (
            <div className="page page-analytics fade-in">
                <AnalyticsIndex groups={TAB_GROUPS} onSelect={openTool} />
            </div>
        );
    }

    const activeMeta = ALL_TABS.find((t) => t.id === activeTab);
    const about = TOOL_META[activeTab]?.about;

    return (
        <div className="page page-analytics fade-in">
            <button type="button" className="analytics-back-btn" onClick={backToIndex}>
                <Icon name="arrow_back" size="1em" /> Analytics
            </button>

            <h2 className="text-display analytics-tool-title">{activeMeta?.label}</h2>

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

            {about && (
                <AboutModelDrawer key={activeTab}>
                    <p>{about}</p>
                </AboutModelDrawer>
            )}

            {/* Tab content */}
            <div className="analytics-content fade-in">
                {activeTab === 'similarity' && <SimilaritySection />}
                {activeTab === 'mvp' && <AwardsRaceSection />}
                {activeTab === 'impact' && <ImpactSection />}
                {activeTab === 'validation' && <ModelValidationSection />}
                {activeTab === 'ledger' && <PredictionLedgerSection />}
                {activeTab === 'archetypes' && <PlayerArchetypesSection />}
                {activeTab === 'radar' && <RadarCompareSection />}
                {activeTab === 'trends' && <TrendAnalysisSection />}
                {activeTab === 'trajectory' && <TrajectoryForecasterSection />}
                {activeTab === 'vegas' && <VegasScannerSection />}
                {activeTab === 'playoffs' && <PlayoffForecasterSection />}
                {activeTab === 'prospects' && <DraftProspectSection />}
                {activeTab === 'helio' && <HeliocentricitySection />}
                {activeTab === 'wpa' && <ClutchWpaSection />}
                {activeTab === 'matchups' && <MatchupFinderSection />}
                {activeTab === 'lineups' && <LineupChemistrySection />}
                {activeTab === 'pairs' && <PairChemistrySection />}
                {activeTab === 'onoff' && <OnOffSection />}
                {activeTab === 'luck' && <LuckScheduleSection />}
                {activeTab === 'replay' && <GameReplaySection />}
                {activeTab === 'withwithout' && <WithWithoutStarSection />}
                {activeTab === 'fatigue' && <ScheduleFatigueSection />}
                {activeTab === 'referees' && <RefereeTendenciesSection />}
                {activeTab === 'garbage' && <GarbageTimeSection />}
                {activeTab === 'dad' && <DadIndexSection />}
                {activeTab === 'spacing' && <SpacingLabSection />}
                {activeTab === 'contracts' && <ContractValueSection />}
                {activeTab === 'pipeline' && <CollegePipelineSection />}
                {activeTab === 'madness' && <MarchMadnessSection />}
            </div>
        </div>
    );
}
