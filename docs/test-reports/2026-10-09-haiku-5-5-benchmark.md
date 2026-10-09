# Claude Haiku 5.5 on the AI Assistant's cases, 9 October 2026

The five case sets run once each on `claude-haiku-5-5` through the Anthropic API, against the
simulator, on the code of PR 121 (`agent/haiku-pr`, b699b4c: the four fixes below) with the dev
suite of `agent/schedule-display` (79d18d1). Compared with the recordings of
`gemini-3.5-flash-lite` in `evals/recorded/`, which were made on the code of phase D and E.

## What Haiku needed first

Four fixes, each found by a failing run and checked against the live API; all in PR 121.

1. Haiku 5.5 refuses temperature 0 ("`temperature` is deprecated for this model"): the first
   message failed. Models in `MODELS_WITHOUT_TEMPERATURE` get no temperature.
2. The eyes take old images out of their history; Anthropic then refused every later request
   ("Invalid `signature` in `thinking` block"). The answer's thinking goes with its image.
3. Anthropic refuses a tool argument name with a percent sign (`camera_delay_%`): every turn in
   the Full tool set failed. Offered as `camera_delay_pct`, given back under its own name.
4. The same signature refusal when the memory changes as a turn starts (an older turn compacted
   or trimmed off): a session died on its fourth message. From the first changed message on,
   the replies lose their thinking and keep their text and calls.

## Pass rates

| Set | Cases | Haiku 5.5 | flash-lite, recorded |
|---|---|---|---|
| Smoke, multi-step | 54 | 26 | 26 |
| Single-step | 158 | 149 | 158 |
| Held-out single-step | 158 | 150 | 157 |
| Generated, multi-step | 180 | 82 | 91 |
| Unattended, measured values on | 18 | 12 | 12 |

Haiku: one attempt per case. The flash-lite recordings of the held-out and unattended sets kept
a passing run out of up to three attempts; the other three sets were one attempt.

Generated cases by group, 20 each: vague requests Haiku 18 / flash-lite 11; must stop partway
12 / 13; recovery from refusals 20 / 20; centring 16 / 17; live tuning 10 / 11; exposure 5 / 12;
focusing 1 / 7; acquisition with checks 0 / 0; time lapse by schedule 0 / 0.

Haiku asks when a request is vague and recovers from every refusal; it also reports honestly
what it has not checked. On exposure and focusing it takes one step, says what it saw and
stops, where flash-lite keeps adjusting. The two long-task groups fail on both: evidence for
phase H, as before. Haiku's single-step misses repeat in the held-out twins (read-capabilities,
move-half-mm, schedule-at-a-time, vision-centre-with-a-convention, ask without a frame, recall
of a lost readout): consistent behaviour, not noise.

## Speed, 2,088 requests

| | median | 90th percentile | worst |
|---|---|---|---|
| Time to first token | 0.72 s | 0.94 s | 5.8 s |
| Whole request | 1.84 s | 3.56 s | 6.9 s |

Output about 216 tokens a second; a request carries about 12,300 input tokens and returns about
250. The whole run sent 21.5 million input tokens and received 0.62 million.

Measured by streaming each request through a wrapper outside the repository (the agent itself
is unchanged); flash-lite's recordings carry no timings.
