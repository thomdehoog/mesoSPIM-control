# Agent development roadmap: mesoSPIM AI Assistant

Version 1.5, 7 October 2026. Applies to the AI Assistant in mesoSPIM-control 1.27.

Code: `mesoSPIM/src/ai_assistant/` and `mesoSPIM/src/remote_control/`. Work happens on a fork branch
made from `release/candidate-py312`; each phase goes to Nikita as one pull request. Development tools
(evaluation, simulator, recorded frames) stay in the fork.

## Goal

Requests with several steps or a high-level aim work, for example: "centre the sample, optimise the
image, then acquire every three minutes and keep it in focus". Today single requests work well and
multi-step ones often lose the thread.

## Principles

- **Simple building blocks, composed by the model.** No coded procedures such as "autofocus". The
  model combines look, move, set and wait.
- **Code measures, the model judges.** Distances, focus, brightness and drift are computed in code;
  the vision model adds what numbers cannot.
- **Measure before and after.** Every change is judged against the test set.
- **Safety stays where it is.** Run for the large stage moves, the stage limits, the busy gate and
  Stop microscope are unchanged. Only phase E changes a rule, behind a setting.
- **Stay small.** Every tool, result field and prompt line is paid for on every turn. Two memories
  only: the turn store and the frame history. New tools need a reason a reader of the code can see.

## What the code review found

Facts from the 1.27 code that fix the design. Each is used by one item below.

1. While a time lapse is active the dispatcher refuses every setting and move, whoever started it,
   and Core keeps it active between points. "Keep it in focus" cannot run inside `time_lapse_start`.
   The assistant drives a time lapse as a schedule of single acquisitions. (B4)
2. Core keeps one display frame, refreshed every second plane during a stack, so it holds whatever
   plane was shown last. A chosen image per time point would need a hook in Core or the writer;
   the assistant looks between its own acquisitions instead. (set aside below)
3. While a turn runs, the input is disabled and schedules do not fire, so a wait must end the turn
   and continue later. (D2)
4. The guard's "a value you did not give" reads the numbers in every stored prompt, and a scheduled
   turn's prompt is text the model wrote. A number the model puts into a schedule instruction counts
   as given when it fires. (D1, first)
5. Guard memory is per turn; over hours, "two light changes per turn" means nothing. (D1)
6. Settings return `{}` and Core applies them later; a move's position sits three levels deep. (A3)
7. The scheduler takes a clock; the rest of the assistant reads time directly. Core's time-lapse
   timer and snap polling are Qt timers, so in the simulator time passes only if it owns them. (A2, B1)
8. The focus measure is Laplacian variance on a strided sample over the frame's range squared: a hot
   pixel or saturation shifts it, and it compares only at the same zoom and scene. (B3)
9. The eyes keep frames in their own conversation; explicit `frames=` would send them again. (C2)
10. Ordinary moves are not confirmed, only `CONFIRM_FIRST` commands are. (C4)

## Phase A: foundations

No change in behaviour; everything after depends on it.

- [x] **A1. Port the evaluation and the offline tests** from `remote-control-py312` onto the 1.27
  layout, in the fork: module paths, class renames, the fake-Qt setup (it reuses
  `test/remote_control/conftest.py`). The 158 cases and their twins run as a regression suite and
  report the tokens per case. *Done 7 October 2026 (03c124c, 0d37c5f, 7e46678, dcf7303): a recorded
  gemini-3.5-flash-lite run of every case is replayed offline; each must score as recorded and grow at
  most 3% in estimated tokens.*
- [x] **A2. One clock.** Every time read in the assistant goes through the scheduler's injectable,
  epoch-like clock. *Done 7 October 2026 (ba0c9bc); request spacing to a provider stays on real time.*
- [x] **A3. Results that read back.** Setters return `changed` (the value read back after Core applied
  it, polled with a time-out); every result ends with the state keys that changed since the previous
  result. Existing shapes stay. Accepted when no case in the A1 suite grows by more than a few percent
  in tokens. *Done 7 October 2026 (ae0b323): every score as recorded, +0.04% tokens in all, largest
  case +0.2%.*

## Phase B: measurement, partly at the microscope

- [x] **B1. A simulator that behaves over time:** focus follows the stage, the sample's place follows
  x and y, brightness follows intensity and exposure with saturation, acquisitions progress on the
  clock from A2, and drift and bleaching can be switched on per case. *Done 7 October 2026 (2af6e36,
  4e1c932); cases are scored on its truth.*
- [ ] **B2. Recorded real frames:** a focus series (about 30 frames across ±300 µm) and an x/y grid
  over a few real samples, served by the simulator by position. *Needs the microscope.*
- [ ] **B3. Check the focus measure** on the recorded series: it must peak clearly at the sharp
  frame. If not, fix it here (a percentile range instead of min-max, a binned sample). *On the
  simulator the old measure read highest far from focus; fixed 7 October 2026 (5d342f5, 4e1c932):
  the Laplacian's energy over the squared signal, noise taken off, peaking 58 to 472 times above the
  frames 100 um away. The check on the recorded series (evals/focus_check.py) waits for B2.*
- [x] **B4. Multi-step cases from templates with seeds,** scored on outcomes: about 50 hand-written
  smoke cases, and 150 to 200 generated ones (15 to 25 per group) before each pull request. Groups:
  centring, focusing, exposure, live tuning, acquisition with checks, time lapses by schedule with
  drift, recovery from refusals, vague requests, requests that must stop partway. *Done 7 October
  2026 (f60fbb5): 54 smoke and 180 generated cases, in the replay suite.*
- [x] **B5. Baseline and a strong-model check:** GLM 5.3 Flash through Baseten for routine runs, one
  strong model once. If the strong model already solves most cases, F4 moves forward. *Done 7
  October 2026 (21ded41) on gemini-3.5-flash-lite (GLM is not reachable from the cloud session):
  21 of 54 smoke and 68 of 180 generated cases. gemini-3.1-pro-preview on the smoke set: 26 of 54.
  Both fail every centring, acquisition-with-checks and time-lapse case, so the gap is the tools,
  not the model: a stronger model for long requests is not needed.*

## Phase C: feedback the model can build on

- [x] **C1. The frame history.** Frames from looks, snaps and live are kept as small copies (bin 4 or
  8, 16-bit, capped by bytes, about 100 frames) in the session store, numbered, with time, source,
  position, settings and an optional label (`look(label="before")`). Per frame, code adds brightness,
  saturation, focus measure, centroid, and the offset from centre in pixels and in micrometres from
  the nominal scale (pixel size, binning, the axes box), marked uncalibrated until C4.
  *Done 7 October 2026 (d0366d7).*
- [x] **C2. `look` over chosen frames:** `frames="last 3"`, `frames="1,7"`, `frames="3-10"`, at most
  about 16; `snap=false` reuses recorded frames, so the model decides per look what must be fresh. The
  result adds drift against the first frame shown and the change against the previous one. The
  readout gives the count, the labels and the last three. The eyes keep their text memory and stop
  carrying images. *Done 7 October 2026 (d0366d7). A count is "last 3": one string argument cannot
  tell a count from a frame number.*
- [x] **C3. The map, derived from the frames.** No store of its own: one readout line computed from
  the frame history, grouped by zoom and light settings, with the sample's position in stage
  coordinates, the best focus from the focus curve with its uncertainty, good settings, labelled
  positions and the age of each. Frames flagged by code's checks are left out and named. Scored
  against the simulator's truth before any model uses it.
  *Done 7 October 2026 (d0366d7): on the simulator the best focus within 20 um and the sample
  within 50 um of the truth.*
- [x] **C4. `calibrate`:** moves a known small step, measures the image shift, and stores scale and
  direction per zoom in the microscope's config directory. In `CONFIRM_FIRST`, so one Run covers the
  block. C1 and C3 switch to the calibrated scale once present.
  *Done 7 October 2026 (d0366d7, d35d222): the file is git-ignored.*

## Phase D: long tasks

- [x] **D1. The request.** Store entries carry a request id and whether their text was typed by the
  operator or written by the machine (a schedule instruction, a continuation result). The guard reads
  typed text only, keeps the request's first prompt as its reference, and keeps its memory for the
  request. Budgets per request and time window: light changes per ten minutes, measured moves per
  request. Safety test: a number in a schedule instruction or a continuation never counts as given.
  *Done 7 October 2026 (969924a). Light changes are counted per request over ten minutes, so a new
  typed request starts afresh; the budget of measured moves comes with E1, which makes them.*
- [x] **D2. `wait` as a continuation.** `wait(until, max_s)` with `until` "done", "idle" or seconds.
  "Done": the operation this request started last is finished or released, core state is not
  acquiring and no time lapse is active; with nothing started it returns at once. The wait ends the
  turn; the tab starts a follow-up turn of the same request when the condition is met, with the result
  in the readout. One request class with one explicit state; one pending continuation at a time; a
  continuation limit per request. The window shows the open request with its turns and tokens and a
  Cancel of its own; Stop microscope, Cancel, Disconnect and Clear context end it. *Done 7 October
  2026 (969924a, f7f9387). Phases C and D on flash-lite: 26 of 54 smoke and 91 of 180 generated
  cases (before: 21 and 68); centring 6/6 and 17/20, every focusing-dependent group still 0.*
- [x] **D3. The plan** is text: a checklist the model writes in its reply, which the tab renders and
  keeps for the request. No tool, three lines in the manual. *Done 7 October 2026 (969924a); the
  readout of the request's later turns carries the plan back.*

## Phase E: autonomy within bounds, with a session at the microscope

- [ ] **E1. Measured values through the turn guard.** A value passes without Run when it matches
  code's own numbers and the measurement is fresh: its frame was taken at the current position, after
  the last move or setting. Bounds: a move within about 20% of the computed offset and at most one
  field of view, each next measured offset smaller than the last or the guard refuses and asks; a
  focus step toward the curve's best focus, or a search of at most 100 µm per step within ±300 µm of
  the request's start; an intensity or exposure change within a factor of two, after a fresh look.
  Measured moves are capped per request. Behind a setting, off until checked on the microscope with an
  operator present. Safety tests on both sides, including a stale frame and a wrong calibration sign.
  Adds at most a paragraph to the manual.
  *Built offline in the fork, 7 October 2026 (agent/e, ac8c28f and ed494e6), off by default
  (`ai_assistant_measured_values` in the microscope config). The paragraph goes into the prompt only
  when it is on, not into the manual. Convergence is checked per image direction: a move that makes
  either worse stops the next one. On 18 unattended cases on flash-lite, with an operator who cancels
  every question: centring 6 of 6 with no question asked, a wrong coordinate system 6 of 6 with the
  sample kept near where it was, acquisition 0 of 6 (focus). The box stays open until the microscope
  cells pass with an operator present.*

## Set aside: only if needed

Taken out of the plan in 1.4. Each would change code Nikita maintains or add a model, and none is
needed for multi-step requests; each comes back only with a reason from the lab's practice.

- **Frames from acquisitions** (a representative image per time point, by a hook in Core or the
  image writer): the assistant takes its own look between the acquisitions it schedules.
- **Moves between the points of a time lapse** (in the dispatcher, so `time_lapse_start` could
  replace the schedule): the schedule of single acquisitions works; only Core's own time-lapse
  timing or file naming would call for it.
- **`wait` over TCP and MCP** (in the shared layer): it helps other clients only, and changes an
  interface they rely on.
- **A stronger model for long requests:** B5's strong model fails the same cases as flash-lite.

## Phase G: skills, the last polishing step

Skills come only after the workflows work well without them; they never compensate for weak blocks.

- [ ] **G1. Skills for expert routines,** written as instructions, not code: ETL tuning, light-sheet
  alignment, a pre-acquisition check. Which settings to change, in which order, what to look for and
  when it is good enough. Loaded on demand, so the prompt stays small.
- [ ] **G2. Cases per skill** in the same test matrix.

## Phase H: what should be code, after the skills

A function is moved from model steps or a skill into Python only with evidence from the test matrix
that it needs precision (registration, PSF fitting, curve fitting), speed (a loop every few seconds),
determinism and an audit trail (a calibration later measurements depend on), or heavy computation.

- [ ] **H1. Review the measurements and the skills for candidates** against those four criteria.
  *First evidence, from the runs of 7 October 2026: focusing. Flash-lite steps f once, sees "better"
  and stops; no focusing, acquisition-with-checks or time-lapse case ends in focus, with or without
  C and D (precision, and in a time lapse speed and determinism). A first take on a `focus_sweep`
  block is parked on agent/h. Also: conditional requests ("if nothing is visible, stop; otherwise
  run") are run regardless, with the condition read correctly. From the E1 runs: with a wrong
  coordinate system the model negates `centre_move_um` instead of moving by it, and it reports
  success after its moves were refused.*
- [ ] **H2. Build each promoted function as a measuring block,** such as `register_frames`: it returns
  numbers, the model decides. Cases in the matrix; a skill it replaces is retired.

## Order, effort and dependencies

| Item | Effort | Builds on |
|---|---|---|
| A1 Port evaluation | 1 day | |
| A2 One clock | 2 hours | |
| A3 Results that read back | 1 day | A1 |
| B1 Simulator over time | 1 day | A2 |
| B2 Recorded frames | afternoon + 2 hours | B1 |
| B3 Focus measure check | 2 hours | B2 |
| B4 Multi-step cases | 2 days | B1 |
| B5 Baseline, strong-model check | 1 hour | B4 |
| C1 Frame history | 1 day | A3, B3 |
| C2 `look` over frames | 1 day | C1 |
| C3 Map from frames | 1.5 days | C2, B1 |
| C4 `calibrate` | 1 day | C1 |
| D1 The request | 1 day | A1 |
| D2 `wait` as continuation | 1.5 days | D1, A2 |
| D3 Plan as text | half a day | D1 |
| E1 Guard bounds | 1.5 days + bench | C2, C3, C4, D1 |
| G Skills | half a day per skill | A to E |
| H What should be code | half a day; 1 to 2 days per routine | G |

Phases A to E: about fifteen days. B2 needs the microscope and can run beside A; D can run beside C.
Each phase is one pull request, measured against the baseline before it is sent.

## How it is validated

Every item passes three environments, each without and with the model. "Without the model" replays
a recorded sequence of tool calls: a successful model run is saved and replayed in the next
environment, so both columns test the same thing and no scripts are written by hand.

| | Without the model (replayed) | With the model |
|---|---|---|
| **1. Offline simulator** | Plumbing, guards, continuations, timing; deterministic | Composition by the model, at scale on the generated set |
| **2. Demo mode** | The real app, Qt loop and Core, same sequences | The real app with the real model |
| **3. Microscope, operator present** | Real timing and motion with a known sequence | The full system |

- **Gates.** An item moves to the next environment only when both columns pass in the current one.
  E1 stays off until both microscope cells pass. The replayed run goes first on the microscope: it
  separates hardware problems from model problems with a known, small sequence.
- **Where.** Only Gemini is reachable from the cloud session; Baseten and demo mode run on the lab
  machine. Demo mode's camera is synthetic, so vision is tested offline on recorded frames and on the
  microscope.
- **Safety tests** in every environment: machine-written numbers never count as given (D1), E1's
  bounds on both sides with a stale frame and a wrong calibration, a `wait` ended by Stop, Cancel,
  Disconnect and Clear context.
- **Cost.** Every pull request reports tokens per case and per simulated hour of a time lapse.
- **The microscope requests:** the three from the LinkedIn post and two longer ones.

## Versions

- **1.0, 6 October 2026.** First version, after the 1.27 release candidate: phases A to G, validation
  in three environments, each without and with the model.
- **1.1, 6 October 2026.** Phase H: which functions should become Python routines, by evidence.
- **1.2, 6 October 2026.** After an independent review against the code and for leanness. The time
  lapse refuses moves between points, so the assistant drives it by schedule until F2. The guard reads
  model-written prompts as the operator's: fixed first in D1 with a safety test. E1 requires a fresh
  frame and convergence. D2 defines "done", one pending continuation, a request line with its own
  Cancel. Two memories instead of three: the map is derived from the frame history; `mark` and
  `recall_map` dropped, labels go on `look`; the plan is text, not a tool; the envelope reduced to
  `changed` on setters plus changed state keys, accepted on measured tokens. `calibrate` joins
  `CONFIRM_FIRST`; C1 uses a nominal scale until C4. Duplicated text removed; efforts live in the table
  only; A to E re-estimated at fifteen days.

- **1.3, 7 October 2026.** After phases A to D in the fork. B5 ran on gemini-3.5-flash-lite, the
  strong-model check on gemini-3.1-pro-preview: the strong model fails the same groups, so F4 stays.
  C2's count is written "last 3". D1's light budget is per request over ten minutes; the budget of
  measured moves moves to E1. The prompt-size test no longer caps the prompt: first make the agent
  work on cloud models, then scale down to local ones. H1 has its first evidence (focusing,
  conditional requests); routines wait for H, failures before it are recorded, not fixed.

- **1.4, 7 October 2026.** Phase F taken out of the plan and listed as set aside, only if needed:
  none of it is needed for multi-step requests, three items would change Nikita's code, and B5
  showed a stronger model does not help. The letters G and H stay.

- **1.5, 7 October 2026.** E1 built offline, off by default: the convergence check looks at each
  image direction, after a run with one axis set wrong made two wrong moves. H1 gets two findings
  from the unattended runs.

When an item is done, tick its box and note the date and the pull request beside it. When the plan
changes, raise the version and add a line here saying what changed and why.
