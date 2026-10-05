// Player suggestions that carry the NBA id (GET /workbench/entities via
// utils/playerChoice.js), for any box where a player is typed. Labels carry
// the career span ("Brandon Williams · 2021-22 to 2025-26"), so two players of
// one name stay apart in an AutocompleteDropdown; pick(label) gives back
// { id, name, from, to, team }.
import { useEffect, useState } from 'react';
import { playerOption, searchPlayers } from './playerChoice';

export default function usePlayerSuggestions(query, resolvedName, limit = 8) {
  const q = (query || '').trim();
  const [hits, setHits] = useState({ q: '', list: [] });
  const [dismissed, setDismissed] = useState(null);
  const off = q.length < 2 || q.toLowerCase() === (resolvedName || '').toLowerCase() || q === dismissed;

  useEffect(() => {
    if (off) return undefined;
    let live = true;
    const timer = setTimeout(() => {
      searchPlayers(q)
        .then((list) => { if (live) setHits({ q, list: list.slice(0, limit) }); })
        .catch(() => { if (live) setHits({ q, list: [] }); });
    }, 200);
    return () => { live = false; clearTimeout(timer); };
  }, [q, off, limit]);

  const list = !off && hits.q === q ? hits.list : [];
  const labels = list.map(playerOption);
  return {
    labels,
    pick: (label) => list[labels.indexOf(label)] ?? null,
    // Hide the list until the text changes (after a pick or Enter).
    dismiss: () => setDismissed(q),
  };
}
