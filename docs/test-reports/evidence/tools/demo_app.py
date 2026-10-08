"""Start mesoSPIM in demo mode in this process, for the reproduction scripts.

    from demo_app import start_demo, pump, wait_until
    app, window = start_demo()          # refuses anything but DemoStage

The window is shown; pump() and wait_until() keep the Qt event loop running while a script waits.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import drive_gui as dg  # noqa: E402  (sets the repo path and the working directory mesoSPIM needs)
from PyQt5 import QtCore, QtWidgets  # noqa: E402

pump = dg.pump
wait_until = dg.wait_until
PACKAGE = dg.PACKAGE


def start_demo(config=None, title="mesoSPIM (reproduction)"):
    from mesoSPIM.mesoSPIM_Control import dark_mode_check, get_logger
    from mesoSPIM.src.plugins.manager import PluginRegistry
    from mesoSPIM.src.utils.config_loader import load_config_from_file

    cfg = load_config_from_file(config or os.path.join(PACKAGE, "config", "demo_config.py"))
    if cfg.stage_parameters.get("stage_type") != "DemoStage":
        raise SystemExit("refused: the config is not DemoStage")
    get_logger(cfg, PACKAGE)
    QtCore.QThread.currentThread().setObjectName("MainThread")
    app = QtWidgets.QApplication(sys.argv)
    dark_mode_check(cfg, app)
    PluginRegistry(cfg)
    from mesoSPIM.src.mesoSPIM_MainWindow import mesoSPIM_MainWindow
    window = mesoSPIM_MainWindow(PACKAGE, cfg, title)
    window.show()
    pump(8)
    stage = type(window.core.serial_worker.stage).__name__
    if stage != "mesoSPIM_DemoStage":
        raise SystemExit(f"refused: the stage is {stage}")
    print(f"demo mesoSPIM up: Qt {QtCore.QT_VERSION_STR}, stage {stage}", flush=True)
    return app, window


def connect_without_model(tab):
    """Connect the AI Assistant tab with a placeholder key: Connect makes no network call, so the
    tab, its worker and its rules can be checked without a model (as the repo's smoke tests do)."""
    tab.language.key.setText("placeholder-not-a-key")
    tab.on_connect()
    return wait_until(lambda: tab._state == "ready", 15)
