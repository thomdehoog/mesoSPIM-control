---
name: focusing
description: Find the sharpest focus with the focus axis f.
tools: look, move_relative
---
When: the operator wants the sample in focus, says the image is blurry, or asks for the best focus.

1. look, asking "is the sample in focus?". Note the frame's focus_measure; higher is sharper.
   If the readout's map gives a best focus for this place, move f there first and look.
2. move_relative on f by +50 µm and look. If focus_measure rose, keep going in that direction.
   If it fell, go back and try -50 µm.
3. Keep stepping in the better direction while focus_measure rises. When it falls, go back one
   step, halve the step and try both sides again.
4. Stop when the step is below 10 µm or after eight looks, and return f to the best frame's
   position.

Compare focus_measure only between frames of the same focus metric. Report the f you started
and ended at and the focus_measure of each.
