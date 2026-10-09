# Remote Control: the acquisition manager's features as calls

Branch `agent/gui-pr`, on `agent/lean-pr` (the AI Assistant's lean package, which it needs: the
assistant's own row tool is removed here in favour of the registered call). Eight commits: five
lifts in `utils/`, each its own commit so it can be taken or dropped alone, then the calls, the
live sweep for them, and the writer readout.

## Summary

- **One way in.** The acquisition manager's buttons and wizards become eleven Remote Control calls,
  registered like the other 56 (67 in all): the same schema check, the same `accept`, the same busy
  gate, the same execute path on Core's thread, reachable by TCP, MCP and the AI Assistant alike.
  - `update_acquisition_row`, `add_acquisition_rows`, `delete_acquisition_rows`,
    `move_acquisition_row`, `mark_acquisition_rows` (the six "mark current" buttons, by `marks`)
  - `save_acquisition_list`, `load_acquisition_list` (CSV, as the window writes and reads it)
  - `name_acquisition_rows` (the filename wizard), `track_focus` (the focus tracking wizard),
    `build_tiling_list` (the tiling wizard)
  - `set_snap_folder`
- **No path of their own.** Every table edit builds the whole new list and hands it to
  `set_acquisition_list`'s own accept and install: the same row checks (limits, options, plane
  count, writer and file name), the same bridge to the manager window.
- **One implementation.** The wizards' arithmetic and the table model's CSV and "mark current"
  code move into `utils/` as plain functions; the wizards and the model call them, and so do the
  calls. Moved, not changed: the filename rule was checked equal to the wizard's output over 256
  combinations.
- **A reader for each choice.** `get_config` now lists the image writers with their extensions, and
  a call given an unknown writer names the ones there are.
- **Checked, not promised.** `test_window_coverage.py` parses both `.ui` files and maps every control
  to its call, "display", or "not exposed" with the reason; a new button upstream fails it. The
  two windows reach 39 distinct calls. Five features are left for the owner: auto illumination, the
  image processing chain, "Save to config", the two zero-ETL toggles and freeze galvo.

## Footprint

`git diff --stat agent/lean-pr agent/gui-pr -- mesoSPIM/src/mesoSPIM_Core.py
mesoSPIM/src/mesoSPIM_MainWindow.py mesoSPIM/src/mesoSPIM_State.py
mesoSPIM/src/mesoSPIM_AcquisitionManagerWindow.py mesoSPIM/src/utils/`:

```
 mesoSPIM/src/utils/acquisitions.py                 | 138 +++++++++++++++++++++
 mesoSPIM/src/utils/filename_wizard.py              |  76 +-----------
 mesoSPIM/src/utils/focus_tracking_wizard.py        |   8 +-
 mesoSPIM/src/utils/models.py                       |  38 +-----
 .../src/utils/multicolor_acquisition_builder.py    |  18 +++
 .../src/utils/multicolor_acquisition_wizard.py     |  17 +--
 mesoSPIM/src/utils/utility_functions.py            |  15 +--
 7 files changed, 173 insertions(+), 137 deletions(-)
```

Core, the main window and the acquisition manager window: 0 lines. `utils/` gains the lifted
functions and loses the bodies they replace. The rest: the calls in `remote_control/` (420
lines), the AI Assistant's own row tool removed (42 lines) and two lines of its config, the calls
page, and the live sweep with its contracts.

## Validation

- **Offline.** The dev suite passes (627, with 10 tests of the table calls and 4 of the coverage
  map). `test/test_tiling.py` cannot run on either side: it builds the wizard without the parent
  window it now needs. Its five fixtures, given to the lifted `image_counts`, all give the
  expected counts.
- **Demo, Linux.** The all-commands live sweep, with a fresh two-row table before each table call
  and the table read back after it: 67 of 67 over TCP and over MCP, the demo's state restored. Run
  offscreen with the three Windows-only imports stubbed in a scratch launcher.
- **Assistant.** Two cases and their held-out twins (a tiling list, a table round trip): 12 of 12
  on gemini-3.5-flash-lite.
- **Package branch.** On `agent/gui-pr` itself, the real-Qt scripts pass (67 commands) and
  `run.py live tcp` against a demo started from it: 2 passed, 67 calls verified.
- **Please check on Windows** (demo mode): `python mesoSPIM/test/remote_control/run.py live tcp`
  and `live mcp`; then with the acquisition manager open, each table call by TCP shows in the
  table, and each wizard still produces the rows it did.
- **On the microscope.** One tiling list built by call and run.
- **Found, not fixed (upstream Core).** `get_free_disk_space` calls `os.statvfs('')` off Windows, so a
  time lapse cannot start on Linux or macOS.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01RH8VsZbnUgYcVt3KuXjNFk
