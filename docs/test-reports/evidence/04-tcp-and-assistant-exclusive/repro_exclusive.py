"""Show: the AI Assistant tab refuses to connect while a Remote Control transport runs.

Starts demo mesoSPIM, starts TCP from the Remote Control tab, presses Connect on the AI Assistant
tab, then stops TCP and connects again. No model, no key: Connect makes no network call.

    python repro_exclusive.py

Exit code 0: the behaviour is as documented below (refused while TCP runs, connects after Stop).
Exit code 1: it differs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from demo_app import connect_without_model, pump, start_demo, wait_until  # noqa: E402


def main():
    app, window = start_demo()
    rc, tab = window.remote_control, window.ai_assistant
    rc.RemoteControlModeComboBox.setCurrentText("TCP")
    rc.start()
    tcp = wait_until(lambda: rc.running, 15)
    print(f"Remote Control: {rc.RemoteControlStatusLabel.text()}")

    refused = not connect_without_model(tab)
    print(f"Connect while TCP runs: state={tab._state!r}, status line={tab.status_label.text()!r}")

    rc.stop()
    pump(1)
    print(f"Remote Control after Stop: {rc.RemoteControlStatusLabel.text()}")
    connected = connect_without_model(tab)
    print(f"Connect after TCP stops: state={tab._state!r}, status line={tab.status_label.text()!r}")

    tab.on_disconnect()
    window.close()
    pump(1)
    ok = tcp and refused and connected
    print("AS DOCUMENTED: refused while TCP runs, connects after Stop" if ok else "DIFFERENT from the documented behaviour")
    os._exit(0 if ok else 1)


if __name__ == "__main__":
    main()
