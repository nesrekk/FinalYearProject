# CLAUDE.md — session handoff for NBA Hub

Auto-loaded by every new Claude Code chat in this repo. **Keep it current:** at the end of any session that ships something or makes a real decision, update "Current state" and "Open items" below (and the README), in the same commit.

## Read first
- `README.md` is the full, authoritative handoff doc (features, schema, conventions, known gaps, roadmap with dated "Just shipped" entries). This file is the short version plus things learned the hard way.
- Owner's Obsidian vault (planning/navigation layer, **read-only for coding chats — never write there**): `/Users/kersenjonathan/Desktop/Second Brain/FinalYearProject/` — `Home.md`, `Roadmap.md`, `Known-Gaps.md`, `Session-Log.md`, `Ideas (unshipped)/`, `Landing Rehaul/Plan.md`. The owner maintains it in a separate chat. If the vault and README disagree, the README (and the code) win.

## Current state (updated 2026-09-26)
- All five features of the owner's advanced-analytics plan are shipped, verified and pushed: Garbage-Time Deflator, DAD Index, Exploit Guide (Scouting Report) v1+v2, Gravity Index & Spacing Lab, Contract Value.
- Smoke tests: **52/52** (`/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pytest api/tests`). Frontend lint clean (`cd frontend && npx eslint src` exits 0); keep it that way.
- **Layerbase cloud mirror shipped this session:** full parity, all 62 tables including `player_shots`/`pbp_events`, 0 row-count mismatches vs. local, ~1,968MB of the 5GB free tier. `DB_TARGET=layerbase` verified end-to-end (shot-zone and Game Replay endpoints returned real data). `layerbase` is a new `DB_TARGET` value alongside `local` (still default) and `cloud`. Sync script: `scripts/migrate_to_layerbase.py`. Supabase mirror (`DB_TARGET=cloud`, missing the two big tables) is left in place, not retired.
- **Landing page redesigned from scratch (owner's pick after 8 mockups):** B's loud-editorial style + C's cursor-reactive dithered ribbon (`DitherRibbon.jsx`, raw WebGL) + A's scroll-driven 3D court of real shots (`ShotCourtFlight.jsx`, three.js, lazy-loaded) + Lenis. Data from the new `GET /shots/league-sample` and existing endpoints; old landing components deleted (GamesHub still uses `ParticleField`/`CustomCursor`, CSS in `styles/effects.css`). Mockups are kept outside the repo (session scratchpad), not committed.
- Local Postgres (`nba_analytics`, ~2.15GB) is the source of truth.

## Open items
- **Learn the Game page (owner-requested, next):** new app page in the blueprint style of mockup F (court drawn like an engineering sheet, annotated with real zone FG% from `player_shots`) explaining basketball to beginners; add to top nav and link from the landing.
- **Suspicious BPM/VORP values (found, not investigated):** `player_season_stats` has SGA 2025-26 at BPM 22.0 / VORP 13.8, far above any realistic value; `scripts/build_bpm_vorp.py` may be off, and it feeds DAD Index and others. Check before trusting BPM anywhere.
- **B5 — Ask the Database:** parked on purpose (costs Anthropic API money; needs owner buy-in).
- Minor known issue: `/games/by-date` can still be slow for old historical dates (README Known real gaps).

## How the owner works
- Short messages ("next", "continue", "yea") = pick the next roadmap item and build it **end to end**: check the real data first → pipeline script → Postgres table → endpoint (+ `_source` badge) → `services/api.js` → UI → browser check → smoke test → README update → commit → push. Then report back and wait.
- Owner is a final-year student, hands-off on implementation, wants terse, action-oriented updates and honest disclosure. They asked for "no mistakes": verify claims against real data before writing them anywhere (README numbers, commit messages, UI copy).
- **Never** start money-costing items (B5) or sensitivity-flagged items without an explicit OK in *that* chat. Anything that writes to an external service (Supabase/Layerbase sync, GitHub PRs beyond normal pushes) needs the owner's OK first.
- Commits: one per feature, detailed message on what's real and what was verified; use the attribution line the current session's system instructions give. Grep changed files for secret values from `api/.env` before staging; `api/.env` is gitignored and must stay that way.

## Environment gotchas
- Always use `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3` (plain `python3` is an Anaconda without the deps).
- Whole app in one terminal: `./start.sh` (3 backends + frontend, Ctrl+C stops all; skips backend ports already in use). Or individually — backends: `cd api` then `uvicorn mvp_api:app --port 8000`, `similarity_api:app --port 8001`, `impact_api:app --port 8002` (always pass `--port`; add `--reload` for dev). Frontend: `preview_start` name `nba-frontend` — `.claude/launch.json` has `autoPort`, so if 5173 is taken (e.g. by another worktree session) it picks another port; read the port from the result.
- `DB_TARGET` in `api/.env`: `local` (default), `layerbase` (full-parity Postgres mirror, all tables), or `cloud` (Supabase, missing `player_shots`/`pbp_events`). An env var overrides it for one command, e.g. `DB_TARGET=layerbase python3 -c ...` — handy for read-only checks.
- Gitignored local-only data: `nba_data/kaggle_1947_present/` (Basketball-Reference-derived export) and `nba_data/salaries/` (third-party salary CSVs, no license — see README Known real gaps for where to get them). Contract Value's smoke test skips if the salary tables were never built.
- Season ints are **end year** (`2026` = 2025-26). Label: `f"{season - 1}-{str(season)[-2:]}"`.
- `player_shots` mixes regular season (`game_id` `002…`), playoffs (`004…`) and play-in (`005…`); `shot_zone_basic` is NULL on bulk-loaded rows, so zones come from `api/shots_lib.classify_zone()` (fixed to real court geometry 2026-09-26).
- `pbp_games` has nba_api/ESPN twin games — use `scripts/wpa_lib.PBP_DEDUP_WHERE` when reading play-by-play.
- No `statsmodels` here; clustered-SE regression lives in `scripts/stats_lib.py`.

## Verification pitfalls learned this session
- **Check pytest's own exit code** (`pytest ... > out.txt; echo $?`). Piping into `tail` hides failures, and a commit once went out that way.
- **A hidden browser pane freezes framer-motion page transitions** (`AnimatePresence mode="wait"` never finishes its exit), so navigation looks stuck and screenshots come back black. Check `tabs_context` for "pane hidden". To verify new components anyway, render them in a temporary Vite page (`frontend/zz-harness.html` + `src/zz-harness.jsx` importing the component and `styles/*.css`) against the live API, then **delete it** before committing.
- Initial page load in a 0-size viewport used to blank the app (fixed); set a viewport with `resize_window` before loading.
- The app shell's live stats.nba.com calls were once slow enough to stall every other request (fixed, PR #3). If an endpoint that's fast via curl hangs in the browser, check `read_network_requests` for a pile of pending app-shell calls first.
- Several source datasets had real errors found only by checking against known facts (stale season stats, duplicate games, inflation-adjusted salaries, stale salary rows). Keep doing sniff tests against real, well-known numbers before shipping.
