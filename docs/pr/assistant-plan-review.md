# A critical review of the AI Assistant plan

Written on 2026-11-19, as a review of `assistant-plan.md` in this folder and of what has been
built from it. It is kept so that the points are not lost.

**What happened since.** The plan was one file when this was written. Following point 12 below,
it was split the same day into `assistant-built.md`, `assistant-decisions.md` and
`assistant-local-models.md`, and the passages that point 12 lists as wrong were corrected there.
The other points still stand. Where this review says "the plan", it means the file as it was
before the split.

**In one sentence:** the built part is in decent shape, but the plan document has drifted, one of
its safety claims is weaker than it reads, and the part about local models rests on a stack of
assumptions that nobody has measured.

The points are in order of importance within each group.

## Things that are wrong or overstated in the plan

### 1. The guard for the cloud models guards the wrong configuration

The plan says that the recorded runs of the cloud models, replayed offline, protect Gemini and
Claude from any change made for local models. Those replays run **without** the five tools that
were added for a chat started from the window (`get_status`, `search`, `read_passage`,
`save_skill`, `edit_skill`).

In the real window every chat has those tools. Every request to a cloud model is therefore about
850 tokens larger than before, and the model has five more tools to choose from. Whether that
makes it choose worse among the old tools was never measured. Section 10 of the plan (the
evaluation with the new tools) is the piece that would measure it, and it is not done. Until it
is, "the existing tests are the guard" is only true for a configuration that operators do not
use.

### 2. The separate vision model was removed without being asked for

The handwritten notes said that the vision setting moves to the config file. What was built
drops the second model altogether: the model that answers also looks at the images, or nobody
does. The assistant in #8 could pair a text-only model with a second one that sees. That
possibility is gone from the tab and has no line in the config file. The part of the plan about
local models now wishes it back ("let a cloud model do the looking"). This was a decision made
while building, not one made by the person whose plan it is, and it should be put to them.

### 3. Google's Gemma is not the Gemma that would run on the microscope computer

The result of 22 of 23 cases came from the full model on Google's servers. On the microscope
computer the same model would be stored at 4 bits per number, with what it has read stored at 8
bits. Both cost some accuracy, and a model that calls tools is the first to show it. The result
is therefore the best the local model could do, not what it will do. The plan does not say so.

### 4. "The shorter tool list cost no accuracy" says more than the data can

The comparison is 23 cases, each run once. Only a handful of them need a command from the
catalogue. A model does not answer the same way twice, so one case more or less means nothing at
this size. In addition, the scoring left the extra "describe this command" calls out of the list
of calls, so a case with a limit on the number of calls could not fail because of them. The
honest statement is: on a small sample the shorter list did no visible harm.

## Risks in what has been built

### 5. It has never run inside mesoSPIM

The tab and the chat window were run on their own, off screen, on Linux. They have not been run
in mesoSPIM's demo mode, not on Windows, and not with the real Windows password store. The user
documentation was rewritten, and two screenshots were made, before anyone had used the feature.

### 6. The work is piling up on top of work that is not merged

This branch is 17 commits on top of a draft of 26 commits (#8). The offline tests live on a
different branch (`agent/gui-handles`), so the new tests cannot run on this branch alone, and two
of the new test files replace files of the same name there. How any of this reaches #8, and how
#8 reaches the public repository where #122 shows the old window, is still under "for later".
Every further addition makes that step larger.

### 7. Logging every call may bury the log

While it waits for a move, the assistant asks the microscope for progress about seven times a
second, and each of those is a line. A long run with a client that waits will write many
thousands of lines into the same file as mesoSPIM's own messages. The decision to log everything
was made on purpose and for a good reason: a log with gaps cannot be trusted. It should still be
looked at with a real log from a real session before it ships, to see whether the calls need a
file of their own.

### 8. Skills and saved conversations let text persist that nobody meant to keep

A skill that the model writes becomes instructions that the model follows in later chats. If
misleading text reached the model in one chat (the microscope's readout can contain a folder
name that someone wrote), it could end up inside a skill. The only check is the dialog that shows
the text before it is saved, and people skim dialogs. Saved conversations are similar: they are
found again by `search` and read by the model. They are also plain files with no limit on how
long they are kept, and they hold sample names and folder paths.

## The part about local models

### 9. It stacks one unknown on another

None of these has been measured by us:

- whether the model's server keeps what it has read for a Gemma model, or reads it all again on
  every request;
- how fast the prompt is read when most of the model sits in ordinary memory;
- whether current builds of the server still support a graphics card as old as the GTX 1080 Ti;
- how much ordinary memory the computer has (64 or 128 GB), and how many processor cores;
- whether Gemma 4's way of calling tools works properly through that server.

Most of the figures in that part of the plan come from blog posts, several of them written to
attract searches, and not one of the server's settings in the plan has been run by us. The plan
says so in places, but it builds a good deal on them all the same.

### 10. Running the model on the microscope computer puts acquisitions at risk

A model that shares the processor, the memory and the disk with a running acquisition can
disturb it, and a disturbed acquisition is lost data. The plan answers this with settings and a
check. The safer design is a second computer that runs the model, reached through the config
file's `address` line, which already works today. The plan mentions it in one line. It deserves
to be the first option, with "on the same computer" as the one that has to prove itself.

### 11. The aim has grown

The aim at the start was to finish the assistant without adding much. Since then the plan has
gained a search with a memory of earlier conversations, a catalogue of tools, a shortlist of
models, and the idea that mesoSPIM starts and manages a model server. Each is reasonable. Taken
together they are a second project, and it should be decided as one: after the half-hour test,
either go on or stop.

## The document itself

### 12. It is three documents in one

The file is over 900 lines and holds the original plan, a report of what was built, and a
research log on local models. A reader cannot tell what is decided, what is built and what is
only an idea without reading to the end. Sections 1 to 10 are still written as things to come,
and several are now wrong:

- the config sketch in section 2 (the `vision` line and the path of the system prompt are not
  what was built);
- section 6, which says an outside program gets the microscope half of `get_status` (it is a
  tool of the assistant only);
- the example of a log line in section 8 (the built format differs and includes the result);
- section 10, which speaks of four new calls where there are five;
- the figure of 21,000 tokens for a request, which Gemma's own count corrected to about 13,000,
  and the estimates that were derived from it;
- the test for the 26B model, which is still called half an hour's work and has grown well
  beyond that.

### 13. It does not say when the work is done

There is no list of what has to be true before the assistant counts as finished, and no order
for the steps that remain.

## What to do next, in this order

1. **Try the built assistant in demo mode on the real computer.** Everything else rests on it
   working there.
2. **Run the cloud evaluation with the five new tools.** This closes the hole in point 1, and it
   is the only way to know that the cloud models were not made worse.
3. **Decide about the separate vision model** (point 2): bring it back as lines in the config
   file, or leave it out on purpose.
4. **Split the plan** into what is built, what is decided, and the research on local models, and
   correct the sections that are out of date.
5. **Decide how this reaches #8 and the public repository**, before more is added.
6. **Only then the local model**, starting with the test, and with a second computer as the
   first design to consider.

## Two housekeeping points

- Two API keys were pasted into the conversation in which this work was done. Both should be
  revoked once the testing is over.
- The evaluation of Gemma 4 26B is limited to about one request a minute by the key's allowance,
  which makes the full 137 cases a matter of hours. A higher allowance would make such runs
  practical.
