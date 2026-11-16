"""One small picture of a whole acquisition, for the view from far away.

The page draws every tile of an acquisition as a source of its own, and with a
hundred tiles on screen at once that is a hundred sources to set up, load and
draw for a picture in which each tile covers a few dozen pixels. From that far
away one small picture of the whole acquisition shows the same thing.

:class:`Overview` is that picture. It is put together from the tiles' own
smaller copies, each placed where the tile lies, and where two tiles overlap
the brighter voxel is kept, as the page does when it draws the tiles
themselves. Nothing is written to disk: the picture is made in memory when the
page first asks for a piece of it, one time point and channel at a time, and
handed over as an OME-Zarr store (the way ``filestack.py`` hands over a TIFF).

The page shows the overview while the view is zoomed out so far that it is as
sharp as the screen, and the tiles themselves from there inwards
(``source/src/overview.js`` and the build's last edits to the engine). For that
the page is told, for every tile, how much coarser the overview is than the
tile: :meth:`Overview.rules`.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .omezarr import Store, read_levels

logger = logging.getLogger(__name__)

# The overview is at most about this many voxels across: a screenful.
ACROSS = 1024
# The piece of it the page is handed at a time: planes, rows, columns.
CHUNK = (16, 256, 256)
# Smaller copies of the overview are offered until one would be narrower than this.
SMALLEST = 64
# How much of the overview is kept in memory: the pictures used longest ago go first.
KEEP_BYTES = 384 * 1024 * 1024


@dataclass(frozen=True)
class _Tile:
    """One tile, and the copy of it the overview is made from."""

    store: Store
    address: str  # the page's address of the store, without the server: "data/3"
    level: str  # the folder of the copy that is read
    voxel_um: tuple[float, float, float]  # of that copy: z, y, x
    origin_um: tuple[float, float, float]
    shape: tuple[int, int, int]  # of that copy
    finest_um: tuple[float, float, float]  # the tile's own finest voxel


def _micrometres(store: Store) -> tuple[list[float], list[float], list[int]]:
    """A store's voxel size, origin and size along z, y and x, in micrometres."""
    voxel, origin, size = [], [], []
    for name in "zyx":
        index = store.axis_index(name)
        base, factor = store.axes[index].si
        to_um = factor * 1e6 if base == "m" else 1.0
        voxel.append(store.scale[index] * to_um)
        origin.append(store.translation[index] * to_um)
        size.append(store.shape[index])
    return voxel, origin, size


class Overview:
    """A small picture of all the tiles of one acquisition, answering as an OME-Zarr store.

    ``tiles`` are the stores and the addresses the page reads them at. All of them must
    have the same axes. ``usable`` says whether an overview can be made of them at all.
    """

    def __init__(self, name: str, tiles: list[tuple[Store, str]]) -> None:
        self.name = name
        self.usable = False
        self._lock = threading.Lock()
        self._pictures: "OrderedDict[tuple[int, int, int], object]" = OrderedDict()
        self._kept = 0
        self._making: dict[tuple[int, int], threading.Lock] = {}
        if not tiles:
            return
        first = tiles[0][0]
        self.axes = [axis.name for axis in first.axes]
        if any([axis.name for axis in store.axes] != self.axes for store, _ in tiles):
            return
        if not all(name in self.axes for name in "zyx"):
            return
        from . import filestack

        for store, _ in tiles:
            in_a_file = filestack.virtual(store.path)
            if in_a_file is not None and in_a_file.costly:
                return  # an HDF5 file without smaller copies: reading all of it is no shortcut
            if in_a_file is None:
                try:
                    import zarr  # noqa: F401
                except ImportError:
                    return
        self.times = max((store.shape[store.axis_index("t")] if "t" in self.axes else 1) for store, _ in tiles)
        self.channels = first.channel_count if "c" in self.axes else 1

        about = [(store, address, *_micrometres(store)) for store, address in tiles]
        low = [min(origin[a] for _, _, _, origin, _ in about) for a in range(3)]
        high = [max(origin[a] + size[a] * voxel[a] for _, _, voxel, origin, size in about) for a in range(3)]
        wanted = max(high[1] - low[1], high[2] - low[2]) / ACROSS  # micrometres to a voxel, across
        self._tiles: list[_Tile] = []
        coarsest_used = 0.0
        for store, address, voxel, origin, size in about:
            levels = read_levels(store)
            if not levels:
                return
            # Of the tile's own copies, the smallest that is still at least as fine as the
            # overview is to be: nothing finer needs to be read.
            chosen, chosen_voxel, chosen_shape = None, None, None
            for level in levels:
                shape = [level.shape[store.axis_index(name)] for name in "zyx"]
                factors = [_factor(whole, part) for whole, part in zip(size, shape)]
                level_voxel = [v * f for v, f in zip(voxel, factors)]
                if chosen is None or max(level_voxel[1], level_voxel[2]) <= wanted * 1.01:
                    chosen, chosen_voxel, chosen_shape = level, level_voxel, shape
            self._tiles.append(_Tile(
                store=store, address=address, level=chosen.path, voxel_um=tuple(chosen_voxel),
                origin_um=tuple(origin), shape=tuple(chosen_shape), finest_um=tuple(voxel),
            ))
            coarsest_used = max(coarsest_used, chosen_voxel[1], chosen_voxel[2])
        # As fine as the copies it is made from, and no finer than a screenful needs.
        across = max(wanted, coarsest_used)
        depth = max(max(tile.voxel_um[0] for tile in self._tiles), 1e-9)
        self.voxel_um = (depth, across, across)
        self.origin_um = tuple(low)
        self.shape = tuple(max(1, math.ceil((high[a] - low[a]) / self.voxel_um[a] - 1e-6)) for a in range(3))
        # Its own smaller copies: every second voxel across, and in depth once a voxel is
        # as wide as the planes are apart.
        whole = max(0, math.floor(math.log2(depth / across))) if depth > across else 0
        self.factors: list[tuple[int, int, int]] = []
        level = 0
        while True:
            step = 2**level
            self.factors.append((2 ** max(0, level - whole), step, step))
            if min(self.shape[1], self.shape[2]) // (step * 2) < SMALLEST:
                break
            level += 1
        self.shapes = [tuple(-(-n // f) for n, f in zip(self.shape, factors)) for factors in self.factors]
        self._described = self._describe()
        self.usable = True

    # -- what the page is told -----------------------------------------------------------

    def rules(self) -> dict[str, list[float]]:
        """For every tile, by the address the page reads it at: how many times coarser the
        overview is than the tile's finest copy, across (its smallest voxel edge) and by
        the volume of a voxel. The page lets the overview stand in for a tile once the
        screen asks for nothing finer than that."""
        found = {}
        for tile in self._tiles:
            across = min(self.voxel_um) / max(min(tile.finest_um), 1e-12)
            volume = math.prod(self.voxel_um) / max(math.prod(tile.finest_um), 1e-30)
            found[tile.address] = [round(across, 6), round(volume, 6)]
        return found

    def _describe(self) -> dict[str, bytes]:
        lead = [name for name in self.axes if name in "tc"]
        kinds = {"t": {"name": "t", "type": "time"}, "c": {"name": "c", "type": "channel"}}
        axes = [kinds[name] for name in lead] + [
            {"name": name, "type": "space", "unit": "micrometer"} for name in "zyx"
        ]
        counts = {"t": self.times, "c": self.channels}
        described = {".zgroup": json.dumps({"zarr_format": 2}).encode()}
        datasets = []
        for level, (factors, shape) in enumerate(zip(self.factors, self.shapes)):
            datasets.append({
                "path": str(level),
                "coordinateTransformations": [
                    {"type": "scale", "scale": [1.0] * len(lead) + [v * f for v, f in zip(self.voxel_um, factors)]},
                    {"type": "translation", "translation": [0.0] * len(lead) + list(self.origin_um)},
                ],
            })
            described[f"{level}/.zarray"] = json.dumps({
                "zarr_format": 2,
                "shape": [counts[name] for name in lead] + list(shape),
                "chunks": [1] * len(lead) + list(CHUNK),
                "dtype": "<u2",
                "compressor": None,
                "filters": None,
                "fill_value": 0,
                "order": "C",
                "dimension_separator": "/",
            }).encode()
        described[".zattrs"] = json.dumps({
            "multiscales": [{"version": "0.4", "name": "overview", "axes": axes, "datasets": datasets}]
        }).encode()
        self._lead = lead
        return described

    # -- the picture ---------------------------------------------------------------------

    def _read(self, tile: _Tile, time: int, channel: int):
        """The copy of one tile that the overview is made from, at one time point and
        channel, as an array of planes, rows and columns; None if the tile has none there."""
        import numpy as np

        store = tile.store
        from . import filestack

        in_a_file = filestack.virtual(store.path)
        if in_a_file is not None:
            level = int(tile.level)
            depth, rows, columns = in_a_file.shapes[level]
            return in_a_file._cut(level, slice(0, depth), slice(0, rows), slice(0, columns))  # noqa: SLF001
        import zarr

        array = zarr.open_array(store=str(store.path / tile.level), mode="r")
        picked = []
        for axis, size in zip(store.axes, array.shape):
            if axis.name == "t":
                if time >= size:
                    return None
                picked.append(time)
            elif axis.is_channel:
                if channel >= size:
                    return None
                picked.append(channel)
            else:
                picked.append(slice(None))
        return np.asarray(array[tuple(picked)])

    def _compose(self, time: int, channel: int):
        """The whole overview at its finest, for one time point and channel."""
        import numpy as np

        picture = np.zeros(self.shape, dtype=np.uint16)
        for tile in self._tiles:
            try:
                data = self._read(tile, time, channel)
            except Exception as error:  # noqa: BLE001 -- a tile that cannot be read is left out
                logger.debug("the overview of %s leaves out %s: %s", self.name, tile.store.path, error)
                continue
            if data is None:
                continue
            if data.dtype != np.uint16:
                data = np.clip(data, 0, 65535).astype(np.uint16)
            # For each voxel of the overview that lies in this tile, the nearest voxel of
            # the tile's copy. Along each axis those are a run of the overview's voxels.
            runs, picks = [], []
            for axis in range(3):
                centres = self.origin_um[axis] + (np.arange(self.shape[axis]) + 0.5) * self.voxel_um[axis]
                index = np.floor((centres - tile.origin_um[axis]) / tile.voxel_um[axis]).astype(np.int64)
                inside = np.nonzero((index >= 0) & (index < data.shape[axis]))[0]
                if inside.size == 0:
                    break
                runs.append(slice(int(inside[0]), int(inside[-1]) + 1))
                picks.append(index[inside[0] : inside[-1] + 1])
            else:
                placed = data[np.ix_(*picks)]
                where = picture[tuple(runs)]
                np.maximum(where, placed, out=where)  # where tiles overlap, the brighter shows
        return picture

    def _picture(self, level: int, time: int, channel: int):
        key = (level, time, channel)
        with self._lock:
            held = self._pictures.get(key)
            if held is not None:
                self._pictures.move_to_end(key)
                return held
            making = self._making.setdefault((time, channel), threading.Lock())
        with making:  # one at a time for a time point and channel; others wait for it
            with self._lock:
                held = self._pictures.get(key)
                finest = self._pictures.get((0, time, channel))
            if held is not None:
                return held
            if finest is None:
                finest = self._compose(time, channel)
                self._keep((0, time, channel), finest)
            if level == 0:
                return finest
            zf, yf, xf = self.factors[level]
            import numpy as np

            held = np.ascontiguousarray(finest[::zf, ::yf, ::xf])
            self._keep(key, held)
            return held

    def _keep(self, key, picture) -> None:
        with self._lock:
            self._pictures[key] = picture
            self._kept += picture.nbytes
            while self._kept > KEEP_BYTES and len(self._pictures) > 1:
                _, dropped = self._pictures.popitem(last=False)
                self._kept -= dropped.nbytes

    def read(self, relative: str) -> bytes | None:
        """The bytes of one file of the overview's store, or None where it has none."""
        if not self.usable:
            return None
        described = self._described.get(relative)
        if described is not None:
            return described
        parts = relative.split("/")
        if len(parts) != 4 + len(self._lead) or not all(part.isdigit() for part in parts):
            return None
        numbers = [int(part) for part in parts]
        level, at = numbers[0], numbers[1:]
        if level >= len(self.shapes):
            return None
        where = dict(zip(self._lead, at))
        time, channel = where.get("t", 0), where.get("c", 0)
        if time >= self.times or channel >= self.channels:
            return None
        cz, cy, cx = at[len(self._lead):]
        shape = self.shapes[level]
        starts = (cz * CHUNK[0], cy * CHUNK[1], cx * CHUNK[2])
        if any(start >= size for start, size in zip(starts, shape)):
            return None
        import numpy as np

        picture = self._picture(level, time, channel)
        piece = picture[starts[0] : starts[0] + CHUNK[0], starts[1] : starts[1] + CHUNK[1], starts[2] : starts[2] + CHUNK[2]]
        if piece.shape != CHUNK:
            whole = np.zeros(CHUNK, dtype="<u2")
            whole[: piece.shape[0], : piece.shape[1], : piece.shape[2]] = piece
            piece = whole
        return np.ascontiguousarray(piece, dtype="<u2").tobytes()


def _factor(whole: int, part: int) -> int:
    """By how much a copy is smaller than the full picture along an axis. The writers halve,
    so a power of two is preferred where the sizes allow it."""
    if part <= 0 or whole <= part:
        return 1
    ratio = whole / part
    two = 2 ** round(math.log2(ratio))
    return int(two) if -(-whole // two) == part else max(1, round(ratio))


def signature(stores: list[Store]) -> tuple:
    """What an overview depends on: which tiles, and the state each is in. A different
    answer means the overview has to be made anew."""
    return tuple(sorted((str(store.path), store.stamp, store.shape) for store in stores))

