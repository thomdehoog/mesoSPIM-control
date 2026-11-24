# The AI Assistant: where to find what

The plan for finishing the AI Assistant was one long file. On 2026-11-19 it was split, because a
reader could no longer tell what was built, what was decided and what was only an idea.

| File | What it holds |
|---|---|
| `assistant-built.md` | What exists on this branch, how it was checked, and what is not done. |
| `assistant-decisions.md` | The rules that were agreed, what is agreed but not built, the questions still open, and the order suggested for the next steps. |
| `assistant-local-models.md` | Research on running a model at the facility: the idea of a core set of tools and a catalogue, which models to try, the test, the first results for Gemma 4 26B, how Inkling 2 Mini could be run, a third candidate that fits on the card whole (SenseNova Loop-9B), and an idea for later: training a small model on our own examples. Nothing in it is built. |
| `assistant-model-comparison.md` | Nine models on the whole evaluation of 137 cases: the four Claude models, Gemma 4 26B, two Qwen models, Ministral 14B and, added on 2026-11-21, Inkling 2 Mini. Which local model is good enough, and which misses point at the assistant itself. |
| `assistant-plan-review.md` | A critical review of the plan as it stood before the split. |
| `assistant-plan-evidence/` | The traces of the evaluation runs on Gemma 4 26B, and the script of the experiment. |

**The next step is to try the built assistant in mesoSPIM's demo mode on the real computer.**
Nothing has been run there yet.

The plan as it stood before the split is in the history of this file (commit `113ce91` and the
ones after it up to the split).
