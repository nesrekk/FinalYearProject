import React, { useState } from 'react';
import Icon from '../common/Icon';
import GuessThePlayer from './GuessThePlayer';
import BlurredPlayer from './BlurredPlayer';
import HigherOrLower from './HigherOrLower';
import Trivia from './Trivia';

const tabs = [
    { id: 'guess', label: 'Guess the Player', icon: 'sports_esports' },
    { id: 'blurred', label: 'Blurred Player', icon: 'blur_on' },
    { id: 'higherlower', label: 'Higher or Lower', icon: 'swap_vert' },
    { id: 'trivia', label: 'Trivia', icon: 'quiz' },
];

export default function GamesHub() {
    const [activeTab, setActiveTab] = useState('guess');

    return (
        <div className="page page-games fade-in">
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

            <div className="analytics-content fade-in">
                {activeTab === 'guess' && <GuessThePlayer />}
                {activeTab === 'blurred' && <BlurredPlayer />}
                {activeTab === 'higherlower' && <HigherOrLower />}
                {activeTab === 'trivia' && <Trivia />}
            </div>
        </div>
    );
}
