# 01. Shutter and filter boxes not refreshed after a remote change

**Kind:** bug, upstream Core / main window code (not the AI Assistant). Not Windows-specific.
**Status:** open.

## What was seen

When the shutter configuration or the filter is changed by the AI Assistant, or by any Remote
Control client over TCP or MCP, Core takes the new value but the main window's combo box keeps the
old one. For the shutter, the ETL controls of the wrong side stay enabled. Zoom and laser boxes do
follow, because their path refreshes the whole window.

The report first named only the shutter. The reproduction below shows the filter box is stale too:
in the driven GUI run the filter looked right only because the assistant set the laser after it,
and the laser path refreshes every box.

## Reproduce (no model, no key)

From any clone, with the mesoSPIM environment's Python:

    python docs/test-reports/evidence/01-shutter-and-filter-boxes-not-refreshed/repro_combo_boxes.py

It starts mesoSPIM in demo mode (refuses anything but DemoStage), starts Remote Control TCP from its
tab, sends `set_shutterconfig`, `set_filter`, `set_zoom` and `set_laser` one at a time as a TCP
client, waits for Core to take each value, pumps events for 3 s, and compares Core's state with the
box. Exit code 1 means the shutter box is stale (the bug); 0 means it follows Core (fixed).

Output on the test machine ([repro_output.txt](repro_output.txt)), 8 October 2026:

    start: Core shutterconfig='Right', box='Right'
    set_shutterconfig('Left') [processing]: Core='Left', box='Right', left ETL enabled=False, right ETL enabled=True -> STALE BOX
    set_filter('405-488-647-Tripleblock') [processing]: Core='405-488-647-Tripleblock', box='Empty' -> STALE BOX
    set_zoom('1x') [processing]: Core='1x', box='1x' -> OK
    set_laser('405 nm') [processing]: Core='405 nm', box='405 nm' -> OK
    stale boxes: ['shutterconfig', 'filter']
    REPRODUCED: the shutter box does not follow Core

| Setting | Box follows a remote change | Why |
| --- | --- | --- |
| shutterconfig | No | No full refresh on this path |
| filter | No | No full refresh on this path |
| zoom | Yes | ETL update from zoom ends in a full refresh |
| laser | Yes | ETL update from laser ends in a full refresh |

## First seen with the model

Driven GUI run, step 2g, [../run-logs/step2_run1.log](../run-logs/step2_run1.log) lines 155 to 182.
The assistant (gemini-3.5-flash-lite) set filter, laser and shutter in one turn:

    tool: set_filter({"filter": "405-488-647-Tripleblock"})
    tool: set_laser({"laser": "405 nm"})
    tool: set_shutterconfig({"shutterconfig": "Left"})
    FAIL g: ... state ['405-488-647-Tripleblock', '405 nm', 'Left'], boxes ['405-488-647-Tripleblock', '405 nm', 'Right'], echoes []

Nothing was sent back to Core ("echoes []"): the separate fix that stops the boxes echoing Core's
own changes (test_combobox_state_requests.py) holds. This is the opposite direction: Core's change
does not reach the box.

## Cause (code at 41839f2)

- `mesoSPIM_Core.set_shutterconfig` (mesoSPIM/src/mesoSPIM_Core.py:650) emits the state request and
  `sig_update_gui_from_shutter_state` only.
- That signal's slot, `mesoSPIM_MainWindow.update_GUI_by_shutter_state`
  (mesoSPIM/src/mesoSPIM_MainWindow.py:710), reads `ShutterComboBox.currentText()` to choose which
  ETL controls to enable. It never sets the box, so it enables the side the stale box shows.
- `mesoSPIM_Core.set_filter` (mesoSPIM_Core.py:487) emits the state request only.
- Zoom and laser go through `update_etl_parameters_from_csv`
  (mesoSPIM/src/mesoSPIM_WaveFormGenerator.py:281), which ends with `sig_update_gui_from_state`
  (line 325): the full refresh in `update_gui_from_state` (mesoSPIM_MainWindow.py:809).
- The operator's own changes are not affected: the box already shows what they chose.

## Impact

- The box shows a light sheet or filter that is not in use.
- With the shutter stale, the left/right ETL offset, amplitude and zero controls of the wrong side are
  enabled, so an operator could tune the ETL of the side that is not illuminated.
- Any later full refresh (a zoom or laser change, the end of an acquisition, Stop) corrects the boxes.

## Fix to consider

Make the window follow Core's state after these changes, without echoing them back. Options:

1. In `update_GUI_by_shutter_state`, set the box from `self.state['shutterconfig']` first, inside
   `showing_state` like `update_gui_from_state` does, then enable the ETL controls from the state,
   not from the box. For the filter, a matching refresh after the filter request.
2. In Core, emit `sig_update_gui_from_state` after the state request for filter and shutter has been
   applied. A refresh emitted before the worker applies the queued request would show the old value,
   so it must come after (the remote layer cannot guarantee that ordering).

Add the reproduction's checks to `mesoSPIM/test/test_combobox_state_requests.py` (offscreen, no
hardware) so the fix stays fixed, and rerun this script: it must exit 0 and report no stale boxes.
