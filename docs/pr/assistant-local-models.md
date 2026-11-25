# The AI Assistant: research on local models

This file is research, not a plan. It asks whether the assistant could answer quickly and well
with a model that runs at the facility instead of at a cloud provider, and it records what was
found on 2026-11-19. **Nothing in it is built, and most of it is not measured by us.**

What exists is in `assistant-built.md`. Decisions and open questions are in
`assistant-decisions.md`, where "whether to pursue a local model at all" is one of them. A
critical review is in `assistant-plan-review.md`.

## Where this stands

| Question | What we know | How we know |
|---|---|---|
| How large is a request? | 12,987 tokens for Gemma with all 88 tools | Counted by Google |
| Can Gemma 4 26B use the tools? | Yes: 115 of 137 cases, against 121 for Claude Haiku 5.5, and every safety case passed | Our evaluation, see `assistant-model-comparison.md` |
| Is there a better local model? | Probably one. Inkling 2 Mini, released on 2026-11-20, passes 127 and every safety case. Other people run it on cards smaller than ours, but the software for it is days old and still has faults; see "Running Inkling 2 Mini on the microscope computer". Qwen3.6-35B-A3B scores 118 but obeys instructions planted in the readout. The 14B models pass about 102. A third candidate, SenseNova Loop-9B, is small enough to fit on the card whole. Its compressed copy passed 46 of 50 cases on a slow machine of ours, about level with Gemma on the same cases, but it tends to do more than was asked. | The same, and other people's write-ups |
| Does a shorter tool list hurt? | No visible harm on 23 cases | Our evaluation, a small sample |
| Does the model fit on the microscope computer? | Probably, with part of it in ordinary memory | Other people's write-ups |
| How fast is it there? | Unknown | Not measured |
| Does the server keep what it has read? | Unknown, and there is a report that it does not for Gemma | Not measured |
| Does it disturb an acquisition? | Unknown on the microscope computer; not at all on a second computer | Not measured |

The first thing to do is the test in "Which models to try, and the test". Until then the rest is
preparation.

## An idea: a core set of tools and a catalogue

This is an idea to explore, not a decision, and nothing of it is built.

### A rule for everything in this file

**Nothing that is done for a local model may make the assistant worse with a cloud model.** The
assistant was built and evaluated for Gemini 3.5 Flash-Lite and Claude Haiku 5.5, and most
facilities will keep using one of them. So:

- **Every change for local models is switched on only when a local model is chosen.** With a
  cloud model the instructions, the list of tools and the behaviour stay exactly as they are
  today. This holds for the core set and the catalogue, for a shorter readout, and for anything
  else in this section.
- **Settings for the local model get their own place in the config file**, apart from the lines
  that hold for every model. One example shows why. We suggest switching camera images off for
  a local model, so that a look does not push the main chat out of the server's memory. The
  `vision` line as it is today holds for every model, so using it for this would take the images
  away from the cloud models too. The setting has to sit with the local model.
- **A local model uses memory and the processor only when it is in use.** A facility that works
  with a cloud model starts no server, and nothing is taken from mesoSPIM.
- **The tests have to guard this, and today they only half do.** The recorded runs of the cloud
  models are replayed offline, and they fail when a request to the model grows or its score
  changes. They must keep passing unchanged after every step taken for local models. But they
  run without the five tools that every chat from the window now has, so they do not yet test
  what operators use. See rule 9 in `assistant-decisions.md`.

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

Measured on this branch on 2026-11-19. How many tokens a request is depends on who counts: each
family of models cuts text into tokens in its own way. For one request with all 88 tools, Google
counts 12,987 tokens for Gemma, and Anthropic counted about 21,000 for Claude (the figure the tab
showed in October). The shares below are by number of characters and hold for both.

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

- **A shorter start.** For Gemma, a request with all 88 tools is 12,987 tokens by Google's
  count, and a core set of 22 tools on its own is 2,994. With the catalogue and the two general
  tools added, the fixed start is about 40 % of the full list by number of characters, so
  roughly 5,500 tokens; the provider has not counted that one. The first, slow reading becomes
  more than twice as short, and the conversation has more room on an 11 GB card.
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
   same server and can push the main conversation out of the cache. Switching camera images off
   for the local model only, or letting a second model do the looking, avoids it. Neither can be
   set today: the `vision` line holds for every model, and the separate vision model was removed
   (see the open questions in `assistant-decisions.md`).

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

Written after a search of the web on 2026-11-19. Nothing in this section has been run by us.
The figures are from other people's write-ups, most of them blog posts, so they are indications
and not measurements. Not one of the server's settings below has been tried by us.

### Three candidates for the 11 GB card

| Model | How it runs on this computer | What speaks for it | What is unknown |
|---|---|---|---|
| **Gemma 4 26B** (its full name is 26B A4B) | On the standard server (llama.cpp), with part of the model in ordinary memory | The model we wanted. Reported at about 20 tokens per second on an older card with only 8 GB. | How fast it reads our long start, and whether it chooses well among 88 tools. |
| **Gemma 4 12B** | Whole on the card, with Ollama or llama.cpp | The simplest setup. About 9 GB with room for 32,000 tokens. | Whether a model this small chooses well among 88 tools. |
| **Qwen3.6 35B** (35B A3B) | On the Colibri engine, which is built for this kind of model | A larger model that uses only a small part of itself per word. | Colibri's fast path is for newer graphics cards than ours, the model is not a Gemma, and it has never been through our evaluation. |

The 26B model is the one to try first.

**Since settled by the evaluation of 2026-11-19** (`assistant-model-comparison.md`): Gemma 4 26B
is the candidate. Qwen3.6-35B-A3B is ruled out, because it follows instructions that are planted
in the microscope's readout. Gemma 4 12B could not be evaluated, because no service we could
reach offers it; two models of its size class, Qwen3-14B and Ministral 14B, did clearly worse
than the 26B, mostly on camera images.

**Added on 2026-11-21: a second candidate, Inkling 2 Mini.** Thinking Machines Lab released
this open model on 2026-11-20. It is of the same kind and size as Gemma 4 26B (26.4 billion
numbers, about 3.4 billion used per word, about 20 GB), and it takes images. On our evaluation it
passed 127 of 137 cases, against 115 for Gemma 4 26B, and it ignored planted instructions in 20
runs of 20. The details, and one habit to watch, are in `assistant-model-comparison.md`.

The model can be downloaded, and the server we planned to use (llama.cpp) has been able to run
it since the day of the release. Both are very new. What other people report, and what it means
for our card, is in "Running Inkling 2 Mini on the microscope computer" below. The test stays as
it is, with Gemma 4 26B first, and Inkling 2 Mini is tried the same way afterwards.

The model is also made to be trained further on one's own examples, and its maker offers a
service for that. This matters if we ever train a small model for the assistant.

**Added on 2026-11-24: a third candidate, SenseNova Loop-9B.** SenseTime released this open
model around 2026-11-17, after a preview at the end of October. When this was written, nothing
about it had been run by us. A day later it was; see "Changed on 2026-11-25" below.

- **It is small.** It has about 9 billion numbers. Like Inkling 2 Mini it passes through its
  middle layers more than once: 32 layers are run as 56 steps.
- **It takes images**, and its maker names tool use and tasks of many steps among the things it
  is meant for. It can hold a conversation of up to 512,000 tokens.
- **It fits on the 11 GB card whole.** The maker's own 4-bit file is 5.8 GB, and the file that
  lets the model take images adds 0.9 GB. For a conversation of up to 64,000 tokens the maker
  gives about 10 GiB of the card's memory in all. That leaves room for a long conversation, with
  nothing in ordinary memory. This is what the other two candidates cannot offer, and it is the
  reason to look at this model.
- **Its maker says it keeps up with Qwen3.8-27B** on a selection of tests of tool use. That is
  the maker's own comparison.

**Changed on 2026-11-25: we ran it.** No shared service offers the model. OpenRouter does not
list it, and Hugging Face names no provider for it. So it was run on the machine where the
evaluation itself runs, which has four processor cores and no graphics card. The result is in
`assistant-model-comparison.md`, "A tenth model, on 50 of the cases: SenseNova Loop-9B". In
short:

- **The 4-bit copy passed 46 of 50 cases**, three of every kind of case. On the same 50 cases
  Gemma 4 26B passed 45, Inkling 2 Mini 50, and the two 14B models 44 and 43.
- **Tool calls and images both work** with the maker's files. It passed all ten cases in which
  it had to look at a camera image, and all four safety cases.
- **In three of its four misses it did more than was asked.** In one case it would not accept
  that an acquisition had run, and it started the acquisition twice more.
- **On processor cores it is far too slow to use**: about 5 tokens per second read and 1.5
  written. This says nothing about the graphics card.

This also settles some of what was open on 2026-11-24:

- **The licence** is Apache 2.0, by the maker's own page for the model.
- **The software.** The maker's page asks for the maker's own copy of llama.cpp. The proposed
  change to llama.cpp itself (number 32588) was still a proposal on 2026-11-24, but built from
  it, the server ran the maker's 4-bit file with tool calls and images. We built it for
  processors only.
- **The maker names one weakness that touches us**: the model "may misunderstand or miss
  requirements when given many complex tool definitions or multiple constraints". With our 88
  tools this did not show in 50 cases.

What is still not settled:

- **Whether the server runs it on our graphics card.** The card needs the server built for
  CUDA 12.4, as described for Inkling 2 Mini below, and nobody has reported this model on a
  card as old as ours.
- **How fast it is there.** It fits on the card whole, so it should be much faster than a model
  that is split between the card and ordinary memory. We have no measurement.
- **The other 87 cases**, and whether it ignores a planted instruction on every repeat. Once
  the microscope computer serves the model, the whole evaluation can be pointed at it and
  should take minutes per run, not days.

What we ran, as a starting point for the microscope computer. The last line is for a graphics
card and was not tried by us; the rest is what ran here.

```
llama-server -m SenseNova-Loop-9B-Q4_K_M.gguf --mmproj mmproj-SenseNova-Loop-9B-F16.gguf
  --jinja -c 32768 --parallel 1 -fa on -ctk q8_0 -ctv q8_0
  -ngl 99
```

`--jinja` is needed for tool calls. `-c` is the room for the conversation in tokens; one request
of ours starts at about 14,600 tokens for this model, so 32,768 is a floor and the card should
hold twice that. `-ctk q8_0 -ctv q8_0` store the conversation in less memory. `-ngl 99` puts the
whole model on the card.

The model is also a natural one to train further (see the end of this file): it is small, and
its maker published guidance for training it.

### Other models that were looked at

- **They fit on the card whole**, like Gemma 4 12B: Qwen3-14B (about 8 to 9 GB), Ministral 3 14B
  (about 8.2 GB), Qwen3.5 9B and Granite 4.2 8B.
- **Built from experts, medium size**, like Gemma 4 26B: gpt-oss-20b, OpenAI's open model. One
  guide reports 53 to 85 tokens per second for it on a 12 GB card.
- **Built from experts, very large**: gpt-oss-120b (117 billion numbers, about 5 billion used
  per word) and Mistral Small 4 (119 billion, about 6.5 billion used per word). They need about
  63 GB of ordinary memory for themselves, so they are only worth considering if the computer
  has 128 GB. One person reports 18 to 22 tokens per second for gpt-oss-120b on a 12 GB card
  with 128 GB of memory; another calls it unreliable on the same kind of card.
- **They do not fit**: ordinary models above about 14B, such as Gemma 4 31B. They are not built
  from experts, so splitting them between the card and ordinary memory makes every word slow.

The guides disagree with each other, and none tests a list of 88 tools at a microscope. Our own
evaluation is the only measurement that counts. It can be run through a hosting service for
cents per model, before anything is downloaded.

### Running Inkling 2 Mini on the microscope computer

Written after a search of the web on 2026-11-21, one day after the model came out. Nothing here
has been run by us. Most of it comes from other people's posts and from the discussion among the
people who wrote the software, and several pages could only be read as summaries. The software
changes daily, so every point should be checked again on the day of the test.

**The software exists, and it is very new.** Support for the model was added to llama.cpp on
2026-11-20. A version from before that date cannot load the model. Ollama lists the model too.

**Our card needs the right build of the server.** The GTX 1080 Ti belongs to an older generation
of cards (Pascal). The newest builds of llama.cpp are made for a newer version of NVIDIA's
software (CUDA 13), which no longer supports that generation; on such a card they fall back to
the processor without saying so. The build to download for Windows is the one marked CUDA 12.4.

**There are two families of model files, and they do not mix.** A model file for llama.cpp has
the ending `.gguf`.

- The files from the llama.cpp project itself (`ggml-org/Inkling-2-Mini-GGUF`) work with the
  ordinary server.
- The files from Unsloth (`unsloth/Inkling-2-Mini-GGUF`) are laid out differently and need
  Unsloth's own build of the server. People who loaded them in the ordinary server got an error.
  Unsloth also offers a folder `Mainline_Support` for the ordinary server, which on 2026-11-21
  still needed a change that had not been accepted yet.

**The compressed copies and their sizes.** These are Unsloth's; the smaller the file, the more
has been rounded away.

| Copy | Size | On an 11 GB card |
|---|---|---|
| Q8 (the least compressed) | 22.9 GB | Mostly in ordinary memory |
| Q4_K_XL, the one Unsloth recommends | 17.6 GB | Part on the card, part in ordinary memory |
| Q4_K_M | 16.6 GB | The same |
| Q3_K_XL | 13.6 GB | The same; this is what ran on an 8 GB card |
| IQ3_XXS | 11.7 GB | Just too large to sit on the card whole |
| Q2_K_XL | 9.1 GB | Fits whole, with about 2 GB left for the conversation |
| IQ2_XXS | 8.5 GB | Fits whole |

A second, small file (the "projector") is needed for the model to take images.

**Two ways to run it on our card.**

1. *A 3-bit or 4-bit copy, split between the card and ordinary memory.* This is the arrangement
   planned for Gemma 4 26B. One person reports 17 tokens per second for the answer and about 100
   tokens per second for reading, with the Q3_K_XL copy on an 8 GB card (RTX 4060) and 16 GB of
   ordinary memory, at a conversation of 10,000 tokens. Another reports 24 tokens per second for
   the Q4_K_XL copy on a 12 GB laptop card. Both cards are newer than ours.
2. *A 2-bit copy, whole on the card.* One person runs it this way on a 12 GB card. It should be
   the fastest, but it leaves little room for a conversation of our length, and nobody knows what
   so much rounding does to the choice among 88 tools. It would have to go through our
   evaluation first.

**A place to start** for the first way, put together from two of these reports and not tried by
us:

```
llama-server -m <the model file> --mmproj <the projector file> --jinja
  -c 32768 -ngl 99 --n-cpu-moe 12 -fa on -ctk q8_0 -ctv q8_0 -ub 512 --parallel 1
```

`-c` must be given: left out, the server prepares for the model's full million tokens and runs
out of memory. `--mmproj` names the projector file for images, `-ub 512` limits how much is read
in one go, and `--parallel 1` says that one conversation is served at a time. The other settings
are explained in "The test" below.

**What to expect, and what to watch.**

- *The first message is slow.* At about 100 tokens per second, reading our start of 13,000
  tokens or more takes about two minutes. After that the server should keep what it has read, so
  that only the new part of each message is read. Whether it really does is the first thing to
  check, as for Gemma.
- *The repeated layers cost memory three times.* Each of the three passes keeps its own notes
  on the conversation, so the conversation takes more memory than for a model of this size
  without the repeats.
- *A large working buffer.* People report that the server sets aside many gigabytes for reading
  when the conversation is allowed to be long. A modest `-c` and a smaller `-ub` keep this down.
  One of the software's authors called this "unusable as it is right now" for long conversations
  and it was being worked on.
- *A possible fault in exactly our arrangement.* One report says the model writes nonsense on
  long messages when the experts of the repeated layers are kept in ordinary memory, and that
  keeping only the other layers' experts there avoids it. We could not read that report itself,
  so it is a warning and not a fact.
- *Tool calls and images.* One person reports that both work. A fault is reported for a related
  feature (answers forced into a given form come back empty). Our assistant asks for tool calls
  in the standard way, which is not the same thing, but it has to be seen to work.
- *The compressed copy is not the model we evaluated.* The 127 cases were passed by the model as
  its maker serves it. The copy on our computer has to pass the same evaluation, which can be
  pointed at the local server.

**What we suggest.** Wait a week or two before spending an afternoon on this: the software is
days old and its faults are being fixed daily. Do the test with Gemma 4 26B as planned. Then try
Inkling 2 Mini with the same steps: the CUDA 12.4 build, the 4-bit copy from the llama.cpp
project, the settings above, and our 137 cases against the local server.

### Why a 26B model can run on an 11 GB card

Gemma 4 26B is a "mixture of experts" model. It has about 25 billion numbers in all, but for
each word it writes it uses only about 3.8 billion of them: a part that every word needs, and a
few "experts" chosen for that word. The server has a setting made for this (`--n-cpu-moe`). It
keeps the part every word needs on the graphics card, where it is fast, and puts the experts in
the computer's ordinary memory. Because only a few experts are used per word, this costs far
less speed than it would for an ordinary model of the same size. The experts of this model
need about 15 GB of ordinary memory. The computer has at least 64 GB (see "Memory: what goes
where" below), which is plenty.

### Where the model should run: a second computer first

There are two places a local model can run, and they should be considered in this order.

1. **On a second computer, reached over the network.** The model's server runs on another
   machine at the facility, and the config file's `address` line points mesoSPIM at it. This
   already works today and needs no change to mesoSPIM. It takes no memory and no processor time
   from the microscope computer, so it cannot disturb an acquisition, and the second computer can
   have a better graphics card than the microscope computer does. One server can also serve
   several microscopes. Camera images then travel over the facility's own network and stay
   inside it.
2. **On the microscope computer itself.** Nothing else has to be bought or kept running, and
   the microscope works without a network. But the model then shares the processor, the memory
   and the disk with a running acquisition, and a disturbed acquisition is lost data. This route
   has to prove that it leaves acquisitions alone before it is chosen; the check is under
   "Sharing the computer with the microscope" below.

The test below is written for the microscope computer, because that is the harder case and the
one that was asked about. Its first five steps are the same on any computer.

### The test

Allow an afternoon; it changes nothing in mesoSPIM. While it runs, keep Task Manager
open on Performance, GPU: "Dedicated GPU memory" should stay below 11 GB and "Shared GPU memory"
should stay near zero.

1. Download llama.cpp for Windows. Take the build for CUDA 12 or the Vulkan build. The newest
   NVIDIA toolkit (CUDA 13) no longer supports a card as old as the GTX 1080 Ti.
2. Download the 4-bit file of Gemma 4 26B A4B (its name ends in `Q4_K_M.gguf`).
3. Start the server. The line below is a sketch and was not run by us; check the names against
   the server's `--help`.

   ```
   llama-server -m gemma-4-26b-a4b-Q4_K_M.gguf -ngl 99 --n-cpu-moe 20 -c 32768 --jinja -fa on -ctk q8_0 -ctv q8_0 -t 6 --cache-ram 8192 --port 8080
   ```

   - `-ngl 99` puts as much as possible on the graphics card.
   - `--n-cpu-moe 20` puts the experts of 20 layers in ordinary memory. If the server runs out
     of memory, raise the number. If it loads with room to spare, lower it.
   - `-c 32768` gives room for 32,000 tokens. Our fixed start alone is about 13,000 for Gemma.
   - `--jinja` switches on the model's own way of calling tools.
   - `-fa on` switches on a faster and leaner way of computing ("flash attention"). The next
     setting needs it. Older versions of the server take `-fa` without the word `on`.
   - `-ctk q8_0 -ctv q8_0` stores what the model has read at 8-bit precision, which halves its
     memory. `q4_0` halves it again, but we would try that last: a model that calls tools is the
     first to suffer from the lost precision.
   - `-t 6` is the number of processor cores the server uses. The experts in ordinary memory are
     computed by the processor for every word, so this number matters here. Count the real cores
     of the computer, not the doubled number that Windows shows as "logical processors" (Task
     Manager shows both under Performance, CPU), and give the server about two fewer than that,
     so that some are always free for mesoSPIM. The 6 in the line above is for a computer with 8.
   - `--cache-ram 8192` lets the server keep up to 8 GB of snapshots of what it has read in
     ordinary memory; the number is in megabytes. See "Memory: what goes where" below.
4. Stop the graphics driver from quietly using ordinary memory as graphics memory. In the
   NVIDIA Control Panel, under Manage 3D settings, set **CUDA - Sysmem Fallback Policy** to
   **Prefer No Sysmem Fallback**. Without this, when the card is full the driver moves the
   overflow into ordinary memory without saying so ("Shared GPU memory" in Task Manager), and the
   model becomes many times slower. With it, the server stops with an out-of-memory message
   instead, which tells us to raise `--n-cpu-moe`. Keeping the card below about 90 % full has
   the same effect.
5. In the config file's `ai_assistant` block, set `'address': 'http://localhost:8080/v1'`. In
   the AI Assistant tab choose **OpenAI-style** and start a chat.
6. Send two messages, for example "where is the stage?" and then "move x by 100 micrometres",
   and read the server's log.

### Memory: what goes where

The computer has two kinds of memory, and the difference decides everything here. The graphics
card has 11 GB of its own, which is fast. The computer's ordinary memory (RAM) is much larger and
much slower for this work. Ordinary memory lets things fit; only the card makes them fast.

How much ordinary memory the computer has is not settled. Task Manager's "Shared GPU memory" of
63.9 GB is normally half of what is installed, which would mean 128 GB, but it may be 64 GB.
Task Manager shows the real figure under Performance, Memory. The plan below works with 64 GB.

| What | Where it lives | How much |
|---|---|---|
| The part of the model every word needs | The graphics card | A few GB |
| The conversation the model is reading | The graphics card | About 1 GB per 24,000 tokens, half that at 8-bit precision |
| The model's experts | Ordinary memory | About 15 GB for Gemma 4 26B |
| Snapshots of what the model has read | Ordinary memory | 8 GB to start |
| mesoSPIM and Windows | Ordinary memory | Everything that is left, about 40 GB of 64 |

**Snapshots are the best use of spare ordinary memory.** The risk we found is that the server
reads the whole conversation again on every request for Gemma models. The server's own remedy is
to keep snapshots of what it has read and to continue from the latest one. They also protect the
main chat when the assistant looks at a camera image: the look is a conversation of its own, and
without a snapshot it pushes the main chat out. Three settings belong to this. The names should
be checked against the server's `--help`, as we have not run them.

- `--cache-ram` sets how much ordinary memory the snapshots may use, in megabytes.
- `--ctx-checkpoints` sets how many snapshots are kept per conversation.
- `--swa-full` makes a Gemma model keep its full history, so that what it has read can be reused
  as with other models. It costs more of the card's memory, so try it only if the log still
  shows the whole prompt being read again.

**The conversation itself should stay on the card.** The server can also keep the conversation
in ordinary memory (`--no-kv-offload`), which removes the limit on its size. Every request then
works through that memory at the processor's pace, and a chat that takes minutes to answer is of
no use at a microscope. We do not plan to use this.

**How large the conversation can realistically be.** The model accepts 256,000 tokens, which is
out of reach here. The card's memory should allow 64,000 or more. What limits it in practice is
the time it takes to read, so we start at 32,000 and go to 64,000 only if the second message is
read quickly and the card's memory stays well below 11 GB. With all 88 tools the fixed start is
about 13,000 tokens, which leaves room for roughly 8 to 12 exchanges in 32,000; with a core set
and a catalogue it is about 5,500, which leaves room for roughly 12 to 17. Small models also
choose less reliably as a conversation grows, so clearing the context between tasks stays good
practice whatever the memory allows.

**With 64 GB, the largest models drop off the list.** gpt-oss-120b and Mistral Small 4 are built
from experts too and could run the same way, but they need about 63 GB of ordinary memory for
themselves. They are only worth considering if the computer turns out to have 128 GB.

### Sharing the computer with the microscope

This computer runs the microscope. The assistant must never get in the way of an acquisition.

- **Memory.** During an acquisition mesoSPIM holds camera frames in memory and writes large
  files, and Windows uses spare memory to keep that writing smooth. The model and its snapshots
  must leave enough for that, which is why the snapshots start at 8 GB and not at 30.
- **The processor.** With the experts in ordinary memory, the processor computes part of every
  word the model writes. An acquisition needs the processor too. This is why the server gets
  fewer cores than the computer has.
- **The check.** After the test above, run an acquisition in demo mode and chat with the
  assistant while it runs. The acquisition's progress must not stutter and no frame may be lost,
  and the chat should stay usable. If the two disturb each other, lower `-t` first.
- **If the check fails**, the model belongs on a second computer, as described under "Where the
  model should run".

### What we expect for responsiveness

The aim is that the assistant stays responsive and does not waste the computer. Whether it does
is exactly what the test measures, so this is an expectation and not a promise.

| Moment | What we expect | What it depends on |
|---|---|---|
| The first message of a chat | Slow, perhaps up to a minute | Reading the fixed start once. A shorter tool list shortens it. |
| Every later message | A few seconds before the answer starts | The server keeping what it has read. This is the open question for Gemma. |
| The answer itself | About 20 tokens a second, which is roughly 15 words | Reported by others for this model on a similar card. |
| A request that needs several calls | Several times the above | Each call is a new request to the model. |
| During an acquisition | No effect on the acquisition | Enough memory and processor cores left for mesoSPIM. |

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
  to 85 % for its smallest function-calling model). By itself it does not shorten the start,
  because the tool definitions are still sent with every request. It would be a project of its
  own; the idea is worked out in "An idea for later: training a small model on our own examples"
  at the end of this file.
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
- Inkling 2 Mini, from news reports of 2026-11-20 and 2026-11-21, because the company's own page
  (`thinkingmachines.ai/news/inkling-2-mini/`) could not be read from where the evaluation runs:
  [Online Tech Tips](https://www.online-tech-tips.com/thinking-machines-releases-open-weight-inkling-2-mini/),
  [DEV Community](https://dev.to/delimitter/thinking-machines-releases-inkling-2-mini-an-open-looped-transformer-that-runs-on-a-laptop-7g3),
  [TestingCatalog](https://www.testingcatalog.com/thinking-machines-releases-inkling-2-mini-looped-open-model/).
- SenseNova Loop-9B, read on 2026-11-24, most of them only as summaries:
  the maker's announcement (<https://www.sensetime.com/en/news/sensenova-loop-9b-release-20261118-1435>),
  a news report (<https://technode.com/2026/11/18/sensetime-releases-open-weight-loop-9b-model-that-reuses-layers-for-deeper-computation/>),
  the maker's files (<https://huggingface.co/sensenova/SenseNova-Loop-9B> and
  <https://huggingface.co/sensenova/SenseNova-Loop-9B-GGUF>),
  the proposed change to llama.cpp (<https://github.com/ggml-org/llama.cpp/pull/32588>),
  and a guide to running it (<https://openclawdc.com/blog/can-i-run-sensenova-loop-9b-locally/>).
  On 2026-11-24 the maker's page for the model and its files were downloaded and read in full;
  the file sizes, the licence, the memory figures and the weakness quoted above are from there.
- Running Inkling 2 Mini locally, read on 2026-11-21, several of them only as summaries:
  the change that added the model to llama.cpp (<https://github.com/ggml-org/llama.cpp/pull/32694>),
  Unsloth's own change (<https://github.com/ggml-org/llama.cpp/pull/32695>),
  Unsloth's files (<https://huggingface.co/unsloth/Inkling-2-Mini-GGUF>) and guide
  (<https://unsloth.ai/docs/models/inkling-2-mini>),
  the llama.cpp project's files (<https://huggingface.co/ggml-org/Inkling-2-Mini-GGUF>),
  the report from an 8 GB card (<https://x.com/analogalok/status/2124135141555720653>),
  a guide for 12 GB cards (<https://aliteq.com/can-you-run-inkling-2-mini-locally>),
  the memory needed for long conversations (<https://vramcalculator.com/inkling-2-mini-vram/>),
  and older cards after CUDA 13 (<https://localaimaster.com/blog/pascal-gpus-after-cuda-13>).

## First results: Gemma 4 26B, tried through Google's service

Added on 2026-11-19. Google serves the open Gemma 4 models through the same service as its
Gemini models, so the 26B model could be put through our existing evaluation without setting it
up on a computer of our own. This shows **how well the model chooses and uses the tools**. It
says **nothing about speed** on the microscope computer, because Google's servers did the work.

**It is also the best case for accuracy.** Google runs the full model. On the microscope computer
the same model would be stored at 4 bits per number, with what it has read stored at 8 bits. Both
cost some accuracy, and a model that calls tools is the first to show it. The local model can be
expected to do somewhat worse than the figures below, and by how much is not known.

The evaluation ran on the simulated microscope. Nothing real was moved.

### How large a request is for this model

Counted by Google for one request, a question that needs no tool:

| What the model is offered | Tokens in one request |
|---|---|
| All 88 tools, as the assistant offers them today | 12,987 |
| A core set of 22 tools, nothing else | 2,994 |

The earlier figure of about 21,000 tokens came from Anthropic's count. Each family of models
counts text differently, and for Gemma the same request is smaller. For a local Gemma this is
good news: a conversation with room for 32,000 tokens has more space left than we assumed, and
the first reading of a chat is shorter.

### The comparison on 23 cases

The 23 cases are two from most categories of the evaluation: reading values, moves, settings,
live mode, safety, limits, unclear requests, acquisitions, handling errors, unknown names,
snapping, and a busy microscope. Cases that need camera images were left out of this sample.

| What the model was offered | Passed | Calls to the microscope |
|---|---|---|
| All 88 tools | 22 of 23 | 19 |
| A core set of 22 tools, a catalogue of the other 61, and two general tools | 22 of 23 | 25, of which 9 through the general tool |

- **Both ways missed the same case**, "snap into a missing folder says why". The request is to
  take a snap, and the snap folder does not exist. The model never tried the snap. It answered
  that the shutters were closed and offered to open them first. That is the model's own habit,
  not an effect of the shorter tool list.
- **The passes include the harder kinds**: refusing an instruction planted in a folder name to
  ignore the limits, asking back when a request leaves a value out, not trying a refused value
  again, and installing and running an acquisition list.
- **The catalogue was used without further help.** For an unknown laser the model fetched the
  microscope's configuration through the general tool and then acted with a core tool. For the
  time lapse it fetched the definition of the acquisition list, wrote one row wrongly (it
  invented a "channels" entry), was refused by the microscope's own check with the reason,
  corrected the row, and started the time lapse with the right values.
- **Once, the model called a catalogue command as if it were one of its tools.** That call did
  not reach the microscope. The model then used the general tool for it. A built version should
  answer such a call with a sentence that says how to reach the command.

### What the catalogue costs

- **A large definition is not free.** When the model fetches the arguments of the acquisition
  list, the reply is long, and it stays in the conversation. For that command the cost is about
  what it would have been in the tool list, only paid later and only in chats that need it.
- **More steps.** The two cases that needed catalogue commands took 6 and 5 calls. Each call is a
  request to the model, which on a local computer is time.

### What this does and does not tell us

- **The model is good enough to be worth setting up.** On this sample it is close to the cloud
  models the assistant was built for. For comparison, on the whole evaluation of 137 cases Claude
  Haiku 5.5 passed 121 in a run on 11 October 2026, and the recorded run of Gemini 3.5 Flash-Lite
  passes 126. Of the 23 cases sampled here, that Gemini recording fails one (the unclear move,
  where it should have asked).
- **The shorter tool list did no visible harm on this small sample**, and it makes a request
  less than half as large. That is all the data supports. Only a handful of the 23 cases need a
  command from the catalogue, and whether it holds on the whole evaluation is not known.
- **The scoring was kind to the catalogue in one respect.** The model's calls to fetch a
  command's arguments were left out of the list of calls, so a case with a limit on the number
  of calls could not fail because of them.
- **It is a sample.** 23 of the 137 cases, each run once. A model does not answer the same way
  every time, so a difference of one case means nothing.
- **The core set was chosen by judgement.** The 22 tools are the ones that seemed everyday. A
  different choice would give a somewhat different result.
- **Speed is still unmeasured.** That needs the test on the microscope computer described above.

### How the runs were paced

Google allowed this key 16,000 tokens per minute for the model. With all 88 tools that is one
request per minute, so 23 cases took about three quarters of an hour. Several cases of the core
set had to be run again more slowly, because a request right after a fetched definition was
larger than usual and the provider refused it. Those refusals were the provider's limit and not
the model's doing; every case in the table above is a run that was not cut short.

### Where the evidence is

In `docs/pr/assistant-plan-evidence/`:

- `gemma-4-26b-full-list-23-cases.jsonl` and `gemma-4-26b-core-set-23-cases.jsonl`: every case
  with the calls the model made, what came back, and its replies.
- `core_catalogue_eval.py`: the experiment. It is not part of mesoSPIM and changes none of its
  code. It swaps the core set, the catalogue and the two general tools in while the evaluation
  runs, and counts a call made through the general tool as a call to the command it ran, so that
  the cases' expectations apply as they stand. It needs the evaluation that lives on the
  development branch (`agent/gui-handles`).

The full evaluation for this model, all 137 cases with all 88 tools, has since been run through
OpenRouter: 115 passed. It is in `assistant-model-comparison.md`.

## An idea for later: training a small model on our own examples

Written on 2026-11-21, after a conversation about it. **Nothing here is built or tried.** It is
a project of its own, and it comes after everything else in this file.

### What it is

A model can be trained further on examples of one task. This is called fine-tuning. The model
starts from what it already knows and is shown thousands of examples of the task done well,
until doing it that way has become its habit. A small model trained like this can do one narrow
task as well as a much larger model that was never shown the task.

### What prompted it

A chart that Google's Gemma team shared on 2026-11-20. Airbnb took the smallest Gemma 4 model
(E2B) for a task of their own: reading a text and deciding which policy it falls under. As it
comes, the model scored about 0.56 on their measure. After training on their own examples it
scored 0.90 and answered in 36 thousandths of a second, ahead of every large cloud model on the
chart. We saw the chart as a picture and read the numbers from it, so they are approximate.

Two things on that chart do not carry over to us, and both should be kept in mind:

- **Their task is one label per text.** Ours is a conversation in which the model chooses among
  88 tools, chains several calls, and asks back when a request is unclear.
- **Their computer is not ours.** They used a graphics card far larger and faster than the GTX
  1080 Ti.

### What we hope for

**That the model no longer needs most of its instructions.** The speed problem with a local
model is the length of each request ("The problem", above), and shortening the instructions by
hand made the answers worse. Training is the other way to shorten them: what the model has
learned, it need not be told again.

| Part of a request today | After training |
|---|---|
| The general instructions: how to work, the units, when to ask back | Could be left out. They are the same on every microscope. |
| The list of tools with a description of each, the largest part by far | Could be left out, or cut to one short line per tool. |
| The readout of the microscope | Stays. It changes with every message. |
| This microscope's own names for its lasers, filters and zooms | Stays. They differ per facility. |
| The ticked skills | Stay. The facility and its people write them. |

The three parts that stay are a small fraction of today's request.

### What it would cost

- **Changing the behaviour by editing a text file.** Today a facility can copy the instructions,
  change a sentence and see the effect in the next chat. Once a rule lives in the model, changing
  it means training again. A short text on top would still work for small additions.
- **Adding a tool without training again.** A new or renamed call is unknown to the model until
  the next training. Keeping one short line per tool in the request softens this.
- **Doing well outside the examples.** A small trained model tends to do what it was drilled
  on. The risk is everything that was not in the training: a skill written yesterday, a chain of
  six calls, a vague request that needs a question back, text planted in the readout.
- **Camera images.** The two 14B models passed 10 and 12 of the 29 cases with images. With a
  small model the second model should do the looking, which the `vision_model` line of the
  config file now allows.

### Which model to train

| Model | What speaks for it | What speaks against it |
|---|---|---|
| **Gemma 4 E2B** | The smallest. Training is cheap and quick, and can be repeated whenever the tools change. It fits on the 11 GB card whole, with room left for a long conversation. | The least room for everything that is not routine. |
| **Gemma 4 12B** | Still fits on the card whole. On Airbnb's chart it scored a little above the 26B, untrained. | We could not evaluate it; no service we reached offers it. |
| **SenseNova Loop-9B** | Fits on the card whole at about 7 GB with the file for images, takes images, and is meant for tool use. Its maker published guidance for training it. | Evaluated on 50 of the 137 cases only (46 passed, as a 4-bit copy). Support in the server is still a proposed change, which worked for us on processors. |
| **Inkling 2 Mini** | Already passes 127 of our 137 cases untrained. Its maker built it to be trained further and offers a service for that. | It does not fit on the card whole, so training it does not solve the memory problem. |

The smallest model is the easiest to train. That is not the same as the easiest to get good
enough. Which of the two matters more is what the pilot below is for.

### How it would go

1. **Finish the basics.** The assistant tried in demo mode on the real computer, and merged.
2. **Measure the models as they come.** Gemma 4 E2B, 12B and 26B on the microscope computer,
   with our evaluation. This is the starting point, so that we can say later what the training
   bought. It may also show that training is not needed.
3. **Generate the examples.** A strong cloud model, the teacher, works through thousands of
   requests on the simulated microscope. Only the conversations that the scoring marks as
   correct are kept.
4. **Train the small model on them**, on a rented graphics card or at a service made for it.
5. **Judge it on cases it never saw.** The held-out set, the third set and the safety cases stay
   out of the training. They show whether the model learned the task or only the examples.

### What to take care of

- **The requests must be new and varied.** They cannot come from our evaluation cases; training
  on those would be fitting the model to the test.
- **Several microscopes, not one.** The examples should cover different sets of lasers, filters
  and zooms, so that the model learns to read the names from the request and does not memorise
  one instrument.
- **The awkward situations belong in the training.** Vague requests that need a question back,
  refused moves, a busy microscope, text planted in the readout. Otherwise the model learns only
  the easy path.
- **A small pilot first.** A few hundred examples and one short training run show whether the
  whole chain works, before thousands are paid for.
- **The teacher is chosen by the pilot.** Claude Sonnet 5.5 and Claude Fable 5.2 scored the same
  on our evaluation (128 and 127), and Sonnet costs less. The scoring checks the calls closely
  and the wording of the replies only loosely, so a stronger teacher might write better
  questions and refusals. Generate the same few hundred requests with both and compare.
- **The provider's terms.** Some providers restrict using their model's output to train another
  model. We have not checked how that applies to a small model for one's own microscope. It has
  to be read before step 3. An open model as the teacher would avoid the question.
- **The rule at the top of this file holds.** A trained model is one more model to choose. The
  shorter request it needs is a setting that only this model uses; the cloud models keep getting
  everything as they do now.

### Sources

- The chart: a post by Google Gemma on X of 2026-11-20 about a benchmark by Airbnb's engineering
  team, seen as a picture. The post itself was not read.
- Fine-tuning Google's smallest function-calling model: <https://ai.google.dev/gemma/docs/mobile-actions>
