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

A caution about rule 9. The tests that are meant to guard it replay the recorded runs of the
cloud models **without** the five tools that every chat from the window now has. So they do not
yet test what operators use. Until the evaluation below is done, rule 9 is a rule and not a
checked fact.

## Agreed, and not built yet

- **The evaluation with the new tools.** The tool-choice cases are to be run for the cloud
  models with `get_status`, `search`, `read_passage`, `save_skill` and `edit_skill` on offer, to
  see whether the new tools make a model choose worse among the old ones, and whether it picks
  the new ones when it should. Picking the right skill was the weakest result in #8 (61 of 75),
  so the cases for skills get the most attention.
- **A starter set of system skills.** mesoSPIM is to come with a few. The place for them and
  everything around them is built; no skill is written. Each has to be written and tested, at
  least in demo mode, and its description tested like the others. The five test skills of the
  evaluation (centring, focusing, exposure, checking before a run, an acquisition with checks)
  are the natural place to start.
- **Trying it in mesoSPIM.** In demo mode on the real computer first, then on a microscope with
  an operator present. The test on a real microscope from #8 is also still to be run.

## Open questions that need an answer

### 1. The separate vision model

The notes said the vision setting moves to the config file. What was built removes the second
model altogether: the model that answers also looks at camera images, or nobody does. In #8 a
text-only model could be paired with a second one that sees. The choice is:

- bring it back as lines in the config file (a provider, a model and where its key comes from);
- or leave it out on purpose.

A local model makes this more pressing, because there a look can push the main chat out of the
server's memory, and letting a second model do the looking avoids that.

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
6. The open questions 1 to 3 and 6 above are answered.
7. The work is in one pull request that can be reviewed.

## The order suggested for the next steps

1. Try the built assistant in demo mode on the real computer.
2. Run the cloud evaluation with the five new tools.
3. Decide about the separate vision model.
4. Decide how this reaches #8 and the public repository.
5. Only then the local model, starting with the test.
