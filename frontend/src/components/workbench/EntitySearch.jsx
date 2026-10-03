import React, { useEffect, useId, useState } from 'react';
import { fetchWorkbenchTeams, searchWorkbenchPlayers } from '../../services/api';
import { seasonLabel } from './workbenchShared';

const fold = (s) => s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();

// A search box that adds players (by name, from the server) or teams (every
// franchise, filtered here) to a set. A combobox: arrows move, Enter adds,
// Escape closes. The list sits in the block's flow (not a popup), so a
// block's scrolling body never clips it.
export default function EntitySearch({ kind, memberIds, onPick, disabled }) {
    const listId = useId();
    const [q, setQ] = useState('');
    const [found, setFound] = useState({ q: '', items: [], error: '' });
    const [teams, setTeams] = useState(null);
    const [teamsError, setTeamsError] = useState('');
    const [active, setActive] = useState(0);
    const [open, setOpen] = useState(false);

    useEffect(() => {
        const query = q.trim();
        if (kind !== 'player' || query.length < 2) return undefined;
        const t = setTimeout(() => {
            searchWorkbenchPlayers(query).then(
                (d) => setFound({ q, items: d.results || [], error: '' }),
                () => setFound({ q, items: [], error: 'Player search isn’t reachable (impact_api on port 8002).' }),
            );
        }, 220);
        return () => clearTimeout(t);
    }, [q, kind]);

    useEffect(() => {
        if (kind !== 'team') return;
        fetchWorkbenchTeams().then((d) => setTeams(d.results || []), () => setTeamsError('The team list isn’t reachable (impact_api on port 8002).'));
    }, [kind]);

    let items = [];
    let status = '';
    if (kind === 'player') {
        if (q.trim().length >= 2) {
            if (found.q === q) {
                items = found.items;
                status = found.error || (items.length ? '' : 'No player by that name.');
            } else {
                status = 'Searching…';
            }
        }
    } else if (teams) {
        const f = fold(q.trim());
        items = teams.filter((t) => !f || fold(t.name).includes(f) || t.id.toLowerCase().includes(f) || t.team.toLowerCase().includes(f));
        if (!items.length) status = 'No team by that name.';
    } else {
        status = teamsError || 'Loading teams…';
    }
    const shown = open ? items.slice(0, 25) : [];
    const at = Math.min(active, Math.max(0, shown.length - 1));

    const pick = (item) => {
        if (memberIds.has(item.id)) return;
        onPick(kind === 'player'
            ? { id: item.id, name: item.name, ...(item.team && /^[A-Z]{2,4}$/.test(item.team) ? { team: item.team } : {}) }
            : { id: item.id, name: item.name });
        setQ('');
        setActive(0);
    };

    const onKeyDown = (e) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setOpen(true);
            setActive((a) => Math.min(a + 1, Math.max(0, Math.min(items.length, 25) - 1)));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setActive((a) => Math.max(0, a - 1));
        } else if (e.key === 'Enter' && shown[at]) {
            e.preventDefault();
            pick(shown[at]);
        } else if (e.key === 'Escape') {
            setOpen(false);
        }
    };

    const placeholder = kind === 'player' ? 'Add a player: type a name' : 'Add a team: type a name or code';
    return (
        <div className="wb-search">
            <input
                type="search"
                className="wb-input"
                value={q}
                disabled={disabled}
                placeholder={placeholder}
                aria-label={placeholder}
                role="combobox"
                aria-expanded={shown.length > 0}
                aria-controls={listId}
                aria-autocomplete="list"
                aria-activedescendant={shown[at] ? `${listId}-${at}` : undefined}
                onChange={(e) => { setQ(e.target.value); setActive(0); setOpen(true); }}
                onFocus={() => setOpen(true)}
                onKeyDown={onKeyDown}
            />
            {open && status && (q.trim() || kind === 'team') && <p className="wb-search-status" role="status">{status}</p>}
            {shown.length > 0 && (q.trim() || kind === 'team') && (
                <ul className="wb-search-list" id={listId} role="listbox" aria-label={kind === 'player' ? 'Players' : 'Teams'}>
                    {shown.map((item, i) => {
                        const inSet = memberIds.has(item.id);
                        return (
                            <li
                                key={item.id}
                                id={`${listId}-${i}`}
                                role="option"
                                aria-selected={i === at}
                                aria-disabled={inSet}
                                className={`wb-search-item${i === at ? ' wb-search-item--active' : ''}${inSet ? ' wb-search-item--in' : ''}`}
                                onMouseDown={(e) => e.preventDefault()}
                                onClick={() => pick(item)}
                                onMouseEnter={() => setActive(i)}
                            >
                                <span className="wb-search-name">{item.name}</span>
                                <span className="wb-search-meta">
                                    {inSet ? 'in the set' : `${seasonLabel(item.from)} to ${seasonLabel(item.to)}${kind === 'player' ? (item.team ? ` · ${item.team}` : '') : ` · ${item.id}`}`}
                                </span>
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
}
