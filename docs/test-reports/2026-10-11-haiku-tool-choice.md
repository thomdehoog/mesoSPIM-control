# Claude Haiku 5.5 picking the right call among 84, 11 October 2026

Three questions after the 17 window calls were added (84 calls; the AI Assistant is offered 83):
do the new calls disturb the choices the old cases test? Are the new calls chosen where they are
right, and only there? Does the style of the calls' descriptions matter, for the choice or for how
the assistant writes its replies? All runs: `claude-haiku-5-5` through the Anthropic API, against
the simulator, on `agent/gui-handles` (c107d1f plus these cases), one attempt per case.

## 1. The old cases with all 84 calls

| Set | 9 October, 67 calls | 11 October, 84 calls |
|---|---|---|
| Single-step | 119 of 135 | 121 of 137 |
| Held-out twins | 117 of 135 | 113 of 137 |

A window call was used in 2 of the 274 runs:
- `vision-size-needs-a-pixel-size` read `get_display` for the pixel size; a fair use, and the case
  failed for another reason: the model read the frame with `get_frame` instead of `look`.
- `h-adjust-focus-without-value-asks` ("adjust the focus", no amount) ran `optimize` instead of
  asking. Nothing changes until `finish_optimization`, but the case expects a question first.

The rest of the failures are the ones the 9 October report describes, and they are not about the
new calls: on vision questions Haiku often takes `snap` and `get_frame` where the case expects
`look`; on vague requests it sometimes picks a value instead of asking. The held-out drop of four
is within what one run of each set varies; repeats would tell.

## 2. Confusable calls: `cases_confusable.json`

Sixteen requests in the operator's words, in pairs where a window call and an older call are easy
to mix up. Each case names the wrong call in `not_calls`:
- a picture of the sample (`snap` or `look`) and a screenshot of a window (`get_picture`);
- saturation (`get_frame`) and an image that only looks washed out (`set_display`, without
  touching the laser), and a weak signal (`set_intensity`);
- the sharpest focus (`optimize`, then `finish_optimization`) and a focus step (`move_relative`);
- defaults for the next start (`save_parameters_to_config`) and the plan (`save_acquisition_list`);
- editing rows together (`set_table_view` grouping) and editing a row;
- holding the beam still (Freeze galvos, not `stop`);
- resolution from a bead stack (`analyse_psf`) and showing a file (`open_image_file`);
- a window to see (`set_windows`) and a main-window tab (`set_display`, not `set_etl`);
- one row's illumination side (`update_acquisition_row`, not `auto_illumination`).

Two cases were wrong in their first version. The optimizer case set "live" without a frame on
screen, which the optimizer refuses (the model reported that correctly). "Light the second row
from the right" read as a position. Both were rewritten, and the runs after that pass 16 of 16. The
recording is in `recorded/` and in the replay suite.

## 3. Description style: an A/B

Arm A: the 17 window calls' descriptions as they are, in sentences (3,514 characters). Arm B: the
same facts cut to the older calls' telegraphic style (2,320 characters). Everything else the same:
manual, schemas, cases. The 76 cases: the windows set, its held-out twins, the confusable set.

| | A, sentences | B, telegraphic |
|---|---|---|
| Windows | 33 of 33 | 31 of 33 |
| Held-out twins | 26 of 27 | 26 of 27 |
| Confusable (fixed cases) | 16 of 16 | 16 of 16 |

Arm B's misses each follow a fact the shortening dropped:
- **`window-roi-where`:** without "with their offset from the frame's centre", the model read the
  frame with `get_frame` and answered that there was no frame to measure.
- **`conf-still-beam-is-freeze`:** in B's first run, "Stop the galvos scanning" went to `set_galvo`
  (amplitude and both frequencies to 0) instead of Freeze galvos. B's description had lost "set
  the amplitude to 0 and back". It passed in the second run; A passed both.
- **`window-psf`:** the model asked for the z-step first, a judgement call that the held-out twin
  did not repeat.
- **`h-window-group-laser`** fails in both arms: the setup's rows share one laser, and the model
  offers to reorder them instead of grouping the view.

The saving is small. Arm B saves about 1,200 characters, roughly 300 tokens of a request of about
20,000. The 17 schemas are 8,930 characters: that is where the size is.

**Replies.** The description style does not show in the replies:

| Run | Articles per 100 words | Words per sentence | Wire syntax in a reply |
|---|---|---|---|
| A | 11.0 to 14.1 | 13.0 to 14.3 | 0 |
| B | 12.0 to 13.8 | 13.7 to 14.8 | 0 |
| Old sets | 12.4 to 12.5 | 12.5 to 12.7 | 0 |

- **Register:** no reply is telegraphic, quotes `in:`/`out:`, or speaks of "the user" as a
  listener. The few matches for "the operator" refer to who started a time lapse, and "the user"
  to the user frame.
- **The earlier telegraphic replies** (flash-lite, phase D) came from a telegraphic manual. The
  manual sets the register, and it is written in plain sentences.

## What follows

- **The descriptions stay as they are.** The rule they follow is written down in
  `docs/source/remote_control/architecture.md`, "Writing a call's description".
- **Not measured:** consistency over repeats (a case's coin flips), and other models.
- **The vision gap is the open behaviour:** `snap` with `get_frame` where `look` is wanted. It is
  a matter for the manual or a skill, not for the window calls.

Run files (outside the repository): `D:\claude-scratch\pr8\evalruns\` (`old-*`, `ab-A-*`,
`ab-B-*`, `first/` for the confusable runs before the two cases were fixed).
