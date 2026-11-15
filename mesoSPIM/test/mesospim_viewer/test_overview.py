"""One small picture of a whole acquisition, for the view from far away.

With a hundred tiles on screen the page does a little work for every one of them
on every frame, and loads something of every one. From that far away one small
picture of the whole acquisition shows the same. ``overview.py`` puts it together
from the tiles' own smaller copies, and the page draws it in place of the tiles
while the view is zoomed out. These tests check the picture itself and when it is
offered; what the page does with it is counted in ``test_many_tiles.py``.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pytest

zarr = pytest.importorskip("zarr")

from mesoSPIM.src.mesospim_viewer import Stack, read_store, watch
from mesoSPIM.src.mesospim_viewer.demo import write_tile
from mesoSPIM.src.mesospim_viewer.overview import CHUNK, Overview, signature

from .stores import an_acquisition
from .tools import get, offered

TILE_NAME = "Mag1_Tile{}_Sh0_Rot0.ome.zarr"


def tiles_of(folder, across: int, down: int, timepoints: int = 1) -> list:
    """Tiles of 160 um side by side, 144 um apart, so that neighbours overlap by a strip."""
    return [
        write_tile(folder / TILE_NAME.format(row * across + column), origin_um=(0, row * 144, column * 144),
                   seed=row * across + column, timepoints=timepoints)
        for row in range(down) for column in range(across)
    ]


def picture(overview: Overview, level: int = 0, time_point: int = 0, channel: int = 0) -> np.ndarray:
    """A whole copy of the overview, put together from the pieces the page would ask for."""
    depth, rows, columns = overview.shapes[level]
    whole = np.zeros((-(-depth // CHUNK[0]) * CHUNK[0], -(-rows // CHUNK[1]) * CHUNK[1], -(-columns // CHUNK[2]) * CHUNK[2]), np.uint16)
    for cz in range(whole.shape[0] // CHUNK[0]):
        for cy in range(whole.shape[1] // CHUNK[1]):
            for cx in range(whole.shape[2] // CHUNK[2]):
                raw = overview.read(f"{level}/{time_point}/{channel}/{cz}/{cy}/{cx}")
                whole[cz * CHUNK[0]:(cz + 1) * CHUNK[0], cy * CHUNK[1]:(cy + 1) * CHUNK[1], cx * CHUNK[2]:(cx + 1) * CHUNK[2]] = (
                    np.frombuffer(raw, "<u2").reshape(CHUNK))
    return whole[:depth, :rows, :columns]


def test_the_overview_is_each_tile_where_it_lies(tmp_path):
    paths = tiles_of(tmp_path, 2, 2)
    stores = [read_store(path) for path in paths]
    overview = Overview("run", [(store, f"data/{n}") for n, store in enumerate(stores)])
    assert overview.usable
    # Four tiles of 160 um, 144 um apart: 304 um across, and as deep as one tile.
    assert overview.origin_um == (0.0, 0.0, 0.0)
    assert overview.voxel_um[1] == overview.voxel_um[2] == 1.0, "small enough already: the tiles' own voxels"
    assert overview.shape == (24, 304, 304)
    whole = picture(overview)
    for number, path in enumerate(paths):
        row, column = divmod(number, 2)
        own = np.asarray(zarr.open_array(str(path / "0"), mode="r")[0, 0])
        # Away from the strips it shares with its neighbours, a tile is there voxel for voxel.
        rows = slice(row * 144 + 16, row * 144 + 144) if row else slice(0, 144)
        columns = slice(column * 144 + 16, column * 144 + 144) if column else slice(0, 144)
        inside = (slice(None), slice(rows.start - row * 144, rows.stop - row * 144),
                  slice(columns.start - column * 144, columns.stop - column * 144))
        assert np.array_equal(whole[:, rows, columns], own[inside]), f"tile {number}"
    # In a strip two tiles share, the brighter of the two shows, as on the page.
    left = np.asarray(zarr.open_array(str(paths[0] / "0"), mode="r")[0, 0])[:, :144, 144:160]
    right = np.asarray(zarr.open_array(str(paths[1] / "0"), mode="r")[0, 0])[:, :144, 0:16]
    assert np.array_equal(whole[:, :144, 144:160], np.maximum(left, right))
    # The second channel is a picture of its own.
    other = np.asarray(zarr.open_array(str(paths[0] / "0"), mode="r")[0, 1])
    assert np.array_equal(picture(overview, channel=1)[:, :144, :144], other[:, :144, :144])


def test_a_large_acquisition_is_made_from_the_tiles_smaller_copies(tmp_path, monkeypatch):
    """The overview is about a screenful across, whatever the size of the acquisition, and
    reads of each tile only the copy that is small enough: nothing finer is needed."""
    from mesoSPIM.src.mesospim_viewer import overview as module

    monkeypatch.setattr(module, "ACROSS", 128)       # as if the acquisition were eight times larger
    paths = tiles_of(tmp_path, 3, 3)
    opened = []
    real = zarr.open_array

    def counted(store=None, **more):
        opened.append(str(store))
        return real(store=store, **more)

    monkeypatch.setattr(zarr, "open_array", counted)
    overview = Overview("run", [(read_store(path), f"data/{n}") for n, path in enumerate(paths)])
    # 448 um across in 128 voxels: 3.5 um to a voxel, taken from the tiles' copies of 2 um.
    assert overview.voxel_um[1] == 3.5 and overview.shape[1] == overview.shape[2] == 128
    whole = picture(overview)
    assert whole.max() > 0
    assert opened and all(path.endswith("/1") for path in opened), "only the copy half the size was read"
    own = np.asarray(real(store=str(paths[0] / "1"), mode="r")[0, 0])
    nearest = np.floor((np.arange(36) + 0.5) * 3.5 / 2.0).astype(int)   # the copy's voxel under each of the overview's
    assert np.array_equal(whole[: own.shape[0], :36, :36], own[:, nearest][:, :, nearest])
    # Its own smaller copies are every second voxel of it.
    assert np.array_equal(picture(overview, level=1), whole[:: overview.factors[1][0], ::2, ::2])


def test_the_overview_answers_as_an_ome_zarr_store_with_the_tiles_axes(tmp_path):
    paths = tiles_of(tmp_path, 2, 1, timepoints=3)
    overview = Overview("run", [(read_store(path), f"data/{n}") for n, path in enumerate(paths)])
    attributes = json.loads(overview.read(".zattrs"))["multiscales"][0]
    assert [axis["name"] for axis in attributes["axes"]] == ["t", "c", "z", "y", "x"]
    array = json.loads(overview.read("0/.zarray"))
    assert array["shape"] == [3, 2, 24, 160, 304] and array["chunks"] == [1, 1, *CHUNK]
    assert overview.read("0/2/1/0/0/0") is not None, "the last time point of the second channel"
    assert overview.read("0/3/0/0/0/0") is None and overview.read("0/0/2/0/0/0") is None
    assert overview.read("0/0/0/9/0/0") is None and overview.read("nothing") is None
    # Each tile is told how much coarser the overview is than itself: here not at all.
    assert overview.rules() == {"data/0": [1.0, 1.0], "data/1": [1.0, 1.0]}


def test_tiles_that_differ_in_their_axes_get_no_overview(tmp_path):
    from .stores import write_store

    tile = write_tile(tmp_path / "a.ome.zarr", origin_um=(0, 0, 0), seed=1)
    plain = write_store(tmp_path / "b.ome.zarr")
    stores = [read_store(tile), read_store(plain)]
    if [axis.name for axis in stores[0].axes] != [axis.name for axis in stores[1].axes]:
        assert not Overview("run", [(stores[0], "data/0"), (stores[1], "data/1")]).usable
    assert not Overview("run", []).usable
    assert signature(stores) == signature(list(reversed(stores)))


# -- when it is offered -------------------------------------------------------------------------


def test_the_viewer_gives_a_layer_its_overview_and_takes_it_away(viewers, tmp_path):
    view = viewers()
    for path in tiles_of(tmp_path, 2, 2):
        view.add(path, layer="run")
    assert view.offer_overview("run") is True
    layer = view.scene["layers"][0]
    assert layer["_sources"][:4] == ["store-0", "store-1", "store-2", "store-3"]
    assert layer["_sources"][4].startswith("overview-")
    assert layer["_overview"]["key"] == "overview/" + layer["_sources"][4][len("overview-"):]
    assert sorted(layer["_overview"]["tiles"]) == ["data/0", "data/1", "data/2", "data/3"]
    address = layer["source"][4]["url"].split("|")[0]
    assert get(address + ".zattrs")[0] == 200 and get(address + "0/0/0/0/0/0")[0] == 200
    # Asked again with nothing changed, nothing is made and nothing is said to the page.
    version = view._server.scene.version
    assert view.offer_overview("run") is True and view._server.scene.version == version
    # Taken away, the page is told, and the address answers no more.
    assert view.offer_overview("run", False) is False
    assert view.scene["layers"][0]["_overview"] is None and len(view.scene["layers"][0]["_sources"]) == 4
    assert get(address + ".zattrs")[0] == 404
    assert view.offer_overview("no such layer") is False


def test_a_finished_acquisition_of_many_tiles_is_given_an_overview(library, tmp_path, monkeypatch):
    monkeypatch.setattr(watch, "OVERVIEW_FROM_TILES", 6)
    few = an_acquisition(tmp_path, "few")
    tiles_of(few, 2, 2)
    time.sleep(0.05)
    many = an_acquisition(tmp_path, "many")
    tiles_of(many, 3, 2)
    library.watch(tmp_path)
    library.show("few")
    for _ in range(3):
        library.clock.tick(watch.AWAKE_S + 1)
        library.poll()
    scene = {layer["_acquisition"]: layer for layer in library.viewer.scene["layers"]}
    assert scene["many"]["_overview"] is not None and len(scene["many"]["_overview"]["tiles"]) == 6
    assert scene["few"]["_overview"] is None, "four tiles are drawn as they are"
    assert offered(library.viewer)["many"]["tiles"] == 6, "the overview is not counted as a tile"


def test_an_acquisition_that_is_being_written_has_no_overview(library, tmp_path, monkeypatch):
    """A picture of tiles that are still changing would be out of date as soon as it was
    made. So it is offered only when every tile is finished, and taken away when the
    microscope begins another stack of the run."""
    monkeypatch.setattr(watch, "OVERVIEW_FROM_TILES", 4)
    acquisition = an_acquisition(tmp_path, "run")
    paths = tiles_of(acquisition, 2, 2)
    library.watch(tmp_path)
    library.poll()
    overview = lambda: library.viewer.scene["layers"][0]["_overview"]  # noqa: E731
    assert overview() is None, "written a moment ago: they may still be growing"
    for _ in range(3):
        library.clock.tick(watch.AWAKE_S + 1)
        library.poll()
    assert overview() is not None
    library.begin_stack(Stack(
        acquisition="run", channel="488", channels=("488", "561"), planes=24, frame=(160, 160),
        voxel_um=(5.0, 1.0, 1.0), origin_um=(0.0, 0.0, 0.0), tile="Tile 1", tiles=4,
        folder=acquisition, store=paths[0],
    ))
    library.clock.tick()
    library.poll()
    assert overview() is None, "the run goes on: the tiles are drawn as they are"
