"""A rehearsal of the Data viewer in its real window, for the microscope PC.

The automatic tests draw in a browser that the test tool brings along. This script
opens the Data viewer window itself, as mesoSPIM does, on the PC's own screen and
graphics card, and goes through what an operator would: a saved acquisition, a run
followed live, a time lapse, 2D and 3D, a large acquisition, runs saved as TIFF and
HDF5. After each step it says whether it passed, and at the end how long a frame
takes on this PC and how much memory the viewer could be given.

Run it from the folder that holds ``mesoSPIM``, in the environment mesoSPIM runs in::

    python -m mesoSPIM.test.mesospim_viewer.rehearsal

It needs no microscope and no laser, and it does not start mesoSPIM. It takes a few
minutes, shows a window while it works, and writes what it found to
``data_viewer_rehearsal.txt`` in the folder it was started from. The data it makes
goes into a temporary folder and is deleted at the end (``--keep`` leaves it).

Three things it cannot do, because they happen in mesoSPIM's own windows, stay on
the list of checks by hand in ``docs/source/data_viewer.rst``: the writer's page in
the file-naming wizard, rows run one at a time, and the file names after a time
lapse.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

from mesoSPIM.src.mesospim_viewer import PAGE_DIR, Stack
from mesoSPIM.src.mesospim_viewer.demo import write_tile

TILE = "Mag1_Tile{}_Sh0_Rot0.ome.zarr"

# How much of the picture is lit, read back from the graphics card.
LIT = """(() => {
  const v = window.viewer; if (!v?.display) return null;
  const d = v.display; d.draw();
  const gl = d.gl, canvas = d.canvas, panel = document.querySelector('.neuroglancer-rendered-data-panel');
  if (!panel) return null;
  const box = panel.getBoundingClientRect(), whole = canvas.getBoundingClientRect();
  const sx = canvas.width / whole.width, sy = canvas.height / whole.height;
  const x = Math.round((box.left - whole.left) * sx), w = Math.round(box.width * sx);
  const h = Math.round(box.height * sy), y = canvas.height - Math.round((box.top - whole.top) * sy) - h;
  const px = new Uint8Array(w * h * 4);
  gl.readPixels(x, y, w, h, gl.RGBA, gl.UNSIGNED_BYTE, px);
  let lit = 0;
  for (let i = 0; i < px.length; i += 16) if (Math.max(px[i], px[i + 1], px[i + 2]) > 48) lit++;
  return lit;
})()"""

# What the page holds: its layers and sources, the panel's list, the map, the sliders.
STATE = """(() => {
  const v = window.viewer; if (!v?.layerManager || !window.mesospim) return null;
  const layers = v.layerManager.managedLayers;
  const slider = (id) => { const box = document.querySelector(id); const input = box && !box.hidden ? box.querySelector('input') : null;
    return input ? Number(input.max) - Number(input.min) + 1 : 0; };
  return JSON.stringify({
    layers: layers.length,
    names: layers.map((m) => m.name),
    sources: layers.map((m) => (m.layer?.dataSources ?? []).length),
    loaded: layers.length > 0 && layers.every((m) => (m.layer?.dataSources ?? []).every((s) => s.loadState !== undefined)),
    errors: layers.flatMap((m) => (m.layer?.dataSources ?? []).map((s) => s.loadState?.error?.message).filter(Boolean)),
    listed: [...document.querySelectorAll('.card.acquisitions .acquisition')].map((r) => r.dataset.name),
    cells: document.querySelectorAll('.tile-map .position').length,
    layout: window.mesospim.layout(),
    planes: slider('#slider-z'), times: slider('#slider-t'),
    rules: Object.values(window.mesospim.overviewRules()).filter((rule) => rule.across).length,
  });
})()"""

# About the graphics card and the browser inside the window.
ABOUT = """(() => {
  const gl = window.viewer?.display?.gl; if (!gl) return null;
  const named = gl.getExtension('WEBGL_debug_renderer_info');
  return JSON.stringify({
    card: named ? gl.getParameter(named.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
    browser: (navigator.userAgent.match(/Chrom(?:e|ium)\\/[\\d.]+/) || ['unknown'])[0],
    texture: gl.getParameter(gl.MAX_3D_TEXTURE_SIZE),
    limits: [window.viewer.chunkQueueManager.capacities.gpuMemory.sizeLimit.value,
             window.viewer.chunkQueueManager.capacities.systemMemory.sizeLimit.value],
  });
})()"""

# Move the picture for three seconds and note how long each frame takes.
MOVE = """((kind) => {
  const v = window.viewer, flat = v.navigationState, space = flat.position.coordinateSpace.value;
  const x = space.names.indexOf('x'), began = performance.now(), from = flat.position.value[x], zoom = flat.zoomFactor.value;
  const turn = v.perspectiveNavigationState?.pose?.orientation;
  const frames = []; let last = began;
  window.__moved = null;
  const step = (now) => {
    frames.push(now - last); last = now;
    const phase = (now - began) / 400;
    if (kind === 'pan') { const at = Float32Array.from(flat.position.value); at[x] = from + 30 * Math.sin(phase); flat.position.value = at; }
    else if (kind === 'zoom') { flat.zoomFactor.value = zoom * (1 + 0.4 * Math.sin(phase)); }
    else if (turn) { const q = turn.orientation; const a = 0.02; const s = Math.sin(a), c = Math.cos(a);
      const [qx, qy, qz, qw] = q; q[0] = qx * c + qz * s; q[1] = qy * c + qw * s; q[2] = qz * c - qx * s; q[3] = qw * c - qy * s; turn.changed.dispatch(); }
    if (now - began < 3000) requestAnimationFrame(step);
    else { if (kind === 'pan') { const at = Float32Array.from(flat.position.value); at[x] = from; flat.position.value = at; }
           if (kind === 'zoom') flat.zoomFactor.value = zoom;
           window.__moved = frames.slice(2); }
  };
  requestAnimationFrame(step);
  return true;
})"""
MOVED = "(() => window.__moved ? JSON.stringify(window.__moved) : null)()"

# One frame drawn from scratch, and how often the engine switched buffers for it.
COUNT = """(() => {
  const gl = window.viewer.display.gl;
  if (!gl.__switches) { gl.__switches = { n: 0 }; const stock = gl.bindFramebuffer.bind(gl);
    gl.bindFramebuffer = (...given) => { gl.__switches.n++; return stock(...given); }; }
  for (const panel of window.viewer.display.panels) if (panel.sliceView) panel.sliceView.renderingStale = true;
  const before = gl.__switches.n; window.viewer.display.draw();
  return gl.__switches.n - before;
})()"""


class Rehearsal:
    def __init__(self, folder: Path, tiles: int) -> None:
        from mesoSPIM.src.mesospim_viewer.viewer import _qt
        from mesoSPIM.src.mesospim_viewer.window import make_window_class

        self.folder = folder
        self.many = tiles
        self.qt = _qt()
        self.app = self.qt.QtWidgets.QApplication.instance() or self.qt.QtWidgets.QApplication(sys.argv[:1])
        self.results: list[tuple[str, str, str]] = []
        self.notes: list[str] = []
        self.heard: list[str] = []
        handler = logging.Handler()
        handler.emit = lambda record: self.heard.append(f"{record.levelname} {record.getMessage()}")
        handler.setLevel(logging.WARNING)
        logging.getLogger().addHandler(handler)
        self.data = folder / "data"
        self.data.mkdir(parents=True)
        self.window = make_window_class()(self.data)
        self.window.resize(1280, 820)
        self.window.show()

    # -- talking to the window -----------------------------------------------------------

    def spin(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def js(self, script: str, timeout_s: float = 20.0):
        answer: list = []
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.window.web.page().runJavaScript(script, answer.append)
            self.spin(0.25)
            if answer and answer[-1] is not None:
                return answer[-1]
            answer.clear()
        return None

    def state(self) -> dict:
        raw = self.js(STATE)
        return json.loads(raw) if raw else {}

    def until(self, look, wanted, timeout_s: float = 60.0):
        deadline = time.monotonic() + timeout_s
        seen = look()
        while not wanted(seen) and time.monotonic() < deadline:
            self.spin(0.3)
            seen = look()
        return seen

    def lit(self) -> int:
        return int(self.js(LIT) or 0)

    def sources_of(self, acquisition: str, state: dict | None = None) -> list[int]:
        """How many sources each channel of one acquisition has on the page."""
        state = state if state is not None else self.state()
        return [count for name, count in zip(state.get("names") or [], state.get("sources") or [])
                if name.startswith(acquisition + " ")]

    def click(self, selector: str) -> None:
        self.js(f"(() => {{ const b = document.querySelector({json.dumps(selector)}); if (b) b.click(); return !!b; }})()")

    def frames(self, kind: str) -> dict:
        self.js(f"{MOVE}({json.dumps(kind)})")
        raw = self.until(lambda: self.js(MOVED, timeout_s=2.0), bool, timeout_s=60.0)
        times = sorted(json.loads(raw)) if raw else []
        if not times:
            return {}
        return {"frames": len(times), "median": times[len(times) // 2], "slow": times[int(len(times) * 0.95)], "worst": times[-1]}

    # -- one check ---------------------------------------------------------------------

    def check(self, name: str, work) -> None:
        print(f"  ... {name}", flush=True)
        before = len(self.heard)
        try:
            ok, detail = work()
        except Exception as error:  # noqa: BLE001 -- a check that breaks has failed, and says how
            ok, detail = False, f"it stopped with {type(error).__name__}: {error}"
        refused = [line for line in self.heard[before:] if "PermissionError" in line or "FileNotFoundError" in line]
        if refused:
            ok, detail = False, f"{detail}; the log holds: {refused[0][:160]}"
        self.results.append(("PASS" if ok else "FAIL", name, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)

    def show_only(self, name: str) -> None:
        self.window.library.show(name, True, only=True)
        self.window.viewer.fit()
        self.spin(1.0)

    # -- the checks ----------------------------------------------------------------------

    def the_window(self):
        about = self.until(lambda: self.js(ABOUT), bool, timeout_s=60)
        if not about:
            return False, "the page did not start, or has no WebGL: see 'If something is wrong' in the docs"
        self.about = json.loads(about)
        return True, f"{self.about['card']}, {self.about['browser']}"

    def a_saved_acquisition(self):
        acquisition = self.data / "saved.ome.zarr"
        acquisition.mkdir()
        (acquisition / ".zgroup").write_text('{"zarr_format": 2}')
        for tile, (y, x) in enumerate(((0, 0), (0, 144), (144, 0), (144, 144))):
            write_tile(acquisition / TILE.format(tile), origin_um=(0, y, x), seed=tile, timepoints=2)
        seen = self.until(self.state, lambda s: s.get("listed") == ["saved"] and s.get("loaded") and s.get("sources") == [4, 4], 90)
        if seen.get("sources") != [4, 4]:
            return False, f"its four tiles were not all shown: {seen}"
        lit = self.until(self.lit, lambda n: n > 200, 60)
        ok = lit > 200 and seen["cells"] == 4 and seen["times"] == 2 and seen["planes"] > 1
        return ok, f"four tiles in two channels drawn, {seen['cells']} cells on the map, {seen['times']} time points"

    def the_writers_own_data(self):
        from .test_window import _a_tile_from_the_writer

        found = []
        for shards in (True, False):
            name = "shards" if shards else "chunks"
            acquisition = self.data / f"written_{name}.ome.zarr"
            acquisition.mkdir()
            (acquisition / "zarr.json").write_text('{"zarr_format": 3, "node_type": "group", "attributes": {}}')
            _a_tile_from_the_writer(acquisition / TILE.format(0), shards=shards)
            self.until(self.state, lambda s, n=f"written_{name}": n in s.get("listed", []), 60)
            self.show_only(f"written_{name}")
            lit = self.until(self.lit, lambda n: n > 100, 60)
            found.append((name, lit))
        ok = all(lit > 100 for _, lit in found)
        return ok, "compressed OME-Zarr 0.5 from the microscope's writer is drawn, " + " and ".join(
            f"with {name}" if lit > 100 else f"NOT with {name}" for name, lit in found)

    def a_live_run(self):
        from . import realistic

        why = realistic.writer_available()
        if why:
            return False, why
        window, seen = self.window, {"first": None, "lit": None, "live": 0}

        def camera(what: str, *said) -> None:
            if what == "begin":
                window.begin_stack(said[0])
            elif what == "plane":
                seen["first"] = seen["first"] or time.time()
                window.add_plane(*said)
            elif what == "end":
                window.end_stack()
            elif what == "finished":
                window.end_run()

        problems: list[str] = []

        def acquire() -> None:
            try:
                realistic.acquire(self.data, "live", tiles=2, channels=("488", "561"), planes=96, size=256, fps=24.0, camera=camera)
            except Exception as error:  # noqa: BLE001
                problems.append(repr(error))

        writer = threading.Thread(target=acquire, daemon=True)
        writer.start()
        while writer.is_alive():
            self.spin(0.3)
            seen["live"] = max(seen["live"], max(self.sources_of("live") or [0]))
            if seen["first"] and seen["lit"] is None and self.lit() > 200:
                seen["lit"] = time.time()
        if problems:
            return False, f"the writer failed: {problems[0]}"
        done = self.until(self.state, lambda s: self.sources_of("live", s) == [2, 2] and s.get("loaded"), 90)
        self.show_only("live")
        lit = self.until(self.lit, lambda n: n > 200, 30)
        if self.sources_of("live", done) != [2, 2] or lit <= 200:
            return False, f"after the run the two tiles were not drawn from disk: {done}"
        if seen["lit"] is None:
            return False, "the picture stayed dark while the run was going on"
        return True, f"first picture {seen['lit'] - seen['first']:.1f} s after the first frame; afterwards both tiles from disk"

    def a_time_lapse(self):
        from .stores import Slabs, age

        acquisition = self.data / "lapse.ome.zarr"
        acquisition.mkdir()
        (acquisition / "zarr.json").write_text('{"zarr_format": 3, "node_type": "group", "attributes": {}}')
        tiles = [Slabs(acquisition / TILE.format(n), planes=16, slab=8, size=128, across=64, origin_um=(0, 0, n * 120.0), seed=n) for n in range(2)]
        for tile in tiles:
            for c in range(2):
                tile.write_stack(c=c)
        age(acquisition, 1200)
        self.until(self.state, lambda s: "lapse" in s.get("listed", []), 60)
        self.show_only("lapse")
        full = self.until(self.lit, lambda n: n > 100, 60)
        if full <= 100:
            return False, "the first time point was not drawn"
        least = full
        for tile in tiles:
            tile.create(timepoints=2)
            for c in range(2):
                tile.write_stack(t=1, c=c)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            least = min(least, self.lit())
            if self.state().get("times") == 2:
                break
        times = self.state().get("times")
        for _ in range(8):
            least = min(least, self.lit())
        ok = times == 2 and least > 0.8 * full
        return ok, f"the time slider grew to {times}; the picture kept at least {least * 100 // max(full, 1)} % of itself meanwhile"

    def two_and_three_dimensions(self):
        self.show_only("saved")
        self.until(self.lit, lambda n: n > 200, 30)
        waits = []
        for _ in range(2):
            self.click('.segmented.view button[data-layout="3d"]')
            began = time.monotonic()
            lit = self.until(self.lit, lambda n: n > 200, 60)
            waits.append(time.monotonic() - began)
            if lit <= 200:
                return False, "the volume was not drawn"
            self.click('.segmented.view button[data-layout="xy"]')
            if self.until(self.lit, lambda n: n > 200, 30) <= 200:
                return False, "the flat picture did not come back"
        return True, f"the volume was there after {waits[0]:.1f} s the first time and {waits[1]:.1f} s the second"

    def how_it_moves(self):
        self.show_only("saved")
        self.until(self.lit, lambda n: n > 200, 30)
        told = []
        for kind, said in (("pan", "panning"), ("zoom", "zooming")):
            got = self.frames(kind)
            if not got:
                return False, f"{said} drew no frames"
            told.append((said, got))
        self.click('.segmented.view button[data-layout="3d"]')
        self.until(self.lit, lambda n: n > 200, 60)
        got = self.frames("turn")
        self.click('.segmented.view button[data-layout="xy"]')
        if got:
            told.append(("turning the volume", got))
        self.moved = told
        worst = max(got["worst"] for _, got in told)
        detail = "; ".join(f"{said}: a frame every {got['median']:.0f} ms (slowest {got['worst']:.0f} ms)" for said, got in told)
        return worst < 1500, detail

    def the_map(self):
        self.show_only("saved")
        self.until(self.state, lambda s: s.get("cells") == 4, 30)
        where = "(() => { const n = window.viewer.navigationState, s = n.position.coordinateSpace.value; return n.position.value[s.names.indexOf('x')] * s.scales[s.names.indexOf('x')] * 1e6; })()"
        before = self.js(where)
        self.js("(() => { const cell = document.querySelectorAll('.tile-map .position')[1]; cell.dispatchEvent(new MouseEvent('click', { bubbles: true })); return true; })()")
        after = self.until(lambda: self.js(where), lambda x: x is not None and before is not None and abs(x - before) > 20, 15)
        chosen = self.js("(() => document.querySelectorAll('.tile-map .chosen').length)()")
        ok = after is not None and before is not None and abs(after - before) > 20 and chosen == 1
        self.click("#fit")
        return ok, "a click on a cell of the map took the view to that tile and marked it" if ok else f"the view did not go to the tile (x {before} -> {after})"

    def a_large_acquisition(self):
        from .stores import age

        count = self.many
        across = int(np.ceil(np.sqrt(count)))
        acquisition = self.data / "large.ome.zarr"
        acquisition.mkdir()
        (acquisition / ".zgroup").write_text('{"zarr_format": 2}')
        first = write_tile(acquisition / TILE.format(0), origin_um=(0, 0, 0), seed=5)
        for tile in range(1, count):
            path = acquisition / TILE.format(tile)
            shutil.copytree(first, path)
            attributes = json.loads((path / ".zattrs").read_text())
            for dataset in attributes["multiscales"][0]["datasets"]:
                dataset["coordinateTransformations"][1]["translation"] = [0.0, 0.0, 0.0, (tile // across) * 144.0, (tile % across) * 144.0]
            (path / ".zattrs").write_text(json.dumps(attributes))
        age(acquisition, 7 * 24 * 3600)   # as if it were from last week
        began = time.monotonic()
        self.until(self.state, lambda s: "large" in s.get("listed", []), 60)
        self.show_only("large")
        seen = self.until(self.state, lambda s: s.get("loaded") and min(s.get("sources") or [0]) >= count, 240)
        if min(seen.get("sources") or [0]) < count:
            return False, f"only {seen.get('sources')} of {count} tiles came in"
        shown = time.monotonic() - began
        self.until(self.lit, lambda n: n > 200, 60)
        ruled = self.until(self.state, lambda s: s.get("rules", 0) >= count, 90)
        if ruled.get("rules", 0) < count:
            return False, f"{count} tiles were shown after {shown:.0f} s, but the overview picture did not take over ({ruled.get('rules')} of {count})"
        # From a little further away than "everything fits", where the overview is as
        # sharp as the screen whatever the size of the window.
        self.js("(() => { const n = window.viewer.navigationState; n.zoomFactor.value = n.zoomFactor.value * 2; return true; })()")
        self.spin(2.5)
        far = self.js(COUNT)
        lit_far = self.lit()
        moving = self.frames("pan")
        self.js("(() => { const n = window.viewer.navigationState; n.zoomFactor.value = n.zoomFactor.value / %d; return true; })()" % (across * 4))
        self.spin(4.0)
        near = self.js(COUNT)
        lit_near = self.until(self.lit, lambda n: n > 200, 30)
        self.click("#fit")
        ok = far is not None and far <= 30 and lit_far > 50 and lit_near > 200
        self.large = {"tiles": count, "shown": shown, "far": far, "near": near, "moving": moving}
        return ok, (f"{count} tiles shown after {shown:.0f} s; zoomed out a frame switches buffers {far} times "
                    f"(the overview stands in), zoomed in {near} times"
                    + (f"; panning zoomed out: a frame every {moving['median']:.0f} ms" if moving else ""))

    def runs_saved_as_files(self):
        from mesoSPIM.src.plugins.ImageWriterApi import FinalizeImage, WriteImage, WriteRequest
        from mesoSPIM.src.plugins.ImageWriters.TiffWriter import TiffWriter
        from mesoSPIM.src.utils.acquisitions import Acquisition, AcquisitionList

        planes, rows, columns, pixel, step = 24, 256, 256, 2.0, 5.0
        files = self.folder / "files"
        files.mkdir()

        def write(writer_class, name: str, suffix: str) -> Path:
            rows_of_table = []
            for tile, x_pos in enumerate((0.0, 460.0)):
                for laser in ("488 nm", "561 nm"):
                    filename = f"{name}{suffix}" if suffix == ".h5" else f"{name}_Tile{tile}_Ch{laser[:3]}{suffix}"
                    rows_of_table.append(Acquisition(
                        x_pos=x_pos, y_pos=0.0, z_start=0.0, z_end=step * (planes - 1), z_step=step, planes=planes, laser=laser,
                        filter="Empty", zoom="1x", shutterconfig="Left", folder=str(files), filename=filename,
                        image_writer_plugin=writer_class.name()))
            table = AcquisitionList(rows_of_table)
            writer = writer_class()
            yy, xx = np.mgrid[0:rows, 0:columns]
            for number, acq in enumerate(table):
                uri = os.path.join(acq["folder"], acq["filename"])
                writer.open(WriteRequest(uri=uri, shape=(planes, columns, rows), dtype="uint16", axes="ZYX", x_res=pixel, y_res=pixel,
                                         z_res=step, num_tiles=2, num_channels=2, num_rotations=1, num_shutters=1, acq=acq,
                                         acq_list=table, writer_config_file_values={}))
                for z in range(planes):
                    frame = (400 + 20 * z + 6000 * np.exp(-((yy - 60 - 3 * z - 40 * (number % 2)) ** 2 + (xx - 120) ** 2) / 900.0)).astype(np.uint16)
                    writer.write_frame(WriteImage(image=frame, current_image_counter=z, tile_number=number // 2, laser=number % 2, shutter=0,
                                                  rot=0, x_res=(1 / pixel, 1 / pixel), y_res=(1 / pixel, 1 / pixel), z_res=step,
                                                  acq=acq, acq_list=table))
                writer.finalize(FinalizeImage(acq=acq, acq_list=table))
                if suffix != ".h5":
                    Path(uri + "_meta.txt").write_text("\n".join([
                        f"[Laser] {acq['laser']}", f"[Pixelsize in um] {pixel}", f"[x_pos] {acq['x_pos']}", f"[y_pos] {acq['y_pos']}",
                        f"[z_start] {acq['z_start']}", f"[z_stepsize] {step}", f"[z_planes] {planes}",
                        f"[x_pixels] {rows}", f"[y_pixels] {columns}"]))
            return Path(os.path.join(table[0]["folder"], table[0]["filename"]))

        told = []
        kinds = [("TIFF", TiffWriter, "AsTiff", ".tiff")]
        try:
            from mesoSPIM.src.plugins.ImageWriters.H5BDVWriter import H5BDVWriter

            kinds.append(("HDF5", H5BDVWriter, "AsHdf5", ".h5"))
        except Exception as error:  # noqa: BLE001 -- npy2bdv not installed
            told.append(f"HDF5 not tried ({error})")
        ok = True
        for kind, writer_class, name, suffix in kinds:
            file = write(writer_class, name, suffix)
            self.window.open(file)
            self.until(self.state, lambda s, n=name: n in s.get("listed", []), 60)
            self.show_only(name)
            seen = self.until(self.state, lambda s: s.get("loaded") and s.get("sources") == [2, 2], 60)
            lit = self.until(self.lit, lambda n: n > 100, 60)
            good = seen.get("sources") == [2, 2] and lit > 100 and not seen.get("errors")
            ok = ok and good
            told.append(f"{kind} opened, two tiles in two channels" if good else f"{kind} NOT shown: {seen.get('errors') or seen.get('sources')}")
        return ok, "; ".join(told)

    def resizing_and_closing(self):
        self.show_only("saved")
        self.window.resize(900, 640)
        self.spin(1.5)
        small = self.until(self.lit, lambda n: n > 100, 20)
        self.window.resize(1280, 820)
        self.spin(1.5)
        large = self.until(self.lit, lambda n: n > 100, 20)
        began = time.monotonic()
        self.window.close()
        self.spin(0.5)
        took = time.monotonic() - began
        return small > 100 and large > 100 and took < 10, f"the picture stayed through two sizes; the window closed in {took:.1f} s"

    # -- all of it -----------------------------------------------------------------------

    def run(self) -> int:
        for name, work in (
            ("The window opens and the page starts", self.the_window),
            ("A saved acquisition is drawn, with its map and sliders", self.a_saved_acquisition),
            ("Data from the microscope's own writer", self.the_writers_own_data),
            ("A run is followed live and handed over to the tiles on disk", self.a_live_run),
            ("A time lapse: the tiles keep their picture as time points arrive", self.a_time_lapse),
            ("2D to 3D and back, twice", self.two_and_three_dimensions),
            ("Moving, zooming and turning", self.how_it_moves),
            ("The map takes the view to a tile", self.the_map),
            (f"A large acquisition of {self.many} tiles and its overview", self.a_large_acquisition),
            ("Runs saved as TIFF and HDF5 open from their files", self.runs_saved_as_files),
            ("Resizing, and closing the window", self.resizing_and_closing),
        ):
            self.check(name, work)
            if name.startswith("The window opens") and self.results[-1][0] == "FAIL":
                break
        return sum(1 for verdict, _, _ in self.results if verdict == "FAIL")


def memory_advice(about: dict) -> list[str]:
    """What the PC has, and what the viewer could be given of it."""
    lines = []
    card_mb = None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
        card_mb = int(out[0]) if out else None
    except Exception:  # noqa: BLE001 -- no NVIDIA card, or no nvidia-smi
        pass
    if card_mb is None and platform.system() == "Windows":
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_VideoController | Measure-Object -Property AdapterRAM -Maximum).Maximum"],
                                 capture_output=True, text=True, timeout=20).stdout.strip()
            card_mb = int(int(out) / 1e6) if out else None
        except Exception:  # noqa: BLE001
            pass
    memory_mb = None
    try:
        import psutil

        memory_mb = int(psutil.virtual_memory().total / 1e6)
    except Exception:  # noqa: BLE001
        pass
    limits = about.get("limits") or [0, 0]
    lines.append(f"The viewer keeps up to {limits[0] / 1e6:.0f} MB on the graphics card and {limits[1] / 1e6:.0f} MB in memory now.")
    if card_mb is None:
        lines.append("How much memory the graphics card has could not be read here; look it up in the Task Manager (Performance, GPU).")
        return lines
    lines.append(f"The graphics card has about {card_mb} MB" + (f", the computer {memory_mb} MB of memory." if memory_mb else "."))
    if card_mb < 3000:
        lines.append("That is little: leave the limits as they are.")
        return lines
    gpu = int(min(card_mb * 0.5, 12000) // 500 * 500)
    system = int(min((memory_mb or 16000) * 0.2, 24000) // 1000 * 1000) if memory_mb else 4000
    lines.append("If large acquisitions look coarse for long, or the volume view stays blocky, this in the configuration file would let the viewer keep more:")
    lines.append(f"    data_viewer = {{'gpu_memory_MB': {gpu}, 'system_memory_MB': {max(system, 4000)}}}")
    lines.append("(Half of the graphics card and a fifth of the computer's memory: the camera and the writer need the rest.)")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A rehearsal of the Data viewer in its real window.")
    parser.add_argument("--tiles", type=int, default=100, help="how many tiles the large acquisition has (default 100)")
    parser.add_argument("--keep", action="store_true", help="leave the data the rehearsal made on disk")
    parser.add_argument("--report", default="data_viewer_rehearsal.txt", help="where to write what was found")
    args = parser.parse_args(argv)
    if not (PAGE_DIR / "index.html").is_file():
        print("The viewer's page is not built (mesoSPIM/src/mesospim_viewer/build is missing).")
        return 2
    folder = Path(tempfile.mkdtemp(prefix="data_viewer_rehearsal_"))
    print(f"Rehearsing the Data viewer; its data goes to {folder}\n")
    began = time.time()
    rehearsal = Rehearsal(folder, max(12, args.tiles))
    failed = rehearsal.run()
    lines = [f"Data viewer rehearsal, {time.strftime('%Y-%m-%d %H:%M')}, {platform.platform()}", ""]
    for verdict, name, detail in rehearsal.results:
        lines.append(f"{verdict}  {name}")
        lines.append(f"      {detail}")
    lines.append("")
    about = getattr(rehearsal, "about", {})
    if about:
        lines.append(f"Graphics card: {about.get('card')}. Browser inside the window: {about.get('browser')}.")
        lines.append("")
        lines.append("How it moves on this PC (a frame every 17 ms is as smooth as a 60 Hz screen shows; above 50 ms it feels slow):")
        for said, got in getattr(rehearsal, "moved", []):
            lines.append(f"    {said}: a frame every {got['median']:.0f} ms, one in twenty slower than {got['slow']:.0f} ms, the slowest {got['worst']:.0f} ms")
        lines.append("")
        lines.extend(memory_advice(about))
        lines.append("")
    passed = len(rehearsal.results) - failed
    lines.append(f"{passed} of {len(rehearsal.results)} checks passed, in {time.time() - began:.0f} s."
                 + ("" if not failed else " Send this file along when you report what failed."))
    lines.append("Not rehearsed, still to check by hand in mesoSPIM: the writer's page in the file-naming wizard, "
                 "rows run one at a time, and the file names after a time lapse (docs/source/data_viewer.rst).")
    text = "\n".join(lines)
    print("\n" + text)
    try:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
        print(f"\nWritten to {Path(args.report).resolve()}")
    except OSError as error:
        print(f"\nThe report could not be written ({error}).")
    if not args.keep:
        shutil.rmtree(folder, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
