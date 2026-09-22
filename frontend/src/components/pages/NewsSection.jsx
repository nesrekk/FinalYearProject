import React, { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { fetchCurrentNews } from '../../services/api';
import { localDateIso } from '../../utils/date';
import Icon from '../common/Icon';
import { useMotionMode, motionPreset } from '../../context/MotionModeContext';

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
    Analysis: '#a78bfa',
    'Player Watch': '#34d399',
    Injuries: '#f87171',
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

export default function NewsSection() {
    const { isAdvanced } = useMotionMode();
    const preset = motionPreset(isAdvanced);
    const today = localDateIso();
    const [selectedDate, setSelectedDate] = useState(today);
    const [liveNews, setLiveNews] = useState([]);
    const [loading, setLoading] = useState(false);
    const [visibleCount, setVisibleCount] = useState(6);
    const sourceNews = liveNews;
    const visibleArticles = useMemo(() => sourceNews.slice(0, visibleCount), [sourceNews, visibleCount]);

    useEffect(() => {
        let active = true;
        async function loadNews() {
            setLoading(true);
            try {
                const data = await fetchCurrentNews(selectedDate, 30);
                if (active) {
                    const mapped = (data?.items || []).map((item, idx) => ({
                        id: `live-${idx}-${item.headline}`,
                        headline: sanitizeText(item.headline),
                        summary: sanitizeText(item.summary) || 'Open article for full details.',
                        source: item.source || 'Source',
                        date: item.published_at || selectedDate,
                        category: 'News',
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
    }, [selectedDate]);

    return (
        <div className="page page-news fade-in">
            <div className="input-row" style={{ marginBottom: '1rem' }}>
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
            </div>
            {loading && <p className="page-subtitle" style={{ marginBottom: '0.75rem' }}>Refreshing current-day news...</p>}
            {!loading && sourceNews.length === 0 && (
                <p className="empty-message">No live headlines found for this date yet. Try Today or another date.</p>
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
