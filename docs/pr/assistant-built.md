# The AI Assistant: what is built

This file describes what exists on the branch `claude/jolly-tesla-qw0bme`, which sits on top of
the Remote Control and AI Assistant work of pull request #8 on the fork (branch
`remote-control-ai-assistant`). It was built on 2026-11-18 from a planning conversation and two
pages of handwritten notes.

It is one of four files that replaced the single plan on 2026-11-19:

- **This file**: what is built, how it was checked, and what is not done.
- `assistant-decisions.md`: what is decided but not built, and what is still open.
- `assistant-local-models.md`: the research on running a model on the microscope computer.
- `assistant-plan-review.md`: a critical review of all of it.

The description for operators and facilities is in `docs/source/ai_assistant/` (the pages
"AI Assistant", "Skills" and "Configuration") and in the Remote Control page's section "The log
of calls".

**Read this first:** nothing below has been run inside mesoSPIM itself, on Windows, or on a
microscope. See "How it was checked" and "Not done" at the end.

## The three ideas behind it

- **The window is simpler.** An operator chooses a provider, a model, an API key and the skills
  they want, and starts a chat. Everything else is in the config file.
- **Skills are easier to use.** The operator can see them, switch them on and off, create them
  from a conversation and remove them, without editing files by hand.
- **It is clear what is stored and where.** The window says whether conversations are saved, the
  API key is kept hidden, and the log records every call so that a problem can be traced later.

## 1. The setup tab

```
AI Assistant
  Provider   [ ............ ]    Model  [ ................ ]
  API key    [ ************ ]                    ● connected
             [ ] Remember this key on this computer      [ Forget key ]
  Use your own API key. Your messages, and the camera images the assistant looks at, are sent
  to the provider you choose, and the use is billed to the owner of the key.

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
  Status  no chat is open
  [ Start Chat Window ]   [ Saved conversations… ]
  Conversations are not saved. The calls the assistant makes are written to the mesoSPIM log.
```

- **Provider, model and API key** are the only three settings.
- **The light** beside the key is grey until the key has been checked, green when the provider
  accepted it and red when it did not. The check asks the provider for its list of models, which
  costs nothing. It runs when Start Chat Window is pressed.
- **Two lists of skills**, system and personal, each with a tick box per skill. Each list has a
  fixed height and scrolls. Hovering over a skill shows its description.
- **Start Chat Window** checks the key and opens a chat with the ticked skills. With a chat
  already open it brings that chat to the front: there is one chat at a time. While a chat is
  open the setup is locked.
- **Saved conversations…** is shown only when the facility has turned saving on.
- **The last line** always says whether conversations are saved, and that calls are logged.

What left the tab: the box for a separate vision model, the image bin, the focus metric, the
coordinate system, and Connect and Disconnect. All of these are now lines in the config file.

## 2. The config file

One block in the microscope's config file holds everything that left the tab. Every line is
optional, and a line that is left out takes the value shown.

```python
ai_assistant = {
    'system_prompt':      '',                    # a text file with the assistant's instructions
    'system_skills':      'skills',              # the skills that belong to this microscope
    'personal_skills':    '~/mesoSPIM/skills',   # each person's own skills
    'documentation':      '',                    # the folder with the mesoSPIM documentation
    'save_conversations': False,                 # keep each conversation as a file?
    # 'vision':           False,                 # only to forbid sending camera images
    'address':            '',                    # a model reached at your own address
    # 'vision_model':     {'provider': 'Gemini', 'model': 'gemini-3.5-flash-lite'},   # a second model that looks
    'look_bin':           2,                     # how much a camera image is shrunk before it is sent
    'focus_metric':       'laplacian',           # how sharpness is measured
    'axes':               {'x': 'right', 'y': 'up', 'z': 'toward the camera'},
    'request_interval_s': 0,                     # a pause between requests to the model
}
```

- A path that does not start at the root of the disk is read from the folder the config file is
  in. `~` stands for the home folder of whoever is logged in, which is how one shared line gives
  each person a folder of their own.
- **`system_prompt`** left empty means the file that comes with mesoSPIM
  (`mesoSPIM/src/ai_assistant/manual.md`). A change takes effect in the next chat.
- **`documentation`** left empty means the `docs/source` folder of this copy of the repository.
- **`vision`** has three states. Left out, each model does what it can. `False` forbids sending
  camera images for every model. `True` is needed only together with `address`, to say that the
  model there accepts images.
- **`vision_model`** names a second model that looks at the camera images. The model that
  answers then receives no image, only what the second one says about it. Its key is the one
  typed in the tab when both are at the same provider, else the key that person had remembered
  for that provider, else the provider's environment variable. It is checked with the other key
  when a chat starts; if it cannot be used, the chat opens all the same, the model that answers
  looks itself, and the chat's first lines say so. The first version of this work had dropped the
  second model; it was brought back on 2026-11-19. Checked live: Claude Haiku 5.5 answered,
  Claude Sonnet 5.5 looked and counted three objects in a test image, and no image reached Haiku.
- A line that cannot be used is named on the tab's status line, and its usual value is taken.
- The two older lines `ai_assistant_axes` and `ai_assistant_request_interval_s` are still read.

The demo hardware config carries the block as commented lines. The page
`docs/source/ai_assistant/configuration.md` explains each line.

## 3. What "personal" means

mesoSPIM has no notion of a user and adds none. "Personal" follows the account of whoever is
logged in to the computer:

- personal skills are in that person's home folder;
- saved conversations and the remembered ticks are next to them;
- a remembered API key is in that person's entry in the operating system's password store.

On the microscope computers this was built for, each person signs in to Windows with their own
account, so Windows keeps these private. On a computer where everyone shares one login,
"personal" means shared by everyone on that login.

## 4. Skills

A skill is a short procedure in a text file that the assistant follows when a request matches
its description.

| | System skills | Personal skills |
|---|---|---|
| Who writes them | The developers and the facility | Each operator, with Create skill |
| Where they live | The `skills` folder next to the config file | The operator's home folder |
| Who sees them | Everyone on that microscope | Only that person |
| Can the assistant change them | No | Yes, after the operator confirms |

A skill file has a head with a name, a description and optionally the tools it uses, and then
the steps. All three of name, description and steps are required.

- **A chat has exactly the skills that were ticked when it started.** Their names and
  descriptions are in the model's instructions. The ticks are remembered per person.
- **`load_skill`** hands the model the steps of a ticked skill, and refuses one that is not
  ticked for this chat, in plain words.
- **Create skill** is a button in the chat window. It asks the assistant to look back over the
  conversation and propose a skill. The assistant calls `save_skill`; a dialog shows the name,
  the description, the tools and the steps in full; and the skill is written only when the
  operator presses Save skill. Closing the dialog, or Cancel, counts as no.
- **`edit_skill`** changes a ticked personal skill in the same way, and the dialog also shows
  the steps as they were. The assistant can never change a system skill.
- **Remove**, beside a personal skill, asks first and moves the file to a `deleted` folder next
  to the skills, so that a mistake can be undone.
- **A file that cannot be used** is listed greyed out with the reason. **A skill that names a
  tool mesoSPIM no longer has** is marked "out of date", and the chat says so when it opens.
- If a personal skill has the same name as a system skill, both are listed, and the assistant
  knows the personal one as "name (personal)".
- A skill saved during a chat can be used from the next chat. A file changed by hand shows when
  the tab is opened again.

No system skills come with mesoSPIM yet.

## 5. The chat window

- **Clear context (12,400 of 900,000 tokens)**: the button shows the size of the whole
  conversation as the provider last counted it, out of what the chosen model takes. The earlier
  line with the tokens of each request is gone. The number turns orange where a request starts
  to cost more and red at the ceiling, where no message is sent until the context is cleared.
  For a model with no known ceiling the button shows the tokens alone.
- **Create skill**, described above. Without a conversation it says so and sends nothing.
- **Clearing or closing a chat asks first when the conversation is not saved**, because it would
  be gone for good.
- **Tool calls** are shown as grey lines by default, and the Show tool calls box hides them.
- **Stop microscope** and **Cancel prompt** existed in #8 and are unchanged. Stop microscope
  stops the stage and whatever is running, as the main window's Stop does.
- The chat opens with a few grey lines: the skills it has, any skill that is out of date, any
  config line that could not be used, whether camera images are sent, and whether the
  conversation is saved.

## 6. Five new tools for the assistant

They exist only in a chat started from the window. None is a Remote Control call, so scripts and
outside programs do not have them, and the number of Remote Control calls stays at 84.

| Tool | What it does |
|---|---|
| `get_status` | One short answer on where things stand: the microscope (busy or idle, what is running, position, main settings, disk, warnings) and the assistant (provider, model, tokens used out of the ceiling, this chat's skills, whether the conversation is saved). |
| `search` | Looks through the documentation and the operator's saved conversations and returns up to five short excerpts, each with its place. |
| `read_passage` | Returns one of those places in full. |
| `save_skill` | Saves a new personal skill after the operator has confirmed. |
| `edit_skill` | Saves a changed personal skill after the operator has confirmed. |

- `get_status` takes the microscope's part from the existing call `get_snapshot`. The assistant
  is also still given the full readout with every message, as in #8.
- `search` is a ranked word search, using the database that comes with Python. It needs nothing
  installed and no internet connection. It cuts the documentation at its headings and each saved
  conversation into its turns, and it builds its index anew for every question, so the answer
  reflects the files as they are at that moment.
- A chat that has the search is told in its instructions that it remembers this chat only, and
  to search before it answers about an earlier chat or about how mesoSPIM works.

## 7. Saved conversations

- Saving is off unless the config file turns it on, and the tab says which it is.
- When it is on, each chat is one file in the person's own folder, written again after every
  turn. It holds the time, what the operator typed, the calls with their results and the
  replies. Camera images are never written.
- Clear context starts a new file; the cleared conversation stays saved.
- The Saved conversations dialog lists them, and Remove erases one for good after a question.

## 8. The log of calls

Every Remote Control call writes one line to the mesoSPIM log, whoever made it and whether it
changed something or only read a value.

```text
Remote Control | anna | AI Assistant (claude-haiku-5-5) | set_laser {"laser": "488 nm"} | ok | 0.21 s | {"accepted": true, ...}
Remote Control | anna | TCP 192.168.1.20 | move_absolute {"targets": {"x": 99999}} | refused: ... outside the allowed range | 0.00 s
```

From left to right: the person logged in, the client (for the assistant, with the model's
name), the call with its values, how it ended, how long it took to answer, and what came back.

- A camera image or another very long value is replaced by a note of its size.
- The end of a long run is not a line of its own. It shows in the `get_progress` lines that
  follow.
- The API key is not part of any call, and the conversation text is not logged.

## 9. The API key

- The key shows as dots and is held in memory while mesoSPIM runs.
- **Remember this key on this computer** hands it to the operating system's password store, per
  provider. It needs the `keyring` library, which the optional install now includes. Without
  that library the box is greyed out and says why.
- **Forget key** removes a remembered key.
- The key is never written to the config file, to a log or into a saved conversation, and it is
  not part of the text the model reads.

## A fix made along the way

**Claude Opus 5.5 can now be used.** The assistant sends every model a "temperature" of 0, which
asks for its most likely answer, except the models that refuse that setting. Opus 5.5 refuses
it, like Haiku 5.5, and was not on the list, so its first message failed with an error. It is on
the list now. Sonnet 5.5 needs no entry: the library that talks to Anthropic leaves the setting
out for it by itself. Checked against the real model on 2026-11-19.

**An image is judged with `look`.** A model asked to judge an image often took a snap and read
the frame's numbers, or took a snap and then looked, which puts light on the sample twice. The
assistant's descriptions of `get_frame` and `snap` now each say that `look` is the tool for
judging an image. It was tried before it was built, on three sets of cases with Claude Haiku
5.5, and it was the only one of three changes tried that day that held up; see
`assistant-decisions.md`. The commands' own descriptions, which scripts and outside programs
read, are unchanged.

## How it was checked

- **Offline tests: 1,424 pass.** This is the suite of the development branch
  (`agent/gui-handles`) with the new tests added. Two recorded "open TIFF" cases fail in the
  container this was built in, as they did before any change.
- **The real window toolkit, off screen, on Linux.** The tab and the chat window are built with
  PyQt5, the skill lists scroll with twenty skills, and the assistant's question before it saves
  a skill reaches the window from its own thread and the answer gets back.
- **A real model on the simulated microscope** (`desk_walkthrough.py`, Claude Haiku 5.5): 9 of 9
  checks. The status, the search, a skill created from a conversation and confirmed, that skill
  loaded in the next chat, an earlier conversation found, an unticked skill left alone, and a
  declined skill not written.
- **The key check against the real provider.** An accepted key and a refused key each gave the
  right answer.

## Not done

- **mesoSPIM itself.** The tab has not been run in demo mode inside the full application, and
  not on Windows.
- **The Windows password store.** Remembering a key was tested with a stand-in for the `keyring`
  library.
- **A microscope.** Nothing here has been run on hardware.
- **The new tools in the offline tests.** The recorded runs of the cloud models are replayed
  without the five new tools. A targeted check with Claude Haiku 5.5 found that the new tools do
  not make it choose worse among the old ones (38 of 39 cases with and without them, and 12 of 12
  new cases; see `assistant-decisions.md`). That check is a one-off run, not yet part of the
  tests, and Gemini 3.5 Flash-Lite was not checked.
- **The starter set of system skills.** None are written.
- **The documentation folder of an installed mesoSPIM.** An installation without the
  repository's `docs/source` folder needs the `documentation` line set.

## Where the tests are

The offline tests need test support that lives only on the development branch
(`agent/gui-handles`), so the new test files on this branch do not run on this branch alone. Two
of them, `test_assistant_gui.py` and `conftest.py` in `mesoSPIM/test/ai_assistant/`, replace the
files of the same name there. `test_real_pyqt_assistant_smoke.py` runs on this branch with real
PyQt5.
