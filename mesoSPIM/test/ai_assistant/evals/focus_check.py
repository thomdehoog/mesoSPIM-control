"""Check the focus measure on a focus series (roadmap B3): it must peak clearly at the sharp frame.

    python -m mesoSPIM.test.ai_assistant.evals.focus_check FOLDER [--sharp F]

FOLDER holds one TIFF per focus position, the position in micrometres as the last number in its
name ("f_1250.tif", "f_1250.5.tif"; a minus right before the digits makes it negative), as
recorded for B2: about 30 frames across +-300 um. --sharp names the position
of the sharp frame when the operator knows it; else the check reports where the measure peaks.
The peak is clear when it stands at least CLEAR times above the median of the frames 100 um or
more away from it, and all of those are below half the peak.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np

from mesoSPIM.src.remote_control.frame import focus_measure

CLEAR = 5.0
FAR_UM = 100.0
_POSITION = re.compile(r"-?\d+(?:\.\d+)?")


def check_series(frames, sharp=None):
    """`frames`: {focus position in um: 2-D array}. The measure per position, where it peaks, how
    clearly, and whether it falls on both sides (every frame FAR_UM or more away is below half the
    peak); with `sharp`, how far the peak is from it."""
    measures = {f: focus_measure(np.asarray(frames[f], dtype=np.float64)) for f in sorted(frames)}
    best = max(measures, key=measures.get)
    peak = measures[best]
    far = [v for f, v in measures.items() if abs(f - best) >= FAR_UM]
    clarity = peak / np.median(far) if far and np.median(far) > 0 else float("inf")
    report = {"measures": measures, "peak_at": best, "clarity": round(float(clarity), 2),
              "falls_both_sides": all(v < peak / 2 for v in far), "clear": bool(clarity >= CLEAR)}
    if sharp is not None:
        report["peak_off_sharp_um"] = round(abs(best - sharp), 1)
    return report


def load_series(folder):
    import tifffile
    frames = {}
    for path in sorted(Path(folder).glob("*.tif*")):
        numbers = _POSITION.findall(path.stem)
        if numbers:
            frames[float(numbers[-1])] = tifffile.imread(path)
    return frames


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder")
    parser.add_argument("--sharp", type=float, default=None, help="focus position of the sharp frame, um")
    arguments = parser.parse_args(argv)
    frames = load_series(arguments.folder)
    if len(frames) < 3:
        print(f"need at least three frames with a position in their name in {arguments.folder}", file=sys.stderr)
        return 2
    report = check_series(frames, arguments.sharp)
    for position, value in report["measures"].items():
        print(f"f = {position:10.1f} um   focus_measure = {value:.5f}")
    print(f"\npeak at {report['peak_at']} um, {report['clarity']} times the frames {FAR_UM:.0f} um or more away; "
          f"falls on both sides: {report['falls_both_sides']}"
          + (f"; {report['peak_off_sharp_um']} um from the sharp frame" if "peak_off_sharp_um" in report else ""))
    return 0 if report["clear"] and report["falls_both_sides"] else 1


if __name__ == "__main__":
    sys.exit(main())
