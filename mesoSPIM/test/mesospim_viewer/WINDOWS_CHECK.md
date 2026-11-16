# Checking the Data viewer on the microscope PC

Everything in this branch was built and tested on a Linux machine without a
graphics card. This page is the starting point for the part that can only be
checked on the Windows PC of the microscope. Demo mode is enough for all of
it: no laser has to be on and no sample has to be in.

It takes about an hour. You can go through it yourself with
`docs/source/data_viewer.rst` (the section "Checks that need the microscope
PC"), or let a Claude Code session on that PC do the work and guide you
through the parts that need your hands. The prompt for that is at the bottom
of this page.

## What you need

- The microscope PC, with mesoSPIM's usual Python environment.
- This branch: `data-viewer-py312` of `thomdehoog/mesoSPIM-control`
  (pull request 9 of that fork).
- A folder with a few GB free for test data. Nothing is written to your own
  acquisitions.

## The four parts

1. **The automatic tests** (about 15 minutes, nobody has to watch).
   They include the tests that open the real viewer window.
2. **The rehearsal** (about 5 minutes, leave the window alone while it runs).
   One command opens the Data viewer on the PC's own screen and graphics card
   and goes through eleven steps an operator would. It writes
   `data_viewer_rehearsal.txt` with PASS or FAIL for each step, how long a
   frame takes on this PC, and how much memory the viewer chose.
3. **What the viewer chose for memory.** mesoSPIM's log has lines that begin
   with `The Data viewer keeps`. They should name the PC's real graphics card.
4. **The checks by hand** in mesoSPIM's demo mode, numbered in
   `docs/source/data_viewer.rst`. These are the ones a script cannot do: a
   real Stop, the writer's page in the file-naming wizard, rows run one at a
   time, a time lapse, a writer process ended on purpose.

## What matters most

These are the things that could not be tried on Linux at all. If time is
short, do these first.

- **An acquisition with `MP_OME_Zarr_TCZYX_Writer` starts and is saved**
  (check 18). mesoSPIM now checks before every run whether a file path would
  be too long for Windows, and that check only runs on Windows.
- **The memory the viewer chose** (part 3). If the viewer misbehaves with the
  larger limits, this line in the configuration file brings the old ones back:
  `data_viewer = {'gpu_memory_MB': 1000, 'system_memory_MB': 2000}`.
- **A time lapse of three time points with the viewer open.** The tiles of the
  first time point must stay on the picture, without a flicker, while the
  others are acquired, and the T slider must grow to three.
- **How it moves** with a large acquisition on a real graphics card: the
  frame times in the rehearsal's report.
- **A run saved as TIFF and one as HDF5**, opened in the viewer: do
  neighbouring tiles lie where they should?

## A prompt for a Claude Code session on the microscope PC

Start Claude Code in the folder that holds this repository and paste the
text below. Change the two lines marked with `<...>` first.

```text
You are on the Windows PC of a mesoSPIM light-sheet microscope. Please help me
check the Data viewer of mesoSPIM-control on this PC. It was developed and
tested on Linux only, so this is the first time the latest work runs on
Windows. I am a microscopist, not a software engineer: explain what you do in
plain sentences, and tell me clearly when you need me to click something.

Where things are
- Repository: this folder. Branch to test: data-viewer-py312 of
  thomdehoog/mesoSPIM-control (pull request 9 of that fork).
- Python environment of mesoSPIM: <name of the conda environment, or the path to python.exe>
- A folder you may fill with test data: <for example D:\viewer_check>
- Read first: mesoSPIM/test/mesospim_viewer/WINDOWS_CHECK.md, then the section
  "Checks that need the microscope PC" in docs/source/data_viewer.rst. Follow
  the commands given there.

What to do, in this order
1. Fetch the branch and check it out. Tell me the commit you are testing.
   Do not change mesoSPIM's configuration files.
2. Run the automatic tests as the documentation describes, with the Qt window
   tests switched on (MESOSPIM_VIEWER_QT_TESTS=1). Install pytest,
   pytest-timeout and playwright into the environment if they are missing.
   If a test fails, run it once more on its own before judging it: one test
   measures timing and can fail on a busy PC.
3. Run the rehearsal: python -m mesoSPIM.test.mesospim_viewer.rehearsal
   Tell me beforehand that a window will open and that I should leave it
   alone. Afterwards read data_viewer_rehearsal.txt and summarise it: which
   steps passed, the frame times, the graphics card it found and the memory
   limits the viewer chose.
4. Start mesoSPIM in demo mode (python mesoSPIM/mesoSPIM_Control.py -D), or
   ask me to. Then guide me through the numbered checks by hand in
   docs/source/data_viewer.rst, one at a time. For each, tell me exactly what
   to click and what I should see, wait for my answer, and note the result.
   Begin with the ones listed under "What matters most" in WINDOWS_CHECK.md.
   After each run, look in mesoSPIM's newest log file for errors, above all
   FileNotFoundError, PermissionError and KeyError, and for the lines that
   begin with "The Data viewer keeps".

Rules
- Do not push anything, and do not open or change any pull request. The public
  pull request mesoSPIM/mesoSPIM-control#117 must not be touched.
- Do not change code on your own. If something fails, find out why, show me
  the evidence (the test output or the log lines) and propose a fix. Wait for
  my answer before you change a file.
- Do not delete anything outside the test-data folder named above.
- Never switch a laser on or move real hardware. Demo mode only.

What I want at the end
A file windows_check_report.md in the repository folder with: the commit
tested, the PC (Windows version, graphics card, memory), the result of the
automatic tests, the rehearsal's report in full, the result of every check by
hand, every error found with its log lines, and your judgement of what has to
be fixed before this work can go to the public pull request.
```

Send `windows_check_report.md` and `data_viewer_rehearsal.txt` back to the
session that develops this branch. Both are plain text.
