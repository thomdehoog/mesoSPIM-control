"""Stacks saved as TIFF, raw or BigDataViewer HDF5, shown as if they were OME-Zarr.

The viewer's page reads OME-Zarr and nothing else. mesoSPIM also saves stacks as
TIFF (``Tiff_Writer``, ``Big_Tiff_Writer``), as raw files (``RAW_Writer``) and
as BigDataViewer HDF5 (``H5_BDV_Writer``), and many laboratories use one of
those every day. Such a run could be watched while it was acquired, from the
camera's frames, but not opened again afterwards.

Nothing is converted and nothing is written. A file is given a *description*
as an OME-Zarr store (a few small pieces of text, made here in memory), and
when the page asks for a piece of the picture the bytes are cut out of the file
then and there. The page cannot tell the difference.

The smaller copies of the picture, which a view of a whole acquisition needs,
are not in these files either. They are made by taking every second voxel,
every fourth, and so on. That costs no arithmetic, and for a TIFF or raw file
it reads only the rows that are kept. It is not as smooth as the averaged
copies the OME-Zarr writers make, and reading is slower than from a real
pyramid, most of all for HDF5, where whole blocks have to be read to take a
few voxels out of each.

Where a stack lies and which channel it is comes from what mesoSPIM writes
beside the data: the ``_meta.txt`` file of a TIFF or raw stack, and the
``.xml`` file of a BigDataViewer HDF5 file.
"""

from __future__ import annotations

import json
import math
import re
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from .omezarr import Axis, Channel, Level, NotAStore, NotSupported, Store

TIFF_SUFFIXES = (".tif", ".tiff", ".btf")
FILE_SUFFIXES = TIFF_SUFFIXES + (".raw", ".h5")

# The piece of the picture the page is handed at a time: planes, rows, columns.
CHUNK = (16, 256, 256)
# Smaller copies are offered until the next one would be narrower than this.
SMALLEST = 32
# How many stacks are kept open at a time; the one used longest ago is closed first.
OPEN_AT_ONCE = 128
# What a run of TIFF or raw files is known by: 'Brain.tiff.run' for the files whose names
# share 'Brain'. No file of that name exists; it only stands for the run.
RUN_MARK = ".run"

_SETUP = re.compile(r"s(\d+)$")
# The parts of a file name that differ between the stacks of one run: which tile,
# which channel or laser line, which side the light came from, which angle.
_VARIES = re.compile(
    r"^(tile\d*|ch\w*|\d{3,4}(nm)?|sh\d*|shutter\w*|left|right|rot-?\d*|angle-?\d*|mag[\d.]*x?|\d+)$",
    re.IGNORECASE,
)


def is_stack_file(path: Path) -> bool:
    """Whether ``path`` names a file of a kind read here (by its ending alone)."""
    return path.suffix.lower() in FILE_SUFFIXES


def holds_stacks(path: Path) -> bool:
    """Whether ``path`` is a TIFF, raw or HDF5 file, or one stack inside an HDF5 file."""
    if is_stack_file(path):
        return path.is_file()
    return bool(_SETUP.fullmatch(path.name)) and path.parent.suffix.lower() == ".h5" and path.parent.is_file()


def run_key(file: Path) -> str:
    """What the files of one run have in common in their names.

    ``Brain_Tile3_Ch488_Sh0.tiff`` and ``Brain_Tile4_Ch561_Sh0.tiff`` both give
    ``Brain``: the words that say which tile, channel, side or angle are left out. A
    name made of such words only stands for itself.
    """
    stem = file.name[: -len(file.suffix)] if file.suffix else file.name
    stem = re.sub(r"\.ome$", "", stem, flags=re.IGNORECASE)
    words = [word for word in re.split(r"[_\- ]+", stem) if word]
    kept = [word for word in words if not _VARIES.fullmatch(word)]
    return "_".join(kept) if kept else stem


@dataclass(frozen=True)
class Run:
    """The stacks of one run saved as files: a name for the list, and its stacks."""

    path: Path  # how the viewer knows the run: no file of that name need exist
    name: str
    members: tuple[Path, ...]
    started: float


def runs_in(folder: Path) -> list[Run]:
    """The runs saved as TIFF, raw or HDF5 files directly inside ``folder``.

    The TIFF or raw files of one run are told by their names (:func:`run_key`). An HDF5
    file holds a whole run by itself. Maximum projections (``MAX_...``) are not stacks
    and are left out.
    """
    groups: dict[tuple[str, str], list[Path]] = {}
    found: list[Run] = []
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return found
    for file in entries:
        suffix = file.suffix.lower()
        if suffix not in FILE_SUFFIXES or file.name.startswith("MAX_") or not file.is_file():
            continue
        if suffix == ".h5":
            found.append(Run(path=file, name=run_key(file), members=(), started=_changed(file)))
        else:
            groups.setdefault((run_key(file), suffix), []).append(file)
    for (key, suffix), files in groups.items():
        # Known by a name that stands for all its files, however many there are: a run
        # that is being written starts as one file and must not change its name when
        # the second appears.
        path = folder / f"{key}{suffix}{RUN_MARK}"
        found.append(Run(path=path, name=key, members=tuple(files), started=min(_changed(f) for f in files)))
    return found


def is_run(path: Path) -> bool:
    """Whether ``path`` stands for a run saved as files (by its name alone)."""
    name = path.name.lower()
    return name.endswith(".h5") or (name.endswith(RUN_MARK) and Path(name[: -len(RUN_MARK)]).suffix in FILE_SUFFIXES)


def run_name(path: Path) -> str:
    """The name the list shows for a run saved as files."""
    name = path.name[: -len(RUN_MARK)] if path.name.lower().endswith(RUN_MARK) else path.name
    return run_key(Path(name)) if path.name.lower().endswith(".h5") else Path(name).stem


def run_at(path: Path) -> Run | None:
    """The run a path belongs to: one of its files, or the name :func:`runs_in` gave it."""
    for run in runs_in(path.parent):
        if run.path == path or path in run.members:
            return run
    return None


def members_of(path: Path) -> list[Path]:
    """The stacks of a run, each as the path the viewer reads it at.

    For an HDF5 file these are ``file.h5/s00``, ``file.h5/s01`` and so on, one for each
    stack the file holds. No folder of that name exists; the path is only a name.
    """
    if path.suffix.lower() == ".h5":
        try:
            return [path / f"s{setup:02d}" for setup in sorted(_h5_index(path))]
        except Exception:  # noqa: BLE001 -- being written, or not a BigDataViewer file
            return []
    run = run_at(path)
    return list(run.members) if run is not None else []


def _changed(file: Path) -> float:
    try:
        return file.stat().st_mtime
    except OSError:
        return 0.0


# -- what mesoSPIM writes beside the data ------------------------------------------------


def read_meta(file: Path) -> dict[str, str]:
    """The ``[key] value`` lines of the ``_meta.txt`` file beside a stack ({} without one)."""
    stem = file.name[: -len(file.suffix)] if file.suffix else file.name
    for candidate in (file.with_name(file.name + "_meta.txt"), file.with_name(stem + "_meta.txt")):
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found: dict[str, str] = {}
        for line in text.splitlines():
            match = re.match(r"\s*\[([^\]]+)\]\s*(.*?)\s*$", line)
            if match and match.group(2) != "" and match.group(1) not in found:
                found[match.group(1)] = match.group(2)
        return found
    return {}


def _number(meta: dict, *keys: str) -> float | None:
    for key in keys:
        try:
            return float(meta[key])
        except (KeyError, ValueError):
            continue
    return None


def _channel_label(text: str) -> str:
    """"488 nm" -> "488"; anything else as it is."""
    text = text.strip()
    return text[:-2].strip() if text.lower().endswith("nm") else text


def _tile_name(file: Path, meta: dict) -> str:
    found = re.search(r"tile[_-]?(\d+)", file.name, re.IGNORECASE)
    return f"Tile {int(found.group(1)) + 1}" if found else file.name


# -- one stack ---------------------------------------------------------------------------


@dataclass
class _About:
    """What is known of a stack before any of its pixels are read."""

    file: Path
    shape: tuple[int, int, int]  # planes, rows, columns
    voxel_um: tuple[float, float, float]
    origin_um: tuple[float, float, float]
    channel: str
    signed: bool = False  # BigDataViewer keeps a camera's 16 bits in signed numbers
    setup: int | None = None
    native: tuple[tuple[int, int, int], ...] = ((1, 1, 1),)  # smaller copies the file has itself
    tile: int | None = None  # which tile of the run, where the file says


class _Pages:
    """A TIFF that cannot be read in place (it is compressed): its planes, one at a time."""

    def __init__(self, file: Path, shape) -> None:
        import tifffile

        self._tiff = tifffile.TiffFile(str(file))
        self._pages = self._tiff.series[0].pages
        self.shape = shape
        self._lock = threading.Lock()

    def __getitem__(self, wanted):
        import numpy as np

        planes, rows, columns = wanted
        with self._lock:
            return np.stack([self._pages[z].asarray()[rows, columns] for z in range(*planes.indices(self.shape[0]))])

    def close(self) -> None:
        self._tiff.close()


class VirtualStore:
    """One stack in a file, answering as an OME-Zarr 0.4 store would.

    ``read("…")`` gives the bytes of a file of that store: its description
    (``.zattrs``, ``0/.zarray`` …) or a piece of the picture (``2/0/1/3``: copy 2,
    piece 0 in depth, 1 down, 3 across). ``store`` is what :func:`read_store` says of it.
    """

    def __init__(self, path: Path, about: _About, stamp: tuple[int, int]) -> None:
        self.path = path
        self.about = about
        self.stamp = stamp
        self._lock = threading.Lock()
        self._data = None
        dz, dy, dx = about.voxel_um
        # Rows and columns are halved from one copy to the next. The depth is halved
        # only once a voxel has become as wide as the planes are apart, as the OME-Zarr
        # writers do it, so that the copies stay about as fine in depth as across.
        whole = max(0, math.floor(math.log2(dz / max(dy, dx)))) if min(dz, dy, dx) > 0 else 0
        self.factors: list[tuple[int, int, int]] = []
        level = 0
        while True:
            across = 2**level
            self.factors.append((2 ** max(0, level - whole), across, across))
            if min(about.shape[1], about.shape[2]) // (across * 2) < SMALLEST:
                break
            level += 1
        self.shapes = [tuple(-(-size // factor) for size, factor in zip(about.shape, factors)) for factors in self.factors]
        self._described = self._describe()
        self.store = Store(
            path=path,
            format="zarr2",
            axes=tuple(Axis(name=name, type="space", unit="micrometer") for name in "zyx"),
            scale=tuple(float(v) for v in about.voxel_um),
            translation=tuple(float(v) for v in about.origin_um),
            shape=tuple(about.shape),
            channels=(Channel(label=about.channel),),
            levels=tuple(str(level) for level in range(len(self.factors))),
            top=65535.0,
            stamp=stamp,
        )

    @property
    def levels(self) -> tuple[Level, ...]:
        return tuple(
            Level(path=str(level), shape=shape, files=CHUNK, prefix="", separator="/")
            for level, shape in enumerate(self.shapes)
        )

    def _describe(self) -> dict[str, bytes]:
        about = self.about
        datasets = []
        described = {".zgroup": json.dumps({"zarr_format": 2}).encode()}
        for level, (factors, shape) in enumerate(zip(self.factors, self.shapes)):
            datasets.append(
                {
                    "path": str(level),
                    "coordinateTransformations": [
                        {"type": "scale", "scale": [v * f for v, f in zip(about.voxel_um, factors)]},
                        {"type": "translation", "translation": list(about.origin_um)},
                    ],
                }
            )
            described[f"{level}/.zarray"] = json.dumps(
                {
                    "zarr_format": 2,
                    "shape": list(shape),
                    "chunks": list(CHUNK),
                    "dtype": "<u2",
                    "compressor": None,
                    "filters": None,
                    "fill_value": 0,
                    "order": "C",
                    "dimension_separator": "/",
                }
            ).encode()
        attributes = {
            "multiscales": [
                {
                    "version": "0.4",
                    "name": self.path.name,
                    "axes": [{"name": name, "type": "space", "unit": "micrometer"} for name in "zyx"],
                    "datasets": datasets,
                }
            ],
        }
        # No omero block: which channel this is, the viewer knows from ``store`` below,
        # and the engine refuses a channel entry that has no colour.
        described[".zattrs"] = json.dumps(attributes).encode()
        return described

    # -- the pixels ----------------------------------------------------------------------

    def _open(self):
        """The stack as something that is cut like an array: ``data[planes, rows, columns]``."""
        about = self.about
        suffix = about.file.suffix.lower()
        if suffix == ".h5":
            return _H5(about.file, about.setup or 0, about.native)
        import numpy as np

        if suffix == ".raw":
            return np.memmap(str(about.file), mode="r", dtype="<u2", shape=about.shape)
        import tifffile

        try:
            mapped = tifffile.memmap(str(about.file), mode="r")
            return mapped.reshape(about.shape)
        except Exception:  # noqa: BLE001 -- compressed, or laid out in pieces: read plane by plane
            return _Pages(about.file, about.shape)

    def _cut(self, level: int, planes: slice, rows: slice, columns: slice):
        import numpy as np

        with self._lock:
            if self._data is None:
                self._data = self._open()
            data = self._data
        zf, yf, xf = self.factors[level]
        wanted = (
            slice(planes.start * zf, min(planes.stop * zf, self.about.shape[0]), zf),
            slice(rows.start * yf, min(rows.stop * yf, self.about.shape[1]), yf),
            slice(columns.start * xf, min(columns.stop * xf, self.about.shape[2]), xf),
        )
        if isinstance(data, _H5):
            piece = data.cut(wanted)
        else:
            piece = np.asarray(data[wanted])
        if piece.dtype == np.int16 and self.about.signed:
            piece = piece.view(np.uint16)
        elif piece.dtype != np.uint16:
            piece = np.clip(piece, 0, 65535).astype(np.uint16)
        return piece

    def read(self, relative: str) -> bytes | None:
        """The bytes of one file of the store, or None where it has none."""
        described = self._described.get(relative)
        if described is not None:
            return described
        parts = relative.split("/")
        if len(parts) != 4 or not all(part.isdigit() for part in parts):
            return None
        level, cz, cy, cx = (int(part) for part in parts)
        if level >= len(self.shapes):
            return None
        shape = self.shapes[level]
        starts = (cz * CHUNK[0], cy * CHUNK[1], cx * CHUNK[2])
        if any(start >= size for start, size in zip(starts, shape)):
            return None
        import numpy as np

        slices = [slice(start, min(start + chunk, size)) for start, chunk, size in zip(starts, CHUNK, shape)]
        piece = self._cut(level, *slices)
        if piece.shape != CHUNK:
            # A piece at the edge is handed over at full size, the rest of it empty.
            whole = np.zeros(CHUNK, dtype="<u2")
            whole[: piece.shape[0], : piece.shape[1], : piece.shape[2]] = piece
            piece = whole
        return np.ascontiguousarray(piece, dtype="<u2").tobytes()

    @property
    def costly(self) -> bool:
        """Whether a small picture of the whole stack is expensive to make. It is for an
        HDF5 file without smaller copies of its own: every block of a plane has to be
        read to take a few voxels out of each."""
        return self.about.setup is not None and len(self.about.native) < 2

    def sample(self, planes: int):
        """A small picture of the whole stack, for setting the contrast: the smallest
        copy, through up to ``planes`` planes spread over the depth."""
        import numpy as np

        level = len(self.shapes) - 1
        depth, rows, columns = self.shapes[level]
        picked = np.unique(np.linspace(0, depth - 1, min(planes, depth)).round().astype(int))
        return np.stack([self._cut(level, slice(z, z + 1), slice(0, rows), slice(0, columns))[0] for z in picked])

    def close(self) -> None:
        with self._lock:
            data, self._data = self._data, None
        closer = getattr(data, "close", None)
        if closer is not None:
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
        else:
            del data


_h5_open: "OrderedDict[Path, object]" = OrderedDict()
_h5_lock = threading.Lock()


def _h5_file(file: Path):
    """The HDF5 file, opened for reading and kept open: one opening serves all its stacks."""
    import h5py

    with _h5_lock:
        handle = _h5_open.get(file)
        if handle is not None and handle.id.valid:
            _h5_open.move_to_end(file)
            return handle
        handle = _h5_open[file] = h5py.File(str(file), "r")
        while len(_h5_open) > 8:
            _, old = _h5_open.popitem(last=False)
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass
        return handle


def _h5_let_go(file: Path) -> None:
    with _h5_lock:
        handle = _h5_open.pop(file, None)
    if handle is not None:
        try:
            handle.close()
        except Exception:  # noqa: BLE001
            pass


class _H5:
    """One stack of a BigDataViewer HDF5 file: ``t00000/sNN/<copy>/cells``."""

    def __init__(self, file: Path, setup: int, native) -> None:
        self._file = file
        self._setup = setup
        self._native = native

    def cut(self, wanted: tuple[slice, slice, slice]):
        """Cut from the smaller copy the file has itself that fits best, so that as little
        as possible is read to take every n-th voxel."""
        import numpy as np

        steps = tuple(part.step or 1 for part in wanted)
        best, factors = 0, (1, 1, 1)
        for number, native in enumerate(self._native):
            if all(step % own == 0 for step, own in zip(steps, native)) and native[1] >= factors[1]:
                best, factors = number, native
        with _h5_read:  # HDF5 reads one thing at a time anyway
            dataset = _h5_file(self._file)[f"t00000/s{self._setup:02d}/{best}/cells"]
            cut = tuple(
                slice(part.start // own, min(-(-part.stop // own), size), (part.step or 1) // own)
                for part, own, size in zip(wanted, factors, dataset.shape)
            )
            return np.asarray(dataset[cut])

    def close(self) -> None:
        pass  # the file stays open for its other stacks (see _h5_file)


_h5_read = threading.Lock()


# -- reading what a file holds -----------------------------------------------------------


_h5_known: dict[Path, tuple[tuple[int, int], dict[int, _About]]] = {}


def _h5_index(file: Path) -> dict[int, _About]:
    """Every stack of a BigDataViewer HDF5 file, by its number, read once per version of
    the file: the sizes from the file itself, places and channels from the ``.xml``
    beside it. A file of two hundred stacks is opened once for this, not two hundred times."""
    about_file = file.stat()
    stamp = (about_file.st_mtime_ns, about_file.st_size)
    known = _h5_known.get(file)
    if known is not None and known[0] == stamp:
        return known[1]
    import h5py

    _h5_let_go(file)  # an opening from before the file changed
    found: dict[int, _About] = {}
    with h5py.File(str(file), "r") as handle:
        first = handle.get("t00000")
        for name in (first if first is not None else ()):
            if not _SETUP.fullmatch(name):
                continue
            setup = int(name[1:])
            cells = handle.get(f"t00000/{name}/0/cells")
            if cells is None:
                continue
            steps = handle.get(f"{name}/resolutions")
            # BigDataViewer lists its smaller copies as steps in x, y, z.
            native = tuple((int(z), int(y), int(x)) for x, y, z in steps[...]) if steps is not None else ((1, 1, 1),)
            found[setup] = _About(
                file=file, shape=tuple(int(n) for n in cells.shape), voxel_um=(1.0, 1.0, 1.0),
                origin_um=(0.0, 0.0, 0.0), channel=f"channel {setup}", signed=cells.dtype.kind == "i",
                setup=setup, native=native,
            )
    try:
        root = ET.parse(file.with_suffix(".xml")).getroot()
    except (OSError, ET.ParseError):
        root = None
    if root is not None:
        names: dict[str, dict[str, str]] = {}
        for attributes in root.iter("Attributes"):
            names[attributes.get("name", "")] = {
                (entry.findtext("id") or "").strip(): (entry.findtext("name") or "").strip() for entry in attributes
            }
        for view in root.iter("ViewSetup"):
            number = (view.findtext("id") or "").strip()
            about = found.get(int(number)) if number.isdigit() else None
            if about is None:
                continue
            size = view.findtext("voxelSize/size")
            if size:
                x, y, z = (float(part) for part in size.split())
                about.voxel_um = (z, y, x)
            channel = (view.findtext("attributes/channel") or "").strip()
            label = names.get("channel", {}).get(channel, "")
            about.channel = _channel_label(label) if label else f"channel {channel or number}"
            placed = (view.findtext("attributes/tile") or "").strip()
            about.tile = int(placed) if placed.isdigit() else None
        for registration in root.iter("ViewRegistration"):
            number = registration.get("setup") or ""
            about = found.get(int(number)) if number.isdigit() else None
            if about is None or registration.get("timepoint") not in ("0", None):
                continue
            # mesoSPIM writes the place of the tile as a shift, counted in voxels of each axis.
            for transform in registration.iter("ViewTransform"):
                numbers = [float(part) for part in (transform.findtext("affine") or "").split()]
                if len(numbers) == 12 and (transform.findtext("Name") or "").lower().startswith("translation"):
                    about.origin_um = (
                        numbers[11] * about.voxel_um[0], numbers[7] * about.voxel_um[1], numbers[3] * about.voxel_um[2]
                    )
    _h5_known[file] = (stamp, found)
    return found


def _about_h5(file: Path, setup: int) -> _About:
    about = _h5_index(file).get(setup)
    if about is None:
        raise NotAStore(f"{file} holds no stack number {setup}")
    return about


def _about_tiff(file: Path) -> _About:
    try:
        import tifffile
    except ImportError as error:
        raise NotSupported(
            f"{file} is a TIFF file, and the tifffile package that reads it is not installed",
            reason="the tifffile package is not installed",
        ) from error
    try:
        with tifffile.TiffFile(str(file)) as tiff:
            series = tiff.series[0]
            shape, axes, kind = tuple(series.shape), series.axes, series.dtype
    except Exception as error:  # noqa: BLE001 -- being written, or not a TIFF after all
        raise NotAStore(f"{file} could not be read as a TIFF file ({error})") from error
    spatial = [size for size, axis in zip(shape, axes) if axis in "YX"]
    others = [size for size, axis in zip(shape, axes) if axis not in "YX"]
    if len(spatial) != 2 or any(axis in "CS" and size > 1 for size, axis in zip(shape, axes)):
        raise NotSupported(
            f"{file} holds several channels in one TIFF, which the viewer does not read",
            reason="it holds several channels in one file; save one file per channel",
        )
    if kind.kind not in "ui" or kind.itemsize > 2:
        raise NotSupported(f"{file} holds {kind} numbers", reason=f"its numbers are {kind}, not 8 or 16 bit whole numbers")
    planes = 1
    for size in others:
        planes *= size
    meta = read_meta(file)
    across = _number(meta, "Pixelsize in um") or 1.0
    apart = abs(_number(meta, "z_stepsize") or across)
    origin = (_number(meta, "z_start", "z_pos") or 0.0, _number(meta, "y_pos") or 0.0, _number(meta, "x_pos") or 0.0)
    channel = _channel_label(meta["Laser"]) if meta.get("Laser") else _channel_from_name(file)
    return _About(file=file, shape=(planes, spatial[0], spatial[1]), voxel_um=(apart, across, across),
                  origin_um=origin, channel=channel)


def _about_raw(file: Path) -> _About:
    meta = read_meta(file)
    planes = _number(meta, "z_planes")
    width, height = _number(meta, "x_pixels"), _number(meta, "y_pixels")
    if not planes or not width or not height:
        raise NotSupported(
            f"{file} is a raw file without its _meta.txt, so its size is not known",
            reason="its _meta.txt file, which says how large a plane is, is missing",
        )
    try:
        voxels = file.stat().st_size // 2
    except OSError as error:
        raise NotAStore(f"{file} could not be read") from error
    # The camera's pixels may have been binned (2x2, 4x4): the file's size says by how much.
    plane = voxels / planes
    binned = round(math.sqrt(width * height / plane)) if plane > 0 else 0
    if binned < 1 or (width // binned) * (height // binned) * int(planes) != voxels:
        raise NotSupported(f"{file} does not have the size its _meta.txt gives", reason="its size does not fit its _meta.txt")
    across = (_number(meta, "Pixelsize in um") or 1.0) * 1.0
    apart = abs(_number(meta, "z_stepsize") or across)
    origin = (_number(meta, "z_start") or 0.0, _number(meta, "y_pos") or 0.0, _number(meta, "x_pos") or 0.0)
    channel = _channel_label(meta["Laser"]) if meta.get("Laser") else _channel_from_name(file)
    # A frame is saved turned a quarter: its rows are the camera's x pixels.
    return _About(file=file, shape=(int(planes), int(width // binned), int(height // binned)),
                  voxel_um=(apart, across, across), origin_um=origin, channel=channel)


def _channel_from_name(file: Path) -> str:
    found = re.search(r"(?:ch[_-]?(\d+))|(\d{3})\s*nm", file.name, re.IGNORECASE)
    return (found.group(1) or found.group(2)) if found else "channel 0"


# -- the stores that are open ------------------------------------------------------------

_held: "OrderedDict[Path, VirtualStore]" = OrderedDict()
_held_lock = threading.Lock()


def virtual(path: Path) -> VirtualStore | None:
    """The stack at ``path`` as a store, or None if ``path`` is not a stack in a file.

    Kept between calls for as long as the file is unchanged. Raises
    :class:`NotAStore` or :class:`NotSupported` for a file of the right kind that
    cannot be shown, with the reason.
    """
    setup = None
    file = path
    if not is_stack_file(path):
        found = _SETUP.fullmatch(path.name)
        if found is None or path.parent.suffix.lower() != ".h5":
            return None
        file, setup = path.parent, int(found.group(1))
    try:
        about_file = file.stat()
    except OSError:
        return None
    stamp = (about_file.st_mtime_ns, about_file.st_size)
    with _held_lock:
        held = _held.get(path)
        if held is not None and held.stamp == stamp:
            _held.move_to_end(path)
            return held
    suffix = file.suffix.lower()
    try:
        if suffix == ".h5":
            try:
                import h5py  # noqa: F401
            except ImportError as error:
                raise NotSupported(
                    f"{file} is an HDF5 file, and the h5py package that reads it is not installed",
                    reason="the h5py package is not installed",
                ) from error
            if setup is None:
                raise NotAStore(f"{file} holds several stacks; each is read at {file.name}/s00, s01 and so on")
            about = _about_h5(file, setup)
        elif suffix == ".raw":
            about = _about_raw(file)
        else:
            about = _about_tiff(file)
    except (NotAStore, NotSupported):
        raise
    except Exception as error:  # noqa: BLE001 -- a file being written, or damaged
        raise NotAStore(f"{file} could not be read ({error})") from error
    if min(about.shape) < 1:
        raise NotAStore(f"{file} holds no planes yet")
    made = VirtualStore(path, about, stamp)
    with _held_lock:
        replaced = _held.pop(path, None)
        _held[path] = made
        closing = [replaced] if replaced is not None else []
        while len(_held) > OPEN_AT_ONCE:
            closing.append(_held.popitem(last=False)[1])
    for old in closing:
        old.close()
    return made


def position_name(path: Path) -> str | None:
    """How the panel names a stack in a file: "Tile 3" where the file or its name says
    which tile it is. None for a path that is not a stack in a file."""
    if not is_stack_file(path) and not (_SETUP.fullmatch(path.name) and path.parent.suffix.lower() == ".h5"):
        return None
    with _held_lock:
        held = _held.get(path)
    if held is not None and held.about.tile is not None:
        return f"Tile {held.about.tile + 1}"
    if held is not None and held.about.setup is not None:
        return f"Stack {held.about.setup + 1}"
    return _tile_name(path, {})
