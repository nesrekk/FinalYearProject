# Reproducing NBA Hub and its paper

How to rebuild the database and the conference paper's inputs, what is pinned, what to expect to differ, and how a double-blind review artifact would work. Written for round 5 step 9 (2026-09-30). `docs/DATASHEET.md` covers the data itself: sources, terms, coverage, known errors, and what isn't redistributed.

## Environment

- **Python:** 3.14.3 (the python.org framework build; on the author's machine `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`).
  - `requirements.txt` pins the 17 packages the repository imports.
  - `requirements-lock.txt` pins all 74 packages installed in that interpreter (`pip freeze`).
  - `api/tests/test_reproducibility.py` checks that every third-party import is in `requirements.txt` and that the pins equal the installed versions.
- **PostgreSQL:** 18.1, database `nba_analytics`, credentials in `api/.env` (README "`.env` setup"). The content hashes use settings that print rows identically on any Postgres 12 or later.
- **Hardware used:** Apple M3, 8 GB RAM, macOS 26.6 (arm64) for everything through 2026-10-01; from 2026-10-02 an Apple M5, 16 GB RAM, macOS 27.0.1, same Python and package pins (see "macOS 27 and Apple Accelerate" below). Heavy fits run with `OMP_NUM_THREADS=4`; `rebuild_all.sh` sets it.
- **macOS extras:** `brew install libomp` (sportsdataverse imports xgboost). The frontend needs Node (22.20 used), with `npm ci` in `frontend/`.

### macOS 27 and Apple Accelerate (found 2026-10-02)

numpy 2.5.3 and scipy 1.18.1 use Apple Accelerate for linear algebra. On macOS 27.0.1 (Apple M5), `scipy.linalg.cho_factor`, which from scipy 1.18 runs through the batched C++ module `_batched_linalg._cholesky`, corrupted the process in the Rating Tracker's ~1,850 × 1,850 factorisations (`scripts/rating_tracker_lib.py`). Symptoms: `paper_eval.py --only impact` died with SIGBUS, SIGILL or SIGSEGV, or ran on with silently overwritten arrays (a three-season design's season column read as one value, a sparse row selection returned the wrong number of rows). It was not seen on macOS 26.6, where the same code and pins built the tracker and ran the protocol (round 6 step 7).

- **Proof:** a script running only the tracker step, under Guard Malloc (`DYLD_INSERT_LIBRARIES=/usr/lib/libgmalloc.dylib VECLIB_MAXIMUM_THREADS=1`; also with `MALLOC_PROTECT_BEFORE=1` and `MALLOC_STRICT_SIZE=1`), faulted in the first factorisation every time. The faulting thread jumped to a heap page inside Accelerate's `dpotrf$NEWLAPACK`, called from `_batched_linalg._cholesky`; Guard Malloc caught no heap write before it. scipy's classic f2py wrapper (`scipy.linalg.lapack.dpotrf`) calls the same Accelerate symbol and runs clean under all three guard modes and multithreaded, so the trigger is how the batched module calls the routine, not Accelerate on an ordinary call.
- **`VECLIB_MAXIMUM_THREADS=1` is not a fix:** with it the tracker step stopped crashing but the arrays were still overwritten (the failure moved later in the run). Accelerate ignores `OMP_NUM_THREADS`.
- **Fix:** `rating_tracker_lib.py` (the only module in `scripts/` and `api/` that uses `scipy.linalg`; everything else uses `numpy.linalg`) calls `dpotrf` / `dpotrs` / `dpotri` directly. Don't use `scipy.linalg.cho_factor` or `scipy.linalg.cholesky` in this repository on scipy 1.18 with macOS 27.
- **Results after the fix:** the tracker step gives bit-identical output (40,063 values) single-threaded, multithreaded and under Guard Malloc, and reproduces the old Mac's stored tune RMSE to 1.8e-15. Two full `paper_eval.py --only impact` runs give identical tables. Against the macOS 26.6 rows (read from the Layerbase mirror), the RAPM-family predictions differ by at most 4e-13 points and the metrics by at most 1.6e-14. Models that solve no linear system (BPM, on/off, zero) are bit-identical, and every hyperparameter choice is the same. `build_rating_tracker.py` rebuilt on the new Mac gives byte-identical `player_rating_tracker` and `rating_tracker_validation`; `rating_tracker_fit`'s optimiser lands within 3e-7 of the old hyperparameters (tune RMSE within 5e-14). `rebuild_all.sh paper-inputs` then changed no result number in `paper/numbers.tex`, and the figures and tables came out byte-identical.
- **What to expect:** last-digit differences in anything that solves a dense linear system, between the two macOS versions. Content hashes of such tables differ; the numbers the paper prints don't.

### Database locale: create it with `C` (found 2026-10-02)

Create the database with `createdb -T template0 -E UTF8 --locale=C nba_analytics` (Homebrew's `initdb` otherwise picks up the Mac's `en_US.UTF-8`). The locale changes two things. First, how text sorts: `ORDER BY` on a text column (names, game ids) gives a different order under `en_US.UTF-8`. Second, the manifest's content hash, which hashes each row's text form: under `en_US.UTF-8` Postgres quotes a lone value like `Šarić` (its UTF-8 bytes include 0xA0, which that locale's `isspace()` treats as a space), and under `C` it doesn't. The Layerbase mirror uses `C`, and the old Mac's database hashed `pbp_events` exactly as `C` does. A restore into an `en_US.UTF-8` database on 2026-10-02 hashed `pbp_events` differently: 59 rows in 5 NBA.com games of 2024-25 with players such as Šarić, every value identical. Recreated as `C`, every one of the 216 tables hashes the same as before except `pbp_events`, which again equals the old Mac's and Layerbase's hash, and no paper number moved.

## One script, five stages

`scripts/rebuild_all.sh` runs every pipeline in dependency order. With no arguments it prints the plan and runs nothing.

| command | runs | time on the author's machine |
|---|---|---|
| `scripts/rebuild_all.sh paper-inputs` | manifest → `paper/numbers.tex` → reference check (`paper_refs_check.py`, no database) → `paper/figures/*.pdf` → `paper/SHA256SUMS` (read-only on the database) | ~1 min |
| `scripts/rebuild_all.sh paper paper-inputs` | the paper's own tables (`paper_*`), then its inputs | ~45 min (beliefs ~22, ablations ~11, eval ~7) |
| `scripts/rebuild_all.sh derived` | every derived table: the app's and the paper's inputs | a few hours (not timed as one run) |
| `scripts/rebuild_all.sh load derived paper paper-inputs` | from the local raw files up | the above plus the loads (`player_shots` 6.3M rows) |
| `scripts/rebuild_all.sh fetch` | network fetches (ESPN, stats.nba.com, CollegeBasketballData.com) | hours; stats.nba.com times out from the author's machine since 2026-09-26 |

**Options:**
- `--dry-run` prints the steps without running them.
- `--from <script.py>` resumes at a step; a failing step prints this line for you.
- `--offline` skips steps that need the network inside the stages you named.

**Tags:**
- `net`: public ESPN or NBA CDN endpoints.
- `nbaapi`: stats.nba.com.
- `key`: needs an API key.
- `kaggle`, `shotcsv`, `salaries`, `torvik`: need a gitignored input folder. The run stops before a step whose input is missing.

**Checking the order:** `api/tests/test_reproducibility.py` checks it against the data. No step may read a derived or paper table whose producer runs later, counting reads in the script and in every local module it imports. A deliberately swapped pair is caught.

**Side effects of the derived stage:**
- It rewrites the app's tables.
- It rewrites the committed model files (`models/*.pkl`, `scripts/*.pkl`); `git status` shows them afterwards.
- Restart the three API services afterwards, because most routers cache their tables per process.

**What was run for this step:**
- `paper-inputs` was run end to end: 60 s. The figures came out byte-identical, and `numbers.tex` changed only by the new manifest macros.
- The other stages were checked by `--dry-run` and by the order test, not re-run: a full rebuild rewrites every app table.
- Each `paper_*` script's own reproducibility was checked when it shipped (two runs identical; README "Paper …" sections).

## The manifest: checking that a rebuild holds the same data

`scripts/paper_manifest.py` (~40 s, read-only) writes `paper/manifest.json` and `paper/manifest.tsv`. For each of the 167 tables it records:
- the kind (source, derived, paper, cache, legacy or ledger) and the script that writes it;
- the row count and column count;
- a schema md5;
- an order-independent content md5: each row's md5 on the server, the two 64-bit halves summed as exact numerics;
- the table's timestamp columns.

The database digest is a sha256 over all tables. The paper prints its first 16 hex digits (`\pnManDigest`, `b9f5cf00c72f80ff` on 2026-09-30). `paper_numbers.py` stops if the manifest no longer matches the database's table set, schemas and row counts.

`paper_manifest.py --files` writes `paper/SHA256SUMS` for the generated inputs: `numbers.tex`, `tables/*.tex`, `figures/*.pdf` and the manifest. Check them with `cd paper && shasum -a 256 -c SHA256SUMS`.

**What to expect after a rebuild:**
- **Tables with a build timestamp:** their hash changes even when the data don't. The manifest lists every table's `time_columns`: `award_chance_calibration`, `model_backtest_*`, `all_nba_backtest_summary`, `shot_making_validation`, `wpa_model_validation`, `pair_synergy_validation`, `player_id_map`, `draft_history`, `defense_tracking_stats`, plus the fetch logs and caches.
- **Fit timings:** some tables store how long a fit took (`shot_making_validation.notes.fit_seconds`).
- **Feeds that change:** a re-fetch of a live feed returns what the feed says that day, which need not be what it said when this database was built.
- **Float sums in SQL:** in a few tables, SQL float sums may differ in the last digit with row order (`pair_seasons` and `lineup_seasons` are known to). The data audit sums as exact numerics for this reason.

**What must be equal:**
- every `paper_*` table, whenever its inputs are equal. When each script shipped:
  - `paper_tests.py`: two full runs byte-identical.
  - `paper_ablations.py`: two full runs content-hash identical, and it reproduces `paper_eval.py`'s stored full-model predictions exactly (max difference 0) before scoring.
  - `paper_data_audit.py`: two runs identical.
  - `paper_beliefs.py`: two full runs printed identical aggregates.
- `numbers.tex` and the figures, byte for byte.

## Seeds

Every random draw in the pipeline is seeded; nothing reads the clock or an unseeded generator.

| where | seed |
|---|---|
| `paper_tests.py`, `paper_ablations.py` (bootstrap, sign-flip) | per result row: md5 of the row's key and `SEED = 20260929` (`paper_tests.row_seed`) |
| `paper_beliefs.py` (permutations) | per family: md5 of its key and `SEED = 20260929` |
| `paper_eval.py` (shot-model subsample and fits) | `build_shot_making.SEED = 0` |
| season simulator (`build_season_sim.py`, `paper_eval.py`, `paper_ablations.py`) | `season_sim_lib.sim_seed(season, date, method)` = first 8 hex digits of md5("season\|date\|method") |
| `build_rapm.py` (bootstrap), `build_rim_deterrence.py` | `SEED = 20260929` |
| `build_player_on_off.py` (bootstrap) | `SEED = 20260928` |
| `build_hot_streak_persistence.py`, `build_situational_splits.py` | `default_rng(20260928)` |
| `build_shot_making.py` (HistGradientBoosting, cross-fit folds) | `SEED = 0` |
| `build_stat_stability.py`, `build_aging_curves.py`, `build_ncaa_model.py` | `default_rng(2026)` |
| `build_college_pipeline.py`, `build_player_roles.py` (and its KMeans `random_state=42`) | `default_rng(0)` |
| award models, clustering, win model, WPA model, SHAP | scikit-learn `random_state=42` |
| RAPM and xRAPM cross-validation folds | `build_rapm.game_fold()`: md5 of the game id (no generator) |

**Row order.** A fit whose result depends on row order reads its input in a stated order:
- `build_shot_making.py` reads `player_shots` `ORDER BY id`;
- `build_hot_streak_persistence.py` sorts by `game_id` last.

The README says so where each was fixed.

## A review artifact, if the venue is double-blind

The venue isn't chosen yet; `paper/ROUND5_PLAN.md` has the blank. If reviewing is double-blind, the plan is below. Step 11 carries it out; nothing here has been uploaded.

1. **Build from an allowlist, not the repository.** The repository and its history name the author:
   - commit author and e-mail;
   - absolute home-directory paths in `CLAUDE.md` and `walkthrough.md`;
   - `Mini Project Report-2.docx`, `journals.rtf`, `progress.txt` and screenshots in the root.

   So the artifact is a fresh folder with `scripts/`, `api/` (without `.env`), `frontend/` (without `node_modules`, `dist`), `docs/`, `requirements*.txt`, a README cut down to running and reproducing, and the paper's generated inputs (`paper/numbers.tex`, `tables/`, `figures/`, `manifest.json`, `manifest.tsv`, `SHA256SUMS`). There's no git history: a `git archive` of the allowlisted paths, then a grep of the result for the author's name, e-mail, user name and institution, which must come back empty.
2. **Data the artifact may carry.**
   - It must not carry the gitignored third-party files.
   - Nothing that restricts the paper's own results is known: per-unit predictions, intervals and belief tests, `paper_*` tables, ~0.5 GB in the database. A compressed dump of them would let a reviewer rerun `paper_tests.py`, `paper_numbers.py` and `paper_figures.py` without any raw feed; its size hasn't been measured.
   - Whether the committed stats.nba.com season CSVs go in is the owner's call (see the datasheet).
3. **Hosting.** Use an anonymising host the venue accepts, with a link in the paper's availability section in place of the repository URL. The options, to check against the venue's instructions:
   - a service that mirrors a GitHub repository with identifying strings removed;
   - an anonymous view-only project link on a research-data host;
   - the venue's own supplementary-material upload.
4. **After acceptance.** Swap the link for the public repository and add an archived snapshot with a DOI if the venue asks for one.
