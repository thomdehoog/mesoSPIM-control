# Configuration

The AI Assistant tab asks the operator for three things only: the provider, the model and the
API key. Everything else is decided once, by the facility, in the microscope's config file. This
page describes that block.

## The block

Add this to the microscope's config file. Every line is optional, and a line that is left out
takes the value shown here.

```python
ai_assistant = {
    'system_prompt':      '',                    # a text file with the assistant's instructions
    'system_skills':      'skills',              # the skills that belong to this microscope
    'personal_skills':    '~/mesoSPIM/skills',   # each person's own skills
    'documentation':      '',                    # the folder with the mesoSPIM documentation
    'save_conversations': False,                 # keep each conversation as a file?
    # 'vision':           False,                 # only to forbid sending camera images; see below
    'address':            '',                    # a model reached at your own address
    # 'vision_model':     {'provider': 'Gemini', 'model': 'gemini-3.5-flash-lite'},   # a second model that looks
    'look_bin':           2,                     # how much a camera image is shrunk before it is sent
    'focus_metric':       'laplacian',           # how sharpness is measured
    'axes':               {'x': 'right', 'y': 'up', 'z': 'toward the camera'},
    'request_interval_s': 0,                     # a pause between requests to the model
}
```

Two rules hold for every path in the block:

- A path that does not start at the root of the disk is read from the folder the config file is
  in. `'skills'` is therefore the `skills` folder next to the config file.
- `~` stands for the home folder of whoever is logged in to the computer, for example
  `C:\Users\anna`. The config file is shared by everyone who uses the microscope, so this is how
  one line gives each person a folder of their own.

In a configuration that is split into a hardware file and a user file, the block describes the
microscope, so it belongs in the hardware file (`config/hardware/…_hw.py`).

If a line cannot be used (a value of the wrong kind, a file that is not there), the assistant
still starts with the usual value in its place, and the tab's status line says which line it was.

## The lines, one by one

**`system_prompt`** is the text file that holds the assistant's general instructions: how to
work, the units, what to do when a request is unclear. Left empty, the file that comes with
mesoSPIM is used (`mesoSPIM/src/ai_assistant/manual.md`). To change the instructions, copy that
file, edit the copy and point this line at it. A change takes effect in the next chat. mesoSPIM
adds the list of commands and the list of ticked skills to whatever this file says.

**`system_skills`** is the folder with the skills that belong to this microscope. Everyone who
uses the microscope sees them, and the assistant can read them but never change them. See
[Skills](skills.md).

**`personal_skills`** is the folder with each person's own skills. Written with `~`, as above,
each person gets their own. Two more things are kept next to it, in the same personal folder:
that person's saved conversations (`conversations`), and a small file that remembers which skills
they ticked.

On microscope computers where each person signs in to Windows with their own account, Windows
keeps these folders private. On a computer where everyone shares one Windows login, "personal"
means shared by everyone on that login. A facility in that situation can write a fixed path here
and accept that.

**`documentation`** is the folder the assistant's `search` looks in when asked how mesoSPIM
works. Left empty, mesoSPIM looks for the documentation of its own copy of the repository
(`docs/source`). Where mesoSPIM was installed without that folder, point this line at a copy of
it. When no folder is found, `search` says plainly that the documentation is not available on
this computer, and searching saved conversations still works.

**`save_conversations`** decides whether each chat is written to a file. It is off unless this
line turns it on, and the tab always says which it is. A saved conversation holds what the
operator typed, the calls the assistant made with their results, and its replies; never a camera
image. The reason to save is that the assistant can then search earlier conversations: "what did
we do last time on this sample?". Each person sees only their own, and can remove any of them in
the tab.

**`vision`** decides whether camera images may be sent to the provider.

- Left out, each model does what it can: a model that accepts images is shown them.
- `False` forbids it for every model. This is the line for a facility whose samples may not
  leave the building. The assistant then works from the numbers that describe an image
  (brightness, sharpness, where the signal is) and the chat says so when it opens.
- `True` is only needed together with `address`, to say that the model there accepts images.
  mesoSPIM cannot find that out by itself.

**`address`** is for a model that the facility reaches through a service of its own, for example
one hosted by the university, instead of through a commercial provider. It is the address of a
server that speaks the OpenAI API, such as `http://models.example.org/v1`. The operator then
chooses **OpenAI-style** as the provider. It stays empty for everyone else.

**`vision_model`** names a second model that looks at the camera images, so that the model that
answers never receives an image. When the assistant looks, the image goes to this second model
with the question, and only its answer in words comes back into the conversation. Leave the line
out and the model that answers looks itself, when it can.

A second model is worth having in three situations: the model that answers cannot take images;
it is a small model on the facility's own computer, and a stronger one should do the looking; or
the images should go to a different provider than the conversation.

The block has a `provider`, which is one of the names the tab offers, and optionally a `model`
(left out, that provider's usual model is taken) and an `address` (for a model reached at your
own address):

```python
'vision_model': {'provider': 'Gemini', 'model': 'gemini-3.5-flash-lite'},
```

The tab has no second key field. The key for the vision model is found like this:

- When both models are at the same provider, the key typed in the tab serves both.
- Otherwise it is the key that person had remembered for that provider. To set it up, choose
  that provider in the tab once, type the key, tick **Remember this key on this computer**, and
  start a chat.
- Failing that, the provider's environment variable.

The vision model's key is checked together with the other one when a chat starts. If it has no
key, or its provider refuses it, the chat still opens: the model that answers then looks itself,
if it can, and the chat says so in its first lines. `'vision': False` forbids images for both.

**`look_bin`** is how much a camera image is shrunk before it is sent to the model: 1, 2, 4 or
8. With 2, a frame 2048 pixels wide arrives 1024 wide. A coarser image is cheaper and faster, and
enough for "is it centred?" or "is it saturated?". The numbers that describe the image always
come from the full frame.

**`focus_metric`** is how sharpness is measured: `'laplacian'`, or `'dct_shannon'`, which is the
measure the Auto-Focus tool uses.

**`axes`** says what a positive move on each axis does to the sample in the image: x `'right'`
or `'left'`, y `'up'` or `'down'`, z `'toward the camera'` or `'away from the camera'`. The model
is told, so that "move it up", "a bit to the left" and "closer to the camera" become moves on the
right axis in the right direction. Check it once per microscope; the assistant's `calibrate` also
measures it.

**`request_interval_s`** spaces the requests to the model by at least this many seconds. It is
for a provider with a tight limit on requests per minute; the status line shows it. Leave it at 0
otherwise.

## Lines from before this block

Two settings had a line of their own before the block existed, and are still read when the block
does not set them: `ai_assistant_axes` (now `axes`) and `ai_assistant_request_interval_s` (now
`request_interval_s`).

## What is not in the config file

The API key is never in the config file. Each person types their own in the tab, and can have it
remembered in the password store of their Windows account. See [AI Assistant](index.md).
