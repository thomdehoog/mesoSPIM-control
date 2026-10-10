---
name: acquisition-with-checks
description: Centre and focus the sample, fixing what is off, and then run the acquisition list.
tools: look, move_relative, run_acquisition_list
---
When: the operator wants the sample checked or prepared (centred, in focus) and then acquired.

1. look, asking "where is the sample, and is it in focus?". If no sample is visible, say so and
   do not run.
2. Centre: move_relative by the frame's centre_move_um, x and y, then look; repeat until offset_px
   is under about a tenth of the frame's width, at most three moves.
3. Focus: step f by 50 µm in the direction where focus_measure rises, look after each step, halve
   the step when it falls, stop below 10 µm or after eight looks, and return to the best f.
4. If the acquisition list is empty in the readout, ask what to acquire instead of running.
5. run_acquisition_list.

Report the centring and focus results and that the run started.
