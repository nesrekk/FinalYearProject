# Workbench usability check (round 7, step 10)

A moderated test of the Workbench with 5–8 people, run on the owner's laptop. Each person does the
same five tasks (plus an optional sixth) while the moderator watches, and the app records what
happened. The results are counts and times, written up by a script, for the project report's
evaluation chapter.

- **Tasks, answers and checks:** `frontend/src/utils/studyTasks.json` (task set
  `workbench-2026-10-04`). They were fixed before any session: don't edit them once the first real
  session has run (make a new task set instead). `api/tests/test_usability_study.py` re-derives every
  answer from the database.
- **The recorder:** open any page with `?study=1`. A moderator panel appears bottom-right
  (`components/common/StudyPanel.jsx`, `utils/studyLog.js`).
- **The summary:** `scripts/usability_summary.py` reads the exported sessions and writes the tables.

## Who to ask

Five to eight people who have **not** seen the Workbench being built. A mix helps:

- 2–3 who follow the NBA and have used a stats site (Basketball-Reference, NBA.com stats);
- 2–3 casual fans;
- 1–2 who don't follow basketball but are comfortable with spreadsheets.

Run them one at a time, each on a fresh session.

## Before the sessions (once)

1. Start everything: `./start.sh` (the Workbench needs impact_api on 8002 and the frontend).
2. Use Chrome or Safari at a normal laptop width (1,280 px or more). Use the same theme for everyone
   (Paper, the default) unless you are testing Ink on purpose.
3. For the optional task 6, the computer must be online and `GEMINI_API_KEY` must be in `api/.env`.
   If not, untick the optional task when starting a session.
4. Do one **pilot** session yourself or with a friend (tick *Pilot run*): it is left out of the
   results. The developer's own pilot (P0, 2026-10-04) completed all six tasks; what it found is at
   the end of this file.

## Each session (about 30 minutes)

1. Open `http://localhost:5173/?page=workbench&study=1`. In the panel, type a participant code
   (`P1`, `P2`, … never a name) and press **Start session**. The participant now has an empty
   Workbench of their own; your own boards are hidden and untouched.
2. Read this to the participant (consent and think-aloud):

   > Thanks for helping. I'm testing the website, not you: if something is hard, that's the site's
   > fault and exactly what I need to find. Please think out loud: say what you're looking for and
   > what you expect to happen. I'll give you six short tasks; I can't help much while you work,
   > because I want to see where the site doesn't explain itself. You can stop at any time. The site
   > records what you click and how long each task takes, and I'll write down where you get stuck.
   > Nothing records your name, voice or screen; what you type into the boxes is saved, and in the
   > last task one sentence you type is sent to Google's Gemini to be read. Is that OK?

3. For each task: let them read the task in the panel, press **Start task**, and stay quiet.
   - When they say they're done, press **Participant is done**. If they give up, or after **5
     minutes**, press **Gave up**.
   - **Hints:** only the standard hint shown in the panel, and only when they have been stuck for
     about two minutes or ask twice. Press **Hint given** each time. A task finished after a hint is
     "Success after a hint".
   - **Stuck points:** whenever they hesitate, go the wrong way or ask a question, pick the closest
     tag under *Moderator* and press **Log it**, with a short note of what happened (no names). These
     tags are what the report counts.
   - Then: type their answer if the task asks a question (exactly as they said it), pick the
     outcome, and ask *"Overall, how difficult or easy was this task?"* (1 very difficult, 7 very
     easy). **Save and go on.**
4. At the end, the participant answers the ten SUS statements in the panel (1 strongly disagree to 5
   strongly agree). **Finish and export** downloads the session as
   `nba-hub-study-P3-<date>.json`.
5. Move that file (it lands in Downloads) to `docs/usability/sessions/` in the repo. The panel also lists every session kept
   in this browser, with Export, Resume (if a session was paused or the page reloaded) and Delete.

Rules for marking the outcome: **Success** = done without help and it matches the panel's *Counts as
success* line. **Success after a hint** = the same after one or more hints. **Failed** = gave up,
timed out, or said done with something that doesn't match. The script also checks every board on its
own and lists any task where it disagrees with the moderator.

## The tasks

| # | Task (as the participant reads it) | Counts as success |
|---|---|---|
| 1 | Make a table comparing Nikola Jokić and Joel Embiid: points, rebounds and assists per game, every season 2020-21 to 2025-26. *In which season did Jokić average the most assists?* | Table bound to a set with both, those stats per game by season over those seasons; answer 2025-26 (10.7) |
| 2 | Find every player who, in a season from 2022-23 on, averaged 20+ points on 60%+ true shooting (40+ games); put them on the board; chart usage against true shooting. | A set with the found players (42 players, 80 player-seasons) and a scatter of usage % vs TS% bound to it |
| 3 | In 2025-26, in how many games did Shai Gilgeous-Alexander score 40 or more? | Answer 8 (of his 68 games) |
| 4 | Add Stephen Curry's 2015-16 shot chart to the board. | A shot chart block showing Curry, 2015-16 |
| 5 | Get something you could send a friend so they can see your board. | A share link copied or the board exported as a file |
| 6 | *(optional)* Use the Finder's type-in box to find players who averaged 10+ rebounds and 10+ assists in a season, and run it. | The sentence filled both conditions and the search ran |

Task 2 says "player" where the round-7 plan's example said "guards": positions aren't in the
Workbench's catalogue, so a position filter can't be asked for yet.

## After the sessions

```bash
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 scripts/usability_summary.py docs/usability/sessions --out docs/usability/RESULTS.md
```

It needs the local database (it re-derives the answers and stops if the data no longer gives them).
It leaves pilot sessions out, refuses two files for one participant or files from another task set,
and writes: per task the completions, the moderator's outcomes, the board checks, the answers, the
median time, hints and ease; the stuck tags counted by participants; the errors and pauses the app
logged; the SUS score; and every disagreement between the moderator and the checks.

Then, in a new chat: "round 7 step 10b: fix the top problems from docs/usability/RESULTS.md". The top
problems are the stuck tags and failures that affected the most participants (failed > needed a
hint > slow). Fix the top three to five, rerun the tests, and put the counts (not opinions) in the
README's evaluation section. The session files contain no names; commit them with the results unless
a participant asks not to.

## The developer's pilot (P0, 2026-10-04; not user data)

All six tasks were done in the app (1,280 px, Paper) and every board check passed on the boards the
app saved. Found and fixed before any participant:

- **Grader:** a table with no first season shows the last five seasons, not all of them; the check
  first read it as "all seasons" and would have passed a table that starts in 2021-22.
- **Recorder:** an event detail named `type` overwrote the event's own type (an added Table was
  logged as "table", not "add_block"); detail fields can no longer replace `t` or `type`.
- **Type-in box:** it said its accuracy was measured "with an earlier version of the prompt" from the
  day after the evaluation, because the prompt names today's date and its hash changes daily. It now
  compares the prompt as it was on the evaluation day and the seasons "this season" refers to
  (`routers/workbench_parse.py` `_eval_current`, tested).

Seen in the pilot, left alone until participants show whether they matter:

- A new Table bound to a set shows the last five seasons; task 1 needs the first season changed.
- A chart bound to the found set draws every season of those 42 players in its range (196
  player-seasons), not only the 80 that matched the search.
- A board with a 42-player set is too big for a share link (2,104 characters); the page says so and
  points to Export file.
- A new Finder runs a default search (25+ points a game, any season) as soon as it's added.
- Choosing True shooting % for a new condition prefills a 200-attempt floor.
