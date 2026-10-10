---
name: exposure
description: Set the laser intensity so the sample is bright but not saturated, in live or not.
tools: start_live, look, set_intensity
---
When: the operator wants a good exposure, says the image is too dim or saturated, or asks to tune
the intensity in live.

1. If they asked for live, call start_live first unless live is already running, and leave it
   running at the end.
2. look, asking "is the sample well exposed?". Judge from the numbers, not the picture.
3. If saturated_fraction is above 0.01, halve the intensity. If max is below a tenth of
   full_scale, double it (at most 100). Otherwise it is done.
4. After each change, look again. At most four changes.

Report the intensity you started and ended at, and the saturated_fraction and max of the last frame.
