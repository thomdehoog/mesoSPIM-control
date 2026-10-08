# 07. After moving a clone, pytest reports the old location

**Kind:** tooling (pytest), not mesoSPIM. **Status:** a note for anyone moving a clone.

## What was seen

The clone was first made in `C:\mesoSPIM-dev\mesoSPIM-control`, tested, then moved to
`C:\ProgramData\MinicondaZMB\home\mesoSPIM-control`. Afterwards a live-suite run listed its skips
under the old, deleted folder:

    SKIPPED [1] ..\..\..\..\mesoSPIM-dev\mesoSPIM-control\mesoSPIM\test\remote_control\live\test_valid.py:60: set MESOSPIM_LIVE_TCP_TOKEN for the live TCP server

The tests themselves ran from the new folder; only the reported paths were stale. Deleting the
clone's `__pycache__` folders (30 of them, git-ignored) fixed it.

## Reproduce (no mesoSPIM needed)

    python docs/test-reports/evidence/07-stale-bytecode-after-move/repro_moved_clone.py [scratch-parent]

In a scratch folder it writes a test that skips, runs pytest, moves the folder and runs pytest again,
with a plain module import as a control. The scratch folder is removed afterwards.

Output on the test machine ([repro_output.txt](repro_output.txt)), pytest 9.1.1, Python 3.12.13:

    cache files after the first run: ['test_where.cpython-312-pytest-9.1.1.pyc', 'where_mod.cpython-312.pyc']
    pytest, before the move: SKIPPED [1] test_where.py:4: where am I
    pytest, after the move:  SKIPPED [1] ..\first_place\test_where.py:4: where am I
    python import, before:   ...\first_place\where_mod.py
    python import, after:    ...\second_place\where_mod.py
    REPRODUCED: pytest reports the old path after the move
    control: python import reports the new path

## Cause

pytest rewrites test modules for its assertions and caches them as `*-pytest-<version>.pyc` in
`__pycache__`. That cached code keeps the filename it was compiled with, and the cache is reused after
the move because the source's size and time are unchanged. A plain import does not have this problem:
importlib corrects the filename when it loads a cached `.pyc`.

## What to do

After moving or copying a clone, delete its `__pycache__` folders before running the tests (this is
what fixed it here; they are git-ignored and rebuilt on the next run). From the clone, in Git Bash:

    find . -name __pycache__ -type d -not -path "./.git/*" -prune -exec rm -rf {} +

Not Windows-specific.
