"""Reproduce: a shutter change from a remote client leaves the main window's shutter box stale.

Starts mesoSPIM in demo mode in this process (demo_config.py, DemoStage only), starts Remote Control
TCP from its tab, and sends set_shutterconfig as any TCP client (or the AI Assistant) would. Then it
compares Core's state with the main window's ShutterComboBox and with the ETL controls that
update_GUI_by_shutter_state enables. Filter, zoom and laser are sent the same way, each alone,
to show which settings refresh the window.

    python repro_shutter_box.py            # needs no model and no API key

Exit code 1: the shutter box is stale (the bug). Exit code 0: it follows Core (fixed).
The other boxes are reported, not judged.
"""
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import drive_gui as dg  # noqa: E402  (sets the repo path and the working directory mesoSPIM needs)
from PyQt5 import QtCore, QtWidgets  # noqa: E402

from mesoSPIM.mesoSPIM_Control import dark_mode_check, get_logger  # noqa: E402
from mesoSPIM.src.plugins.manager import PluginRegistry  # noqa: E402
from mesoSPIM.src.utils.config_loader import load_config_from_file  # noqa: E402


def call_over_tcp(name, **args):
    """One Remote Control call from a thread, so the GUI and Core keep running meanwhile."""
    from mesoSPIM.test.remote_control.support.clients import RemoteControl
    out = {}

    def run():
        client = RemoteControl("127.0.0.1", 42000, "smart_mesospim", timeout=30)
        try:
            out["result"] = client.call(name, **args)
        except Exception as error:  # noqa: BLE001  reported, not raised: this is a probe
            out["error"] = repr(error)
        finally:
            client.close()

    thread = threading.Thread(target=run)
    thread.start()
    dg.wait_until(lambda: not thread.is_alive(), 30)
    return out


def main():
    cfg = load_config_from_file(os.path.join(dg.PACKAGE, "config", "demo_config.py"))
    if cfg.stage_parameters.get("stage_type") != "DemoStage":
        raise SystemExit("refused: not DemoStage")
    get_logger(cfg, dg.PACKAGE)
    QtCore.QThread.currentThread().setObjectName("MainThread")
    app = QtWidgets.QApplication(sys.argv)
    dark_mode_check(cfg, app)
    PluginRegistry(cfg)
    from mesoSPIM.src.mesoSPIM_MainWindow import mesoSPIM_MainWindow
    window = mesoSPIM_MainWindow(dg.PACKAGE, cfg, "mesoSPIM (shutter box reproduction)")
    window.show()
    dg.pump(8)
    stage = type(window.core.serial_worker.stage).__name__
    if stage != "mesoSPIM_DemoStage":
        raise SystemExit(f"refused: the stage is {stage}")

    rc = window.remote_control
    rc.RemoteControlModeComboBox.setCurrentText("TCP")
    rc.start()
    if not dg.wait_until(lambda: rc.running, 15):
        raise SystemExit("TCP did not start")

    core, box = window.core, window.ShutterComboBox
    print(f"Qt {QtCore.QT_VERSION_STR}, PyQt5, stage {stage}")
    print(f"start: Core shutterconfig={core.state['shutterconfig']!r}, box={box.currentText()!r}")

    # Each combo-box setting alone, over the same path: does its box follow Core? Laser last: its
    # path makes the waveform generator refresh the whole window, which would hide the others.
    boxes = [("shutterconfig", window.ShutterComboBox), ("filter", window.FilterComboBox),
             ("zoom", window.ZoomComboBox), ("laser", window.LaserComboBox)]
    stale = {}
    for key, combo in boxes:
        target = next(combo.itemText(i) for i in range(combo.count()) if combo.itemText(i) != core.state[key])
        reply = call_over_tcp(f"set_{key}", **{key: target})
        dg.wait_until(lambda: core.state[key] == target, 30)
        dg.pump(3)                                    # ample time for any refresh to arrive
        state, shown = core.state[key], combo.currentText()
        stale[key] = shown != state
        extra = ""
        if key == "shutterconfig":
            extra = (f", left ETL enabled={window.LeftETLOffsetSpinBox.isEnabled()}, "
                     f"right ETL enabled={window.RightETLOffsetSpinBox.isEnabled()}")
        status = (reply.get("result") or {}).get("operation", {}).get("status") if "result" in reply else reply
        print(f"set_{key}({target!r}) [{status}]: Core={state!r}, box={shown!r}{extra} -> "
              f"{'STALE BOX' if stale[key] else 'OK'}")
    results = [not stale["shutterconfig"]]
    print(f"stale boxes: {[key for key, value in stale.items() if value] or 'none'}")

    rc.stop()
    window.close()
    dg.pump(1)
    reproduced = not all(results)
    print("REPRODUCED: the shutter box does not follow Core" if reproduced else "NOT REPRODUCED: the shutter box follows Core")
    os._exit(1 if reproduced else 0)


if __name__ == "__main__":
    main()
