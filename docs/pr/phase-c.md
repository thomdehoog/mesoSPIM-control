# AI Assistant: frame history, look over frames, the map and calibrate

Branch `agent/c-pr`, on top of `agent/b-pr`. One commit: `ai_assistant/` (a new `frames.py`,
`assistant.py`, `config.py`, `manual.md`), `remote_control/` (`get_frame` gets `array_side`) and
`.gitignore`.

## Summary

- **The frame history.** Every frame a look, a snap or live delivers is kept for the session as a
  small 16-bit copy, with its number, time, source, position, settings and an optional label. Code
  adds its measures, among them the offset from the centre and the stage move that would centre the
  sample. The scale is nominal, from the pixel size and the coordinate system, until calibrated.
- **`look` over chosen frames.** `look` takes `frames` ("last 3", "1,7", "3-10") and a `label`, and
  compares the frames it shows: image shift by phase correlation, focus and peak. The eyes get
  exactly those frames and keep only text afterwards, so they no longer carry eight images per
  request.
- **The map.** The readout carries the history in brief and a map derived from it. Per zoom and
  light, the map gives where the frames put the sample, its best focus from the focus curve, the
  last good light, labelled places, and the frames left out.
- **`calibrate`.** It is confirm-first, one Run for the block. It measures how the image moves with
  the stage at a zoom, and keeps the result beside the configuration in a git-ignored file. Centring
  moves then use the calibrated scale.
- **`get_frame`.** The new argument `array_side` returns the frame as 16-bit values, binned to at
  most that side. The assistant's tools don't offer it to the model.

## Validation

| Check | Result |
|---|---|
| Map on the simulator | best focus within 20 µm, sample within 50 µm of the truth |
| `calibrate` with the coordinate system set wrong | measured direction corrects the centring move |
| 316 recorded cases, replayed | every score unchanged, +1.2% estimated tokens |
| Centring cases on flash-lite | 0 of 6 before, 6 of 6 after |

All ten real-PyQt scripts pass. Demo mode and the microscope have not been run yet. Please try
`calibrate` on the instrument: it moves x and y by a tenth of the field and back.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01RH8VsZbnUgYcVt3KuXjNFk
