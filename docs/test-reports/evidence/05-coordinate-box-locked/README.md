# 05. The Coordinate system box is locked while connected

**Kind:** gap in the test brief; the behaviour is by design. **Status:** brief to correct.

## What was seen

Step 2c of the brief says: change the coordinate system box and check the agent is rebuilt and the
prompt follows. While the assistant is connected the x, y and z boxes are disabled; the setup is
applied by Connect, so a change means Disconnect, change, Connect.

From the driven GUI run with the model ([../run-logs/step2_run1.log](../run-logs/step2_run1.log),
lines 43 to 55):

    PASS c: the box is locked while connected: enabled=False
    PASS c: the worker holds the new axes and the prompt follows: worker x=left, prompt says 'toward the left': True
    PASS c: the agent was rebuilt with them and the model answers by them: agent axes x=left, reply 'left'
    PASS c: put back: x=right

Asked "which way does a positive x move carry the sample?", gemini-3.5-flash-lite answered "left"
after x was set to left.

## Reproduce (no model, no key)

    python docs/test-reports/evidence/05-coordinate-box-locked/repro_axes_box.py

Output on the test machine ([repro_output.txt](repro_output.txt)):

    connected=True; axis boxes enabled while connected: {'x': False, 'y': False, 'z': False}
    after Disconnect, axis boxes enabled: True
    x changed 'right' -> 'left'; after Connect: worker.axes['x']='left', prompt says 'toward the left of the image': True
    AS DOCUMENTED: locked while connected; a change made while disconnected reaches the worker and the prompt

## Where it is decided (code at 41839f2)

- `_set_setup_enabled` (mesoSPIM/src/ai_assistant/gui.py:608) disables the language and vision
  pickers, the preferences and the axis boxes unless the tab is idle: "Disconnect first to change it".
- `_release_session` (gui.py:650) drops the worker on Disconnect; Connect builds a new one
  (`_ensure_worker`, gui.py:397), whose agent is built on its first turn with the axes then chosen.

## Observation for the code

`AssistantWorker._run_one` rebuilds the agent when `self.axes` differs from the axes it was built
with (mesoSPIM/src/ai_assistant/assistant.py:1901). From the GUI that never happens: the boxes can
change only while there is no worker, and a new worker has no agent yet. The check is harmless, and
it matters if the box is ever unlocked while connected, but "the agent is rebuilt" in step 2c holds
by construction rather than through that check.

## Suggested change

In the brief, step 2c: "Disconnect, change the coordinate system box, Connect, and ask which way a
positive x move carries the sample." No code change.
