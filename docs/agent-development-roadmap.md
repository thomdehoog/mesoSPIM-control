# Agent development roadmap: mesoSPIM AI Assistant

Version 2.0, 9 October 2026. Applies to the AI Assistant in mesoSPIM-control 1.27, after upstream
`release/candidate-py312` b152c91 (PR 120, phases A to E; PR 121, Claude Haiku 5.5).

Code: `mesoSPIM/src/ai_assistant/` and `mesoSPIM/src/remote_control/`. Work happens on a fork branch
made from `release/candidate-py312`, one branch per phase (`agent/<phase>`); each phase goes to
Nikita as one pull request with the src changes only. Development tools (evaluation, simulator,
recorded runs) stay in the fork.

## Goal

The assistant is a full remote control of a mesoSPIM for a trained operator, on a cloud model:
everything the operator can do in the window, the model can do by tool, and the same tools serve
TCP and MCP clients. Procedures (centring, exposure, focusing, tiling, alignment, ETL tuning) are
written as skills the model loads when a request calls for one. Nothing in the code exists to fit
a small model.

## Principles

- **Simple building blocks, composed by the model.** No coded procedures. The model combines look,
  move, set and the GUI commands; a skill says in which order.
- **Code measures, the model judges.** Distances, focus, brightness and drift are computed in code;
  the vision model adds what numbers cannot.
- **Measure before and after.** Every change is judged against the test set, on
  gemini-3.5-flash-lite and claude-haiku-5-5.
- **Safety is Core's.** The stage limits, the busy gate and Stop microscope are Core's and stay. The
  assistant asks only when a request leaves something open; it does not ask for confirmation.
- **Contained.** Code in `mesoSPIM_Core.py`, `mesoSPIM_MainWindow.py` and the other upstream modules
  is the fewest lines that let the packages do their work. Every item below states its count, and
  every pull request description gives `git diff --stat` on those files.
- **Stay small.** Every tool, result field and prompt line is paid for on every turn.

## Delivered in 1.x, kept

- Phases A to E (PR 120): one clock, results that read back, the simulator, the multi-step case
  matrix, the frame history, `look` over frames, the map, `calibrate`.
- PR 121: Claude Haiku 5.5 runs (no temperature 0, argument names it accepts, thinking blocks
  handled). Benchmark: `docs/test-reports/2026-10-09-haiku-5-5-benchmark.md`.
- The Windows demo test and its evidence: `docs/test-reports/windows-demo-agent-e.md`.
- Evidence for phase 5 (was H): focusing and the long tasks fail on three models.

## What the code review found

Facts from the code at b3b0b48 (the dev branch, src identical to upstream b152c91). Each is used
by one item below.

1. `Core.sig_update_gui_from_state` refreshes the window from state (`mesoSPIM_MainWindow.py:808`).
   `set_filter`, `set_shutterconfig`, `stop`, the end of a list, a preview and the ETL table reload
   emit it; `set_intensity` and everything that goes through `Core.state_request_handler` (`set_state`,
   `set_camera`, `set_etl`, `set_galvo`, `set_laser_timing`) do not. (1)
2. The window's state-to-widget table (`mesoSPIM_MainWindow.py:580-617`) has no entry for the
   binning and the two subsampling boxes, the "scale galvo with zoom" box, the ETL file and snap
   folder indicators, the time-lapse tab or the time point progress bar. `set_camera` changes the
   first three. (1)
3. Remote commands run on Core's thread, not the GUI thread. The one GUI-side path is the list
   install: `set_acquisition_list` emits a signal of `RemoteControlGUI`, queued to the window, which
   calls the acquisition manager's `model.setTable` (`remote_control/gui.py:177`). The tiling wizard
   installs its list the same way (`utils/multicolor_acquisition_wizard.py:102`). (1, 3)
4. The tiling wizard's list is built by `MulticolorTilingAcquisitionListBuilder`
   (`utils/multicolor_acquisition_builder.py:17`) from a dict the pages fill: bounding box, z step,
   zoom, shutter, overlap or offsets, per channel laser, intensity, filter, ETL, f start and end,
   folder. The builder has no GUI in it. The filename, focus-tracking and image-processing wizards
   write columns of the model with `setData`. (3)
5. The guard, the requests and the scheduler are one knot: the guard tells typed from machine turns
   through the requests, `wait` learns what it waits for through the guard, schedules fire as
   machine turns of a request, and the scheduler is the assistant's clock for the store, the eyes
   and the requests. Together about 900 lines of `assistant.py`, `requests.py`, `measured.py` and
   `gui.py`, 60 tests and the harness's gate, continuation and schedule code. (2)
6. The small-model scaffolding, with the reason each gives in its own comment: history compaction
   and trimming ("twenty readouts would outweigh the system prompt on a small model"), the
   "called nothing" challenge, the Regular tool set, the memory tools that exist because the
   history is cut, the Local AI server mode. About 700 lines of src and 70 tests. The read-back,
   `state_changed`, the result shortening, rows by reference, the failure advice and the eyes'
   image detachment are cost or correctness controls for any model and stay. (2)
7. Core's time lapse (`mesoSPIM_Core.py:1532-1580`) repeats the installed list every interval,
   each row at its own position, with a `_TimeNNN` filename suffix; it refuses settings and moves
   between points. `time_lapse_start` offers it over all three clients. (2)
8. The manual is 9,420 characters; about a quarter is procedure (how to judge exposure, look after
   a change, the pre-run summary, centring and focusing notes). Nothing covers focusing order,
   tiling, light-sheet alignment or ETL tuning. Remote Control's `get_manual` carries four recipes
   for TCP and MCP clients. (4)
9. A file next to the microscope config has a precedent: `processor_chain.json`, found by
   `os.path.dirname(cfg.__file__)` (`mesoSPIM_MainWindow.py:1129`). (4)
10. The simulator scores position, focus and brightness only; nothing of the ETL or the light
    sheet. Skill cases for those can check calls and their order, not outcomes. (4)
11. Haiku 5.5 refuses a request when any message before a signed thinking block has changed. With
    an append-only history the case cannot arise; fix 4 of PR 121 goes with the compaction. (2)
12. Flash-lite answers "wait 30 seconds" with `wait` 8 times in 11 and with `schedule` 3 times
    (evidence 08). Two tools for one thing. (2)
13. `Core.state_request_handler` lists `laser_l_max_amplitude` without the `_%` the window sends
    (`mesoSPIM_Core.py:368`, `mesoSPIM_MainWindow.py:597`), so that box never reaches Core. Not the
    assistant's; reported to Nikita.

## Phase 1: the window follows every remote change

Branch `agent/refresh`. Core: 1 line. Main window: 4 lines. Shared layer: about 20 lines.

- [ ] **1.1. A refresh after every setter.** `Core.state_request_handler` emits
  `sig_update_gui_from_state` once it has sent the request on (1 line). `set_intensity` gets the
  same (or goes through the handler). The named setters that already emit it are left alone. The
  emit is queued to the window; a value the worker has not written yet is caught by the next
  refresh, so the shared layer's setters also emit it after their read-back (`read_back`,
  `assistant.py:109`), which is when the state is known to hold the value. Shared layer, no Core.
- [ ] **1.2. The widgets that are never refreshed.** The binning, the two subsampling boxes and the
  "scale galvo with zoom" box join the state-to-widget table (4 lines). The ETL file and snap folder
  indicators, the time-lapse tab and the progress bar are listed for Nikita with the one-line fix
  each; they are his widgets and not what the assistant changes.
- [ ] **1.3. Checked, not assumed.** A test per setter in `test_combobox_state_requests.py` (runs on
  Windows), and the Windows demo driver (`docs/test-reports/evidence/tools/drive_gui.py`) with a
  step that sets each value by tool and reads each widget.

## Phase 2: lean, for cloud models

Branch `agent/lean`. Core: 0 lines. Main window: 0 lines. One pull request; the recorded cases
are re-recorded once at its end, on flash-lite and on Haiku.

- [ ] **2.1. No confirmation and no guard.** Out: `ConfirmationGate`, `TurnGuard` and its number
  helpers, `CONFIRM_FIRST`, the guard's config block, the light budget, the memory of refusals,
  `measured.py` and `MEASURED_*`, the confirm bar, calibrate's question, look's guard hooks. The
  manual keeps one rule: when a request leaves a value or a choice open, ask; otherwise act and
  say what was chosen. Out of the cases: the five confirmation cases and their twins, the 18
  unattended cases, the `answer`, `confirm` and `measured` keys of the harness. The 14 "asks"
  cases stay: asking on a vague request is the model's job, not a gate's.
- [ ] **2.2. No small-model scaffolding.** Out: `compact_history`, `trim_history`, the thinking
  helpers and `ProcessHistory`; `MAX_HISTORY_TURNS` and the Memory box; the "called nothing"
  challenge and its memory cleanup; the Regular tool set, `REGULAR_ARGS`, `_narrowed`, `_only_keys`,
  the "# Tool set" prompt section and the Tools box (one tool set: all 56 commands); `recall_turn`
  and `search_history`; the Local AI mode (`local.py`, its widgets, the models folder, the
  `ai-assistant-local` extra, `run.py --local`). Kept: the OpenAI-style preset with a base URL (the
  door to a local server later, at no cost now), `CONTEXT_TOO_SMALL_HELP` for it, the empty-reply
  check (Gemini answered "_"), result shortening, rows by reference, the failure advice, the eyes'
  image detachment and the bin box. The history is append-only until Clear context: about 12,000
  tokens a request plus one to three thousand per turn, which the cloud models carry.
- [ ] **2.3. No timer.** Out: the scheduler and its two tools, `wait`, `requests.py`, the
  continuation, the schedule rows and the tick, the request line and its Cancel, the scheduler
  smoke test. The clock stays one injectable callable on the worker (A2), without the scheduler
  around it; the harness passes the simulator's. Time courses are Core's: `time_lapse_start` runs
  the installed list every interval. A long run returns "still running" after the tool's cap
  (`WAIT_CAP_S`); the operator comes back to the tab. Out of the cases: the "time" category, the
  time-lapse-by-schedule group, `run_for_s`, `schedules` and the continuation code of the harness.
  Open for Nikita: a hook so that a time lapse can refocus or re-centre between points (fact 7);
  until then "keep it in focus" is not promised.
- [ ] **2.4. The plan, for the model only.** Out: the plan parsing and the request line's plan. In
  the manual, one rule: if the request needs more than one tool call, or the next step depends on
  what a look shows, write a short numbered plan to yourself before the first call and tick it as
  you go; otherwise act. The single-step cases get a check that the reply carries no checklist.
- [ ] **2.5. The docs follow.** `index.md`, `architecture.md`, `tool-sets.md` (retired) and the
  CHANGELOG lose the removed features; the manual shrinks to facts (target 6,000 characters).
- [ ] **2.6. Re-record.** All case files on flash-lite and once on Haiku; the numbers go into the
  pull request. Expected: the single-step sets unchanged but for the removed cases, the vague
  requests unchanged, the multi-step groups at or above today's.

Size: about 1,600 of the 4,305 src lines and 150 of the 230 tests go. What a cloud model loses:
nothing it used; what the operator loses: the Run/Cancel question and the schedule rows.

## Phase 3: the window's features as commands

Branch `agent/gui`. Core: 0 lines. Main window: 0 lines. Shared layer only; the GUI-side handlers
hang on `RemoteControlGUI`, which already holds the window (fact 3), so every new command serves
the tab, TCP and MCP alike.

- [ ] **3.1. `build_tiling_list`.** The tiling wizard's dict as arguments (bounding box, z step,
  zoom, shutter, overlap or offsets, per channel laser, intensity, filter, ETL, f start and end,
  folder, filename pattern); the list comes from the wizard's own builder (fact 4) and is
  installed through the existing list install. The wizard's GUI is untouched. Returns the tile
  counts and the first rows, so the model can report before the run.
- [ ] **3.2. Table operations.** `save_acquisition_list` and `load_acquisition_list` (the manager's
  CSV read and write, called on the GUI thread through the install signal's sibling), `mark_rows`
  (the "mark current" actions: xy, focus, rotation, state, ETL, all, for chosen rows, computed in
  the shared layer from state), and `set_rows_folder`. `update_acquisition_row` already edits a row.
- [ ] **3.3. The other wizards as functions.** Filename pattern (the filename wizard's rule) and
  focus tracking (f per row from two reference points) as arguments of `build_tiling_list` and as
  row updates; both are small pure functions once lifted from the wizards.
- [ ] **3.4. Settings that lack a command.** The snap folder as a lasting setting (Core's handler
  has no `snap_folder` key, evidence 06: if it needs Core, 1 line, else the shared layer keeps it);
  the ETL increment and zero toggles only if a skill needs them.
- [ ] **3.5. Not now.** The optimizer (a GUI-thread window with its own flow; phase 5 decides whether
  focusing becomes code), the camera window's levels and overlays, the contrast window, the script
  editor (code execution), PSF and field curvature tools. Each is listed with the reason, so a
  reader sees it was a choice.
- [ ] **3.6. Cases.** A tiling case on the simulator (the list's rows and counts are checked, not an
  image), a table round trip, and the MCP and TCP live suites extended by the new commands.

## Phase 4: skills

Branch `agent/skills`. Core: 0 lines. Main window: 0 lines.

- [ ] **4.1. The format and the place.** One Markdown file per skill with a two-line head (name,
  one-line description) and the body: when it applies, which tools in which order, what to look
  for, when it is good enough, what to report. Shipped defaults in `mesoSPIM/src/ai_assistant/skills/`;
  the microscope's own in `skills/` next to its config (fact 9), which add to or replace the
  shipped ones by name. Lab-specific knowledge goes in the config folder, never in the package.
- [ ] **4.2. Loading.** The prompt lists the skills' names and descriptions under "# Skills" and
  one rule: when a request matches a skill, load it before the first call. A `get_skill` command in
  the shared layer serves the text to all three clients and replaces `get_manual`'s recipes; the
  tab offers it as the `load_skill` tool. A loaded skill is a tool result in the history, so it is
  paid for once per request, not per turn.
- [ ] **4.3. The first skills, lifted from the manual** (fact 8): exposure, look after a change,
  the pre-run check, centring, focusing. The manual loses the procedure text and keeps the facts.
  Tool descriptions stay factual; chaining advice (snap, ask_eyes) moves into the skills.
- [ ] **4.4. The new skills, written with the lab:** tiling (on 3.1), light-sheet alignment, ETL
  tuning. Each is checked at the microscope by an operator before it ships as a default.
- [ ] **4.5. Cases per skill** in the matrix: the skill is loaded, the calls come in the skill's
  order, and the outcome is scored where the simulator can (exposure, centring, focusing); for
  alignment and ETL, calls and order only (fact 10). Run on flash-lite and Haiku.

## Phase 5: what should be code, by evidence

As phase H of 1.x. A function moves from model steps or a skill into Python only with evidence
from the matrix that it needs precision, speed, determinism or heavy computation.

- [ ] **5.1. Review the skills and the measurements for candidates.** *Evidence so far: focusing
  fails on three models (flash-lite 1 to 7 of 20, Haiku 1 of 20); the long tasks on all. A first
  `focus_sweep` block is parked on `agent/h`.*
- [ ] **5.2. Build each promoted function as a measuring block;** a skill it replaces is retired.
  A time lapse that refocuses between points needs Core's hook first (2.3).

## Order, effort and dependencies

| Phase | Effort | Depends on | Core / window lines |
|---|---|---|---|
| 1 Refresh | 1 day, plus a Windows demo run | none | 1 / 4 |
| 2 Lean | 3 days, plus the re-recording | 1 (so the demo checks see the state) | 0 / 0 |
| 3 GUI commands | 3 days; 3.1 first | 2 (one re-recording) | 0 / 0 |
| 4 Skills | half a day per skill, plus the lab's time | 2, 3.1 for tiling | 0 / 0 |
| 5 Code by evidence | per function | 4 | per function, stated then |

1 and 3.1 do not need 2, but every case recorded before 2 is re-recorded after it, so 3's cases
are recorded once if 3 follows 2.

## How it is validated

- **Offline, every change:** the ai_assistant and remote_control suites, the replay suite (each
  recorded case scores as recorded, with at most 3% more estimated tokens), ruff with no new
  finding, `run.py pyqt`.
- **Demo mode on Windows, each phase:** the driver from the evidence folder, extended per phase.
- **The microscope, with an operator present:** phase 1 (the boxes follow), phase 3 (a tiling
  list built by tool and run), phase 4 (each skill once).
- **Models:** flash-lite for every run, Haiku once per phase; any other model is asked for first.
- **Containment:** the pull request description carries `git diff --stat` on `mesoSPIM_Core.py`,
  `mesoSPIM_MainWindow.py` and `mesoSPIM_State.py`, and the count matches the item.

## Open questions

- 2.3: is a long run that outlives the tool's cap acceptable as "still running, come back", or
  does the tab need a passive "done" notice (a label, no model turn)?
- 2.3 and 5.2: will Nikita take a hook in Core's time lapse that calls back between points?
- 3.2: the acquisition manager's CSV read and write are methods of its window; calling them
  through the GUI signal is contained, lifting them into the shared layer is cleaner. Which?
- 4.1: are lab skills committed to the lab's config repository, so they are versioned with the
  hardware file?
- Fact 13: a one-line Core fix for the laser max amplitude key; Nikita's call.

## Versions

- **1.0 to 1.6, 6 to 9 October 2026.** Phases A to E built and merged (PR 120), F set aside, G and
  H kept as long-term goals; Haiku 5.5 fixes (PR 121) and benchmark. The 1.x entries are in the
  git history of this file.
- **2.0, 9 October 2026.** A new direction after the first runs on two cloud models and the Windows
  demo test. The agent targets cloud models: everything built to fit a small model goes (2.2). No
  confirmation (2.1): the operator wants the assistant to act, and Core's limits and Stop stay.
  No timer (2.3): time courses are the software's, and the model confused `wait` with `schedule`.
  The plan is for the model, not the operator (2.4). The window's features become commands for
  all three clients (3), and the procedures become skills (4), which were phase G. Phase H is
  phase 5. Containment is a principle with a count per item.

When an item is done, tick its box and note the date and the pull request beside it. When the plan
changes, raise the version and add a line here saying what changed and why.
