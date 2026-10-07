# Remote Control: a focus measure that peaks at the sharp frame

Branch `agent/b-pr` of thomdehoog/mesoSPIM-control. It stacks on `agent/a-pr`, so send it after
phase A. One commit, one file: `mesoSPIM/src/remote_control/frame.py`.

## Summary

`get_frame` and `look` report a focus measure that a model uses to judge focus. On simulated focus
series it read highest far from focus: on a dim, blurred frame, camera noise dominated the
Laplacian variance, and dividing by the frame's range squared made it larger still. A single hot
pixel flattened it as well.

The measure is now the Laplacian's energy over the squared signal above the background:

- **Hot pixels and saturation.** The brightest 0.1% of pixels is clipped before anything else, so
  a hot pixel or a saturated patch does not decide the value.
- **Noise.** The frame is binned to about 512 pixels, and the noise's share, estimated from the
  pixel-to-pixel differences, is taken off. A dim frame far from focus no longer reads sharp.
- **Smooth structure.** The second derivative answers to detail, not to a smooth body or a
  gradient in the background.
- **Light.** The value is the same at any intensity or exposure.

A frame whose detail is below the noise now reads 0, and so does a frame of pure noise.

## Validation

- **Simulated series** across ±300 µm, dim, saturated, at 2x, with a hot pixel and with a large
  sample: the measure peaks at the sharp frame in every series.

| Measure | Peak above the frames 100 µm or more away |
|---|---|
| Before | 1 to 2 times, mostly at the edge of the series |
| After | 58 to 472 times |

- **Real PyQt.** All ten scripts of `run.py pyqt` pass offscreen on this branch.
- **Not run yet.** A real focus series from the microscope. The fork has a check for it:

```
python -m mesoSPIM.test.ai_assistant.evals.focus_check FOLDER --sharp F
```

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01RH8VsZbnUgYcVt3KuXjNFk
