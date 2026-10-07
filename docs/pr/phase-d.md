# AI Assistant: requests, wait as a continuation, and the plan

Branch `agent/d-pr`, on top of `agent/c-pr`. One commit: `ai_assistant/` (a new `requests.py`,
`assistant.py`, `config.py`, `gui.py`, `manual.md`).

## Summary

- **Requests.** A typed message opens a request; its schedules and waits come back as turns of the
  same request, written by the machine. The turn guard keeps its memory per request and takes the
  operator's numbers from typed text only. A number the model wrote into a schedule instruction or
  a continuation therefore waits for Run. Light changes are counted per request over ten minutes.
- **`wait`.** `wait(until="done"|"idle"|seconds)` ends the turn. The request continues when the
  condition holds, in a turn that starts with the result. One wait can be pending at a time, at most
  30 per request.
- **The request line.** The window shows the open request, with its turns, tokens, wait and plan,
  and a Cancel of its own. Stop microscope, Cancel, Disconnect and Clear context end it.
- **The plan.** A checklist in a reply becomes the request's plan; the request's later turns get it
  back in the readout.

## Validation

- **Safety tests.** A number in a schedule instruction, or in a continuation, waits for Run. The
  guard keeps its memory across a request's continuation and starts afresh with the next typed
  request.
- **Recorded cases, replayed.** All 316 score as recorded, with about +2.4% estimated tokens for
  this phase.
- **Real PyQt.** All ten scripts pass; the scheduler smoke test runs the new machine-turn path
  through the real worker.
- **Please check.** The request line and its Cancel on Windows, in demo mode and on the instrument.

## Results so far, phases C and D together, flash-lite

| Set | Before C and D | After |
|---|---|---|
| Smoke cases | 21 of 54 | 25 of 54 |
| Generated cases | 68 of 180 | to follow |

Focusing does not improve: the model takes one focus step and stops. That is recorded as evidence
for phase H, not fixed here.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01RH8VsZbnUgYcVt3KuXjNFk
