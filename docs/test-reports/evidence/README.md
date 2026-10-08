# Evidence: Windows demo test of agent/e, 8 October 2026

Evidence for each finding in [../windows-demo-agent-e.md](../windows-demo-agent-e.md), so that it can be
reproduced on another machine and checked after a fix. Each finding has its own folder and commit:
a README (what was seen, how to reproduce, expected and actual), log excerpts, and a reproduction
script where one is possible without a language model.

Tested: agent/e at 41839f2, Windows Server 2019 (10.0.17763), Python 3.12.13, PyQt5 5.15.11
(Qt 5.15.2), pydantic-ai 2.14.1, mesoSPIM in demo mode (-D, DemoStage, synthetic camera), model
gemini-3.5-flash-lite. No hardware.

| Folder | Finding | Kind | Reproduction |
| --- | --- | --- | --- |
| [01-shutter-and-filter-boxes-not-refreshed](01-shutter-and-filter-boxes-not-refreshed/) | The main window's shutter and filter boxes keep their old value after a remote change | Bug, upstream Core/GUI | Script, no model |
| [02-startup-from-repo-root](02-startup-from-repo-root/) | Starting from the repo root fails on a cwd-relative .ui path | Bug, upstream | Script, no model |
| [03-live-suite-environment](03-live-suite-environment/) | `run.py live tcp/mcp` skips every test with only the two variables the brief names | Test brief / runner | Script, no demo needed |
| [04-tcp-and-assistant-exclusive](04-tcp-and-assistant-exclusive/) | Connect is refused while a TCP/MCP transport runs | Test brief (by design) | Log; driver |
| [05-coordinate-box-locked](05-coordinate-box-locked/) | The coordinate system box is disabled while connected | Test brief (by design) | Log; driver |
| [06-demo-folders](06-demo-folders/) | The demo config writes to D:/tmp/; snap_folder is not a Core state request | Demo config / test brief | Log; driver |
| [07-stale-bytecode-after-move](07-stale-bytecode-after-move/) | Moving a clone leaves __pycache__ with old paths in test output | Tooling | Steps |
| [08-model-behaviour](08-model-behaviour/) | Vague requests get a question; wait vs schedule; measured values off/on | Model, for test design | Logs; driver |

## Shared material

- `run-logs/`: the full output of every run in the test, as produced (pytest, run.py pyqt, the
  driven GUI runs, the walkthroughs, the live suites, the measured-values runs). `mesospim_env_before.txt`
  is the `pip freeze` of the conda env before the AI Assistant was installed into it.
- `tools/drive_gui.py`: drives the real demo GUI (Remote Control and AI Assistant tabs) through its
  own widgets with the Gemini preset, and checks Core's state and the window after each step.
  `--steps a,...,g` is step 2 of the brief, `--steps m` the measured-values check, `--serve TCP|MCP`
  keeps a demo running for the walkthrough and live suites. It refuses anything but DemoStage.
  Needs `GEMINI_API_KEY` in the environment (not for `--serve`).
- `tools/snapshot.py`: prints position and settings over TCP, to compare before and after a run.
- `tools/demo_config_measured.py`: a copy of demo_config.py with `ai_assistant_measured_values = True`
  and the folders moved next to the clone.

Run the tools with the mesoSPIM environment's Python from anywhere; they find the repository from
their own location. Snaps and acquisitions go to `<clone>/../mesospim-probe` unless `--probe` says
otherwise.
