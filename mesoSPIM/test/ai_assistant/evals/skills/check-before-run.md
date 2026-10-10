---
name: check-before-run
description: Check one condition the operator names, and run the acquisition list only if it holds.
tools: look, run_acquisition_list
---
When: the operator says to check something (visible, focused, not saturated) and run only if it
is fine, or to stop and tell them otherwise. It is a check, not a fix.

1. look, asking about exactly the condition they named.
2. Decide from the frame's numbers: nothing visible is no sample above the background; saturated
   is saturated_fraction above 0.01; out of focus is a low focus_measure compared with a frame
   one step either side, so look at f ± 50 µm if the single frame does not decide it, then return.
3. If the condition fails, stop: change nothing, do not run, and tell them what you saw.
4. If it holds, run_acquisition_list.

Report what the check found and whether the run started.
