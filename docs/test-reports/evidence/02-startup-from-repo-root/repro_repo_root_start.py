"""Reproduce: `python mesoSPIM/mesoSPIM_Control.py -D` from the repository root fails.

1. Lists every loadUi(...) in mesoSPIM/src whose path is relative to the working folder.
2. Starts mesoSPIM in demo mode from the repository root, as AGENTS.md line 37 says, and watches its
   output for up to 90 s. A FileNotFoundError for a .ui file is the bug. If the GUI is still running
   after 60 s without one, startup worked: this script ends its own child process and reports so.

    python repro_repo_root_start.py

Exit code 1: the startup fails (the bug). Exit code 0: it starts (fixed).
"""
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, *[".."] * 4))
SRC = os.path.join(REPO, "mesoSPIM", "src")

# loadUi('gui/...') or loadUi("../gui/...") : a literal path that does not start from package_directory.
RELATIVE = re.compile(r"""loadUi\(\s*['"](\.\./)?gui/""")


def scan():
    hits = []
    for root, _dirs, files in os.walk(SRC):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(root, name)
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for number, line in enumerate(handle, 1):
                        if RELATIVE.search(line):
                            hits.append(f"{os.path.relpath(path, REPO)}:{number}: {line.strip()}")
    return hits


def main():
    print("cwd-relative loadUi calls in mesoSPIM/src:")
    for hit in scan():
        print("  " + hit)

    print(f"\nstarting from the repository root: {REPO}")
    command = [sys.executable, os.path.join("mesoSPIM", "mesoSPIM_Control.py"), "-D"]
    child = subprocess.Popen(command, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace")
    lines = []
    threading.Thread(target=lambda: lines.extend(child.stdout), daemon=True).start()
    started = time.monotonic()
    while child.poll() is None and time.monotonic() - started < 60:
        if any("FileNotFoundError" in line for line in lines):
            break
        time.sleep(0.5)
    still_running = child.poll() is None
    if still_running:
        child.kill()                       # our own child only
        child.wait(30)
    time.sleep(1)
    output = "".join(lines)
    error = next((line.strip() for line in lines if "FileNotFoundError" in line), None)
    tail = [line.rstrip() for line in lines if line.strip()][-6:]
    print("last lines of its output:")
    for line in tail:
        print("  " + line)
    if error:
        print(f"\nREPRODUCED: startup from the repository root fails: {error}")
        os._exit(1)
    if still_running and "Traceback" not in output:
        print("\nNOT REPRODUCED: mesoSPIM was still running after 60 s (stopped by this script)")
        os._exit(0)
    print(f"\nUNCLEAR: exit code {child.returncode}; see the output above")
    os._exit(2)


if __name__ == "__main__":
    main()
