import React from 'react';
import Icon from '../common/Icon';
import Section from '../ui/Section';
import { BentoGrid, Tile } from '../ui/BentoGrid';
import ToolPreview from './ToolPreview';
import { TOOL_META } from './toolMeta';

export default function AnalyticsIndex({ groups, onSelect }) {
    return (
        <div className="analytics-index">
            <p className="text-eyebrow analytics-index-eyebrow">Every tool below traces to a real source &mdash; nothing on this page is fabricated.</p>

            {groups.map((group) => (
                <Section key={group.name} className="dashboard-section" eyebrow={group.name}>
                    <BentoGrid>
                        {group.tabs.map((tab) => {
                            const meta = TOOL_META[tab.id] || {};
                            return (
                                <Tile
                                    key={tab.id}
                                    span={4}
                                    className="analytics-tile"
                                    onClick={() => onSelect(tab.id)}
                                    role="button"
                                    tabIndex={0}
                                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') onSelect(tab.id); }}
                                >
                                    <div className="analytics-tile-icon"><Icon name={tab.icon} /></div>
                                    <ToolPreview variant={meta.visual} />
                                    <h3 className="text-headline analytics-tile-title">{tab.label}</h3>
                                    {meta.tagline && <p className="analytics-tile-tagline">{meta.tagline}</p>}
                                </Tile>
                            );
                        })}
                    </BentoGrid>
                </Section>
            ))}
        </div>
    );
}
