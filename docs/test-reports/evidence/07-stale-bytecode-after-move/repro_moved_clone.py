"""Reproduce: after moving a folder of tests, pytest reports the old location.

In a scratch folder: writes a test that skips, runs pytest once (it caches the rewritten test module
in __pycache__ as *-pytest-*.pyc), moves the folder, and runs pytest again. Plain Python imports of
a moved module are checked as a control: importlib corrects co_filename when it loads a cached .pyc.

    python repro_moved_clone.py [scratch-parent-folder]

Exit code 1: pytest reported the old path after the move (reproduced). Exit code 0: it did not.
The scratch folder is removed at the end.
"""
import os
import shutil
import subprocess
import sys
import tempfile

TEST = "import pytest\n\ndef test_skips():\n    pytest.skip('where am I')\n"
MODULE = "def where():\n    return where.__code__.co_filename\n"


def pytest_skip_location(folder):
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", "test_where.py"],
                         cwd=folder, capture_output=True, text=True).stdout
    return next((line.strip() for line in out.splitlines() if line.startswith("SKIPPED")), out.strip()[-200:])


def python_import_location(folder):
    code = "import sys; sys.path.insert(0, '.'); import where_mod; print(where_mod.where())"
    return subprocess.run([sys.executable, "-c", code], cwd=folder, capture_output=True, text=True).stdout.strip()


def main():
    parent = sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir()
    scratch = tempfile.mkdtemp(prefix="moved_clone_", dir=parent)
    first, second = os.path.join(scratch, "first_place"), os.path.join(scratch, "second_place")
    try:
        os.makedirs(first)
        with open(os.path.join(first, "test_where.py"), "w") as handle:
            handle.write(TEST)
        with open(os.path.join(first, "where_mod.py"), "w") as handle:
            handle.write(MODULE)
        before_pytest, before_import = pytest_skip_location(first), python_import_location(first)
        print(f"cache files after the first run: {sorted(os.listdir(os.path.join(first, '__pycache__')))}")
        shutil.move(first, second)
        after_pytest, after_import = pytest_skip_location(second), python_import_location(second)
        print(f"pytest, before the move: {before_pytest}")
        print(f"pytest, after the move:  {after_pytest}")
        print(f"python import, before:   {before_import}")
        print(f"python import, after:    {after_import}")
        stale = "first_place" in after_pytest
        print("REPRODUCED: pytest reports the old path after the move" if stale else "NOT REPRODUCED")
        print(f"control: python import reports the {'OLD' if 'first_place' in after_import else 'new'} path")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    sys.exit(1 if stale else 0)


if __name__ == "__main__":
    main()
