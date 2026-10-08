"""
mesoSPIM user configuration, converted from the single-file format.

The microscope itself is described in hardware/demo_config_hw.py: camera, stages, lasers, filters,
objectives, DAQ lines, writer defaults. Change that file when the instrument changes.

Here, keep only what differs for your session. Anything assigned below the include()
line overrides the hardware file, e.g.:

    startup['camera_exposure_time'] = 0.05
    filterdict['Empty-Alignment'] = 0
"""
config_format = 2

import os as _os
# Snaps and acquisitions go next to the clone, not to D:/tmp/ (absent on the test machine).
_PROBE = _os.path.abspath(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), *['..'] * 5, 'mesospim-probe'))

include('../../../../mesoSPIM/config/hardware/demo_config_hw.py')   # relative to this file

startup.update({
    'state': 'init',
    'folder': _PROBE,
    'snap_folder': _PROBE,
    'file_prefix': '',
    'file_suffix': '000001',
})

# Windows demo test, step 4: E1 on (copy of demo_config.py; the original is untouched).
ai_assistant_measured_values = True
