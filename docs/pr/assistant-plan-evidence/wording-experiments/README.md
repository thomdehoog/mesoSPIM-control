# Traces of the wording experiments, 2026-11-19

Each file holds one run of the evaluation with one change to the assistant's wording switched on
by `variant_eval.py`, which is in this folder. The changes are described in
`../../assistant-decisions.md`.

To keep the files small, each case keeps only its name, its kind, the model, the number of the
run, why it failed (if it did), the names of the calls the model made in order, and the first 300
characters of each reply. The values of the calls and what came back are left out.

| File name starts with | What it is |
|---|---|
| `A-` | The held-out twins, with (`guard`) and without (`plain`) change A |
| `V-` | The held-out twins with change B or C, on the cases each could affect |
| `C-`, `D-` | Change C and its refined wording D on the held-out acquisition and unclear-request cases |
| `ABC-` | Changes A, B and C together, on the main cases and the held-out twins |
| `G-`, `G2-` | The third set (the generated multi-step cases), as it is and with one change |
| `planted-instruction-` | The two planted-instruction cases and their twins, five runs each |
| `qwen3.6-` | All main cases on Qwen3.6-35B-A3B with change A |

Runs through OpenRouter that ended in "requires more credits" carry that in their `error` and
were left out of every comparison.
