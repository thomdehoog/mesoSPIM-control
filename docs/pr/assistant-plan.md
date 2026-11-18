# Finishing the AI Assistant: the plan

Written on 2026-11-18, from a planning conversation and two pages of handwritten notes. Nothing in
this file is built yet. It describes what we agreed to build on top of the Remote Control and AI
Assistant work in pull request #8 on the fork (branch `remote-control-ai-assistant`).

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

## 3. What "personal" means

mesoSPIM has no notion of a user today. Nobody logs in to it. The plan does not add accounts or
passwords to mesoSPIM. Instead, "personal" follows the account of whoever is logged in to the
computer:

- Personal skills live in that person's home folder.
- Saved conversations live there too.
- A remembered API key is kept in that person's entry in the Windows password store.

Where each person signs in to Windows with their own account, Windows already keeps these private.

There is one honest limit. On a computer where everyone shares one Windows login, "personal" means
shared by everyone on that login. A facility in that situation can also choose to write a fixed
path in the config file and accept that. The notice in the window says where things are stored, so
that nobody is surprised.

**This is an assumption that still needs confirming**: how people sign in on the microscope
computers we have in mind.

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
- **The lines go into the existing log file**, so that one timeline shows each request next to
  what the microscope did.

```
14:02:11  Remote Control  AI Assistant (claude-haiku-5-5)  set_laser {"laser": "488 nm", "intensity": 10}  ok  0.21 s
14:02:15  Remote Control  TCP 192.168.1.20                 move_absolute {"x": 1200}                       refused: outside the x limits
14:03:40  Remote Control  MCP                              run_acquisition_list {}                         ok  184.5 s
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

## Still to settle

1. **How people sign in on the microscope computers**: each with their own Windows account, or
   one shared login. Section 3 assumes the first.
2. **Which skills go into the starter set.** None are written yet.
3. **Whether the documentation is installed along with mesoSPIM.** `search` depends on it. This
   will be checked in the code when the work starts.
