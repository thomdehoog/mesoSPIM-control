# The AI Assistant: what is decided, and what is still open

This file holds the decisions behind the work, the things that are agreed but not built, and the
questions that still need an answer. What exists is in `assistant-built.md`. The research on
local models is in `assistant-local-models.md`. A critical review is in
`assistant-plan-review.md`.

Written on 2026-11-19, when the single plan was split into these files.

## The aim

Finish the AI Assistant so that it is done. It does not need many more features. The point of
the remaining work is to make it simple to set up, to make skills pleasant to use, and to make
clear what is stored.

## Rules that are decided

These were agreed in the planning conversation and hold for everything that is built.

1. **A chat has exactly the skills that were ticked when it started.** The list does not change
   while the chat is open. This keeps the chat predictable, and it keeps the start of every
   request unchanged, which is what a provider's cache needs.
2. **Nothing is saved without a yes.** A skill that the assistant writes or changes is shown to
   the operator in full first. No answer counts as no.
3. **The assistant never changes a system skill.** System skills belong to the whole microscope.
4. **Conversations are not saved unless the facility turns saving on**, and the window always
   says which it is. Each person sees only their own.
5. **Every call to the microscope is logged, for every client, including calls that only read a
   value.** A log is there to trace a problem afterwards, and a log with gaps cannot be trusted.
   The log holds calls only, never the conversation and never a key.
6. **Each person uses their own API key.** It is never written to the config file, to a log or
   into a saved conversation.
7. **"Personal" follows the Windows account** of whoever is logged in. mesoSPIM adds no accounts
   of its own.
8. **The facility decides in the config file; the operator decides in the tab.** The tab asks
   only for the provider, the model, the key and the skills.
9. **Nothing done for a local model may make the assistant worse with a cloud model.** Every
   change for local models is switched on only when a local model is chosen, the settings for a
   local model get their own place in the config file, and a local model takes memory and
   processor time only when it is in use.

A caution about rule 9. The offline tests that are meant to guard it replay the recorded runs of
the cloud models **without** the five tools that every chat from the window now has. So they do
not test what operators use. A targeted check with a real model was run on 2026-11-19 and found
no harm (see "The targeted check of the new tools" below), but that check is not part of the
offline tests, so nothing repeats it automatically after a later change.

## The targeted check of the new tools

Run on 2026-11-19 with Claude Haiku 5.5 on the simulated microscope. The question was whether
the five tools that a chat from the window has (`get_status`, `search`, `read_passage`,
`save_skill`, `edit_skill`) make a cloud model choose worse among the old tools. Running all 137
cases twice would mostly have tested things the new tools cannot touch, so the check took the
cases where a new tool could be mistaken for an old one.

| What was run | Without the new tools | With the new tools |
|---|---|---|
| 39 existing cases: reading values, acquisition lists, handling errors, and the 16 "confusable" cases | 38 of 39 | 38 of 39 |
| 12 new cases written for this check | not applicable | 12 of 12 |

- **In none of the 39 existing cases did the model call a new tool.** It was not drawn to them
  where an old tool was right.
- **The one miss on each side is a different case, and both are a habit of Haiku.** The two
  cases ask it to install an acquisition and run it, or to start a time lapse. Haiku sometimes
  edits a row that has an intensity of 0, notices that the run would be dark, and asks before
  running. That is sensible, and it is not what the case expects. Each of the two cases was run
  three more times on each side: without the new tools they passed 0 of 4 and 2 of 4 runs in
  all, with them 4 of 4 and 1 of 4. So these two cases are unsteady for Haiku either way, and
  the new tools do not explain it. Claude Sonnet 5.5 passed the first of them twice out of
  twice, which shows that the case and the assistant are sound.
- **The 12 new cases.** Four check that a new tool is used when it should be (its own status,
  the documentation, an earlier conversation, saving a skill on request). Eight check that it is
  not used when an old tool is right: saving the acquisition list or the settings is not saving
  a skill, loading the sample is not loading a skill, changing a row is not changing a skill,
  and "search for the sample" means looking, not searching the documentation. One of them failed
  on the first run because the case itself was wrong: it asked for the list to be saved under a
  name ending in `.bin`, and Haiku correctly answered that the list can only be saved as CSV.
  With the name corrected it passed.

**What it shows:** on the cases most at risk, the new tools did no harm to Haiku's choices, and
it used them when it should.

**What it does not show:** it is one model, each case run once apart from the two repeats, and
Gemini 3.5 Flash-Lite was not checked. It was run by a script outside mesoSPIM
(`assistant-plan-evidence/cloud-check/desk_eval.py`), so it is not repeated by the offline tests.

**A side finding, since fixed.** Claude Opus 5.5 could not be tested at first: the provider
refused the request, because the assistant sent a "temperature" setting that this model does
not accept. The assistant keeps a list of the models that refuse it, and only Haiku 5.5 was on
it. Opus 5.5 was added to the list the same day, and it now works.

## Suggested by the comparison of models

The comparison of eight models (`assistant-model-comparison.md`) suggested three changes to the
assistant.

- **Make the instructions and the cases agree on which tool judges an image.** Done on
  2026-11-19, as change B in the next section: the descriptions of `get_frame` and `snap` now
  point to `look`.
- **Decide whether a choice the operator did not make is always asked about.** Tried as change C
  in the next section, and dropped: no wording did it without harm.
- **Show a provider's refusal as a refusal.** Not done. When Anthropic's filter ends a reply as
  a refusal, the assistant shows a failed turn.

## Changing the instructions without fitting them to the cases

Written on 2026-11-19 as a proposal, and then followed the same day; the outcome is below. The comparison of models showed where the
assistant's instructions are unclear. The danger in fixing that is to write sentences that make
the known cases pass and help nowhere else. The instructions in #8 were deliberately kept "general
and not fitted to test cases", and an earlier attempt to improve them by rewording backfired.

### The method

1. **Decide first whether the case or the behaviour is right.** A miss is only a fault of the
   instructions if the case's expectation is what we really want at the microscope. Where the
   model reached the right result by another tool, that question comes before any rewording.
2. **Fix the cause where it lives.** Not every miss is a matter of the instructions:
   - a tool that sets a poor default is fixed in the tool;
   - two tools that are easily mixed up are told apart in their own descriptions, which is where
     the October test showed that a model's choice is decided;
   - a model that makes dozens of calls is stopped by a limit in the code;
   - a case that is too strict is fixed in the case.
   Only what is left over goes into the instructions.
3. **Write a rule, not an answer.** A sentence added to the instructions must be true for a
   request nobody has written yet. It names the situation in general ("a choice the operator did
   not make"), never the words of the case that revealed it ("another laser").
4. **Keep three sets of cases apart.**
   - The cases that revealed a problem are used to find it, and for nothing else.
   - The held-out twins (`cases_holdout.json`: the same intents in other words) judge a change.
   - A third set is written after the wording is fixed, by someone who has not read the new
     wording. The generated set (`cases_generated.json`) can serve until then.
5. **Change one thing at a time, and run both versions side by side.** The same day, the same
   model, with and without the change. Each case three times, because one run varies by about
   three cases in 137. A change is kept only if its gain on the held-out twins is larger than
   that, and no kind of case gets worse.
6. **Look for the opposite mistake.** Every rule has a cost on the other side. "Ask when a
   choice is missing" can turn into asking when the request was clear. "Use `look`" can turn
   into more light on the sample. For each change, name the cases that would show the opposite
   mistake and watch them.
7. **Check several models.** A real clarification helps a weak model and does not hurt a strong
   one. A sentence that helps one model on its one failing case is a patch. It belongs in that
   model's own settings, or nowhere.
8. **Give the instructions a size limit.** They are 34 lines today. A new sentence should
   replace an old one where it can, and the whole should not grow by more than a handful of
   lines.

### What was tried, and what was kept

Three changes were tried on 2026-11-19. Each was switched on by a script outside mesoSPIM, and
nothing went into the assistant until the results were in. **One was kept and two were dropped.**

| Change | On the held-out twins | On the third set | Outcome |
|---|---|---|---|
| A. A blunter warning about text that is not from the operator | No difference for Haiku (351 of 411 with and without) | **Worse**: 57 of 100 against 71 | Dropped |
| B. `get_frame` and `snap` say that an image is judged with `look` | Better: 102 of 135 image runs against 93 | **Better**: 77 of 100 against 71 | **Kept, and built** |
| C. "Do not choose for the operator", in two wordings | Better: unclear requests 40 of 42 against 33 | **Worse**: 27 and 31 of 60 against 38 | Dropped |

All figures are for Claude Haiku 5.5. The held-out twins are the 137 single-step cases in other
words, run three times. The third set is the generated multi-step cases, which were not looked
at while the changes were written: 100 of them for A and B, 60 for C, run once.

**Why A was dropped.** It did what it was written for: Qwen3.6-35B-A3B obeyed text planted in a
folder name in 6 of 11 runs without it and in 0 of 11 with it. But on multi-step work it made
Haiku hesitant. Asked to centre the sample, the assistant as it is looks, moves by what the look
suggests, and looks again; with the warning it used the plain centring command and stopped, and
centring fell from 14 of 20 to 6. The warning told the model to distrust anything in a tool's
result that reads like guidance, and the assistant relies on exactly that: a look's result says
which move would centre the sample, and a refusal says what to do next. The single-step cases
could not show this, because in them no step depends on the result of the one before.

**Why C was dropped.** On the cases that revealed the problem and on their twins it worked. On
independent cases it did two things nobody wanted. Asked to "improve the picture", the assistant
as it is says it has no image to judge and asks; with the new sentence it looked, raised the
exposure by itself and reported success. Asked to start live and focus, it took one look and
left the focus as it was. A first wording also made it refuse a complete request for a tiling
list because the zoom had not been named; a refined wording repaired that and nothing else. The
sentence that is in the instructions today is better than both replacements.

**Why B was kept.** It held on every set, and it moved the calls the way it was meant to: in the
exposure, focusing and centring cases of the third set, `get_frame` was called 2 times in place
of 20 and `snap` not at all in place of 3, while `look` was called 117 times in place of 97.
Focusing rose from 8 of 20 to 14. It is two sentences in the assistant's descriptions of those
two tools (`TOOL_NOTES` in `mesoSPIM/src/ai_assistant/config.py`); the commands' own
descriptions, which scripts and outside programs read, are unchanged.

### What this taught about the method

- **The third set decided two of three.** Judged on the held-out twins alone, all three changes
  would have been kept, and two of them make the assistant worse at real work. The twins are the
  same requests in other words; they cannot show a sentence that helps one kind of request and
  harms another.
- **Single-step cases cannot judge a sentence about how results are used.** Any change to how
  the model treats a tool's result has to be tried on cases with several steps.
- **A change belongs where the choice is made.** The one that worked sits in the descriptions of
  the two tools that were being confused. The two that failed were sentences in the general
  instructions, where they were read more widely than they were meant.

### What is left open by this

- **Text planted in the readout.** The cloud models and Gemma 4 26B ignore it as things are.
  Qwen3.6-35B-A3B does not, and the warning that fixes it cannot go into the general
  instructions. If that model is ever used, the warning would have to be part of its own
  settings, and tried on multi-step cases with that model first.
- **Requests that leave a choice open.** A few cases still see a model pick a value ("another
  laser", "brighter"). No wording tried here fixed that without harm. They stay as known misses,
  unless the cases themselves are judged too strict.
- **Gemma and Qwen on the third set.** The runs for those two stopped when the credit at
  OpenRouter ran out. B's effect on Gemma rests on 47 of its 135 image runs (41 passed, against
  78 % without the change), and nothing of the third set was run on either.

The traces are in `assistant-plan-evidence/wording-experiments/`.

## Agreed, and not built yet

- **Making the check above part of the tests.** The cases for the new tools should join the
  evaluation on the development branch, with a recorded run that the offline tests replay, and
  Gemini 3.5 Flash-Lite should be checked as Haiku was. Picking the right skill was the weakest
  result in #8 (61 of 75), so the cases for skills still deserve the most attention.
- **A starter set of system skills.** mesoSPIM is to come with a few. The place for them and
  everything around them is built; no skill is written. Each has to be written and tested, at
  least in demo mode, and its description tested like the others. The five test skills of the
  evaluation (centring, focusing, exposure, checking before a run, an acquisition with checks)
  are the natural place to start.
- **Trying it in mesoSPIM.** In demo mode on the real computer first, then on a microscope with
  an operator present. The test on a real microscope from #8 is also still to be run.

## Open questions that need an answer

### 1. The separate vision model: decided and built

The notes said the vision setting moves to the config file. The first version of this work
removed the second model altogether. On 2026-11-19 it was decided that it comes back, and it was
built the same day as the `vision_model` block of the config file; see `assistant-built.md`. The
tab still has no second set of fields for it.

### 2. How this reaches #8 and the public repository

This branch is 18 commits on top of a draft of 26 commits. Pull request #122 on the public
repository shows the window as it was. The choices are to fold this work into #8, to keep it as
a pull request of its own on top of #8, or to wait until #122 is merged and propose it as a
follow-up. The tests also have to be brought together: the new ones are on this branch, and the
support they need is on `agent/gui-handles`.

### 3. Where the log of calls goes

Rule 5 stands. What is open is whether the lines stay in mesoSPIM's own log file or get a file
of their own. While it waits for a move the assistant asks for progress about seven times a
second, and each of those is a line. This should be decided after looking at a real log from a
real session.

### 4. How long saved conversations are kept

They are kept until the operator removes them. They are plain files that hold sample names and
folder paths. Whether there should be a limit, by age or by number, is open.

### 5. Whether to pursue a local model at all

The research is in `assistant-local-models.md`. It contains several ideas that would each change
a decision made in #8, so they need a decision of their own:

- **A different tool list for a local model** (a core set and a catalogue). Step 2 of #8 removed
  the tool lists per model on purpose.
- **mesoSPIM starting the model's server itself**, with the memory and processor settings in the
  config file. Step 2 of #8 removed the "Local AI mode" on purpose.
- **Where the model runs.** The agreed order is: a second computer reached through the `address`
  line first, because that already works and puts no acquisition at risk; the microscope computer
  itself only if it proves that it leaves acquisitions alone.

The suggested way to decide: run the test described in that file, and then either go on or
stop.

### 6. Whether `get_status` earns its place

The assistant is given the microscope's full readout with every message, as in #8. So of the two
halves of `get_status`, only the one about the assistant itself (the model, how full its context
is, this chat's skills, whether the conversation is saved) tells the model something it does not
already have. The tool also adds to every request that a cloud model receives.

The agreed way to decide: if the cloud evaluation with the new tools shows that they cost
accuracy among the old tools, `get_status` is the first to be removed or cut down to the
assistant's half. If it shows no cost, the tool stays as it is.

**Outcome, 2026-11-19:** the targeted check above showed no cost, so `get_status` stays as it is.

### 7. Open decisions carried over from #8

Pull request #8 lists its own: the size of each request, whether waveform generation should
apply the max amplitude, whether limits on intensity or move size per call are wanted, and the
disk check that fails off Windows. They are unchanged.

## Left out on purpose

- **A spending limit.** The context ceiling already stops a conversation from growing without
  end.
- **Tick boxes that work during a chat.** Rule 1 replaced them.
- **An edit button for skills in the tab.** A skill is changed by asking the assistant, or by
  editing the file.
- **Accounts and passwords inside mesoSPIM.** Rule 7 replaced them.
- **The token count of each request**, in the window and in the log.

## When the assistant counts as finished

Agreed on 2026-11-19 as the list to work towards. An item can still be changed when there is a
reason.

1. It runs in mesoSPIM's demo mode on Windows: start a chat, send a request, create a skill,
   use it in the next chat, clear the context, close the chat.
2. A key is remembered and forgotten with the real Windows password store.
3. The cloud evaluation with the new tools shows no loss against the recorded results.
4. The sweep on a real microscope from #8 has been run with an operator present.
5. A starter set of skills exists, or it is decided that mesoSPIM ships without one.
6. The open questions 2, 3 and 6 above are answered.
7. The work is in one pull request that can be reviewed.

## The order suggested for the next steps

1. Try the built assistant in demo mode on the real computer.
2. Run the cloud evaluation with the five new tools.
3. Decide about the separate vision model.
4. Decide how this reaches #8 and the public repository.
5. Only then the local model, starting with the test.
