# Handoff: two upstream pull requests to open on mesoSPIM/mesoSPIM-control

Everything is pushed; this file holds what is needed to pick the work up later.
The two branches below are rebased onto upstream `release/candidate-py312` (the
branch mesoSPIM PR #106 targets), each a single commit, with the code contained
in new files the way #106 does it.

| Branch on this fork | Head | Touches in existing files |
|---|---|---|
| `tczyx-writer` | 716e956 | changelog, two doc pages, config example (one comment line each); `omezarr_writer.py` untouched |
| `data-viewer` (stacked on `tczyx-writer`) | 245737a | 2 lines in `mesoSPIM_Control.py`, 2 lines in `mesoSPIM_MainWindow.py`, the menu action in the `.ui`, docs, changelog |

Claude cannot open pull requests on mesoSPIM/mesoSPIM-control (GitHub answers
403: the Claude GitHub App is installed on this account, not on the mesoSPIM
organisation), so they are opened by hand from the compare links, writer first.
The viewer package the Data viewer needs is on thomdehoog/ZMART-viewer, PR #2
(`mesospim-view` -> `main`); the docs install it from that repository's default
branch, so merge that PR first.

The fork's `master` still carries the earlier, scattered version of the same
work (#2 to #5 on the fork). It can be reset to upstream plus these two commits
when convenient.

# PR 1

Open at: https://github.com/mesoSPIM/mesoSPIM-control/compare/release/candidate-py312...thomdehoog:mesoSPIM-control:tczyx-writer

Title: Add MP_OME_Zarr_TCZYX_Writer: one (t, c, z, y, x) OME-Zarr store per tile

## Summary

A new mode of the multi-process OME-Zarr writer, `MP_OME_Zarr_TCZYX_Writer`, that writes **one `(t, c, z, y, x)` store per tile** instead of one store per tile and channel. The existing `MP_OME_Zarr_Writer` is not changed in behaviour; the new plugin subclasses it and reuses its pipeline (ring buffer, worker process, live pyramid).

- Channels of a tile are its `c` axis; the time points of a time lapse are **appended along `t`** to the stores already on disk (mesoSPIM's `_Time###` file mark is read and stripped, so every time point lands in the same store).
- **No chunk or shard spans a channel or a time point** (leading `(1, 1)` chunk and shard dimensions), and a shard is always exactly one z-chunk deep, so every shard is written in one go by zarr v3 and a later stack only ever adds files.
- Stage position goes into the OME `translation`, channel names and colours into `omero`.
- OME-NGFF 0.4 (zarr v2) or 0.5 (zarr v3, sharding); chunks and shards are configurable with defaults (`MP_OME_Zarr_TCZYX_Writer = {...}` in `demo_config.py`, documented in `configuration.rst` and `file_formats.rst`).

## Files

- `mesoSPIM/src/plugins/ImageWriters/OmeZarrWriterMPTCZYX.py` (new): the plugin, a subclass of `OMEZarrWriterMP`.
- `mesoSPIM/src/plugins/support_files/ImageWriters/OmeZarrWriterMP/omezarr_writer_tczyx.py` (new): opens a tile's store as `(t, c, z, y, x)` and hands the existing live pyramid pipeline (`Live3DPyramidWriter`) a window onto `[t, c]` of each array. **`omezarr_writer.py` is not changed**, so the existing `MP_OME_Zarr_Writer` cannot be affected.
- `mesoSPIM/test/test_omezarr_tczyx_writer.py`: 6 tests (layout, appended time points, chunk/shard rules, naming). `mesoSPIM/test/benchmark_omezarr_shards.py`: a shard-vs-chunk write benchmark to run on the acquisition PC.
- Config, docs and changelog.

The only edits to existing files are the config example, two doc pages and the changelog (one comment line each in `demo_config.py` and `configuration.rst` list the new writer).

# PR 2

Open at (after PR 1 is open; it stacks on it): https://github.com/mesoSPIM/mesoSPIM-control/compare/release/candidate-py312...thomdehoog:mesoSPIM-control:data-viewer

Title: Data viewer: View → Open Data Viewer, the acquisition being written shown as it lands

## Summary

A **Data viewer** window (`View → Open Data Viewer`) that shows the acquisition being written, tile by tile and time point by time point, as it lands on disk. It follows the newest acquisition in the folder the acquisition list saves into; a dropdown in its panel switches to earlier acquisitions of the session. 2D/3D, per-channel window with histogram and colour, depth and time sliders, projection and detail controls in 3D.

The viewer itself is the separate `mesospim_view` package (a small stdlib-Python driver for a native neuroglancer page, https://github.com/thomdehoog/ZMART-viewer), installed with `pip install "git+https://github.com/thomdehoog/ZMART-viewer"` plus `PyQtWebEngine`. mesoSPIM-control gains no hard dependency: without the package the menu entry shows a message saying how to install it.

It reads the layout `MP_OME_Zarr_TCZYX_Writer` writes, so this PR is stacked on the writer PR and includes its commits until that one merges.

## Files

- `mesoSPIM/src/mesoSPIM_DataViewer.py` (new): everything. `open_window()` (lazy import of the package, a message with the install line when it is missing, the acquisition folder or a folder dialog) and `prepare_qt()` (the guarded `QtWebEngineWidgets` import with `AA_ShareOpenGLContexts`).
- `mesoSPIM/src/mesoSPIM_MainWindow.py`: two lines, the import and the menu action's connect. `mesoSPIM/gui/mesoSPIM_MainWindow.ui`: the menu action.
- `mesoSPIM/mesoSPIM_Control.py`: two lines calling `prepare_qt()` before any `QApplication` exists, because Qt allows the WebEngine import only then.
- `docs/source/data_viewer.rst` (new): what is on screen, installing, a test without a microscope (`python -m mesospim_view.demo --live --window`), troubleshooting. Toctree, user guide and changelog entries.
