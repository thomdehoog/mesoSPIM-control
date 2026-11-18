# Skills

A skill is a short procedure the AI Assistant follows when a request calls for it: centring the
sample, finding focus, preparing and running an acquisition. The general instructions stay short
and work on any microscope; a skill is where a lab writes down how *its* instrument is best
handled, with its own step sizes, thresholds and order of steps.

## Two kinds of skill

- **System skills** belong to the microscope. The facility writes them, everyone who uses the
  microscope sees them, and the assistant can read them but never change them.
- **Personal skills** are your own. Only you see them. You can write one yourself, or let the
  assistant write one for you from a conversation.

The AI Assistant tab lists both kinds, each in a list of its own, with a tick box per skill.
Hover over a skill to read its description.

## How the assistant uses them

1. **You tick the skills you want and press Start Chat Window.** That chat has exactly the
   ticked skills, and the list does not change while the chat is open. The chat's first grey line
   names them. To change the selection, close the chat and start a new one. Your ticks are
   remembered for next time.
2. The model's instructions list each ticked skill by its name and its one-line description only.
3. When a request matches a description, the model calls `load_skill` with that name before it
   changes anything; a first look is allowed. Only then does it receive the steps, so ten skills
   cost ten lines, not ten pages. A skill that is not ticked for this chat cannot be loaded.
4. The model follows the steps with the usual tools (`look`, `move_relative`, `set_intensity`,
   ...). A skill gives no new powers: everything still goes through the same commands, checks and
   limits.

When no description matches, the model works without a skill.

## Creating a skill from a conversation

This is the easiest way to get a personal skill.

1. Do the procedure once with the assistant, in the chat.
2. Press **Create skill** in the chat window.
3. The assistant looks back over the conversation, works out which procedure is worth keeping,
   and shows you what it would save: the name, the description and the steps, in full.
4. Read it through, the description above all, because it decides when the skill is used. Press
   **Save skill**, or **Don't save** and tell the assistant in the chat what to change.

A saved skill appears in your personal list, already ticked, and can be used from the next chat.
In the chat where it was made the assistant still has the steps in front of it.

You can also ask in your own words at any time: "save what we just did as a skill called morning
check".

## Changing and removing a skill

- **To change a personal skill**, ask the assistant in a chat where that skill is ticked, for
  example "change my overview scan skill so it uses the 488 laser". It shows you the new text
  next to the old one, and saves only when you say yes. You can also edit the file yourself in a
  text editor.
- **To remove a personal skill**, press **Remove** next to it in the tab. You are asked first,
  and the file is moved to a `deleted` folder next to your skills, not erased, so a mistaken
  click can be undone by moving it back.
- **System skills** have no Remove button, because they belong to the whole microscope. Untick
  one and it is gone for you without affecting anyone else. The facility changes system skills by
  editing the files in the folder.

A change to a file made by hand shows in the tab the next time the tab is opened, and takes
effect in the next chat.

## What the tab marks

- **(cannot be used)**: the file is not a valid skill, for example it has no description or no
  steps. Hover over it to read why. It is listed, greyed out, so that it does not silently go
  missing.
- **(out of date: no tool ...)**: the skill names a call that this version of mesoSPIM does not
  have, for example after a call was renamed. It would fail half way, so change the skill or
  untick it.

If a personal skill has the same name as a system skill, both are listed, and the assistant
knows the personal one as "name (personal)".

## Where they live

One file per skill. System skills are in a `skills` folder next to the microscope's config file,
and personal skills are in each person's home folder:

```
mesoSPIM/config/
├── my_microscope_config.py
└── skills/                      system skills, for everyone
    ├── centring.md
    ├── focusing.md
    └── exposure.md

C:\Users\anna\mesoSPIM\
├── skills/                      anna's personal skills
│   ├── morning-check.md
│   └── deleted/                 skills she removed, kept in case
└── conversations/               her saved conversations, when saving is on
```

Both folders can be set in the config file (see [Configuration](configuration.md)). mesoSPIM is
built to come with a starter set of system skills, but none are written yet, so the system
folder starts empty.

## Writing one

A skill is a Markdown file with a head between two `---` lines and the steps below it:

```markdown
---
name: centring
description: Bring the sample to the middle of the camera image.
tools: look, calibrate, move_relative
---
When: the operator wants the sample centred, or says it is off to one side or not in the middle.

1. look, asking "where is the sample in the image?". If no sample is visible, say so and stop.
2. If the frame's centre_move_um is marked nominal and calibrate is offered, run calibrate once.
3. move_relative by the frame's centre_move_um, x and y together.
4. look again. Done when offset_px is under about a tenth of the frame's width in both directions.
   Otherwise repeat steps 3 and 4, at most three moves in all.

Report where the sample started, the moves made and where it ended. If it did not get there,
say what offset remained.
```

- **name**: short and unique in the folder. It is what `load_skill` is called with.
- **description**: one line. This is the only part the model sees until it loads the skill, so
  it decides when the skill is used. Say what the skill achieves, not how.
- **tools**: the tools the steps use, comma-separated. The tab marks the skill as out of date
  when one of them does not exist, for example after a rename.
- **The body**: when it applies, the steps in order with the tool for each, what to check after a
  step, when it is done, and what to report.

Good skills:

- **Cover a short request with a long procedure.** "Focus" needs a skill; "look, and run the
  list only if it is sharp" does not, because the request already says every step.
- **Do one job.** Keep each under half a page; the hard limit is 6,000 characters. A loaded skill
  stays in the conversation and is paid for on every later request.
- **Use the real tool names and fields** (`centre_move_um`, `focus_measure`, `saturated_fraction`).
  The model already has each tool's full description; the skill says how to combine them.
- **Say when to stop.** Without a stop rule a model loops, or stops too early. A bound on the
  number of rounds is the simplest.
- **Say what to report**, so the operator can see what was done.
- **Hold the lab's numbers.** Step sizes, thresholds and safe intensities belong here, not in the
  general instructions.

## Testing one

Try each skill in demo mode first, then on the microscope with an operator watching. Check both
sides: two or three requests that should use it, worded the way people actually talk ("I can only
see half the specimen"), and a similar request that should not ("move x by 100 µm" must not start
centring). The fork's evaluation has a benchmark that does this with five test skills
(`mesoSPIM/test/ai_assistant/evals/skills/`): run it with
`python -m mesoSPIM.test.ai_assistant.evals.run --cases mesoSPIM/test/ai_assistant/evals/cases_skills.json --skills <folder>`.
