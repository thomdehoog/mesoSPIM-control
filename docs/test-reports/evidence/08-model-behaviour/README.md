# 08. What the model did: inputs for test design

**Kind:** model behaviour (gemini-3.5-flash-lite), recorded for designing tests; no bug.
**Status:** for the record. Reproducing these needs the model: `tools/drive_gui.py` with
`GEMINI_API_KEY` set; the commands below are run from `docs/test-reports/evidence/`. The model's choices vary between runs; the counts below are what these runs gave.

## A. A vague request gets a question, not a value

The guard's Run / Cancel question for "a value the operator did not give" only appears if the model
picks a value. A vague request made it ask instead:

| Run | Prompt | Model did | Run / Cancel |
| --- | --- | --- | --- |
| [step2_run1.log](../run-logs/step2_run1.log) lines 36 to 40 | "Make the laser a bit brighter." | No tool; replied "Which laser would you like to use, and what intensity setting would you prefer?" | Not asked |
| [step2_run2.log](../run-logs/step2_run2.log) lines 24 to 30 | "Make the laser a bit brighter; choose the new intensity yourself." | `set_intensity {"intensity": 20}` | Asked; Cancel kept intensity 10 |

Reproduce: `python tools/drive_gui.py --steps b` (uses the second prompt).
For tests of the guard: phrase the request so the model must pick the number.

## B. "Wait N seconds" is usually `wait`, sometimes `schedule`

Prompt "Wait 30 seconds, then tell me the x position." across the three driven runs:

| Run | `wait` | `schedule` |
| --- | --- | --- |
| [step2_run1.log](../run-logs/step2_run1.log) (lines 99 to 133) | 3 | 1 |
| [step2_run2.log](../run-logs/step2_run2.log) | 3 | 0 |
| [step2_run3.log](../run-logs/step2_run3.log) | 2 | 2 |
| Total | 8 of 11 | 3 of 11 |

The schedule calls were `schedule({"name": "tell_x_position", "instruction": "tell me the x position", "in_seconds": 30})`.
"Use the wait tool to wait 30 seconds, then tell me the x position." gave `wait` both times it was used
(step2_run3.log). Both end correctly on Stop microscope and Disconnect, but only `wait` opens a
continuation on the request line, so step 2e's check needs `wait`; the driver retries with the second
prompt. Reproduce: `python tools/drive_gui.py --steps e`.

## C. Measured values (E1) off and on

"Centre the sample." with an operator who cancels every question, on the demo's synthetic grating:

| Setting | Tools | Run / Cancel | Stage | Reply | Tokens |
| --- | --- | --- | --- | --- | --- |
| Off, demo_config.py ([step4_off.log](../run-logs/step4_off.log)) | look, move_relative x +7.2 y -1.4, look | Asked for the move; Cancel | Not moved | "To centre the sample, how many micrometres would you like me to move on the x and y axes?" | 33,602 |
| On, tools/demo_config_measured.py ([step4_on.log](../run-logs/step4_on.log)) | look, move_relative x +7.2 y -1.4, look | None | Moved x +7.2, y -1.4 um | Reports the move and a remaining offset of x +6.4, y -0.4 um | 34,487 |

The setting was read from the config each time (`worker.measured_values` False, then True) and the
"# Measured values" paragraph was in the system prompt only when on (10,965 against 11,488
characters). The demo has no sample, so whether centring converges waits for the microscope.
Reproduce: `python tools/drive_gui.py --steps m` and
`python tools/drive_gui.py --config <clone>/docs/test-reports/evidence/tools/demo_config_measured.py --steps m`
(an absolute path: the driver changes into `mesoSPIM/` before it reads `--config`).

## D. Failures reported honestly

- Missing snap folder: "The acquisition finished, but the look failed because the snap folder
  'D:/tmp/' does not exist ... Would you like to create that folder or change it to an existing one?"
  ([step2_run1.log](../run-logs/step2_run1.log) line 86).
- A move past the stage limit: refused, and the reply named the range
  ([step3_gemini.log](../run-logs/step3_gemini.log), the "Move X to 30000 um" step).

## E. Tokens per turn

From the request line in [step2_run1.log](../run-logs/step2_run1.log): a plain one-tool turn used
15,859 to 17,412 tokens; the acquisition request's first turn 32,358, and 50,527 after its
continuation (2 turns). The centring turns of C used about 34,000.
