// Players picked by NBA id, not by name: 19 names belong to two players
// (e.g. two Brandon Williams). Results come from GET /workbench/entities
// (stored player_season_stats, accents ignored), latest career first.
import { searchWorkbenchPlayers } from '../services/api';

const fold = (s) => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
const label = (s) => `${s - 1}-${String(s).slice(-2)}`;

// "2021-22 to 2025-26", or one season.
export const playerSpan = (p) => (p.from === p.to ? label(p.from) : `${label(p.from)} to ${label(p.to)}`);

// One line per player for a suggestion list; unique even for two players of one name.
export const playerOption = (p) => `${p.name} · ${playerSpan(p)}`;

export async function searchPlayers(q) {
  const data = await searchWorkbenchPlayers(q);
  return data?.results ?? [];
}

// Every player whose name is exactly `name` (case and accents ignored), latest career first.
export async function namesakes(name) {
  const hits = await searchPlayers(name);
  return hits.filter((p) => fold(p.name) === fold(name));
}
