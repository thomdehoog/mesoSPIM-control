# AI Assistant: measured values through the turn guard, off by default

Branch `agent/e-pr`, on top of `agent/d-pr`. One commit: `ai_assistant/` (a new `measured.py`,
`assistant.py`, `config.py`, `gui.py`).

## Summary

- **The setting.** `ai_assistant_measured_values` in the microscope config, off unless set. Off,
  nothing changes: a value the operator did not give waits for their Run.
- **What passes without Run when it is on.** A value that follows from the assistant's own
  measurement, while that measurement is fresh: the newest frame was taken after the last move or
  setting.
  - A move within 20% of the frame's centring move, at most one field of view.
  - A focus move to the map's best focus, or a search step of at most 100 µm within 300 µm of where
    the request started.
  - An intensity or exposure within a factor of two of the frame's.
  - At most eight measured moves per request.
- **Convergence.** Each next measured offset must be smaller than the last, and neither image
  direction may grow. A wrong calibration sign or a wrong coordinate system therefore stops at the
  second move.
- **The prompt.** One paragraph is added to the system prompt only when the setting is on.

## Validation

- **Safety tests.** Off by default. A stale frame, a move off the measurement, a wrong sign, one
  wrong axis while the other converges, the move cap, a move past one field and a light change
  past a factor of two each wait for Run. Each bound was checked by breaking it.
- **Recorded cases, replayed.** All earlier cases score as recorded, as do 18 new unattended ones.
- **Unattended runs on flash-lite,** with an operator who cancels every question:

| Group | Passed |
|---|---|
| Centring | 6 of 6, no question asked |
| Wrong coordinate system | 6 of 6, sample kept near where it was |
| Acquisition | 0 of 6, on focus |

- **Please check.** Leave the setting off until it has been tried on the microscope with an
  operator present: first the replayed cases, then a live centring.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01RH8VsZbnUgYcVt3KuXjNFk
