import React, { useState } from 'react';
import Icon from '../common/Icon';
import ParticleField from '../landing/ParticleField';
import { BentoGrid, Tile } from '../ui/BentoGrid';
import GuessThePlayer from './GuessThePlayer';
import BlurredPlayer from './BlurredPlayer';
import HigherOrLower from './HigherOrLower';
import Trivia from './Trivia';
import GuessTheGame from './GuessTheGame';

const tabs = [
    { id: 'guess', label: 'Guess the Player', icon: 'sports_esports', tagline: 'One mystery player, one clue-filled guess at a time.' },
    { id: 'blurred', label: 'Blurred Player', icon: 'blur_on', tagline: 'A real headshot, sharpening with every wrong guess.' },
    { id: 'higherlower', label: 'Higher or Lower', icon: 'swap_vert', tagline: 'Chain correct guesses to build a streak.' },
    { id: 'trivia', label: 'Trivia', icon: 'quiz', tagline: 'Five real questions, real answers, daily.' },
    { id: 'guessgame', label: 'Guess the Game', icon: 'movie', tagline: 'Name a team from a real win-probability curve.' },
];

export default function GamesHub() {
    const [activeTab, setActiveTab] = useState(null);

    if (activeTab) {
        return (
            <div className="page page-games fade-in">
                <button type="button" className="analytics-back-btn" onClick={() => setActiveTab(null)}>
                    <Icon name="arrow_back" size="1em" /> Games
                </button>
                {activeTab === 'guess' && <GuessThePlayer />}
                {activeTab === 'blurred' && <BlurredPlayer />}
                {activeTab === 'higherlower' && <HigherOrLower />}
                {activeTab === 'trivia' && <Trivia />}
                {activeTab === 'guessgame' && <GuessTheGame />}
            </div>
        );
    }

    return (
        <div className="page page-games fade-in">
            <div className="games-hub-hero">
                <ParticleField density={0.5} />
                <div className="games-hub-hero-content">
                    <p className="text-eyebrow">Daily games &middot; real stats</p>
                    <h2 className="text-display games-hub-hero-title">Play today's puzzles.</h2>
                </div>
            </div>

            <BentoGrid className="games-hub-grid">
                {tabs.map((tab) => (
                    <Tile
                        key={tab.id}
                        span={4}
                        className="games-hub-tile"
                        onClick={() => setActiveTab(tab.id)}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setActiveTab(tab.id); }}
                    >
                        <div className="games-hub-tile-icon"><Icon name={tab.icon} /></div>
                        <h3 className="text-headline games-hub-tile-title">{tab.label}</h3>
                        <p className="games-hub-tile-tagline">{tab.tagline}</p>
                    </Tile>
                ))}
            </BentoGrid>
        </div>
    );
}
