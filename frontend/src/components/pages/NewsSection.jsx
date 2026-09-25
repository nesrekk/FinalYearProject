import React, { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { fetchCurrentNews } from '../../services/api';
import { localDateIso } from '../../utils/date';
import Icon from '../common/Icon';
import InfoTooltip from '../common/InfoTooltip';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';
import { TEAM_NAME_TO_ABBR } from '../../utils/teamAssets';

const TEAM_NAMES = Object.keys(TEAM_NAME_TO_ABBR).sort();

const GRID_VARIANTS = {
    hidden: {},
    show: (stagger) => ({ transition: { staggerChildren: stagger } }),
};
const CARD_VARIANTS = {
    hidden: { opacity: 0, y: 10 },
    show: { opacity: 1, y: 0 },
};

const categoryColors = {
    'Game Recap': '#38bdf8',
    'Player Watch': '#34d399',
    Injuries: '#f87171',
    'Trade Rumors': '#facc15',
    News: '#94a3b8',
};

const sourceSearchBase = {
    ESPN: 'https://www.espn.com/search/_/q/',
    'The Athletic': 'https://www.nytimes.com/search?query=',
    'NBA.com': 'https://www.nba.com/search?query=',
    'Bleacher Report': 'https://bleacherreport.com/search?query=',
    FiveThirtyEight: 'https://abcnews.go.com/search?searchtext=',
    'CBS Sports': 'https://www.cbssports.com/search/?q=',
    'The Ringer': 'https://www.theringer.com/search?q=',
    'Yahoo Sports': 'https://sports.yahoo.com/search?p=',
};

function sanitizeText(value) {
    if (!value) return '';
    return String(value)
        .replace(/<[^>]*>/g, ' ')
        .replace(/&nbsp;/gi, ' ')
        .replace(/&amp;/gi, '&')
        .replace(/&quot;/gi, '"')
        .replace(/&#39;/gi, "'")
        .replace(/\s+/g, ' ')
        .trim();
}

function getArticleUrl(article) {
    if (article.url) return article.url;
    const base = sourceSearchBase[article.source] || 'https://www.google.com/search?q=';
    return `${base}${encodeURIComponent(article.headline)}`;
}

const CATEGORIES = ['All', 'News', 'Game Recap', 'Player Watch', 'Injuries', 'Trade Rumors'];

export default function NewsSection() {
    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);
    const today = localDateIso();
    const [selectedDate, setSelectedDate] = useState(today);
    const [teamFilter, setTeamFilter] = useState('');
    const [liveNews, setLiveNews] = useState([]);
    const [loading, setLoading] = useState(false);
    const [visibleCount, setVisibleCount] = useState(6);
    const [category, setCategory] = useState('All');
    const [query, setQuery] = useState('');

    useEffect(() => {
        let active = true;
        async function loadNews() {
            setLoading(true);
            try {
                const data = await fetchCurrentNews(selectedDate, 40, teamFilter || null);
                if (active) {
                    const mapped = (data?.items || []).map((item, idx) => ({
                        id: `live-${idx}-${item.headline}`,
                        headline: sanitizeText(item.headline),
                        summary: sanitizeText(item.summary) || 'Open article for full details.',
                        source: item.source || 'Source',
                        date: item.published_at || selectedDate,
                        category: item.category || 'News',
                        url: item.url,
                    }));
                    setLiveNews(mapped);
                }
            } catch {
                if (active) setLiveNews([]);
            } finally {
                if (active) setLoading(false);
            }
        }
        loadNews();
        return () => {
            active = false;
        };
    }, [selectedDate, teamFilter]);

    const filteredNews = useMemo(() => {
        const q = query.trim().toLowerCase();
        return liveNews.filter((a) => {
            if (category !== 'All' && a.category !== category) return false;
            if (q && !a.headline.toLowerCase().includes(q) && !a.summary.toLowerCase().includes(q)) return false;
            return true;
        });
    }, [liveNews, category, query]);
    const sourceNews = filteredNews;
    const visibleArticles = useMemo(() => sourceNews.slice(0, visibleCount), [sourceNews, visibleCount]);

    return (
        <div className="page page-news fade-in">
            <div className="input-row" style={{ marginBottom: '0.75rem', flexWrap: 'wrap' }}>
                <input
                    type="date"
                    className="input-field"
                    value={selectedDate}
                    onChange={(e) => {
                        setSelectedDate(e.target.value);
                        setVisibleCount(6);
                    }}
                />
                <button className="action-btn" onClick={() => setSelectedDate(localDateIso())}>
                    Today
                </button>
                <select
                    className="input-field"
                    value={teamFilter}
                    onChange={(e) => { setTeamFilter(e.target.value); setVisibleCount(6); }}
                    style={{ maxWidth: 220 }}
                >
                    <option value="">All teams</option>
                    {TEAM_NAMES.map((name) => (
                        <option key={name} value={name}>{name}</option>
                    ))}
                </select>
                <input
                    type="text"
                    className="input-field"
                    placeholder="Search headlines…"
                    value={query}
                    onChange={(e) => { setQuery(e.target.value); setVisibleCount(6); }}
                    style={{ minWidth: 200, flex: 1 }}
                />
            </div>
            <div className="hb-rail-chips" style={{ marginBottom: '1rem', alignItems: 'center' }}>
                {CATEGORIES.map((c) => (
                    <button
                        key={c}
                        type="button"
                        className={`hb-rail-item ${category === c ? 'hb-rail-item--active' : ''}`}
                        onClick={() => { setCategory(c); setVisibleCount(6); }}
                        style={{ display: 'inline-flex', width: 'auto', textAlign: 'center' }}
                    >
                        {c}
                    </button>
                ))}
                <InfoTooltip label="How categories work" title="Real text classification, not an editorial category">
                    These RSS feeds don't carry a category field, so each headline/summary is matched against a
                    real keyword list (injury terms, trade terms, etc.) to sort it — a disclosed heuristic on the
                    real article text, not a source-provided category.
                </InfoTooltip>
            </div>
            {loading && <p className="page-subtitle" style={{ marginBottom: '0.75rem' }}>Refreshing current-day news...</p>}
            {!loading && liveNews.length === 0 && (
                <p className="empty-message">No live headlines found for this date yet. Try Today or another date.</p>
            )}
            {!loading && liveNews.length > 0 && filteredNews.length === 0 && (
                <p className="empty-message">No headlines match this filter. Try a different category or search.</p>
            )}
            <motion.div
                className="news-grid"
                key={`${selectedDate}-${isAdvanced}`}
                variants={GRID_VARIANTS}
                custom={preset.stagger}
                initial="hidden"
                animate="show"
            >
                {visibleArticles.map((article) => (
                    <motion.article key={article.id} className="news-card" variants={CARD_VARIANTS} transition={preset.fieldSpring}>
                        <a
                            href={getArticleUrl(article)}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ display: 'block', color: 'inherit', textDecoration: 'none' }}
                        >
                            <div className="news-card-image">
                                <span className="news-card-placeholder-icon"><Icon name="newspaper" /></span>
                            </div>
                            <div className="news-card-body">
                                <span
                                    className="news-category-tag"
                                    style={{ background: categoryColors[article.category] || '#38bdf8' }}
                                >
                                    {article.category}
                                </span>
                                <h3 className="news-headline">{article.headline}</h3>
                                <p className="news-summary">{article.summary}</p>
                                <div className="news-meta">
                                    <span>{article.source}</span>
                                    <span className="news-dot">·</span>
                                    <span>{article.date}</span>
                                    <span className="news-dot">·</span>
                                    <span>Open article</span>
                                </div>
                            </div>
                        </a>
                    </motion.article>
                ))}
            </motion.div>
            {visibleCount < sourceNews.length && (
                <div style={{ marginTop: '1rem' }}>
                    <button className="action-btn" onClick={() => setVisibleCount((c) => c + 6)}>
                        Load More News
                    </button>
                </div>
            )}
        </div>
    );
}
