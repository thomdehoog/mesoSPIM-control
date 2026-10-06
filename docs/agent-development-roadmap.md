# Agent development roadmap: mesoSPIM AI Assistant

Version 1.1, 6 October 2026. Applies to the AI Assistant in mesoSPIM-control 1.27.

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

## What the code review found

These shape the order below. The facts are from the 1.27 code.

1. **The frames `look` can reach are thin.** Core keeps one display frame (`frame_queue_display`,
   length 1). During an acquisition the camera puts only the first image of every second step into
   it, so for a z-stack that is the first plane, not a useful image for a focus check. Frames from
   acquisitions need a hook in Core or the image writer: hardware-adjacent code, and Nikita's to
   agree. Frames from looks, snaps and live are reachable from the assistant alone.
2. **A long wait inside a turn blocks everything else.** While a turn runs, the input is disabled and
   scheduled turns do not fire (`fire_due_schedule` skips while running). A 30-minute `wait` inside a
   turn would block the focus checks the time lapse needs. The sound design is a continuation: the
   wait ends the turn, and the tab starts a follow-up turn when the condition is met, the way the
   scheduler already fires turns.
3. **Continuations need state that outlives a turn.** The turn guard's memory (refused axes, light
   changes, values set) resets every turn, and a plan would too. Both must be scoped to the request,
   across its continuation turns.
4. **Tool results have no common shape.** A move returns its position three levels deep, a setting
   returns an empty acknowledgement, `look` returns statistics. Adding readback and a state line to
   each separately would multiply the shapes; one result envelope for every tool is cleaner.
5. **Time is read in about ten places** in the assistant. The simulator, `wait`, schedules and the
   map's ages all need one clock that tests can control.
6. **The focus measure is brightness-normalised but fragile.** It is the variance of a Laplacian on a
   decimated sample, divided by the frame's range squared, so a hot pixel or saturation shifts it,
   and it is only comparable at the same zoom and scene. The map must group by zoom and light
   settings, and the measure needs checking on real frames.
7. **Two image memories would compete.** The eyes keep earlier frames in their own conversation;
   explicit `frames=` would send them again. One mechanism should own frame history.
8. **Calibration moves the stage.** Measuring the image-to-stage scale needs real moves, so it must be
   an explicit, visible block, gated like any move, never automatic.

## Phase A: foundations (about two days)

No change in behaviour; everything after depends on it.

- [ ] **A1. Port the evaluation and the offline tests** from `remote-control-py312` onto the 1.27
  layout, in the fork: module paths, class renames, the fake-Qt setup. The 158 cases and their twins
  run as a regression suite. (One day.)
- [ ] **A2. One clock.** Every time read in the assistant goes through one injectable clock, so the
  simulator can run hours of a time lapse in seconds. (Two hours.)
- [ ] **A3. One result envelope for every tool:** `status`, `changed` (what this call changed, read
  back after it was applied), `state` (the short state line), `note`, `error`. Existing results are
  mapped into it; the evaluation's scorer is updated in the same change. (Half a day.)

## Phase B: measurement (about two and a half days, partly at the microscope)

- [ ] **B1. A simulator that behaves over time:** focus follows the stage, the sample's place
  follows x and y, brightness follows intensity and exposure with saturation, time passes on the
  clock from A2, and drift and bleaching can be switched on per case. (One day.)
- [ ] **B2. Recorded real frames:** at the microscope, a focus series (about 30 frames across
  ±300 µm) and an x/y grid over a few real samples, served by the simulator by position. (An
  afternoon at the microscope, two hours to wire in.)
- [ ] **B3. Check the focus measure** on the recorded series: it must peak clearly at the sharp
  frame. If not, fix it here (for example a percentile range instead of min-max, and a binned rather
  than decimated sample). (Two hours.)
- [ ] **B4. Multi-step cases from templates with seeds,** scored on outcomes: about 50 hand-written
  smoke cases, and 150 to 200 generated ones (15 to 25 per group) before each pull request. Groups:
  centring, focusing, exposure, live tuning, acquisition with checks, time lapses with drift,
  recovery from refusals, vague requests, requests that must stop partway. (One day.)
- [ ] **B5. Baseline and a strong-model check:** GLM 5.3 Flash through Baseten for routine runs,
  one strong model once. If the strong model already solves most cases, F3 moves forward. (One hour,
  plus run time.)

## Phase C: feedback the model can build on (about three and a half days)

- [ ] **C1. Settings report their result** through the envelope: the assistant waits until a setting
  is applied, as it does for moves, and returns the read-back value. Every result carries the state
  line. (Half a day; builds on A3.)
- [ ] **C2. `look` over chosen frames** from looks, snaps and live: `frames=3`, `frames=[1, 7]`,
  `frames="3-10"`, at most about 16; `snap=false` reuses recorded frames, so the model decides per
  look what must be fresh. Frames are kept as small binned copies (about 100 per session), numbered,
  with time, source, position and settings; the readout gives the count and the last three. Per
  frame, code adds brightness, saturation, focus measure, centroid, offset from centre in
  micrometres, drift against the first frame shown, and the change against the previous one.
  Explicit frames become the one frame history: the eyes keep their text memory and stop carrying
  images. (One and a half days.)
- [ ] **C3. The spatial memory:** sample position in stage coordinates, the focus curve with the best
  focus and its uncertainty, good settings found, marked positions (`mark(name)`), and boundaries,
  grouped by zoom and light settings. Conclusions carry confidence and age. Frames flagged by code's
  checks are left out visibly; the model can exclude a frame or name the object to measure, with a
  reason, shown in the map. One readout line; `recall_map` for the rest. Scored against the
  simulator's truth before any model uses it. (One and a half days.)
- [ ] **C4. `calibrate`:** an explicit block that moves a known small step, measures the image shift,
  and stores scale and direction per zoom across sessions. Gated like any move, shown in the
  transcript. C2 and C3 use the calibrated values once present. (Half a day.)

## Phase D: long tasks (about one and a half days)

- [ ] **D1. Request-scoped state.** The turn guard's memory and the request's identity outlive a turn,
  so a continuation is the same request to the guard. (Half a day.)
- [ ] **D2. `wait` as a continuation.** `wait(until, max_s)` with `until` "done", "idle" or seconds.
  It ends the turn; the tab watches the condition and starts a follow-up turn of the same request
  when it is met, with the result. Meanwhile the input is free, schedules fire, and progress shows in
  the window. Stop microscope, Cancel prompt and Disconnect end the request. On live mode, "done"
  returns at once. (Half a day; builds on D1.)
- [ ] **D3. The plan,** kept by `update_plan(steps)` for the whole request across continuations,
  shown as a live checklist in the chat window. Three lines in the manual. (Half a day; builds on D1.)

## Phase E: autonomy within bounds (half a day, plus a session at the microscope)

- [ ] **E1. Measured values through the turn guard.** A value passes without Run when it matches
  code's own numbers within bounds: a move within about 20% of the computed offset and at most one
  field of view; a focus step toward the map's best focus, or a search of at most 100 µm per step
  within ±300 µm of the request's start; an intensity or exposure change within a factor of two and
  only after a fresh look. Behind a setting, off until checked on the microscope with an operator
  present. Safety tests on both sides. (Builds on C2, C3 and D1.)

## Phase F: later, partly with Nikita

- [ ] **F1. Frames from acquisitions.** A hook that hands the assistant one representative image
  per time point (for example the middle plane, or a projection), so a time lapse can be checked by
  `look`. Touches Core or the image writer, so it is designed with Nikita. (One to two days.)
- [ ] **F2. `wait` over TCP and MCP,** in the shared remote-control layer, so external agents need
  not poll. With Nikita. (Half a day.)
- [ ] **F3. A stronger model for long requests:** a second model box, used when a request needs a
  plan. Moves forward if B5 shows the gap is model capability. (Two hours.)

## Phase G: skills, the last polishing step

Skills come only after the workflows already work well without them. They polish: they make good
workflows faster, more consistent, or closer to lab practice. They never compensate for weak building
blocks; if a basic workflow only works with a skill, the gap is fixed in the blocks instead.

- [ ] **G1. Skills for expert routines that are not intuitive,** written as instructions, not code: for
  example ETL tuning, light-sheet alignment, a pre-acquisition check. A skill says which settings to
  change, in which order, what to look for and when it is good enough; the model follows it with the
  same building blocks, so flexibility stays. Skills load on demand, so the prompt stays small.
- [ ] **G2. Cases per skill** in the same test matrix as everything else.

## Phase H: what should be code, after the skills

The last step: with the workflows and skills in use, decide which functions should become Python or
analysis routines instead of model steps or skills. A routine is promoted only with evidence from the
test matrix that a model loop or a skill is too slow, too imprecise or too costly for that job.

- [ ] **H1. Review the measurements and the skills for candidates.** A function belongs in code when it
  needs:
  - **precision:** image registration, PSF or bead fitting, focus-curve fitting;
  - **speed or frequency:** a loop every few seconds, such as closed-loop focus during a long time
    lapse, which is too slow and too expensive as model steps;
  - **determinism and an audit trail:** results that must be identical every run and traceable, such
    as a calibration later measurements depend on;
  - **heavy computation:** projections, statistics over many frames, analysis of saved data.
- [ ] **H2. Build each promoted function as a measuring block,** for example `register_frames` or
  `measure_psf`: it returns numbers, and the model still decides when to use it and what to do with the
  result. Each one gets cases in the test matrix; a skill that it replaces is retired.

## Order and dependencies

| Item | Effort | Builds on |
|---|---|---|
| A1 Port evaluation | 1 day | |
| A2 One clock | 2 hours | |
| A3 Result envelope | half a day | A1 |
| B1 Simulator over time | 1 day | A2 |
| B2 Recorded frames | afternoon + 2 hours | B1 |
| B3 Focus measure check | 2 hours | B2 |
| B4 Multi-step cases | 1 day | B1 |
| B5 Baseline, strong-model check | 1 hour | B4 |
| C1 Settings report back | half a day | A3 |
| C2 `look` over frames | 1.5 days | A3, B3 |
| C3 Spatial memory | 1.5 days | C2 |
| C4 `calibrate` | half a day | C3 |
| D1 Request-scoped state | half a day | A1 |
| D2 `wait` as continuation | half a day | D1, A2 |
| D3 Plan | half a day | D1 |
| E1 Guard bounds | half a day + bench | C2, C3, D1 |
| F1 Acquisition frames | 1 to 2 days | C2, Nikita |
| F2 `wait` over TCP and MCP | half a day | D2, Nikita |
| F3 Model for long requests | 2 hours | B5 |
| G Skills | per skill, half a day | A to E |
| H What should be code | review half a day; per routine 1 to 2 days | G |

Phases A to E: about ten days. B2 needs the microscope and can run in parallel with A. D can run in
parallel with C. Each phase is one pull request, measured against the baseline before it is sent.

## How it is validated

Every item passes a matrix of three environments, each without and with the model. "Without the
model" is a scripted stand-in that replays a fixed sequence of tool calls.

| | Without the model (scripted) | With the model |
|---|---|---|
| **1. Offline simulator** | Plumbing, guards, continuations, timing; deterministic | Composition by the model, at scale on the generated set |
| **2. Demo mode** | The real app, Qt loop and Core, same scripted sequences | The real app with the real model |
| **3. Microscope, operator present** | Real timing and motion with a known sequence | The full system |

- **Record and replay.** A successful model run's tool calls are saved and replayed as the scripted run
  in the next environment, so both columns test the same thing and no scripts are written by hand.
- **Gates.** An item moves to the next environment only when both columns pass in the current one. E1
  stays off until both microscope cells pass.
- **Why the scripted run on the microscope matters.** It separates hardware problems from model
  problems, it is the safer first contact with a known sequence and small moves, and it catches stage
  settling, filter and camera timing that no simulator has.
- **Where.** Only Gemini is reachable from the cloud session; Baseten runs and both demo-mode cells run
  on the lab machine. Demo mode's camera is synthetic, so vision is tested offline with recorded frames
  and on the microscope.
- **Safety tests** in every environment: E1's bounds on both sides, and a `wait` ended by Stop and
  Cancel.
- **The microscope requests:** the three from the LinkedIn post and two longer ones.

## Versions

- **1.0, 6 October 2026.** First version, after the 1.27 release candidate. Built from a review of the
  1.27 code; phases A to G; validation in three environments, each without and with the model.

- **1.1, 6 October 2026.** Phase H added: after the skills, decide which functions should become
  Python or analysis routines, by evidence, and build them as measuring blocks.

When an item is done, tick its box and note the date and the pull request beside it. When the plan
itself changes, raise the version and add a line here saying what changed and why.
