"""Runs saved as TIFF, raw or BigDataViewer HDF5, opened in the viewer.

The page reads OME-Zarr only. A stack in one of these files is given a description
as an OME-Zarr store, and the pieces of the picture are cut out of the file when the
page asks for them (``filestack.py``). The files here are written by mesoSPIM's own
writers, with the ``_meta.txt`` beside them that mesoSPIM writes, so that what is
read is what the microscope leaves on disk.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")

from mesoSPIM.src.mesospim_viewer import NotAStore, NotSupported, filestack, read_store
from mesoSPIM.src.mesospim_viewer.live import Stack
from mesoSPIM.src.mesospim_viewer.omezarr import read_levels, sample_window
from mesoSPIM.src.plugins.ImageWriterApi import FinalizeImage, WriteImage, WriteRequest
from mesoSPIM.src.utils.acquisitions import Acquisition, AcquisitionList

from .conftest import PICTURE
from .tools import get, offered, sources, until

PLANES, ROWS, COLUMNS = 40, 320, 288
PIXEL_UM, STEP_UM = 2.0, 5.0
PER_UM = (1.0 / PIXEL_UM, 1.0 / PIXEL_UM)  # what mesoSPIM hands a writer with every frame: pixels per micrometre
LASERS = ("488 nm", "561 nm")
PLACES = ((100.0, 200.0), (100.0, 700.0))  # x_pos, y_pos of two tiles, in micrometres


def frame(tile: int, laser: int, z: int) -> np.ndarray:
    """A plane whose values say where it belongs, with a bright square that moves with z."""
    rows, columns = np.mgrid[0:ROWS, 0:COLUMNS]
    plane = (400 + 3 * rows + 2 * columns + 50 * z + 5000 * tile + 20000 * laser).astype(np.uint16)
    plane[100 + z : 140 + z, 60:120] = 40000 + 100 * z + 1000 * laser
    return plane


def rows_of(folder: Path, name: str, suffix: str, writer: str) -> AcquisitionList:
    acquisitions = []
    for tile, (x_pos, y_pos) in enumerate(PLACES):
        for laser in LASERS:
            filename = f"{name}{suffix}" if suffix == ".h5" else f"{name}_Tile{tile}_Ch{laser[:3]}_Sh0{suffix}"
            acquisitions.append(Acquisition(
                x_pos=x_pos, y_pos=y_pos, z_start=10.0, z_end=10.0 + STEP_UM * (PLANES - 1), z_step=STEP_UM,
                planes=PLANES, laser=laser, filter="Empty", zoom="1x", shutterconfig="Left",
                folder=str(folder), filename=filename, image_writer_plugin=writer,
            ))
    return AcquisitionList(acquisitions)


def write_meta(file: Path, acq) -> None:
    """The ``_meta.txt`` mesoSPIM writes beside a stack (mesoSPIM_ImageWriter.write_metadata)."""
    lines = [
        f"[Metadata for file] {file}", "", "[CFG]", f"[Laser] {acq['laser']}", "[Intensity (%)] 10", "[Zoom] 1x",
        f"[Pixelsize in um] {PIXEL_UM}", "[Filter] Empty", "[Shutter] Left", "", "[POSITION]",
        f"[x_pos] {acq['x_pos']}", f"[y_pos] {acq['y_pos']}", f"[z_start] {acq['z_start']}",
        f"[z_end] {acq['z_end']}", f"[z_stepsize] {acq['z_step']}", f"[z_planes] {PLANES}", "[rot] 0", "",
        "[CAMERA PARAMETERS]", f"[x_pixels] {ROWS}", f"[y_pixels] {COLUMNS}",
    ]
    Path(str(file) + "_meta.txt").write_text("\n".join(lines))


def acquire(folder: Path, name: str, writer_class, suffix: str, planes: int = PLANES) -> AcquisitionList:
    """Run mesoSPIM's own writer over two tiles in two channels, as an acquisition does."""
    table = rows_of(folder, name, suffix, writer_class.name())
    writer = writer_class()
    for number, acq in enumerate(table):
        tile, laser = divmod(number, len(LASERS))
        uri = os.path.join(acq["folder"], acq["filename"])
        writer.open(WriteRequest(
            uri=uri, shape=(planes, COLUMNS, ROWS), dtype="uint16", axes="ZYX", x_res=PIXEL_UM, y_res=PIXEL_UM,
            z_res=STEP_UM, num_tiles=len(PLACES), num_channels=len(LASERS), num_rotations=1, num_shutters=1,
            acq=acq, acq_list=table, writer_config_file_values={},
        ))
        for z in range(planes):
            writer.write_frame(WriteImage(
                image=frame(tile, laser, z), current_image_counter=z, tile_number=tile, laser=laser, shutter=0, rot=0,
                x_res=PER_UM, y_res=PER_UM, z_res=STEP_UM, acq=acq, acq_list=table,
            ))
        writer.finalize(FinalizeImage(acq=acq, acq_list=table))
        if suffix != ".h5":
            write_meta(Path(uri), acq)
    return table


def whole(tile: int, laser: int) -> np.ndarray:
    return np.stack([frame(tile, laser, z) for z in range(PLANES)])


def piece(held, level: int, at: tuple[int, int, int]) -> np.ndarray:
    raw = held.read(f"{level}/{at[0]}/{at[1]}/{at[2]}")
    return np.frombuffer(raw, dtype="<u2").reshape(filestack.CHUNK)


@pytest.fixture(scope="module")
def tiff_run(tmp_path_factory):
    from mesoSPIM.src.plugins.ImageWriters.TiffWriter import TiffWriter

    folder = tmp_path_factory.mktemp("tiff")
    acquire(folder, "Brain", TiffWriter, ".tiff")
    return folder


@pytest.fixture(scope="module")
def h5_run(tmp_path_factory):
    pytest.importorskip("h5py")
    pytest.importorskip("npy2bdv")
    from mesoSPIM.src.plugins.ImageWriters.H5BDVWriter import H5BDVWriter

    folder = tmp_path_factory.mktemp("h5")
    acquire(folder, "Brain", H5BDVWriter, ".h5")
    return folder


# -- one stack in a file ----------------------------------------------------------------------


def test_a_tiff_stack_is_described_from_the_file_and_its_meta_file(tiff_run):
    store = read_store(tiff_run / "Brain_Tile1_Ch561_Sh0.tiff")
    assert [axis.name for axis in store.axes] == ["z", "y", "x"]
    assert store.shape == (PLANES, ROWS, COLUMNS)
    assert store.scale == (STEP_UM, PIXEL_UM, PIXEL_UM)
    assert store.translation == (10.0, 700.0, 100.0), "z_start, y_pos and x_pos of its _meta.txt"
    assert [channel.label for channel in store.channels] == ["561"]
    assert len(store.levels) >= 3, "smaller copies are offered though the file has none"


def test_the_pieces_of_a_tiff_are_the_files_own_voxels(tiff_run):
    held = filestack.virtual(tiff_run / "Brain_Tile1_Ch488_Sh0.tiff")
    data = whole(1, 0)
    assert np.array_equal(piece(held, 0, (0, 0, 0)), data[:16, :256, :256])
    # A piece at the edge is full size, and empty where the stack has ended.
    edge = piece(held, 0, (2, 1, 1))
    assert np.array_equal(edge[:8, : ROWS - 256, : COLUMNS - 256], data[32:, 256:, 256:])
    assert not edge[8:].any() and not edge[:, ROWS - 256 :].any()
    assert held.read("0/3/0/0") is None, "past the last plane there is nothing"
    # The smaller copies are every second voxel across, every fourth, and so on. The
    # depth is halved only once a voxel is as wide as the planes are apart (5 um here).
    assert held.factors[:4] == [(1, 1, 1), (1, 2, 2), (2, 4, 4), (4, 8, 8)]
    assert np.array_equal(piece(held, 1, (0, 0, 0))[:16, : ROWS // 2, : COLUMNS // 2], data[:16, ::2, ::2])
    assert np.array_equal(piece(held, 2, (0, 0, 0))[:, : ROWS // 4, : COLUMNS // 4], data[::2, ::4, ::4][:16])


def test_the_description_is_ome_zarr_a_reader_accepts(tiff_run):
    import json

    held = filestack.virtual(tiff_run / "Brain_Tile0_Ch488_Sh0.tiff")
    attributes = json.loads(held.read(".zattrs"))
    multiscale = attributes["multiscales"][0]
    assert multiscale["version"] == "0.4" and [axis["name"] for axis in multiscale["axes"]] == ["z", "y", "x"]
    first, second = multiscale["datasets"][:2]
    assert first["coordinateTransformations"][0]["scale"] == [STEP_UM, PIXEL_UM, PIXEL_UM]
    assert first["coordinateTransformations"][1]["translation"] == [10.0, 200.0, 100.0]
    assert second["coordinateTransformations"][0]["scale"] == [STEP_UM, 2 * PIXEL_UM, 2 * PIXEL_UM]
    array = json.loads(held.read("0/.zarray"))
    assert array["shape"] == [PLANES, ROWS, COLUMNS] and array["dtype"] == "<u2" and array["compressor"] is None
    assert json.loads(held.read(".zgroup")) == {"zarr_format": 2}
    assert held.read("nothing/like/this") is None


def test_the_contrast_of_a_tiff_is_set_from_its_data(tiff_run):
    store = read_store(tiff_run / "Brain_Tile0_Ch488_Sh0.tiff")
    low, high = sample_window(store, None)
    assert 300 < low < 5000 and high > 30000
    assert [level.shape for level in read_levels(store)][0] == (PLANES, ROWS, COLUMNS)


def test_a_compressed_tiff_is_read_plane_by_plane(tmp_path):
    data = whole(0, 0)[:6]
    file = tmp_path / "packed_Tile0_Ch488.tif"
    tifffile.imwrite(str(file), data, compression="zlib")
    held = filestack.virtual(file)
    assert held.store.shape == (6, ROWS, COLUMNS)
    assert held.store.scale == (1.0, 1.0, 1.0), "no _meta.txt: a voxel counts as one micrometre"
    assert np.array_equal(piece(held, 0, (0, 0, 0))[:6], data[:, :256, :256])
    # Its voxels are as deep as they are wide, so the smaller copy halves the depth too.
    assert np.array_equal(piece(held, 1, (0, 0, 0))[:3, : ROWS // 2, : COLUMNS // 2], data[::2, ::2, ::2])


def test_a_raw_file_is_read_with_the_size_its_meta_file_gives(tmp_path):
    from mesoSPIM.src.plugins.ImageWriters.RawWriter import TiffWriter as RawWriter  # the class is called that in its file

    acquire(tmp_path, "Lung", RawWriter, ".raw")
    store = read_store(tmp_path / "Lung_Tile1_Ch488_Sh0.raw")
    assert store.shape == (PLANES, ROWS, COLUMNS) and store.translation == (10.0, 700.0, 100.0)
    held = filestack.virtual(tmp_path / "Lung_Tile1_Ch488_Sh0.raw")
    assert np.array_equal(piece(held, 0, (1, 0, 0)), whole(1, 0)[16:32, :256, :256])
    # Without its _meta.txt nobody knows how large a plane is, and the viewer says so.
    os.remove(str(tmp_path / "Lung_Tile0_Ch488_Sh0.raw") + "_meta.txt")
    with pytest.raises(NotSupported, match="_meta.txt"):
        read_store(tmp_path / "Lung_Tile0_Ch488_Sh0.raw")


def test_a_tiff_with_several_channels_in_one_file_is_turned_away_with_the_reason(tmp_path):
    file = tmp_path / "multi.tif"
    tifffile.imwrite(str(file), np.zeros((4, 2, 64, 64), np.uint16), imagej=True, metadata={"axes": "ZCYX"})
    with pytest.raises(NotSupported, match="several channels"):
        read_store(file)
    (tmp_path / "broken.tif").write_bytes(b"not a tiff at all")
    with pytest.raises(NotAStore):
        read_store(tmp_path / "broken.tif")


def test_an_hdf5_file_holds_a_run_of_its_own(h5_run):
    file = h5_run / "Brain.h5"
    members = filestack.members_of(file)
    assert [member.name for member in members] == ["s00", "s01", "s02", "s03"]
    stores = [read_store(member) for member in members]
    assert [store.shape for store in stores] == [(PLANES, ROWS, COLUMNS)] * 4
    assert stores[0].scale == (STEP_UM, PIXEL_UM, PIXEL_UM)
    # Which tile and which channel each stack is, the .xml beside the file says.
    said = [(filestack.position_name(member), store.channels[0].label) for member, store in zip(members, stores)]
    assert said == [("Tile 1", "488"), ("Tile 2", "488"), ("Tile 1", "561"), ("Tile 2", "561")]
    # The place is the one the writer put into the .xml, as BigStitcher reads it: the
    # two channels of a tile in the same place, the two tiles 500 um apart along y.
    assert stores[0].translation[0] == pytest.approx(10.0)
    assert stores[0].translation == stores[2].translation and stores[1].translation == stores[3].translation
    assert abs(stores[1].translation[1] - stores[0].translation[1]) == pytest.approx(500.0)
    assert stores[1].translation[2] == pytest.approx(stores[0].translation[2])


def test_the_pieces_of_an_hdf5_stack_keep_the_cameras_sixteen_bits(h5_run):
    """BigDataViewer keeps a camera's values as signed numbers, where 40000 reads as a
    negative number. They are handed to the page as the values the camera gave."""
    held = filestack.virtual(h5_run / "Brain.h5" / "s03")   # tile 2, channel 561
    data = whole(1, 1)
    assert data.max() > 40000
    assert np.array_equal(piece(held, 0, (0, 0, 0)), data[:16, :256, :256])
    assert np.array_equal(piece(held, 1, (1, 0, 0))[:16, : ROWS // 2, : COLUMNS // 2], data[16:32, ::2, ::2])
    assert held.costly and sample_window(held.store, None) is None, "left to the page: a sample would read every block"


# -- which files make a run ---------------------------------------------------------------------


def test_the_files_of_a_run_are_told_by_their_names(tiff_run, tmp_path):
    key = filestack.run_key
    assert key(Path("Brain_Tile3_Ch488_Sh0_Rot0.tiff")) == "Brain"
    assert key(Path("mouse-12_left_561nm.tif")) == "mouse"
    assert key(Path("Tile0.tif")) == "Tile0", "a name of such words only stands for itself"
    runs = filestack.runs_in(tiff_run)
    assert [(run.name, len(run.members)) for run in runs] == [("Brain", 4)]
    assert runs[0].path.name == "Brain.tiff.run" and not runs[0].path.exists()
    assert filestack.run_at(tiff_run / "Brain_Tile1_Ch561_Sh0.tiff") == runs[0]
    # Maximum projections and files of other kinds are not stacks of a run.
    tifffile.imwrite(str(tmp_path / "MAX_Brain_Tile0.tiff"), np.zeros((8, 8), np.uint16))
    (tmp_path / "notes.txt").write_text("x")
    assert filestack.runs_in(tmp_path) == []


# -- in the library and on the page -------------------------------------------------------------


def test_opening_one_file_of_a_run_opens_the_whole_run(library, tiff_run):
    names = library.open(tiff_run / "Brain_Tile1_Ch488_Sh0.tiff")
    assert names == ["Brain"]
    entry = offered(library.viewer)["Brain"]
    assert entry["shown"] and entry["channels"] == ["488", "561"] and entry["tiles"] == 2
    assert [position["name"] for position in entry["positions"]] == ["Tile 1", "Tile 2"]
    assert entry["positions"][1]["um"]["y"] == [700.0, 700.0 + ROWS * PIXEL_UM]
    assert len(sources(library.viewer, "Brain")) == 2, "two tiles feed each channel"
    # Its files are whole: an hour of looks asks nothing more of them than whether they changed.
    for _ in range(12):
        library.clock.tick(300)
        library.poll()
    assert offered(library.viewer)["Brain"]["tiles"] == 2
    assert library.open(tiff_run / "Brain_Tile0_Ch561_Sh0.tiff") == ["Brain"], "the same run is not listed twice"


def test_a_folder_of_runs_saved_as_files_lists_them(library, tiff_run, h5_run, tmp_path):
    library.watch(tiff_run)
    assert library.names == ["Brain"] and library.shown == ["Brain"]
    assert library.open(h5_run / "Brain.h5") == ["Brain (2)"]
    entry = offered(library.viewer)["Brain (2)"]
    assert entry["channels"] == ["488", "561"] and [p["name"] for p in entry["positions"]] == ["Tile 1", "Tile 2"]
    with pytest.raises(NotAStore):
        library.open(tmp_path / "missing.tiff")


def test_the_server_hands_the_page_a_stack_in_a_file_as_a_zarr_store(library, tiff_run):
    library.open(tiff_run / "Brain_Tile0_Ch488_Sh0.tiff")
    view = library.viewer
    address = view.url + next(iter(sources(view, "Brain").values()))
    status, _, body = get(address + ".zattrs")
    assert status == 200 and b"multiscales" in body
    status, _, body = get(address + "0/0/0/0")
    assert status == 200 and len(body) == 16 * 256 * 256 * 2
    assert get(address + "0/9/0/0")[0] == 404
    assert get(address + "../Brain_Tile0_Ch488_Sh0.tiff_meta.txt")[0] == 404


def test_a_run_saved_as_tiff_is_drawn_by_the_page(pages, viewers, tiff_run):
    from mesoSPIM.src.mesospim_viewer import Library

    view = viewers(ui="simple")
    library = Library(view)
    library.open(tiff_run / "Brain_Tile0_Ch488_Sh0.tiff")
    view.fit()
    page, errors = pages.open(view.start(), width=1000, height=640)
    pages.drawn(page, layers=2, sources=2, timeout_s=60)
    assert until(lambda: page.evaluate(PICTURE)["lit"], lambda lit: lit > 500, 30) > 500
    assert not errors, errors
    library.close()


def test_a_live_run_saved_as_tiff_is_opened_from_its_files_when_it_is_over(library, tmp_path):
    """While it runs, a TIFF cannot be read, and the run is on screen from the camera's
    frames. Its files are not listed as a second run beside it. When the run is over it
    is shown from the files, at full size, and the frames kept in memory are let go."""
    from mesoSPIM.src.plugins.ImageWriters.TiffWriter import TiffWriter

    library.watch(tmp_path)
    table = rows_of(tmp_path, "Liver", ".tiff", TiffWriter.name())
    writer = TiffWriter()
    for number, acq in enumerate(table):
        tile, laser = divmod(number, len(LASERS))
        file = Path(acq["folder"]) / acq["filename"]
        writer.open(WriteRequest(
            uri=str(file), shape=(PLANES, COLUMNS, ROWS), dtype="uint16", axes="ZYX", x_res=PIXEL_UM, y_res=PIXEL_UM,
            z_res=STEP_UM, num_tiles=2, num_channels=2, num_rotations=1, num_shutters=1, acq=acq, acq_list=table,
            writer_config_file_values={},
        ))
        library.begin_stack(Stack(
            acquisition="Liver", channel=LASERS[laser][:3], channels=("488", "561"), planes=PLANES,
            frame=(ROWS, COLUMNS), voxel_um=(STEP_UM, PIXEL_UM, PIXEL_UM),
            origin_um=(10.0, acq["y_pos"], acq["x_pos"]), tile=f"Tile {tile + 1}", tiles=2, file=file,
        ))
        for z in range(PLANES):
            plane = frame(tile, laser, z)
            writer.write_frame(WriteImage(image=plane, current_image_counter=z, tile_number=tile, laser=laser,
                                          shutter=0, rot=0, x_res=PER_UM, y_res=PER_UM, z_res=STEP_UM,
                                          acq=acq, acq_list=table))
            library.add_plane(z, plane)
        library.clock.tick()
        library.poll()
        assert library.names == ["Liver"], "the files being written are not listed as a run of their own"
        writer.finalize(FinalizeImage(acq=acq, acq_list=table))
        write_meta(file, acq)
        library.end_stack()
    assert library.dataset("Liver").path is None and library.dataset("Liver").previews
    library.end_run()
    library.clock.tick()
    library.poll()
    assert library.names == ["Liver"] and library.shown == ["Liver"]
    opened = library.dataset("Liver")
    assert opened.path == (tmp_path / "Liver.tiff.run").resolve() and not opened.previews
    entry = offered(library.viewer)["Liver"]
    assert entry["tiles"] == 2 and entry["channels"] == ["488", "561"] and not entry["live"]
    assert all(name.startswith("store-") for name in sources(library.viewer, "Liver"))
