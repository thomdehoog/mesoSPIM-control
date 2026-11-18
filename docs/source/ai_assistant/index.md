# AI Assistant

An optional chat that drives the microscope in plain language: you type what you want, and the
assistant carries it out with the same commands the Remote Control offers. A [Pydantic AI](https://ai.pydantic.dev)
agent turns the Remote Control commands into tools and dispatches them through the **same**
`Acceptor` as the TCP and MCP transports, so every action obeys the existing accept-validation,
movement limits and one-mutation gate. The tab is idle until the operator sends a message, and the
Assistant and a network transport are mutually exclusive — only one controller holds the session.

This builds on [Remote Control](../remote_control/index.md); read that first.

```{figure} ../../screenshots/AIAssistantTab.png
:alt: AI Assistant tab in the Main window
:width: 60%

The AI Assistant tab: provider, model and API key, the two lists of skills, and Start Chat Window.
```

```{toctree}
:maxdepth: 1

architecture
skills
configuration
```

## Requirements

- `pydantic-ai`, declared as the optional extra `ai-assistant`: install with
  `pip install -e ".[ai-assistant]"`. The extra also keeps the `anthropic` SDK below 1.0, whose
  newer client library pydantic-ai 2.14 cannot drive. It is imported lazily, so the application
  starts and every other feature works without it; the tab reports the missing module when the
  operator sends a first message.
- Your own API key for the chosen provider, or a model that your facility hosts itself (any
  server that speaks the OpenAI API, such as Ollama, vLLM or LM Studio).
  The assistant is built and evaluated for the cloud models Gemini 3.5 Flash-Lite and Claude
  Haiku 5.5.
- The extra also installs `keyring`, the small library that hands a key to the password store of
  the operating system when you tick **Remember this key on this computer**. Without it the
  assistant works and asks for the key each time mesoSPIM starts.

## Setting it up

The tab asks for three things: the **provider**, the **model** and your **API key**. Everything
else is set once by the facility in the microscope's config file (see
[Configuration](configuration.md)).

1. **Choose the provider** (Gemini, OpenAI, Anthropic, or **OpenAI-style** for a model your
   facility hosts itself). The usual model for that provider is filled in; you can type another
   name. Any model the provider serves under your key works by name.
2. **Type your API key.** It shows as dots. An API key is what the provider uses to know who is
   asking and whom to bill, so use your own.
3. **Tick the skills you want** in the two lists below (see [Skills](skills.md)).
4. **Press Start Chat Window.** mesoSPIM first asks the provider whether it accepts the key. That
   request costs nothing. The light beside the key turns green and the chat opens, or the status
   line says what is wrong: the key was not accepted, or the provider could not be reached.

While a chat is open the setup is locked, and Start Chat Window brings the open chat to the
front: there is one chat at a time. Closing the chat window ends the chat and hands the microscope
back, so that the Remote Control tab can start a server without restarting mesoSPIM.

**Where your key goes.** The key is held in memory while mesoSPIM runs. It is never written to
the config file, to a log or into a saved conversation, and the model itself never sees it. When
you tick **Remember this key on this computer**, the key is handed to the password store of your
Windows account (the same place Windows keeps saved network passwords), where it is encrypted, so
you do not have to type it again. Only tick the box if you are the only person who uses that
Windows account: anyone else on it would be spending on your key. **Forget key** removes it
again. An empty key field also falls back to the provider's environment variable
(`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`), for those who prefer to set it there.

**What leaves the microscope computer.** Your messages, and the camera images the assistant
looks at, are sent to the provider you chose, and the use is billed to the owner of the key. A
facility whose samples may not leave the building can forbid sending images with one line in the
config file; the assistant then works from the numbers that describe an image.

**A network that inspects HTTPS.** On a PC whose network re-signs encrypted traffic (an antivirus
or a company proxy), a cloud provider may fail with "could not be reached" although the key is
right: the Python client trusts only its own list of certificates. Point it at one that includes
the Windows roots (`SSL_CERT_FILE=<bundle.pem>` in the environment mesoSPIM starts from).

## Using it

```{figure} ../../screenshots/AIAssistantChat.png
:alt: The chat window of the AI Assistant
:width: 60%

The chat window. The grey lines are the calls the assistant made to the microscope.
```

Type in the chat window. Enter sends; Shift+Enter starts a new line, as in an editor. While the
assistant works the input is locked, so there is one request at a time. The chat opens with a
few grey lines that say which skills this chat has and whether the conversation is saved.

Under the input are four controls, and one beside it:

- **Stop microscope** (beside the input) is the main window's Stop: it stops the stage and
  whatever is running, at once, with nothing of the assistant in between. It stops the assistant
  as well. It works at any time.
- **Cancel prompt** stops the assistant only. The turn ends at once; whatever the microscope was
  already doing carries on.
- **Clear context (12,400 of 900,000 tokens)** empties the conversation and starts afresh, with
  the same model and skills. The numbers show how full the model's memory is: the size of the
  whole conversation as the provider last counted it, out of what this model takes. See *The
  size of the conversation* below.
- **Create skill** asks the assistant to write down the procedure you just did together as one
  of your personal skills. You see the whole text and decide before anything is saved. See
  [Skills](skills.md).
- **Show tool calls** shows or hides the grey lines that list each call the assistant made to the
  microscope. They are on by default, so you can see what the assistant is doing to your
  instrument.

The model is offered every Remote Control command, the same ones TCP and MCP serve. That includes
what the windows show and offer: the live view's levels, lookup tables and regions of interest,
pictures of the windows, the optimizer, the processor chain and the analyses of image files.
Every model is sampled at temperature 0 where it accepts one, which means it gives its most
likely answer and not a creative one.

**Live and long runs do not hold the line.** A live mode the assistant starts returns as soon as
live runs, and from then on it is the operator's live view whoever started it: settings and moves
go through while it runs, from the window or from the assistant, a snap or a run is refused, and
"stop" ends it. An acquisition or time lapse the assistant starts returns as soon as the run is
under way, so the input line is free and "stop" can be typed; the run is refused nothing but
`stop_activity` until it ends, and the main window shows its progress. When it ends, the chat
says so in one grey line ("Acquisition list finished 14:02:00"); no model is asked. A time
course is the software's own time lapse (`time_lapse_start`, an interval and a number of
points); the assistant keeps no timer of its own. During live, a look reads the frame live shows
instead of taking a snap.

**The size of the conversation.** The assistant keeps the whole conversation until **Clear
context**, so every request carries everything said so far, and the number on the button grows
with each message. It turns orange where a request starts to cost more, and red at the model's
ceiling. At the ceiling no new message is sent until you clear the context: nothing is trimmed or
summarised behind your back. Clearing asks first when the conversation is not saved, because it
would be gone for good. On Anthropic models the unchanged start of each request is cached (the
tools and instructions for an hour, the conversation for five minutes), so each request pays in
full only for what is new. Gemini and OpenAI do this on their own.

**What the assistant can look up.** A chat has three tools that touch no instrument:

- `get_status` answers "where do things stand?" in one short reply: the microscope (busy or idle,
  what is running, the position, the main settings, the disk, recent warnings) and the assistant
  itself (the model, how full its context is, this chat's skills, whether the conversation is
  saved).
- `search` looks through the mesoSPIM documentation and your saved conversations and returns a
  few short excerpts with the place of each. Ask "how do I align the light sheet?" or "what did
  we do last time on this sample?".
- `read_passage` reads one of those places in full.

The search is a ranked word search. It finds the passages that contain your words and puts the
best matches first, using the small database that comes with Python, so it needs no internet
connection. It works best with the words the documentation would use.

**Unclear requests and plans.** When a request is unclear, the assistant suggests what it would
do and asks before doing it; when it does not know, it asks. For several steps it says its plan in
a line or two first. The manual stays short and general: each tool's own description says what it
takes, and repeatable workflows belong in skills.

**Frames and the map.** Every frame a look, a snap or live delivers is kept for the session as a
small copy with its number, time, position and settings, and code adds its measures: focus, peak,
and the offset of the sample from the centre with the stage move that would centre it. `look`
takes `frames` ("last 3", "1,7", "3-10") and compares them (image shift, focus, peak); the vision
model sees exactly those frames and keeps only its text afterwards. The readout carries the
history in brief and a map made from it: per zoom and light, where the frames put the sample, its
best focus from the focus curve, the last good light and labelled places. `ask_eyes` puts a
question to the frames seen without taking a new one. Clear context and ending the chat clear
the frames with the transcript.

**Calibrate.** Until calibrated, the move that centres the sample uses the nominal pixel size and
the coordinate system from the config file. `calibrate` moves x and y by
a tenth of the field and back, measures how the image moved, and keeps the result per zoom beside
the configuration, in a git-ignored file. Centring moves then use the measured scale and
direction, which also corrects a coordinate system set the wrong way round.

## Limitations

These are known and deliberate; read them before using the tab on an instrument with a sample
loaded.

- **The safety is the instrument's, for every client alike.** Core's stage limits, the one
  operation at a time and Stop microscope hold whatever the model was told; the assistant adds no
  gate of its own. Load, unload, preview, calibrate and any move within the limits run when asked.
  When a request leaves a value or a choice open, the model is told to ask; otherwise it acts and
  says what it chose. Every refusal carries its advice where the model reads it next.
- **The model call has no time limit of its own.** `WAIT_CAP_S` bounds the microscope leg only. If
  the endpoint stalls (a burst over a tokens-per-minute quota is the usual cause), the turn waits
  until the HTTP layer gives up; **Cancel prompt** ends it at once.
- **A commanded move smaller than `POSITION_TOLERANCE` completes without verifying motion.**
  Arrival is tested as `abs(observed - target) > tolerance`, so with the default 1.0 µm a 1 µm move
  from the current position is "already reached" on the first poll and is reported as a successful
  arrival.

## Privacy

**Conversations are not saved unless the facility turns saving on**, and the tab always says
which it is. When saving is off, prompts, replies, tool calls and frames stay in memory and are
discarded by Clear context, by ending the chat, or by closing mesoSPIM.

When saving is on, each chat is written to a file in your own folder
(`~/mesoSPIM/conversations` unless the config file says otherwise): what you typed, the calls the
assistant made with their results, and its replies. Camera images are never written, only the
numbers that describe them. The **Saved conversations…** button lists your saved conversations,
and **Remove** erases one for good; it then no longer turns up in a search.

**Every call to the microscope is written to the mesoSPIM log**, whether conversations are saved
or not: the person logged in, the model, the call with its values and how it ended. The log holds
calls only, never what you typed or what the model answered. It is there so that a problem can be
traced afterwards. See [Remote Control](../remote_control/index.md).

What leaves the machine is what a cloud provider receives to answer a turn; a model your
facility hosts keeps everything on its own server.

## Testing

`python mesoSPIM/test/remote_control/run.py pyqt` runs the real-PyQt scripts, among them one that
builds the tab offscreen and checks the setup layout, the scrolling skill lists, the input keys,
and that the assistant's question before it saves a skill reaches the window from its own thread.
No model, network or hardware is involved.

`python -m mesoSPIM.test.ai_assistant.live.desk_walkthrough` runs a real model on the simulated
instrument through the status, the search, a skill created from a conversation and loaded in the
next chat, an unticked skill left alone and a declined skill not written. It needs an API key in
`ANTHROPIC_API_KEY` and costs a few cents.
