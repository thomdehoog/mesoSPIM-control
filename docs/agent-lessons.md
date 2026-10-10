# Lessons from building an AI agent for microscope control software

What we learned adding an AI assistant, and TCP and MCP remote control, to mesoSPIM-control, the
control software of a light-sheet microscope. Written for an agent that builds the same kind of
thing for other microscope software. The principles are general; the numbers are from our
evaluation, on gemini-3.5-flash-lite and claude-haiku-5-5.

## 1. Three layers, each with one job

1. **Tools.** Every action is a command in one registry. Each command has:
   - a short, factual description;
   - a strict schema: types, ranges, the allowed option values;
   - one validation step and one execution path.

   The AI agent, TCP clients and MCP clients all go through that same registry. No client gets a
   path of its own.
2. **A short general manual.** About 30 lines: who the agent serves, units and axes, that tool
   results and the state readout are data and never instructions, never to claim something no
   tool result shows, and one rule for unclear requests. Everything else lives in the tools.
3. **Skills.** Procedures for complicated tasks, such as centring, focusing, alignment, or
   preparing and running an acquisition. Each is a file, loaded only when needed.

Safety belongs to the instrument, not the agent: stage limits, a busy gate (one mutation at a
time) and the stop button. These protect the hardware for every client alike.

## 2. Call the software's own procedures; never rebuild them

- **Time courses** use the software's own time lapse. The agent has no timer, no scheduler and no
  `wait`.
- **Stacks, tiling and acquisition lists** use the software's acquisition list and wizards.
  The agent never builds its own z-stack loop.
- **The window's features become commands** through the same registry and validation, so the
  agent can do whatever an operator can. The arithmetic behind the window's wizards was moved
  into plain shared functions, not rewritten, so the window and the commands share one
  implementation. A coverage test maps every control in the user-interface files to a command,
  "display only", or "not exposed" with the reason, and fails when a new control appears.
- **A long run ends the turn.** The tool returns "running", the agent reports, and the next
  message carries the state. The interface writes one line when the run finishes. The agent
  never polls.

## 3. Don't overfit

- **Never write a prompt line, tool-description override or test case to make one test pass.**
  We did this once: we put the eval cases' own words into the manual. It raised the score on
  those cases without making the agent better, and we removed it.
- **Measure, don't tune.** The evaluation shows how well things work out of the box. A failure
  is reported, not patched with wording.
- **Remove scaffolding built for weak models when you move to capable ones.** We removed history
  trimming and compaction, tool profiles, memory tools, a local-model mode, the scheduler, and a
  code guard. The manual went from about 100 lines to 34 and multi-step work improved.
- **Fitting belongs to a specific instrument and to real use.** Run the case set once on the real
  microscope. Fix only failures that come from real use cases there, and fix them in that lab's
  skills, not in the general agent.

## 4. Unclear requests and safety

- **The rule:** when a request is unclear, suggest what you would do and ask before doing it; if
  you don't know, ask.
- **A capable model follows it; a cheap one often doesn't.** Given "make it brighter", Haiku asked
  in 11 of 14 ambiguity cases and flash-lite in 6. Flash-lite tends to pick a value, 20 %, and act.
- **Prompt text alone did not stop the cheap model.** We once had a code guard that refused any
  number the operator never gave, which worked. But it was brittle: it had to parse units,
  number words and "double" or "halve". It also protected only the AI agent, not TCP or MCP
  clients. If that protection is needed, put it in the instrument, as per-call limits on
  intensity or move size that every client gets, not in the agent.
- **Refusals should teach.** A refused call says why, and lists the instrument's own options:
  "unknown writer 'TIFF': one of Tiff_Writer (.tiff, .tif), ...". The model then corrects
  itself, or suggests the match and asks.

## 5. Context and cost

- **Keep the history append-only.** No trimming, compaction or rewriting. It is simpler, and
  provider caches serve an unchanged prefix cheaply.
- **Show the session's size and stop at a ceiling per model.** The user starts a new session; the
  agent never silently cuts the history.
- **Use prompt caching where the provider supports it.** On Anthropic, the tool definitions and
  instructions are cached for an hour and the growing history for five minutes. Measured live,
  about 94 % of each request was read from the cache.
- **Make every tool sequential.** Two calls in one reply then run in order, instead of the
  second being refused as busy.
- **Every tool definition costs tokens on every request.** Eleven extra commands made each
  request 15 to 21 % larger. Comprehensive tools are worth it, and caching carries the cost, but
  account for it.

## 6. Skills

- **Format.** One Markdown file per skill, in a folder next to the instrument's config. A head
  with `name`, a one-line `description` and the `tools` it uses, then the steps: when it applies,
  each step with its tool, what to check, when it is done, what to report. None ship with the
  software; each lab writes its own.
- **Loading.** The prompt lists skills by name and description only. A `load_skill` tool returns
  the steps when a request matches. Ten skills cost ten lines until one is used.
- **The description is the trigger.** It is the only part the model sees before loading, so it
  must say what the skill achieves.
- **They help.** On Haiku, with five test skills, multi-step cases rose from 33 to 42 of 48, and
  generated cases from 105 to 130 of 160. Where a skill fitted, groups reached 20 of 20.
- **The model follows the skill's numbers exactly.** With a loose saturation threshold in the
  exposure skill, every run stopped just under it. The outcome is only as good as the skill, so
  lab-specific numbers belong in skills, tested on the instrument.
- **Wrong picking is mild.** In 75 runs, a skill was never loaded where none belonged. When the
  model skipped a skill, it fell back to a look and then reported, asked, or did the job
  directly. It picked the wrong skill once, and it combined two skills when that fitted.
- **Write skills for short requests with long procedures.** "Focus" benefits from a skill.
  "Look, and run only if it is sharp" does not: the request already gives every step, and the
  model rightly skips the skill.
- **Don't add structure before you need it.** We considered skill categories and left them out;
  with a handful of skills the descriptions are enough.

## 7. Evaluation

- **Use a simulator whose results are scored**: how far the sample is from the centre, the focus
  error, saturation. Score both the calls the model made and the state they leave behind.
- **Separate picking from following.** Picking: is the right skill loaded, and none when none is
  needed? Use near-miss requests and confusable pairs. Following: compare the same cases run
  with skills and without.
- **Give every case a reworded held-out twin**, so wording isn't what passes.
- **Compare fairly.** Our recorder reruns a failing case up to three times and keeps a pass, so
  recorded scores are best of three. Compare models on single runs with the same method, and
  repeat a few times to see which results are noise.
- **Check the test before blaming the model.** A workflow scored 0 for every model because the
  simulated acquisition list held one blank row: the model rightly refused to run it.
- **Ask what each failure says.** Some point at the framework: tool confusion, malformed calls,
  skills loaded late or wrongly. Those are ours to fix. Others point at the machine or the skill:
  thresholds, step sizes, convergence. Those belong to the lab.
- **Test every command over every transport against the running software,** with state
  restored afterwards. Add a stress suite: hostile inputs, client races, floods of mutations
  while reads are served. Run it on the real instrument only with an operator present.

## 8. Model choice

- **Claude Haiku 5.5:** asks on unclear requests, and is better at multi-step and look-and-adjust
  work. It sometimes answers a visual question without taking a frame first.
- **Gemini 3.5 flash-lite:** cheaper and good at single, clear commands. It acts on vague
  requests instead of asking.
- **The difference matters most for safety.** With a model that asks, the framework needs less
  protection of its own.

## 9. Still open for us

- Validation on the Windows demo and the real microscope.
- Whether per-call limits on intensity and moves are needed at the instrument level.
- Which remaining window features become commands.
