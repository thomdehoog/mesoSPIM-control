# 06. The demo config writes to D:/tmp/, and snap_folder is not a state request

**Kind:** demo config and test brief; the assistant handled it well. **Status:** brief to correct.

## What was seen

`mesoSPIM/config/demo_config.py` sets `'folder': 'D:/tmp/'` and `'snap_folder': 'D:/tmp/'`. The test
machine has a D: drive but no D:\tmp, and its rule was to write nothing outside one folder. In the
first driven GUI run, step 2d's continuation tried to look after the acquisition and the snap failed.
The model reported it correctly ([../run-logs/step2_run1.log](../run-logs/step2_run1.log), line 86):

    reply: The acquisition finished, but the look failed because the snap folder 'D:/tmp/' does not exist
    on the microscope PC. Would you like to create that folder or change it to an existing one?

The Gemini walkthrough's "Take a snap into D:\nowhere" step got the same kind of answer
([../run-logs/step3_gemini.log](../run-logs/step3_gemini.log)): refused, folder named, fix proposed.

The driver first set the snap folder with a state request, `sig_state_request.emit({'snap_folder': ...})`,
as one does for filter or zoom. Core ignored it (step2_run1.log line 58 still says `D:/tmp/`). Setting
it the way the main window's Choose snap folder does fixed it (step2_run2.log line 34).

## Reproduce (no model, no key; takes no snaps)

    python docs/test-reports/evidence/06-demo-folders/repro_demo_folders.py

Output on the test machine ([repro_output.txt](repro_output.txt)), with TEMP inside the allowed folder:

    demo_config startup: folder='D:/tmp/' exists=False, snap_folder='D:/tmp/' exists=False
    default acquisition list: 1 row(s); first row folder='tmp'
    after sig_state_request({'snap_folder': 'C:\\ProgramData\\MinicondaZMB\\home\\tmp'}): Core snap_folder='D:/tmp/' -> NOT taken
    after setting it as choose_snap_folder does: Core snap_folder='C:\\ProgramData\\MinicondaZMB\\home\\tmp' -> taken
    REPRODUCED: the demo folders do not exist on this machine

Exit code 1 where the demo folders are missing, 0 where they exist.

## Where it is decided (code at 41839f2)

- `mesoSPIM_Core.__init__` copies `cfg.startup['snap_folder']` into the state
  (mesoSPIM/src/mesoSPIM_Core.py:231). `state_request_handler` (mesoSPIM_Core.py:327) has no entry for
  `snap_folder`, so a request for it does nothing.
- `mesoSPIM_MainWindow.choose_snap_folder` (mesoSPIM/src/mesoSPIM_MainWindow.py:1355) writes
  `self.state['snap_folder']` directly and updates `SnapFolderIndicator`.
- The default acquisition row's folder is `'tmp'` (mesoSPIM/src/utils/acquisitions.py, `Acquisition`
  defaults): relative to the working folder, so a run of the unchanged demo list writes under
  `mesoSPIM/tmp` when started from `mesoSPIM/`.

## How the test handled it

- `tools/drive_gui.py` sets the snap folder as `choose_snap_folder` does, and replaces the
  acquisition list with one 3-plane row in its probe folder before "run the acquisition list".
- `tools/demo_config_measured.py` moves both folders next to the clone.

## Suggested change

In the brief: "Set the snap folder (menu: Choose snap folder) and the acquisition row's folder to an
existing folder before steps 2d, 2f and 3." Optionally make the demo config's folders relative to the
clone, or created on start, so the demo runs on any machine. Scripts that set the snap folder should
do it as `choose_snap_folder` does, not with a state request.
