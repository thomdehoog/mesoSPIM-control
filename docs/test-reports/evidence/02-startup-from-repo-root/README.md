# 02. Starting from the repository root fails

**Kind:** bug, upstream code (not the AI Assistant). Not Windows-specific. **Status:** open.

## What was seen

`python mesoSPIM/mesoSPIM_Control.py -D`, run from the repository root as AGENTS.md line 37 and the
test brief say, stops during startup:

    File "...\mesoSPIM\src\mesoSPIM_MainWindow.py", line 122, in __init__
      self.open_webcam_window()
    File "...\mesoSPIM\src\mesoSPIM_MainWindow.py", line 264, in open_webcam_window
      self.webcam_window = WebcamWindow(self.cfg.ui_options['usb_webcam_ID'])
    File "...\mesoSPIM\src\WebcamWindow.py", line 29, in __init__
      loadUi('gui/WebcamWindow.ui', self)
    FileNotFoundError: [Errno 2] No such file or directory: 'gui/WebcamWindow.ui'

Full output of the first attempt: [../run-logs/gui_run1.log](../run-logs/gui_run1.log). Started from
`mesoSPIM/` (`python mesoSPIM_Control.py -D`), it runs.

## Reproduce (no model, no key)

    python docs/test-reports/evidence/02-startup-from-repo-root/repro_repo_root_start.py

It lists the cwd-relative `loadUi` calls in mesoSPIM/src, then starts demo mesoSPIM from the
repository root and watches its output. Exit code 1: it fails with a missing .ui file (the bug).
Exit code 0: it was still running after 60 s, so startup works (the script then ends its own child).

Output on the test machine ([repro_output.txt](repro_output.txt)), 8 October 2026:

    cwd-relative loadUi calls in mesoSPIM/src:
      mesoSPIM\src\mesoSPIM_CameraWindow.py:69: loadUi('../gui/mesoSPIM_CameraWindow.ui', self)
      mesoSPIM\src\mesoSPIM_Optimizer.py:66: loadUi('gui/mesoSPIM_Optimizer.ui', self)
      mesoSPIM\src\mesoSPIM_Optimizer.py:243: self.results_window = loadUi('gui/mesoSPIM_Optimizer_Results.ui')
      mesoSPIM\src\mesoSPIM_TileViewWindow.py:32: loadUi('../gui/mesoSPIM_Tile_Overview.ui', self)
      mesoSPIM\src\WebcamWindow.py:29: loadUi('gui/WebcamWindow.ui', self)
    REPRODUCED: startup from the repository root fails: FileNotFoundError: [Errno 2] No such file or directory: 'gui/WebcamWindow.ui'

| Call | Used when | Affected |
| --- | --- | --- |
| WebcamWindow.py:29 | Every startup | Yes: startup fails |
| mesoSPIM_Optimizer.py:66 and :243 | Opening the optimizer (mesoSPIM_MainWindow.py:994) | Yes, when opened; not tested |
| mesoSPIM_CameraWindow.py:69 | Only when the module runs as `__main__` | No: the app loads from `package_directory` |
| mesoSPIM_TileViewWindow.py:32 | Only when the module runs as `__main__` | No: same |

## Cause (code at 41839f2)

`WebcamWindow` and `mesoSPIM_Optimizer` load their .ui files relative to the working folder. The
other windows load from `package_directory` (for example mesoSPIM_MainWindow.py:102). The relative
path in WebcamWindow is older; upstream commit 61f2379 (Nikita Vladimirov, 10 June 2024, "Webcam
window always opens at startup, empty if no camera is present in config file") made the window open
at every startup, which turned it into a startup failure.

## Impact

- Anyone following AGENTS.md line 37 or the test brief cannot start the GUI.
- The installed console script (`mesospim-control`) runs with whatever the working folder is, so it
  likely fails the same way unless started from `mesoSPIM/`. Not tested.
- Possibly next in line once the .ui paths are fixed (not tested): the demo hardware config names
  its ETL file relative to the working folder, `'ETL_cfg_file' : 'config/etl_parameters/ETL-parameters.csv'`
  (mesoSPIM/config/hardware/demo_config_hw.py:517).

## Fix to consider

- Load `WebcamWindow.ui` and both Optimizer .ui files from the package directory, as the main window
  does. WebcamWindow is built without a parent, so it needs the directory passed in, or a path built
  from `os.path.dirname(__file__)`.
- Or, if starting from `mesoSPIM/` is the rule, correct AGENTS.md line 37 and the test brief.

After a fix, this script must exit 0. Then check the optimizer window and the ETL file from the
repository root by hand.
