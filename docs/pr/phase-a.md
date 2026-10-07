# AI Assistant: one clock, and results that read back

Branch `agent/a-pr` of thomdehoog/mesoSPIM-control, against `release/candidate-py312`. Two commits,
two files: `mesoSPIM/src/ai_assistant/assistant.py` and `mesoSPIM/src/ai_assistant/config.py`.
Nothing in Remote Control, Core or the GUI changes. The manual and the tool list are unchanged.

## Summary

This is the first step of the agent development roadmap: foundations for multi-step requests, with
no change in what the assistant does.

- **One clock.** Every time the assistant reads about the instrument, the session or the schedules
  goes through the scheduler's clock: the wait on an operation and its cap, the readout's clock, a
  turn's time, a frame's time for the eyes, and a schedule's due time. A simulator can then own
  time. Two reads stay on real time on purpose: the spacing of requests to a rate-limited host and
  the local model server's start-up timeout.
- **Setters read back.** A setter answers `{}` as soon as Core accepts it, and Core applies the
  value later on other threads. The assistant now reads the keys back until they read as asked,
  for 3 seconds at most, and the result carries them as `changed`.
- **Results end with what changed.** Every result of a call that reached the instrument, and of
  `look`, ends with the readout keys that changed since the model last saw them, as
  `state_changed`. A move's new position no longer sits three levels deep in its result, and a
  change made at the microscope meanwhile shows too. A result that changed nothing ends as before.

Example, a relative move of x by -100 µm:

```
{"status": "completed", "operation": "op-000002", "result": {...as before...},
 "state_changed": {"position.x": 24899.0}}
```

## Validation

- **Offline evaluation.** 158 cases and their 158 held-out twins were recorded on
  gemini-3.5-flash-lite and replayed through the real agent, tools, guard and dispatcher. Every
  case scores as recorded with these changes. The fork keeps this suite; it is not in this pull
  request.
- **Tokens.** Input tokens grow by 0.04% over the 316 cases. The largest single case grows 0.2%.
- **Real PyQt.** All ten scripts of `mesoSPIM/test/remote_control/run.py pyqt` pass offscreen on
  this branch, the assistant and scheduler smoke tests included.
- **Not run here.** The combo-box GUI test needs Windows. Demo mode and the microscope have not
  been run yet.

| Set | Cases | Passing on the model | Gemini input tokens per case, mean | Smallest | Largest |
|---|---|---|---|---|---|
| Cases | 158 | 158 | 19,438 | 14,954 | 85,579 |
| Held-out twins | 158 | 157 | 19,300 | 14,958 | 85,749 |

## Please check on the instrument

A setter now waits up to 3 seconds for its value to read back. On the instrument, a filter wheel
or zoom moved with `wait=false` may take longer to show the new value. The result then says what
Core holds at that moment.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01RH8VsZbnUgYcVt3KuXjNFk
