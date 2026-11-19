# Finishing the AI Assistant: the plan

Written on 2026-11-18, from a planning conversation and two pages of handwritten notes. It
describes what we agreed to build on top of the Remote Control and AI Assistant work in pull
request #8 on the fork (branch `remote-control-ai-assistant`).

Most of it has since been built. The last section, "Built so far", says what exists, what is
still missing, and where the result differs from the plan as written below.

The aim is to finish the AI Assistant, not to give it many more features. Three ideas run through
the whole plan:

- **The window becomes simpler.** An operator chooses a provider, a model, an API key and the
  skills they want, and starts a chat. Everything else moves to the config file.
- **Skills are easier to use.** The operator can see them, switch them on and off, create them
  from a conversation and remove them, without editing files by hand.
- **It is clear what is stored and where.** The window says whether conversations are saved, the
  API key is kept hidden, and the log records every call so that a problem can be traced later.

## 1. The setup window

This is the AI Assistant part of the main window. It keeps only what an operator needs before
starting a chat.

```
AI Assistant
  Provider   [ ............ ]
  Model      [ ............ ]
  API key    [ ************ ]        ● connected
             [ ] Remember this key on this computer      [ Forget key ]

  System skills                      (scrolls)
  ┌──────────────────────────────────────────┐
  │ [x] Centre the sample                    │
  │ [x] Find focus                           │
  │ [ ] Tile a large sample                  │
  └──────────────────────────────────────────┘
  Personal skills                    (scrolls)
  ┌──────────────────────────────────────────┐
  │ [x] My cleared brain routine     Remove  │
  │ [ ] Quick overview scan          Remove  │
  └──────────────────────────────────────────┘

  [ Start Chat Window ]
  Conversations are not saved. The calls the assistant makes are written to the mesoSPIM log.
```

What it shows:

- **Provider, model and API key.** These are the only three settings.
- **A green light** that turns on once mesoSPIM has checked the key with the provider. The check
  uses a request that costs nothing. A wrong key gives a plain message here, and not an error in
  the middle of the first question.
- **Two lists of skills**, system and personal, each with a checkbox per skill. Each list has a
  fixed height and its own scrollbar, so the window keeps its size however many skills there are.
  Hovering over a skill shows its one-line description.
- **The Start Chat Window button.** The name says that a new conversation begins with the skills
  that are ticked.
- **A notice** that says whether conversations are being saved, and that calls are logged.

What leaves the window and moves to the config file:

- The system prompt button.
- The vision setting.
- The preference setting.
- The focus metric.
- The coordinate system.

Connect and Disconnect also go away. Start Chat Window replaces them.

## 2. The config file

The settings that left the window live in the microscope's config file. Four lines sit together,
so that anyone can see at a glance where the assistant gets its instructions from. The exact names
can still change; this is a sketch.

```python
ai_assistant = {
    'system_prompt':      'ai_assistant/system_prompt.md',
    'system_skills':      'skills',               # next to this config file
    'personal_skills':    '~/mesoSPIM/skills',    # ~ is the home folder of whoever is logged in
    'documentation':      'docs/source',
    'save_conversations': False,
    'vision':             True,                   # may the assistant send camera images?
    'address':            '',                     # only for a model reached at your own address
}
```

- **The system prompt** is a text file, and the config file points to it. A default file ships
  with mesoSPIM. This keeps the config short and makes the prompt easy to find and read. A change
  to the prompt takes effect in the next chat.
- **The system skills folder** is the `skills` folder next to the config file, as in #8.
- **The personal skills folder** is written with `~`, which stands for the home folder of whoever
  is logged in to the computer. The config file is shared by everyone who uses the microscope, so
  a fixed path would give every operator the same "personal" folder. With `~`, one line gives each
  person their own.
- **The documentation folder** is where the `search` call (section 6) looks. If the folder is
  missing, `search` says plainly that the documentation is not available on this computer.
- **Saving conversations** is off unless this line turns it on.
- **Vision** decides whether the assistant may send camera images to the provider. For
  unpublished samples this is a question of policy and not of taste, so it belongs to the
  facility. The documentation will say clearly that this line is how a facility forbids it.
- **The address** is for sites that reach a model through a service of their own, for example
  one hosted by their university, instead of through a commercial provider. It stays empty for
  everyone else. This matters for labs that are not allowed to send images outside.

## 3. What "personal" means

mesoSPIM has no notion of a user today. Nobody logs in to it. The plan does not add accounts or
passwords to mesoSPIM. Instead, "personal" follows the account of whoever is logged in to the
computer:

- Personal skills live in that person's home folder.
- Saved conversations live there too.
- A remembered API key is kept in that person's entry in the Windows password store.

On the microscope computers this plan is written for, each person signs in to Windows with their
own account. Windows therefore already keeps these private, and mesoSPIM does not need to add
anything.

There is one honest limit for other sites. On a computer where everyone shares one Windows login,
"personal" means shared by everyone on that login. A facility in that situation can also choose to
write a fixed path in the config file and accept that. The notice in the window says where things
are stored, so that nobody is surprised.

## 4. Skills

A skill is a short procedure, written as a text file, that the assistant follows when a request
matches it.

### The two kinds

| | System skills | Personal skills |
|---|---|---|
| Who writes them | The developers and the facility | Each operator, with "Create skill" |
| Where they live | The `skills` folder next to the config file | The operator's home folder |
| Who sees them | Everyone on that microscope | Only that person |
| Can the assistant change them | No, they are read-only to it | Yes, after the operator confirms |

mesoSPIM is built to ship a starter set of system skills, but none are written yet. They can be
added later without changing anything else in this plan.

### What a skill file looks like

Every skill starts with a short head, followed by the steps. This is the layout #8 already uses.

```
---
name: centring
description: Move the sample to the middle of the image.
tools: look, move_relative, calibrate
---
The steps follow here.
```

A skill must have a name, a description and steps. The description matters most, because it is
all the assistant has when it decides whether a skill fits a request. A file that is missing one
of these is not used. It is shown in the setup window, greyed out, with the reason, so that it
does not silently go missing from the list.

### How a chat gets its skills

- **A chat has exactly the skills that were ticked when it started.** The list does not change
  while that chat is open. To change the selection, the operator starts a new chat.
- **Only the descriptions are in the system prompt.** Each costs a few dozen tokens. The steps
  are not in the context at the start, because the assistant only needs the skills that a request
  actually calls for.
- **The assistant fetches the steps with `load_skill`** when it decides that a request matches a
  description. Once loaded, the steps stay in the conversation until the context is cleared.
- **`load_skill` refuses a skill that is not ticked for this chat**, even if the file is in the
  folder. The tick is the operator's decision, and the tool enforces it. The list in the setup
  window is therefore the complete answer to "what can the assistant do in this chat".
- **The ticks are remembered** per person between sessions.

We chose "fixed when the chat starts" over checkboxes that work during a chat. It is simpler to
explain, and it means the system prompt never changes in the middle of a conversation. That
matters because the provider keeps a cached copy of the unchanged start of each request, which is
what makes requests cheap (about 94 % was read from this cache in the tests for #8).

### Creating a skill

1. The operator presses **Create skill** in the chat window.
2. The assistant looks back over the conversation and works out which procedure is worth keeping.
3. It shows what it would save: the name, the description and the steps, written out in full.
4. The operator answers yes or no. To have something changed, they say so in the chat and the
   assistant shows a new version.
5. On yes, the assistant calls `save_skill`. The skill is saved as a personal skill and appears in
   the personal list of the setup window, already ticked.

The new skill formally joins from the next chat. Nothing is lost in the current chat, because the
assistant has just written those steps and still has them in front of it.

### Editing a skill

There are two ways, and the window needs no edit button.

- **Ask the assistant in a conversation**, for example "change my overview scan skill so it uses
  the 488 laser". The assistant loads the skill, writes the changed version and calls
  `edit_skill`. The window shows the new text and waits for the operator to confirm before
  anything is saved. The skill has to be ticked for that chat.
- **Change the file by hand** in a text editor. A skill is a plain text file in a folder.

### Removing a skill

Personal skills have a **Remove** button in the setup window. It asks for confirmation, and it
moves the file to a `deleted` folder instead of erasing it, so that a mistaken click can be
undone. System skills have no Remove button, because they belong to the whole microscope.
Unticking one gives the same result for one person without affecting anyone else.

### Smaller rules

- If a personal skill has the same name as a system skill, both are shown, each in its own list,
  and the assistant is told which kind each one is.
- Removing a skill, or editing its file by hand, takes effect in the next chat. An open chat keeps
  what it started with.
- The assistant can never write a system skill.
- **Skills that have gone out of date are marked.** A skill names the calls it uses in its
  `tools` line. If a call is renamed or removed in a later version of mesoSPIM, the skill would
  break without warning. The setup window therefore checks each skill against the calls that
  exist, and marks the ones that no longer fit, with the name of the missing call.

### The skill calls

| Call | What it does | Who can trigger it |
|---|---|---|
| `load_skill` | Returns the steps of a ticked skill (exists in #8) | The assistant |
| `save_skill` | Saves a new personal skill, after the operator confirms | The assistant, or the Create skill button |
| `edit_skill` | Saves a changed version of a ticked personal skill, after the operator confirms | The assistant |

## 5. The chat window

- **Clear context**, with the size on the button:

  ```
  [ Clear context (12,400 of 200,000 tokens) ]
  ```

  The first number is the size of the whole conversation as the provider last reported it. The
  second is the ceiling for the chosen model. Pressing the button empties the conversation and
  starts fresh with the same provider, model and skills.
- **The lines that showed the size of each request are removed.** The button is the only place
  where size is shown.
- **Near the ceiling** the number turns orange. At the ceiling the assistant stops with a plain
  message that asks the operator to clear the context. Nothing is trimmed or summarised behind the
  operator's back.
- **Clearing asks for confirmation only when conversations are not being saved**, because then
  the cleared conversation is gone for good.
- **Tool calls appear as short grey lines** between the messages as they happen, as in #8, so the
  operator can watch what the assistant is doing to the microscope.
- **The Create skill button** is here, because a skill is written from the conversation.
- **A Stop button** is here too. #8 relies on mesoSPIM's own Stop for safety. Now that the chat
  is a window of its own, the operator needs Stop within reach while the assistant is moving the
  stage or running an acquisition. It stops what the microscope is doing and ends the assistant's
  current turn.
- **There is one chat at a time.** Pressing Start Chat Window while a chat is open brings that
  chat to the front and does not start a second one.
- **Closing the chat window asks first when conversations are not saved**, because the
  conversation would be lost. This is the same rule as for Clear context.

## 6. New calls for the assistant

### `get_status`

One call that answers "what is going on right now", so that the assistant does not need several
calls at the start of every task. It carries two parts.

The microscope:

- Whether it is idle or busy, and if busy, what is running and how far along it is.
- The stage position.
- The main settings: laser and intensity, filter, zoom, shutter.
- The free disk space in the folder where data is saved.

The assistant:

- The provider and model that are connected.
- How full the context is, as tokens used out of the ceiling.
- The skills that are available in this chat, by name.
- Whether conversations are being saved.

The call stays small. The existing calls (`get_state_all`, `get_limits`,
`get_acquisition_list`) remain for the full detail. A script or an outside program that asks for
`get_status` gets only the microscope part, because the assistant part describes the chat inside
mesoSPIM, which an outside client does not have.

### `search`

One call that looks through the documentation, through previous conversations, or through both.
Loading all of this into the context would be wasteful, so the assistant asks for it when it
needs it, in the same way that it loads a skill.

- It returns a handful of short excerpts, each with the place it came from.
- A second call reads one section or one conversation in full, when an excerpt is not enough.
- The search is a ranked word search, using the small database that comes with Python (SQLite).
  It needs no extra installation, no second API key and no internet connection.
- Conversations are stored as text, with the tool calls but without the camera images, so that
  they stay small.
- When saving is off, searching conversations returns a plain message that nothing is saved.
  Searching the documentation still works.

Searching earlier conversations is a form of memory. Step 2 of #8 removed the memory tools to keep
the assistant lean. This brings memory back in a lighter form, where nothing is loaded unless the
assistant asks for it. That is a deliberate change of direction.

## 7. Saving conversations

- Saving is **off** unless the config file turns it on.
- The window always says which it is: "Conversations are not saved" or "Conversations are being
  saved in <folder>".
- Saved conversations are private to the person who had them, in the same way as personal skills
  (see section 3).
- A saved conversation can be found again later with `search`. This is the reason to offer saving
  at all.
- **A saved conversation can be removed again.** The window offers a list of the operator's saved
  conversations, each with a Remove button. A removed conversation also disappears from `search`.
  Without this, "saved" would quietly mean "kept for ever".

## 8. Log files

mesoSPIM already writes one log file per run into its `log` folder. Today the calls themselves are
not recorded, so afterwards nobody can tell who moved the stage or changed a laser.

The log exists so that, when something goes wrong, we can read back what happened. It therefore
has to be complete.

- **Every call is logged, always.** This includes calls that only read a value.
- **Every client is logged.** Remote Control accepts calls from three kinds of client: the AI
  Assistant inside mesoSPIM; a script or program that someone wrote (this route is called TCP);
  and an AI program outside mesoSPIM (this route is called MCP). All three use the same calls, and
  all three are written to the log.
- **Each line says** when the call was made, who made it, which call it was with which values, and
  how it ended: ok, refused with the reason, or failed. When the AI Assistant made the call, the
  line includes the model name.
- **Each line names the person**, as the Windows user who was logged in. This is the first thing
  anyone asks when they read a log after a problem.
- **The lines go into the existing log file**, so that one timeline shows each request next to
  what the microscope did.

```
14:02:11  Remote Control  anna  AI Assistant (claude-haiku-5-5)  set_laser {"laser": "488 nm", "intensity": 10}  ok  0.21 s
14:02:15  Remote Control  anna  TCP 192.168.1.20                 move_absolute {"x": 1200}                       refused: outside the x limits
14:03:40  Remote Control  anna  MCP                              run_acquisition_list {}                         ok  184.5 s
```

What is deliberately left out:

- The API key never appears in a log.
- The conversation text, meaning what the operator typed and what the model answered, is not
  logged. Only the calls are. This keeps "Conversations are not saved" true.
- The token count of each request is not written.
- Images and other very long values are shortened to a note of their size.

## 9. The API key

Today the key is typed into a field that shows dots, or read from a setting on the computer (an
environment variable). A typed key is held only while mesoSPIM runs, so it has to be typed again
every session.

- **Each person uses their own key.**
- **"Remember this key on this computer"** is a checkbox, off by default. When it is ticked, the
  key is handed to the password store that Windows itself provides. It is encrypted there and tied
  to the Windows account of whoever is logged in. mesoSPIM never writes the key to a file of its
  own. This needs one small extra library.
- **The key is remembered per provider**, so switching provider does not mean typing it again.
- **"Forget key"** removes a remembered key.
- **The checkbox carries a caution**: "Only tick this if you are the only person using this
  Windows account." On a shared login, a remembered key is available to the next person, who would
  then be spending the owner's money.

The key never goes into the shared config file, the log file or a saved conversation. It is also
not part of the text the model reads, so the model cannot repeat it.

The notice next to the key reads something like:

> Use your own API key. Your messages, and the camera images the assistant looks at, are sent to
> the provider you choose, and the use is billed to the owner of the key.

## 10. Checking that the assistant still chooses well

The tests in #8 measure how well the model picks the right call and the right skill. This plan
adds four calls for it to choose from: `get_status`, `search`, `save_skill` and `edit_skill`.
The test cases are extended to cover them. Picking the right skill was the weakest result in #8
(61 of 75), so the cases for skills get the most attention, and every skill in a future starter
set has its description tested in the same way.

## Left out on purpose

- **A spending limit.** The context ceiling already stops a conversation from growing without
  end. A separate budget goes beyond finishing the assistant.

## For later

- **How this reaches the public repository.** Pull request #122 there shows the current window,
  and this plan changes that window. We will decide later whether to update #122 or to propose
  this as a follow-up once #122 is merged.
- **The test on a real microscope from #8.** The sweep with an operator present has not been run
  yet.

## Still to settle

1. **Which skills go into the starter set.** None are written yet.
2. **Whether the documentation is installed along with mesoSPIM.** `search` depends on it. This
   will be checked in the code when the work starts.

## Built so far

Built on 2026-11-18 on top of `remote-control-ai-assistant`. The user-facing description is in
`docs/source/ai_assistant/` (index, skills, configuration) and in the Remote Control page's
section "The log of calls".

### What exists

| Section of this plan | State |
|---|---|
| 1. The setup window | Built. |
| 2. The config file | Built, as one `ai_assistant` block. |
| 3. What "personal" means | Built: the home folder and the password store of whoever is logged in. |
| 4. Skills | Built, except the starter set, which has no skills yet. |
| 5. The chat window | Built. |
| 6. `get_status` and `search` | Built. |
| 7. Saving conversations | Built. |
| 8. Log files | Built. |
| 9. The API key | Built. |
| 10. Checking that the assistant still chooses well | Started: a live walkthrough only (see below). |

### Where it differs from the plan

- **`get_status` is a tool of the assistant, not a new Remote Control call.** While building it
  turned out that Remote Control already has `get_snapshot`, one readout of the whole microscope,
  and that the assistant is already given that readout with every message. A second call with
  the same content for scripts and outside programs would only have duplicated it. `get_status`
  therefore takes the essentials from `get_snapshot` and adds the assistant's own half. Outside
  clients keep using `get_snapshot`, and the number of Remote Control calls stays at 84.
- **The separate "Vision model" box is gone, and nothing replaces it in the tab.** The model that
  answers also looks at the camera images when it can. The config file's `vision` line has three
  states: left out (each model does what it can), `False` (never send images), and `True` (needed
  only to say that a model at the facility's own address accepts images).
- **Stop microscope and Cancel prompt already existed** in the chat window of #8 and were kept as
  they are. Stop microscope is the Stop button this plan asks for.
- **Finished long runs are not a log line of their own.** A call that starts a move or an
  acquisition is logged when it is accepted. Its end shows in the `get_progress` lines that
  follow, which are logged like every other call. Because everything is logged, a client that
  waits for a long run produces many such lines: the assistant asks about seven times a second
  while it waits for a move. The log is complete, and it is not short.
- **The "Saved conversations" button is shown only when saving is on.** With saving off there is
  nothing to list.
- **The assistant is told when to search.** In a first live run the model answered a question
  about "last time" from the memory of its own chat. A chat that has the search now gets a short
  paragraph in its instructions: it remembers this chat only, and searches the saved
  conversations and the documentation before answering about either.
- **`search` builds its index anew for every question.** For the size of the mesoSPIM
  documentation this takes a few hundredths of a second, and the answer always reflects the files
  as they are at that moment, also a conversation that was just removed.

### How it was checked

- **Offline tests:** 1,412 pass. This is the existing suite of the development branch
  (`agent/gui-handles`) with the new tests added. Two recorded "open TIFF" cases fail in the
  container this was built in, as they did before any change.
- **The real window toolkit, off screen:** the tab and the chat window are built with PyQt5, the
  skill lists scroll with twenty skills, and the assistant's question before it saves a skill
  reaches the window from its own thread and the answer gets back.
- **A real model on the simulated instrument** (`desk_walkthrough.py`, Claude Haiku 5.5): 9 of 9
  checks. The status, the search over the documentation, a skill created from a conversation and
  confirmed, that skill loaded in the next chat, an earlier conversation found, an unticked skill
  left alone, and a declined skill not written.
- **The key check against the real provider:** an accepted key and a refused key each gave the
  right answer.

### Not done yet

- **The starter set of system skills.** None are written.
- **The evaluation of section 10.** The recorded tool-choice cases still run without the five new
  tools, so they do not yet measure whether the new tools confuse the choice of the old ones. The
  live walkthrough is a first check, not a measurement.
- **mesoSPIM itself in demo mode.** The tab was run on its own, off screen, not inside the full
  application.
- **The Windows password store.** Remembering a key was tested with a stand-in for the `keyring`
  library, not with the Windows Credential Manager.
- **A microscope.** Nothing here has been run on hardware.
- **The documentation folder of an installed mesoSPIM.** The `documentation` line defaults to the
  `docs/source` folder of a copy of the repository. An installation without that folder needs the
  line set, or `search` says that the documentation is not available.


## An idea for local models: a core set of tools and a catalogue

Added on 2026-11-19. This is an idea to explore, not a decision, and nothing of it is built.

### The problem

We would like the assistant to answer quickly with a model that runs on the microscope computer
itself, such as Gemma 4 with 12 billion parameters ("12B"). The computer we looked at has a
GeForce GTX 1080 Ti graphics card with 11 GB of memory. Two things make this hard:

- **Every request starts with the same long text**, which the model has to read before it can
  answer. A cloud provider reads it once and keeps it. A small graphics card takes much longer.
- **The memory of the card is shared** between the model itself and everything it is reading. A
  12B model, stored compactly (at 4 bits per number), takes roughly 7 to 8 GB, which leaves
  about 3 GB for the conversation. The next size up, Gemma 4 26B, takes about 17 GB and does not
  fit on the card whole. It can still be used, because of how it is built; see "Which models to
  try, and the test" below.

The sizes come from guides on the web (2026-11-19), not from our own measurement.

### Where the long text comes from

Measured on this branch on 2026-11-19. The counts are estimates from the number of characters; a
model counts the punctuation of a tool definition more heavily, so the true total is nearer the
21,000 tokens the tab showed in October.

| Part of the fixed start of every request | Share |
|---|---|
| The definitions of the 88 tools | about 90 % |
| The instructions (the manual and the list of commands) | about 10 % |

- The ten largest tool definitions are 43 % of all tool text. `set_display` and `set_state`
  alone are about a fifth.
- The calls that change what the windows show are about 20 % of the tool text.
- Each message from the operator also carries a readout of the microscope, roughly 300 tokens,
  which stays in the conversation.

This explains an earlier experience. Shortening the wording of the instructions works on a tenth
of the text, and it takes away the sentences the model decides by, so it made the assistant worse
without making it much faster. The tool-choice test in #8 found the same for tool descriptions:
the shorter ones lost a choice wherever they dropped a distinguishing fact. The text has to be
reduced by its structure, not by its wording.

### The idea

Treat the tools the way skills are already treated: the model always knows that a tool exists,
and it fetches the details only when it needs them.

1. **A core set stays as it is.** About twenty tools that are used all the time are offered to
   the model in full, as today: moving the stage, the laser, the filter, the zoom, a snap, a
   look, live, stop and the status.
2. **A catalogue lists all the others** by name with one line each, grouped by what they are
   for: the stage, the light, the camera, the acquisition list, the windows, the analyses. The
   catalogue is always part of the instructions.
3. **Two general tools reach the others.** One returns the full definition of a tool from the
   catalogue, as text. The other runs a tool from the catalogue with the values the model gives.

Safety does not change. Every call still goes through the same checks, limits and busy gate,
because the general tool hands the call to the same dispatcher as every other call.

The definition comes back as text inside the conversation. The start of the request therefore
never changes during a chat. This matters for a local model: its server keeps what it has
already read of an unchanged start (the prompt cache), and only reads what is new. Adding real
tools in the middle of a chat would change the start and throw that away, which is why the tools
are not simply switched on and off.

### What it would give, and what it would cost

- **A shorter start.** A rough guess is that the fixed start drops from about 21,000 tokens to
  about 6,000. The first, slow reading becomes three to four times shorter, and the conversation
  may fit in half the memory, which suits an 11 GB card better.
- **One more step for a rare tool.** The first time the model needs a tool from the catalogue it
  has to fetch the definition first. Everyday requests use the core set and are not affected.
- **More mistakes with values.** A small model writes the values of a call less reliably from a
  description in text than from a real tool definition. The dispatcher refuses a wrong call with
  a reason, and the model can correct it, but this has to be measured.
- **A second way of offering the tools.** This would be for local models only. Cloud models keep
  the full list: they are not slowed by it and they choose better from real definitions. Step 2
  of #8 removed the different tool lists per model on purpose, to keep the assistant lean, so
  this brings one back and is a deliberate change of direction.

### What we decided against

- **Choosing the tools by the meaning of each message** (often called RAG, for
  retrieval-augmented generation: a second model picks the tools that seem to match the message,
  and only those are offered). The list would change with every message, so the prompt cache
  would be lost each time. A wrong pick is also silent: when the right tool is left out, the
  model takes a similar one or says it cannot do the task. It would need a second model on a
  card that is already full.
- **A knowledge graph of the tools.** That pays off with thousands of items that refer to each
  other. Here there are 88 tools and a few dozen pages of documentation. Grouping the catalogue
  by area is all the structure that is needed.
- **Shortening the wording**, for the reasons above.

### Before building it

Two things can be found out without changing any code, and they decide whether this is worth
building.

1. **How much the prompt cache already solves.** Run a local model on the microscope computer
   with room for 32,000 tokens, and compare how long the model's server takes to read the first
   message and the second. If the second takes a second or two, the long start is paid once per
   chat and the rest is tuning. For this the server should keep the model loaded, use a single
   conversation, and store what it has read at 8-bit precision, which halves its memory. The
   steps are in "Which models to try, and the test" below. There is a known risk here: a report
   on the server's issue tracker (llama.cpp issue 21831) describes Gemma models reading the whole
   prompt again on every request. If our test shows the same, the shorter start described above
   is no longer a nice extra but the main route.
2. **Whether the camera images get in the way.** A look runs a conversation of its own on the
   same server and can push the main conversation out of the cache. Setting `'vision': False` in
   the config block for the local model, or letting a cloud model do the looking, avoids it.

If it is built, the test is the existing tool-choice evaluation, run on the local model twice:
once with the full list and once with the core set and the catalogue.

### Other, smaller steps for a local model

- Leave out the calls that change what the windows show. They are about a fifth of the tool
  text, and #8 already lists this as an open decision.
- Make the two largest definitions, `set_display` and `set_state`, smaller by their structure,
  for example by splitting them.
- Clear the context between tasks, so that the conversation stays short.
- Send the microscope's readout with each message only where it changed.
- Switch off the model's "thinking" mode if it has one. A tool call is a short answer.

## Which models to try, and the test

Added on 2026-11-19, after a search of the web. Nothing here has been run by us yet. The figures
are from other people's write-ups, most of them blog posts, so they are indications and not
measurements.

### Three candidates for the 11 GB card

| Model | How it runs on this computer | What speaks for it | What is unknown |
|---|---|---|---|
| **Gemma 4 26B** (its full name is 26B A4B) | On the standard server (llama.cpp), with part of the model in ordinary memory | The model we wanted. Reported at about 20 tokens per second on an older card with only 8 GB. | How fast it reads our long start, and whether it chooses well among 88 tools. |
| **Gemma 4 12B** | Whole on the card, with Ollama or llama.cpp | The simplest setup. About 9 GB with room for 32,000 tokens. | Whether a model this small chooses well among 88 tools. |
| **Qwen3.6 35B** (35B A3B) | On the Colibri engine, which is built for this kind of model | A larger model that uses only a small part of itself per word. | Colibri's fast path is for newer graphics cards than ours, the model is not a Gemma, and it has never been through our evaluation. |

The 26B model is the one to try first.

### Why a 26B model can run on an 11 GB card

Gemma 4 26B is a "mixture of experts" model. It has about 25 billion numbers in all, but for
each word it writes it uses only about 3.8 billion of them: a part that every word needs, and a
few "experts" chosen for that word. The server has a setting made for this (`--n-cpu-moe`). It
keeps the part every word needs on the graphics card, where it is fast, and puts the experts in
the computer's ordinary memory. Because only a few experts are used per word, this costs far
less speed than it would for an ordinary model of the same size. The microscope computer appears
to have about 128 GB of ordinary memory, which is much more than the experts need.

### The test

It takes about half an hour and changes nothing in mesoSPIM.

1. Download llama.cpp for Windows. Take the build for CUDA 12 or the Vulkan build. The newest
   NVIDIA toolkit (CUDA 13) no longer supports a card as old as the GTX 1080 Ti.
2. Download the 4-bit file of Gemma 4 26B A4B (its name ends in `Q4_K_M.gguf`).
3. Start the server. The line below is a sketch and was not run by us; check the names against
   the server's `--help`.

   ```
   llama-server -m gemma-4-26b-a4b-Q4_K_M.gguf -ngl 99 --n-cpu-moe 20 -c 32768 --jinja -ctk q8_0 -ctv q8_0 --port 8080
   ```

   - `-ngl 99` puts as much as possible on the graphics card.
   - `--n-cpu-moe 20` puts the experts of 20 layers in ordinary memory. If the server runs out
     of memory, raise the number. If it loads with room to spare, lower it.
   - `-c 32768` gives room for 32,000 tokens. Our fixed start alone is about 21,000.
   - `--jinja` switches on the model's own way of calling tools.
   - `-ctk q8_0 -ctv q8_0` stores what the model has read at 8-bit precision, which halves its
     memory.
4. In the config file's `ai_assistant` block, set `'address': 'http://localhost:8080/v1'`. In
   the AI Assistant tab choose **OpenAI-style** and start a chat.
5. Send two messages, for example "where is the stage?" and then "move x by 100 micrometres",
   and read the server's log.

### What the log tells us

| In the log | What it means |
|---|---|
| The prompt time of the first message | How long the start of a chat takes. Up to a minute is tolerable, because it happens once per chat. |
| The prompt time of the second message | The number that matters. A few seconds means the server kept what it had read. |
| The words "forcing full prompt re-processing" | The server does not keep it for this model. The shorter tool list then becomes necessary. |
| Tokens per second while answering | About 10 or more feels usable. One request to the microscope takes several answers in a row. |

If the second message is read quickly and the answers make sense, the model is usable as it
stands. The next step is then to run the tool-choice evaluation on it, to see how well it
chooses.

### What the web says about small models in general

- **Fewer tools in view.** Several sources agree that a model chooses less well as the list of
  tools grows or contains similar tools, and that small models suffer more. One guide adds that
  tools in the middle of a long list are chosen less well than those at its start or end. This
  supports a core set of about twenty.
- **Forcing the answer into the tool's shape** is the most recommended way to stop a small model
  from writing a malformed call. For Gemma 4 there is a limit: a discussion on the server's
  tracker says that its way of writing a tool call is not the format the server can force in
  full, so the shape is guaranteed but the values are not.
- **Training a small model further on our own tools** raises its accuracy (Google reports 58 %
  to 85 % for its smallest function-calling model). The tool definitions are still sent with
  every request, so it does not shorten the start. It would be a project of its own.
- **Ollama starts with room for only 4,000 tokens** on a card with less than 24 GB. With Ollama
  this has to be raised (`OLLAMA_CONTEXT_LENGTH=32768`), or every request is refused.

### Sources

- Running Gemma 4 26B on an old GTX 1080 with llama.cpp: <https://dev.to/mdda/running-gemma-4-26b-on-an-old-gtx-1080-with-llamacpp-4fi5>
- Mixture-of-experts models with the experts in ordinary memory: <https://huggingface.co/blog/Doctor-Shotgun/llamacpp-moe-offload-guide>
- Gemma 4 26B A4B, memory and speed: <https://www.llamabuilds.ai/models/gemma-4-26b-a4b>
- Gemma 4 sizes and memory: <https://vdf.ai/blog/gemma-4-on-premises-deployment/>
- The whole prompt read again on every request: <https://github.com/ggml-org/llama.cpp/issues/21831>
- Tool calls without mistakes, and Gemma 4's format: <https://github.com/ggml-org/llama.cpp/discussions/21839>
- How many tools a model should see: <https://arxiv.org/html/2605.24660v1>
- Fine-tuning Google's smallest function-calling model: <https://ai.google.dev/gemma/docs/mobile-actions>
- The Colibri engine: <https://github.com/JustVugg/colibri>

