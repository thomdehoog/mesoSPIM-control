---
name: centring
description: Bring the sample to the middle of the camera image.
tools: look, calibrate, move_relative
---
When: the operator wants the sample centred, or says it is off to one side or not in the middle.

1. look, asking "where is the sample in the image?". If no sample is visible, say so and stop.
2. If the frame's centre_move_um is marked nominal and calibrate is offered, run calibrate once.
3. move_relative by the frame's centre_move_um, x and y together.
4. look again. Done when offset_px is under about a tenth of the frame's width in both directions.
   Otherwise repeat steps 3 and 4, at most three moves in all.

Report where the sample started, the moves made and where it ended. If it did not get there,
say what offset remained.
