// The Workbench's starter boards (round 7 step 8): ready-made boards anyone
// can open as a copy of their own and change. The definitions are data
// (starterBoards.json, checked against the database and the catalogue by
// api/tests/test_workbench_starters.py); a copy goes through cleanBoard() like
// any imported board, and every number on it is fetched live by its blocks.

import DATA from './starterBoards.json';
import { board } from './openInWorkbench';
import { cleanBoard } from './workbenchStore';

export const STARTERS = DATA.boards.map(({ key, icon, name, blurb, blocks }) => ({
    key, icon, name, blurb, blocks: blocks.length,
}));

// A fresh copy of one starter board (new ids, its own name), ready to save.
export function starterBoard(key) {
    const s = DATA.boards.find((b) => b.key === key);
    if (!s) throw new Error('That starter board no longer exists.');
    return cleanBoard(board(s.name, s.sets, s.blocks)).board;
}
