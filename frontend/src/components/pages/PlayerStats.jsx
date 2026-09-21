import React, { useEffect, useMemo, useState } from 'react';
import { mockPlayers } from '../../services/mockData';
import { fetchLivePlayerSuggestions, fetchPlayerImage, fetchPlayerProfile } from '../../services/api';

export default function PlayerStats() {
    const [search, setSearch] = useState('');
    const [selected, setSelected] = useState(null);
    const [selectedImage, setSelectedImage] = useState(null);
    const [dbNames, setDbNames] = useState([]);
    const [searchingDb, setSearchingDb] = useState(false);
    const [loadingProfile, setLoadingProfile] = useState(false);

    const filtered = mockPlayers.filter((p) =>
        p.name.toLowerCase().includes(search.toLowerCase())
    );

    useEffect(() => {
        const query = search.trim();
        if (query.length < 2) return;

        let active = true;
        const timer = setTimeout(async () => {
            setSearchingDb(true);
            try {
                const data = await fetchLivePlayerSuggestions(query, 30);
                if (active) setDbNames(data?.results ?? []);
            } catch {
                if (active) setDbNames([]);
            } finally {
                if (active) setSearchingDb(false);
            }
        }, 180);

        return () => {
            active = false;
            clearTimeout(timer);
        };
    }, [search]);

    const displayPlayers = useMemo(() => {
        const query = search.trim();
        if (query.length < 2) return filtered.map((p) => ({ name: p.name, mock: p }));

        return dbNames.map((name) => {
            const mock = mockPlayers.find((p) => p.name.toLowerCase() === name.toLowerCase());
            return { name, mock: mock || null };
        });
    }, [search, filtered, dbNames]);

    function toProfileFromMock(player) {
        return {
            ...player,
            season: null,
        };
    }

    async function handleSelectPlayer(entry) {
        setLoadingProfile(true);
        try {
            const data = await fetchPlayerProfile(entry.name);
            setSelected({
                name: data.player_name,
                team: data.team_abbr || entry.mock?.team || 'Unknown Team',
                position: entry.mock?.position || 'N/A',
                number: entry.mock?.number ?? 'DB',
                season: data.season,
                stats: data.stats,
            });
        } catch {
            if (entry.mock) {
                setSelected(toProfileFromMock(entry.mock));
            }
        } finally {
            setLoadingProfile(false);
        }
    }

    useEffect(() => {
        let active = true;
        async function loadImage() {
            if (!selected?.name) {
                setSelectedImage(null);
                return;
            }
            try {
                const data = await fetchPlayerImage(selected.name);
                if (active) setSelectedImage(data?.image_url || null);
            } catch {
                if (active) setSelectedImage(null);
            }
        }
        loadImage();
        return () => {
            active = false;
        };
    }, [selected]);

    return (
        <div className="page page-players fade-in">
            {/* Search */}
            <div className="player-search-bar">
                <input
                    type="text"
                    className="input-field player-search-input"
                    placeholder="Search players (e.g. LeBron, Jokic)…"
                    value={search}
                    onChange={(e) => {
                        setSearch(e.target.value);
                        setSelected(null);
                    }}
                />
            </div>

            {/* Player List */}
            {!selected && (
                <div className="player-grid">
                    {displayPlayers.map((entry) => (
                        <button
                            key={entry.name}
                            className="player-card"
                            onClick={() => handleSelectPlayer(entry)}
                        >
                            <div className="player-avatar-circle" style={{ background: '#38bdf8' }}>
                                <span className="player-avatar-number">
                                    {entry.mock ? `#${entry.mock.number}` : 'DB'}
                                </span>
                            </div>
                            <div className="player-card-info">
                                <p className="player-card-name">{entry.name}</p>
                                {entry.mock ? (
                                    <>
                                        <p className="player-card-meta">{entry.mock.team} · {entry.mock.position}</p>
                                        <p className="player-card-stats">
                                            {entry.mock.stats.ppg} PPG · {entry.mock.stats.rpg} RPG · {entry.mock.stats.apg} APG
                                        </p>
                                    </>
                                ) : (
                                    <>
                                        <p className="player-card-meta">Found in project database</p>
                                        <p className="player-card-stats">Click to open player profile stats</p>
                                    </>
                                )}
                            </div>
                        </button>
                    ))}
                    {loadingProfile && (
                        <p className="empty-message">Loading player profile...</p>
                    )}
                    {searchingDb && search.trim().length >= 2 && (
                        <p className="empty-message">Searching live player data...</p>
                    )}
                    {!searchingDb && displayPlayers.length === 0 && (
                        <p className="empty-message">No players found matching "{search}"</p>
                    )}
                </div>
            )}

            {/* Player Detail */}
            {selected && (
                <div className="player-detail fade-in">
                    <button className="back-btn" onClick={() => setSelected(null)}>← Back to list</button>

                    <div className="player-profile-card">
                        <div className="player-profile-header">
                            {selectedImage ? (
                                <img
                                    src={selectedImage}
                                    alt={selected.name}
                                    className="player-avatar-large"
                                    style={{ objectFit: 'cover' }}
                                />
                            ) : (
                                <div className="player-avatar-large" style={{ background: '#38bdf8' }}>
                                    <span className="player-avatar-lg-number">#{selected.number}</span>
                                </div>
                            )}
                            <div className="player-profile-info">
                                <h2 className="player-profile-name">{selected.name}</h2>
                                <p className="player-profile-meta">
                                    {selected.team} · {selected.position}
                                    {selected.season ? ` · Season ${selected.season}` : ''}
                                </p>
                            </div>
                        </div>

                        <div className="player-stats-grid">
                            {[
                                { label: 'PPG', value: selected.stats.ppg },
                                { label: 'RPG', value: selected.stats.rpg },
                                { label: 'APG', value: selected.stats.apg },
                                { label: 'SPG', value: selected.stats.spg },
                                { label: 'BPG', value: selected.stats.bpg },
                                { label: 'FG%', value: selected.stats.fgPct },
                                { label: '3PT%', value: selected.stats.threePct },
                                { label: 'FT%', value: selected.stats.ftPct },
                            ].map((s) => (
                                <div key={s.label} className="player-stat-box">
                                    <span className="player-stat-val">{s.value}</span>
                                    <span className="player-stat-label">{s.label}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
