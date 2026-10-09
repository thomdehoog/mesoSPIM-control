# AI Assistant: lean for cloud models, no guard, no timer

Branch `agent/lean-pr`, on `agent/refresh-pr` (WP1, the window's refresh). Five commits, each a
step that can be read alone: lean for cloud models and no timer; no guard and no confirmation; the
plan is the model's; the docs; and a short, general manual.

## Summary

- **Built for the two cloud models.** The assistant targets gemini-3.5-flash-lite and
  claude-haiku-5-5. What existed to fit a small or local model goes: history trimming and
  compaction, the "called nothing" challenge, the Regular and Full tool profiles, the memory tools,
  and the Local AI mode with its `ai-assistant-local` extra. The history is append-only until Clear
  context. The tab shows each session's size, warns past the model's warning level and refuses a
  turn past its ceiling.
- **Prompt caching on Anthropic, through pydantic-ai's settings.** Tool definitions and
  instructions are cached for an hour and the growing history for five minutes. That uses three of
  the four breakpoints, the hour first. Other providers get no cache settings from the assistant.
- **No timer.** The scheduler, `schedule`, `cancel_schedule`, `wait`, the request line and the
  continuation turns go. A time course is the software's own time lapse. A run that returns while
  under way ends the turn; a 2 s check in the tab writes one grey line when it ends, with no model
  turn. Every tool is sequential, so two calls in one reply run in order.
- **No guard, no confirmation.** Core's stage limits, its busy gate and Stop microscope are the
  safety, the same for every client. The Run / Cancel bar, the turn guard, the light budget and
  measured values go. A look right after a snap still reuses that frame, now as a check on the
  frame history.
- **A short, general manual.** About 100 lines become 34. Rules written against particular
  cases or old failures go, and so do rules the tools or the refusals already say. One rule
  covers unclear requests: suggest what you would do and ask before doing it; if you don't
  know, ask. The tools' own descriptions carry the details, and workflows will be skills.
- **Docs.** `index.md` and `architecture.md` follow the code; `tool-sets.md` goes.

## Footprint

`git diff --stat b152c91 agent/lean-pr -- mesoSPIM/src/mesoSPIM_Core.py
mesoSPIM/src/mesoSPIM_MainWindow.py mesoSPIM/src/mesoSPIM_State.py
mesoSPIM/src/mesoSPIM_AcquisitionManagerWindow.py mesoSPIM/src/utils/`: WP1's lines only, nothing
from this package.

```
 mesoSPIM/src/mesoSPIM_Core.py       |  4 ++--
 mesoSPIM/src/mesoSPIM_MainWindow.py | 18 ++++++++++++++++--
 2 files changed, 18 insertions(+), 4 deletions(-)
```

`git diff --stat agent/refresh-pr agent/lean-pr`:

```
 CHANGELOG.md                                       |    2 +-
 docs/source/ai_assistant/architecture.md           |   59 +-
 docs/source/ai_assistant/index.md                  |  173 +---
 docs/source/ai_assistant/tool-sets.md              |   30 -
 mesoSPIM/src/ai_assistant/assistant.py             | 1026 +++-----------------
 mesoSPIM/src/ai_assistant/config.py                |  194 +---
 mesoSPIM/src/ai_assistant/frames.py                |   17 +-
 mesoSPIM/src/ai_assistant/gui.py                   |  519 ++--------
 mesoSPIM/src/ai_assistant/local.py                 |  156 ---
 mesoSPIM/src/ai_assistant/manual.md                |  145 +--
 mesoSPIM/src/ai_assistant/measured.py              |  109 ---
 mesoSPIM/src/ai_assistant/requests.py              |  191 ----
 .../ai_assistant/test_real_pyqt_assistant_smoke.py |   21 +-
 .../ai_assistant/test_real_pyqt_scheduler_smoke.py |  170 ----
 mesoSPIM/test/remote_control/run.py                |    3 +-
 pyproject.toml                                     |    3 -
 16 files changed, 425 insertions(+), 2393 deletions(-)
```

## Results

Recorded on gemini-3.5-flash-lite in the fork's evaluation, each failing case run up to three
times. Before: the committed recording from before this package. First: after the lean and no-timer
step, the guard still in. Second: the final code, no guard and the short manual. Passing runs:

| Case file | Before | First | Second |
|---|---|---|---|
| Single-step | 140 of 140 | 140 of 140 | 124 of 135 |
| Held-out twins | 139 of 140 | 138 of 140 | 124 of 135 |
| Multi-step | 26 of 48 | 31 of 48 | 28 of 48 |
| Generated multi-step | 91 of 160 | 102 of 160 | 96 of 160 |
| Unattended (removed with the guard) | 12 of 18 | 12 of 18 | none |

The case counts drop where categories went with what they tested: tool profiles, memory, the timer,
confirmation.

Per group, multi-step and generated, first to second recording:

| Group | Multi-step | Generated |
|---|---|---|
| Centring | 6 to 6 of 6 | 18 to 19 of 20 |
| Focusing | 1 to 4 of 6 | 5 to 11 of 20 |
| Exposure | 6 to 6 of 6 | 11 to 13 of 20 |
| Live tuning | 3 to 4 of 6 | 13 to 15 of 20 |
| Acquisition with checks | 0 to 0 of 6 | 0 to 0 of 20 |
| Recovery from refusals | 6 to 5 of 6 | 20 to 16 of 20 |
| Vague requests | 6 to 0 of 6 | 20 to 5 of 20 |
| Must stop partway | 3 to 3 of 6 | 15 to 17 of 20 |

What moved, and why:

- **Vague requests fall.** "Make it brighter" or "rotate a bit" now gets a value picked, or offered,
  instead of a question with nothing changed. Before, the turn guard refused a number the operator
  never gave; that check is gone by decision, and the manual is not tuned to replace it.
- **A misspelled option is offered, not corrected.** Given "515 long-pass", the refusal lists
  `515LP` and the model asks whether to set it. The two cases that expect a silent correction fail.
- **Look-and-adjust work improves.** Focusing, exposure and live tuning gain with the shorter manual.
- **Acquisition with checks stays at 0** in both recordings: a weakness of this model on that
  workflow, left to a skill.

The evaluation measures the general manual out of the box; it was not tuned against. Running the
same set once on the microscope shows what breaks on a real instrument; only failures tied to real
use cases there are worth fixing, as fine-tuning for that instrument.

## Validation

- **Offline.** The fork's dev suite passes (1058 tests, 11 skipped), the replay suite included, on
  the second recording. On the package branch the real-Qt scripts pass (9; the combo-box test needs
  Windows, as before).
- **Claude Haiku 5.5.** Not run yet: the one Haiku recording the plan asks for needs an Anthropic
  key. The cache settings are checked offline, read off the request on the wire.
- **Please check on Windows** (demo mode): the AI Assistant tab connects, the size meter shows after
  a turn, and a time lapse started by the assistant ends with one grey line in the transcript.
- **On the microscope.** One session with each model: a look, a move, a run started and ended.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01RH8VsZbnUgYcVt3KuXjNFk
