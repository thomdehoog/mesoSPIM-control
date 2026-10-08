# mesoSPIM AI Assistant: Windows demo test, bugs and lessons

8 October 2026 · Thom de Hoog

## Summary

The AI Assistant on agent/e (41839f2) works on Windows in demo mode: every step of the test passed, with one real bug outside the assistant itself. When the assistant (or any TCP or MCP client) changes the shutter configuration or the filter, Core takes the new value but the main window's box keeps the old one; for the shutter, the ETL controls for the wrong side stay enabled.

Tested on a Windows Server 2019 lab machine with no hardware: DemoStage and the synthetic demo camera only, with gemini-3.5-flash-lite as the model. No code was changed. The evidence for every finding, with reproduction scripts and the full run logs, is in [evidence/](evidence/README.md), one commit per finding. Most of the other findings are gaps in the test instructions, not in the code.

## Results

All 14 checks passed except the shutter box in step 2g; the offline numbers match Linux exactly.

| Step | Check | Result | Different from Linux |
| --- | --- | --- | --- |
| 1 | Offline pytest, ai_assistant + remote_control | Pass: 1258 passed, 11 skipped | Same |
| 1 | run.py pyqt, 10 scripts | Pass: 10/10; test_combobox_state_requests.py 8 passed, first run on Windows | Assistant smoke skipped its width check (offscreen Qt) |
| 2a | "move x by 100 um" | Pass: x 0 to 100 um, display updated, no question | |
| 2b | A value the operator did not give | Pass: Run/Cancel offered intensity 20, Cancel kept 10 | |
| 2c | Coordinate system box | Pass: new axes reach the worker and the prompt; the model answered by them | |
| 2d | Run the list, look when done | Pass: request line showed turns, tokens, "waiting until done"; the continuation fired | |
| 2e | What ends a request | Pass: request Cancel, Stop microscope, Clear context, Disconnect | |
| 2f | Snap every minute | Pass: 2 turns, 2 snaps, none after Stop | |
| 2g | Combo boxes | Zoom pass (one exchange trip, no echo); **shutter fail** (and filter, found later) | Not Windows-specific |
| 3 | demo_walkthrough, scripted | Pass: 15/15 | |
| 3 | demo_walkthrough, Gemini | Pass: 16/16; start state put back | |
| 3 | Live TCP / live MCP | Pass: 8/8 each | Needs more environment variables than the brief lists |
| 4 | Measured values off | Pass: the measured move asked Run/Cancel | |
| 4 | Measured values on | Pass: the same move ran without a question, no errors | Centring itself waits for a real sample |

Step 2 was driven by a script through the real tab widgets rather than by hand. Its first run had three failures, all in the script; after the fixes every step 2 check passed except 2g's shutter.

## Bugs in the software

Two bugs, both in upstream Core and GUI code rather than in the assistant; neither is fixed.

### 1. Shutter and filter boxes not refreshed after a remote change

Evidence and reproduction: [evidence/01-shutter-and-filter-boxes-not-refreshed](evidence/01-shutter-and-filter-boxes-not-refreshed/README.md). The reproduction, with each setting sent alone over TCP, shows the filter box goes stale too; zoom and laser follow.

- **Seen:** the assistant called `set_shutterconfig("Left")`. Core's state became Left; the main window's shutter box still showed Right three seconds later. Filter and laser, set in the same turn, did update, but only because the laser change refreshes the whole window.
- **Cause:** `Core.set_shutterconfig` (mesoSPIM_Core.py:650) emits only `sig_update_gui_from_shutter_state`; `Core.set_filter` (mesoSPIM_Core.py:487) emits only the state request. Its slot, `update_GUI_by_shutter_state` (mesoSPIM_MainWindow.py:710), reads the box to decide which ETL controls to enable; it never sets the box. Filter and laser look right only because the laser change triggers the waveform generator's full refresh (mesoSPIM_WaveFormGenerator.py:325).
- **Impact:** after a shutter change from the assistant or any TCP/MCP client, the box shows the wrong light sheet. The left or right ETL spin boxes for the wrong side stay enabled, so an operator could tune the wrong ETL. An acquisition row's shutter goes through the same setter but is followed by a full refresh, so runs are not affected.
- **Fix to consider:** make the shutter box follow Core's state, for example by refreshing it from state at the start of `update_GUI_by_shutter_state`. A refresh sent from the remote layer could run before the worker applies the queued state request, so the fix belongs in Core or the window. Needs a test in test_combobox_state_requests.py.
- **Status:** left open; it is upstream code and needs your decision.

### 2. Starting from the repo root crashes

Evidence and reproduction: [evidence/02-startup-from-repo-root](evidence/02-startup-from-repo-root/README.md).

- **Seen:** `python mesoSPIM/mesoSPIM_Control.py -D` from the repo root stops with `FileNotFoundError: 'gui/WebcamWindow.ui'`. Started from `mesoSPIM/`, it runs.
- **Cause:** WebcamWindow.py:29 loads its .ui relative to the working folder; mesoSPIM_Optimizer.py does the same. Other windows use `package_directory`. Present since upstream commit 61f2379 ("Webcam window always opens at startup").
- **Impact:** every OS. AGENTS.md line 37 and the test brief both give the repo-root command.
- **Fix to consider:** load both files from `package_directory`, as mesoSPIM_MainWindow.py:102 does; or correct AGENTS.md.
- **Status:** left open; upstream code.

## Gaps in the instructions

Five places where the test brief or the docs don't match the code; each cost time, none is a code bug. Evidence: [03](evidence/03-live-suite-environment/README.md) (live suite), [04](evidence/04-tcp-and-assistant-exclusive/README.md) (TCP and the assistant), [05](evidence/05-coordinate-box-locked/README.md) (coordinate box), [06](evidence/06-demo-folders/README.md) (demo folders).

| Topic | What the brief says | What the code does | Suggested change |
| --- | --- | --- | --- |
| Live suite settings | Set MESOSPIM_ALLOW_DEVICE_CHANGE and MESOSPIM_OPERATOR_PRESENT, then run run.py live tcp | All 8 tests skip silently with only those two | List the rest (below), or have run.py print what is missing |
| TCP and the assistant | Start TCP, then connect the assistant | Connect is refused while TCP or MCP runs: "Stop the Remote Control transport to use the AI Assistant." | Stop TCP before connecting |
| Coordinate system box | Change it while connected and watch the agent rebuild | The box is greyed out while connected | Disconnect, change, Connect; the new axes apply from the next turn |
| Demo folders | The demo config is usable as is | Snaps and acquisitions go to D:/tmp/, which does not exist on this machine | Use a config copy, or set the snap folder from the menu first |
| Starting the GUI | Run from the repo root | Crashes (bug 2) | Run from mesoSPIM/ |

The live suite also needs these, beyond the two in the brief:

- `MESOSPIM_CONFIRM_DEMO_MODE=1`, `MESOSPIM_RUN_ALL_COMMANDS=1`, `MESOSPIM_RUN_LIVE_ADVERSARIAL=1`
- `MESOSPIM_DEMO_ROOT` (the repo's mesoSPIM folder) and `MESOSPIM_DEMO_ETL_CONFIG_PATH` (`config\etl_parameters\ETL-parameters.csv` under it)
- `MESOSPIM_DEMO_PROCESS_ID`, the PID of the running demo mesoSPIM
- For TCP: `MESOSPIM_LIVE_TCP_PORT=42000` and `MESOSPIM_LIVE_TCP_TOKEN`; for MCP: `MESOSPIM_LIVE_MCP_URL` and `MESOSPIM_LIVE_MCP_TOKEN`

The live tests also write marker files to the system temp folder, so `TEMP`/`TMP` need to point inside MinicondaZMB on this machine.

## Windows-specific observations

Nothing Windows-specific broke the assistant; these are setup and tooling details for the next session on this machine.

| Area | Observation | What to do |
| --- | --- | --- |
| Environment | Python 3.12.13, PyQt5 5.15.11 (Qt 5.15.2), pydantic-ai 2.14.1, in the existing `mesospim` env | Package list before the install is in `home\logs\mesospim_env_before.txt` |
| Drivers | nidaqmx 1.0.1, PIPython 2.10.2.1 and pyvcam 2.2.4 all install without devices | None |
| Shells | PowerShell runs in Constrained Language Mode; `conda` is not on PATH in Git Bash | Use Bash or Python; call the env's python.exe directly |
| Line endings | A fresh clone shows docs/make.bat as changed (core.autocrlf=true) | Never commit it, as the repo rules say |
| Moving the repo | pytest's cached rewritten tests kept the old path, so it reported files under `C:\mesoSPIM-dev`, which no longer existed ([evidence 07](evidence/07-stale-bytecode-after-move/README.md)) | Clear `__pycache__` after moving a clone |
| Console encoding | The micro sign in "µm" shows as a replacement character in redirected output | Cosmetic; the app and the model see the right character |
| Folders | No `D:\tmp`; the user's rule is to keep everything inside `C:\ProgramData\MinicondaZMB` | Work in `home\` there: repo, logs, configs, probe (capped at 5 GB) and tmp |
| API key | GEMINI_API_KEY was not in the environment and was pasted into the chat | Set it at user level, and rotate this one |

## What the model did

Flash-lite stayed safe throughout: it never changed a value the operator hadn't approved, though it doesn't always pick the tool the test expects. Log lines and counts: [evidence/08-model-behaviour](evidence/08-model-behaviour/README.md).

- **Vague requests get a question, not a value.** "Make the laser a bit brighter" got "Which laser, and what intensity?" and no tool call. Only "choose the new intensity yourself" made it propose 20%, which the guard then put to Run/Cancel. Tests of the guard need prompts that make the model pick a number.
- **Schedule instead of wait.** "Wait 30 seconds, then tell me the x position" called `wait` 8 times out of 11 over three runs, and `schedule` with `in_seconds: 30` the other 3. Both end correctly on Stop and Disconnect, but only `wait` opens a continuation on the request line.
- **Measured values behave as designed in demo.** Off, "Centre the sample" ran look, a move of x +7.2 and y -1.4 um, and a second look; the move waited for Run, and after Cancel the model asked for the distances. On, the same move ran without a question and the model reported a remaining offset of x +6.4 um. The demo shows a grating, not a sample, so whether centring converges waits for the microscope.
- **Reports stay honest about failures.** Asked to look after a run while the snap folder was missing, it said the run finished, that the look failed because D:/tmp/ does not exist, and offered to change the folder. Moves past the stage limit were refused, and the reply named the limit.
- **Cost.** A plain turn used about 16,000 to 20,000 tokens; the first turn of the acquisition request used 32,000 to 44,000.

## Lessons for testing, and open decisions

Driving the real GUI from a script made step 2 repeatable and caught the shutter bug, which a person watching the screen could easily miss.

- **The driver.** `home\scripts\drive_gui.py` (on the lab machine, not in the repo) starts demo mesoSPIM in-process and works the Remote Control and AI Assistant tabs through their own widgets. It reads Core's state and the window after each step. `--serve TCP|MCP` keeps a demo running for the walkthrough and live suites; `--steps m` is the measured-values check.
- **Snap folder.** Core does not take `snap_folder` as a state request. Set it the way the main window's Choose snap folder does, by writing the window state and the indicator.
- **Assert on Core, not the screen.** "Request ended" is best read from the request object; Disconnect hides the whole chat window, so the request line's own visibility proves nothing.
- **Snapshots around a model run.** Comparing position and settings before and after the Gemini walkthrough confirmed it put the start state back.

Open decisions:

- [ ] Fix the shutter and filter boxes (bug 1) on agent/demo-fixes, or leave it for upstream
- [ ] Fix the cwd-relative .ui paths (bug 2), or correct AGENTS.md line 37
- [ ] Add the missing live-suite variables to the brief, or make run.py report them
- [ ] Rotate the Gemini key
