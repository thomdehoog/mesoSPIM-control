"""Show: the demo config's folders, and how the snap folder can (and cannot) be set.

Starts demo mesoSPIM and prints the startup folder and snap folder and whether they exist here.
Then it sends {'snap_folder': ...} as a state request, as one would for filter or zoom, and checks
Core's state; then sets it as the main window's Choose snap folder does (choose_snap_folder) and
checks again. Takes no snaps and writes no files.

    python repro_demo_folders.py

Exit code 1 when the demo folders do not exist on this machine (the snap and acquisition steps then
need a folder set first). Exit code 0 when they exist.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from demo_app import pump, start_demo  # noqa: E402


def main():
    app, window = start_demo()
    cfg, state = window.cfg, window.core.state
    folder, snap = cfg.startup.get("folder"), cfg.startup.get("snap_folder")
    print(f"demo_config startup: folder={folder!r} exists={os.path.isdir(folder)}, "
          f"snap_folder={snap!r} exists={os.path.isdir(snap)}")
    rows = list(state["acq_list"])
    print(f"default acquisition list: {len(rows)} row(s); first row folder={rows[0]['folder']!r}" if rows else "no rows")

    target = tempfile.gettempdir()                  # only compared, never written to
    window.sig_state_request.emit({"snap_folder": target})
    pump(1)
    print(f"after sig_state_request({{'snap_folder': {target!r}}}): Core snap_folder={state['snap_folder']!r} "
          f"-> {'taken' if state['snap_folder'] == target else 'NOT taken'}")
    window.state["snap_folder"] = target            # what choose_snap_folder does, without its dialog
    window.SnapFolderIndicator.setText(target)
    pump(1)
    print(f"after setting it as choose_snap_folder does: Core snap_folder={state['snap_folder']!r} "
          f"-> {'taken' if state['snap_folder'] == target else 'NOT taken'}")

    window.close()
    pump(1)
    missing = not (os.path.isdir(folder) and os.path.isdir(snap))
    print("REPRODUCED: the demo folders do not exist on this machine" if missing else "the demo folders exist here")
    os._exit(1 if missing else 0)


if __name__ == "__main__":
    main()
