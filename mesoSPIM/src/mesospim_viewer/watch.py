"""What the Data viewer window shows, without the window.

A mesoSPIM run is one folder holding one ``<Sample>.ome.zarr`` group per
acquisition, and inside it one ``(t, c, z, y, x)`` store per tile (see the
``MP_OME_Zarr_TCZYX_Writer``). Tiles appear as they are acquired, and a time
lapse appends time points to the tiles already there.

:class:`Library` is the one model behind the window. It holds a list of
acquisitions -- those of the folder the microscope writes into, and any the
operator opened from disk -- and for each whether it is shown. It keeps a
:class:`~.viewer.Viewer` in line with that list and with the disk:

- the folder the microscope writes into is *watched*: a new acquisition
  appearing in it is shown at once, in place of the one shown before;
- every acquisition that is shown is looked at once a second. A new tile
  becomes a new source; files that landed in a tile already shown are named to
  the page, which reads exactly those (``landing.py``); a tile whose shape
  changed -- a time point appended -- is read again on its own;
- the stack being acquired right now is shown from memory, plane by plane, when
  the microscope feeds its frames in (:meth:`Library.begin_stack`,
  :meth:`Library.add_plane`; see ``live.py``), and the tile on disk takes its
  place once that stack is complete.

Everything works on plain paths and a viewer, so it is tested without Qt; the
window in ``window.py`` drives :meth:`Library.poll` from a timer.
"""

from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import filestack
from .landing import Landing, Progress, newest_change
from .live import Held, PreviewStack, Stack
from .omezarr import Channel, NotAStore, NotSupported, Store, read_levels, read_store
from .viewer import Viewer

logger = logging.getLogger(__name__)

STORE_SUFFIX = ".ome.zarr"

# How closely a tile is looked at depends on how lately it changed.
# One that gained a file this recently is *awake*: looked at every second...
AWAKE_S = 120.0
# ...and all of its folders are listed this often, whatever their modification
# times say (some file systems do not keep those faithfully).
AWAKE_THOROUGH_S = 30.0
# One that may still grow -- its acquisition is the newest, or its newest file
# is not an hour old -- is *dozing*: its top folders are probed this often, and
# all of it listed now and then.
DOZING_S = 15.0
DOZING_THOROUGH_S = 120.0
RECENT_S = 3600.0
# Any other is *asleep*: probed seldom, never listed whole.
ASLEEP_S = 300.0
# A tile nothing has been written to for this long is *settled* when it is first met:
# it is shown as it is, and none of its folders are listed. Listing every folder of
# every tile of an old acquisition, only to learn that nothing is new, kept a network
# drive busy for minutes. A settled tile is still checked for being written again: the
# microscope's writer rewrites the store's description with every stack it begins.
SETTLED_S = 600.0
# "Long" is read from the files' own times, and those are kept by the computer that wrote
# them. If its clock runs behind this one's, a tile being written this minute can look
# old. So a tile taken in as settled is asked once more, this long afterwards by this
# computer's own clock, whether its newest file is still the same one. If it is not, the
# tile is being written after all, and is listed and watched.
CONFIRM_SETTLED_S = 15.0
# A tile whose stack is being acquired right now, and shown from the camera's frames,
# is looked at on disk only this often. Its files are kept from the page until the
# stack is whole, so looking every second would load the acquisition's drive with
# hundreds of requests a second that change nothing on screen. Once the microscope
# has finished the stack the tile is looked at every second again, to hand it over.
FED_EVERY_S = 10.0
# One look at the disk takes about this long at most before the tiles that are in no
# hurry are left for the next look. A large acquisition is then gone through over
# several looks, and the looks stay short.
LOOK_BUDGET_S = 0.5
# A probe looks this many folders deep: far enough to see a new time point or
# channel begin in a tile's finest copy.
PROBE_DEPTH = 4
# An acquisition nobody feeds counts as being written while a file landed this recently.
LIVE_S = 20.0
# A finished stack is given to the page from disk once all of it is there and
# nothing has landed for this long...
SETTLE_S = 2.0
# ...and the page then takes its preview off once the tile's own picture is up.
# This long after, the preview's memory is let go whatever the page did.
HAND_OVER_S = 15.0
# A stack that never completes on disk (the run was stopped) is handed over anyway.
GIVE_UP_S = 90.0
# The preview takes in at most this many planes a second.
PLANES_PER_S = 4.0
# Previews of finished stacks stay in memory up to this much; the oldest go first.
PREVIEW_BYTES = 1 << 30

# The time index mesoSPIM writes into file names while running a time lapse:
# 'Sample.ome.zarr' becomes 'Sample.ome_Time003.zarr'.
_TIME_MARK = re.compile(r"(?:\.ome)?(_Time\d+)$")


def channel_of(store: Store) -> Channel | None:
    """The channel a one-channel store holds, from its own metadata, or None.

    Some writers save one store per tile *and* channel, each without a channel
    axis. Such a store says which channel it holds in its ``omero`` block, with
    exactly one entry; that label, colour and window are used, and stores with
    the same label are shown as one channel. A store with a channel axis says
    what its channels are itself and is left alone, and so is a store without
    an omero block: nothing in it says which channel it is.
    """
    if store.channel_axis is None and len(store.channels) == 1:
        return store.channels[0]
    return None


def free_name(taken, name: str) -> str:
    """``name``, or, when it is taken, a numbered one.

    Two acquisitions of the same name -- the same sample on two days, say -- are
    then two entries in the panel, never one mixing both.
    """
    free, number = name, 1
    while free in taken:
        number += 1
        free = f"{name} ({number})"
    return free


def _is_zarr_group(path: Path) -> bool:
    return path.is_dir() and ((path / "zarr.json").is_file() or (path / ".zgroup").is_file())


def plain_name(path: Path) -> str:
    """A folder's name as the panel lists it: 'Sample.ome.zarr' -> 'Sample', and a
    time point of a time lapse, 'Sample.ome_Time003.zarr' -> 'Sample_Time003'."""
    name = path.name
    if filestack.is_run(path):
        return filestack.run_name(path)  # a run saved as TIFF, raw or HDF5 files
    if name.endswith(STORE_SUFFIX):
        return name[: -len(STORE_SUFFIX)]
    if name.endswith(".zarr"):
        return _TIME_MARK.sub(r"\1", name[: -len(".zarr")])
    return name


@dataclass(frozen=True)
class Acquisition:
    path: Path
    started: float  # when the folder appeared, as a timestamp
    # For a run saved as TIFF or raw files: those files. (An HDF5 file is its own path.)
    files: tuple[Path, ...] = ()

    @property
    def name(self) -> str:
        return plain_name(self.path)


class Acquisitions:
    """The acquisitions inside a data folder, newest first."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        # What each folder turned out to be, so it is opened once and not every second.
        self._known: dict[Path, bool] = {}
        # The runs saved as files, and the folder's modification time they were read at.
        self._runs: tuple[int, list[Acquisition]] = (-1, [])

    def _file_runs(self) -> list[Acquisition]:
        """The runs saved as TIFF, raw or HDF5 files directly in the folder. Read again
        only when the folder has changed: a file added or taken away moves its time."""
        try:
            changed = self.root.stat().st_mtime_ns
        except OSError:
            return []
        if changed != self._runs[0]:
            self._runs = (changed, [
                Acquisition(path=run.path, started=run.started, files=run.members)
                for run in filestack.runs_in(self.root)
            ])
        return self._runs[1]

    def list(self) -> list[Acquisition]:
        found = []
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            return found
        found.extend(self._file_runs())
        for entry in entries:
            path = Path(entry.path)
            # '.ome.zarr', and a time lapse's 'Sample.ome_Time003.zarr'.
            if not entry.name.endswith(".zarr"):
                continue
            is_acquisition = self._known.get(path)
            if is_acquisition is None:
                if not _is_zarr_group(path):
                    continue  # not written yet: looked at again next time
                # A tile store has multiscales itself; an acquisition holds tile stores.
                try:
                    read_store(path)
                    is_acquisition = False
                except NotSupported:
                    is_acquisition = False  # an image, only not one this viewer reads
                except NotAStore:
                    is_acquisition = True
                self._known[path] = is_acquisition
            if is_acquisition:
                try:
                    found.append(Acquisition(path=path, started=entry.stat().st_ctime))
                except OSError:
                    continue
        return sorted(found, key=lambda a: (a.started, a.name), reverse=True)

    def newest(self) -> Acquisition | None:
        listed = self.list()
        return listed[0] if listed else None


@dataclass
class Tile:
    """One store of an acquisition that is shown, and what is known of its files."""

    path: Path
    store: Store
    landing: Landing
    progress: Progress | None
    looked_at: float = 0.0
    woken_at: float = -1e9
    listed_at: float = 0.0  # when all of its folders were last listed
    # Stacks of this store, as (time point, channel), that are shown from their
    # preview: their files are kept back from the page until the stack is whole.
    held: set[tuple[int, int]] = field(default_factory=set)
    # A stack in a TIFF, raw or HDF5 file: all of it is there, and nothing lands in it.
    in_a_file: bool = False
    # For a tile taken in as settled: when its newest file is asked for once more, and
    # which one that was (see CONFIRM_SETTLED_S). None once that has been done.
    confirm_at: float | None = None
    settled_newest: int = 0

    def held_back(self, file: str) -> bool:
        held, progress = self.held, self.progress
        if not held or progress is None:
            return False
        return progress.stack_of(file) in held

    def stack_key(self, time_point: int, channel: int) -> tuple[int, int]:
        """Where a stack lands in this store: a store without a time or channel axis
        holds one stack, at (0, 0)."""
        names = [axis.name for axis in self.store.axes]
        return (time_point if "t" in names else 0, channel if "c" in names else 0)

    def describe(self, store: Store) -> None:
        """Take in the store's description as it is on disk now."""
        self.store = store
        levels = read_levels(store)
        if not levels:
            return
        if self.progress is None:
            self.progress = Progress(store, levels[0])
        else:
            self.progress.reshape(store, levels[0])

    def awake(self, now: float) -> bool:
        if self.held or now - self.woken_at < AWAKE_S:
            return True
        return self.landing.grew and now - self.landing.changed_at < AWAKE_S

    def whole(self) -> bool:
        """Whether every stack the store has room for is on disk in full: each time point
        of each channel. Nothing more can land in such a tile unless the store itself
        grows (a time point appended), and that shows in its description."""
        progress = self.progress
        if progress is None:
            return False
        names = [axis.name for axis in self.store.axes]
        points = self.store.shape[names.index("t")] if "t" in names else 1
        channels = self.store.shape[names.index("c")] if "c" in names else 1
        return all(progress.complete((point, channel)) for point in range(points) for channel in range(channels))

    def recent(self) -> bool:
        """Whether its newest file is young enough for more to follow."""
        return not self.landing.holds_data or time.time() - self.landing.newest_s < RECENT_S


@dataclass
class Dataset:
    """One entry of the panel's list: an acquisition, or a single tile opened on its own."""

    name: str
    path: Path | None  # None for a run that is not saved in a form the viewer reads
    opened: bool = False  # opened from disk by the operator, rather than found in the watched folder
    single: bool = False  # ``path`` is one tile store, not a folder of them
    shown: bool = False
    by_hand: bool = False  # shown because the operator asked, not because it is the newest
    hidden: bool = False  # the operator ticked it off: it is not shown again unasked
    started: float = 0.0
    tiles: dict[Path, Tile] = field(default_factory=dict)
    previews: list[Held] = field(default_factory=list)
    running: Stack | None = None  # the stack the microscope is acquiring into it now
    in_run: bool = False  # the microscope is running it: between its stacks too
    over: bool = False  # the microscope said its run is finished
    # Read once for the panel while it is not shown: its positions' names and its channels.
    listed_positions: list[str] | None = None
    listed_channels: list[str] | None = None
    # Where those positions lie, read from the tiles' descriptions a few at a time, so
    # that the panel can draw the map of an acquisition before it is shown. The tiles
    # still to be read wait in ``_places_to_read`` (None before the first of them).
    listed_places: dict[str, dict] = field(default_factory=dict)
    _places_to_read: list[Path] | None = None
    # The files a run that is being acquired is saved into, when it is saved as TIFF,
    # raw or HDF5: shown from the camera's frames meanwhile, opened from them afterwards.
    files: set[Path] = field(default_factory=set)
    _resolved: dict[str, Path] = field(default_factory=dict)

    def tile_paths(self) -> list[Path]:
        if self.path is None:
            return []
        if self.single:
            return [self.path]
        if filestack.is_run(self.path):
            return filestack.members_of(self.path)
        try:
            entries = sorted(os.scandir(self.path), key=lambda e: e.name)
        except OSError:
            return []
        found = []
        for entry in entries:
            if not entry.name.endswith(STORE_SUFFIX):
                continue
            path = self._resolved.get(entry.name)
            if path is None:
                if not entry.is_dir():
                    continue
                path = self._resolved[entry.name] = Path(entry.path).resolve()
            found.append(path)
        return found


class Library:
    """The acquisitions on offer, which of them are shown, and keeping them up to date."""

    def __init__(
        self,
        viewer: Viewer,
        *,
        clock: Callable[[], float] = time.monotonic,
        look_budget_s: float | None = LOOK_BUDGET_S,
    ) -> None:
        self.viewer = viewer
        self.clock = clock
        # How long one look at the disk may take before tiles in no hurry are left for
        # the next; None for no limit (a test that moves the clock itself).
        self.look_budget_s = look_budget_s
        # How many callers are waiting for a look to make way: the microscope with a
        # frame or a stack, the operator with a click (see poll).
        self._waiting = 0
        self._waiting_lock = threading.Lock()
        self.datasets: list[Dataset] = []
        self.watched: Acquisitions | None = None
        self._top: Path | None = None  # the newest acquisition of the watched folder
        self._retiring: list[tuple[float, Held]] = []
        # Runs saved as files that have ended and wait to be opened from those files,
        # each with the time by which that is given up.
        self._to_open_from_files: list[tuple[float, Dataset]] = []
        self._plane_at = -1e9
        self._offered: list[dict] | None = None
        self._closed = False
        self._resolved: dict[Path, Path] = {}
        # Acquisitions of the watched folder the operator took off the list: not listed again.
        self._dismissed: set[Path] = set()
        # Live: whether runs the microscope acquires are collected, the switch in the panel.
        self.collecting = True
        # poll() runs on a timer, the rest on the page's or the microscope's request.
        self._lock = threading.RLock()
        viewer.on_show(self.show)
        viewer.on_remove(self.remove)
        viewer.on_collect(self.collect)
        viewer.collecting(True)

    def collect(self, on: bool) -> None:
        """Live, switched on or off. Off, nothing the microscope acquires comes in: a run that
        starts is not listed, and one being shown is paused where it is, its new planes and
        files left out. On again, it goes on from what the microscope does next."""
        with self._lock:
            self.collecting = bool(on)
            self.viewer.collecting(self.collecting)
            if self.collecting:
                self.poll()
            else:
                self._offer()
                self._point(self.clock())

    def close(self) -> None:
        """The window is going: nothing is looked at or shown any more.

        This does not wait for a look at the disk that is still going on. Such a look
        can take long on a slow or stalled network drive, and the window is closed from
        mesoSPIM's own window, which would stand still for as long. The look sees the
        mark set here before its next acquisition and stops there.

        The stacks that were shown from the camera's frames are let go as well. They are
        kept in memory, up to a gigabyte of them, and a closed window is not shown again.
        """
        self._closed = True
        # Without waiting: if a look is going on right now, it lets them go itself.
        if self._lock.acquire(blocking=False):
            try:
                self._let_previews_go()
            finally:
                self._lock.release()

    def _let_previews_go(self) -> None:
        """Forget every stack held in memory. Only for a library that is closed."""
        self._retiring = []
        for dataset in self.datasets:
            dataset.previews.clear()
            dataset.running = None

    # -- what is on offer ----------------------------------------------------------

    @property
    def names(self) -> list[str]:
        with self._lock:
            return [dataset.name for dataset in self.datasets]

    @property
    def shown(self) -> list[str]:
        with self._lock:
            return [dataset.name for dataset in self.datasets if dataset.shown]

    def dataset(self, name: str) -> Dataset | None:
        with self._lock:
            return next((d for d in self.datasets if d.name == name), None)

    def _at(self, path: Path) -> Dataset | None:
        return next((d for d in self.datasets if d.path is not None and d.path == path), None)

    def _new(self, name: str, path: Path | None, **about) -> Dataset:
        dataset = Dataset(name=free_name(self.names, name), path=path, **about)
        self.datasets.append(dataset)
        return dataset

    def _start_time(self) -> float:
        """Now, for a run the microscope begins: after every acquisition listed. On Windows the
        system clock moves in steps of 15.6 ms, and a run begun within one step of another
        would have had the same start time and been taken for the older."""
        now = time.time()
        latest = max((dataset.started for dataset in self.datasets if not dataset.opened), default=now - 1.0)
        return now if now > latest else latest + 1e-3

    def _sort(self) -> None:
        self.datasets.sort(key=lambda d: (d.opened, -d.started))

    # -- the folder the microscope writes into --------------------------------------

    def watch(self, folder: str | Path | None) -> None:
        """Follow a data folder: its acquisitions are listed, the newest is shown, and
        one that starts later is shown in its place. None stops following."""
        with self._lock:
            root = Path(folder).expanduser() if folder is not None else None
            if root is not None and self.watched is not None and self.watched.root == root:
                return
            self.watched = Acquisitions(root) if root is not None else None
            self._top = None
            self.poll()

    @property
    def root(self) -> Path | None:
        return self.watched.root if self.watched is not None else None

    def _list_watched(self) -> None:
        if self.watched is None:
            return
        # The files of a run the microscope is acquiring are not listed while it runs: a
        # TIFF or HDF5 file cannot be read while it is written, and the run is on screen
        # from the camera's frames. It is opened from its files when it is over.
        being_written = {file for dataset in self.datasets for file in dataset.files}
        listed = [
            found for found in self.watched.list()
            if not (being_written and being_written.intersection(
                [found.path.resolve(), *(file.resolve() for file in found.files)]))
        ]
        here = set()
        for found in reversed(listed):  # oldest first, so the newest ends up on top
            path = self._resolved.get(found.path)
            if path is None:
                path = self._resolved[found.path] = found.path.resolve()
            here.add(path)
            if self._at(path) is None and path not in self._dismissed and self.collecting:
                self._new(found.name, path, started=found.started)
        for dataset in list(self.datasets):
            # One whose folder was deleted goes from the list.
            gone = dataset.path is not None and not dataset.opened and dataset.path not in here
            if gone and not dataset.in_run and not dataset.previews and not dataset.path.exists():
                self._take_off(dataset)
                self.datasets.remove(dataset)
        top = self._resolved[listed[0].path] if listed else None
        if top is not None and top != self._top:
            # A new acquisition has appeared (or this is the first look): it is what
            # a live view shows, in place of whichever one was shown for being
            # newest -- unless the microscope is running another one right now,
            # which is then the one to watch.
            self._top = top
            if not any(dataset.in_run for dataset in self.datasets):
                self._show_newest(self._at(top))
        self._sort()

    def _show_newest(self, dataset: Dataset | None) -> None:
        if dataset is None or dataset.hidden:
            return
        for other in self.datasets:
            if other is not dataset and other.shown and not other.by_hand:
                self._take_off(other)
        if not dataset.shown:
            dataset.shown = True
            self._put_previews_on(dataset)
            self.viewer.fit()

    # -- opening from disk -----------------------------------------------------------

    def open(self, path: str | Path) -> list[str]:
        """Show a dataset from disk beside what is shown, and return the names it is listed under.

        Three kinds of folder can be opened: one tile store (a ``.ome.zarr`` with
        its own image data), one acquisition (a ``.ome.zarr`` holding tile stores,
        or any folder that holds tile stores directly), or a data folder holding
        acquisitions, of which every one is listed and the newest is shown.
        Anything else raises :class:`NotAStore`, with a sentence saying what was
        expected. A folder that is already listed is simply shown.
        """
        path = Path(path).expanduser().resolve()
        with self._lock:
            held = self._at(path)
            if held is not None:
                self.show(held.name, True)
                return [held.name]
            if filestack.is_stack_file(path) and path.is_file():
                # A TIFF, raw or HDF5 file: the whole run it belongs to is opened, the
                # other tiles and channels saved beside it under the same name included.
                run = filestack.run_at(path)
                if run is None:
                    raise NotAStore(f"{path} could not be read")
                if not filestack.members_of(run.path):
                    raise NotAStore(
                        f"{path} holds no stacks the viewer can read",
                        reason="it holds no image stacks the viewer can read",
                    )
                for member in filestack.members_of(run.path)[:1]:
                    read_store(member)  # says why, if a file of this kind cannot be shown
                dataset = self._at(run.path) or self._new(run.name, run.path, opened=True, started=run.started)
                self.show(dataset.name, True)
                return [dataset.name]
            try:
                read_store(path)
                single, why = True, None
            except NotSupported:
                raise  # an image in a form this viewer does not read: the error says which
            except NotAStore as error:
                single, why = False, error  # not one image: a folder of them, looked at below
            if single:
                dataset = self._new(plain_name(path), path, opened=True, single=True)
            else:
                acquisitions = Acquisitions(path).list()
                if acquisitions:
                    names = []
                    for found in reversed(acquisitions):
                        inside = found.path.resolve()
                        listed = self._at(inside) or self._new(
                            found.name, inside, opened=True, started=found.started
                        )
                        names.append(listed.name)
                    self.show(names[-1], True)
                    return names
                readable = 0
                for tile in Dataset(name="", path=path).tile_paths():
                    try:
                        read_store(tile)
                        readable += 1
                    except NotSupported:
                        raise
                    except NotAStore:
                        continue
                if not readable:
                    if why is not None and why.reason:
                        raise why  # a store, but a broken one
                    raise NotAStore(
                        f"{path} is not a dataset the viewer can open. Pick a .ome.zarr folder "
                        "(one tile, or one acquisition holding tiles), or a folder holding acquisitions."
                    )
                dataset = self._new(plain_name(path), path, opened=True)
            self.show(dataset.name, True)
            return [dataset.name]

    def remove(self, name: str) -> bool:
        """Take an acquisition off the list; the files stay on disk. One of the watched
        folder is remembered, so the next look does not list it again. The run being
        acquired cannot be taken off."""
        with self._lock:
            dataset = self.dataset(name)
            if dataset is None or dataset.in_run:
                return False
            self._take_off(dataset)
            for held in list(dataset.previews):
                self._drop(held)
            if dataset.path is not None and not dataset.opened:
                self._dismissed.add(dataset.path)
            self.datasets.remove(dataset)
            self._offer()
            return True

    # -- shown or not ----------------------------------------------------------------

    def show(self, name: str, visible: bool = True, only: bool = False) -> None:
        """Tick an acquisition on or off; ``only`` shows it alone."""
        with self._in_turn():
            dataset = self.dataset(name)
            if dataset is None:
                return
            if only:
                for other in self.datasets:
                    if other is not dataset and other.shown:
                        self._take_off(other)
                        other.hidden = True
                visible = True
            if visible:
                dataset.hidden = False
                dataset.by_hand = True
                if not dataset.shown:
                    dataset.shown = True
                    self._look(dataset, self.clock())
                    self._put_previews_on(dataset)
                    if only or len(self.shown) == 1:
                        self.viewer.fit()
            elif dataset.shown:
                self._take_off(dataset)
                dataset.hidden = True
            self._offer()

    def _take_off(self, dataset: Dataset) -> None:
        dataset.shown = False
        dataset.by_hand = False
        for held in dataset.previews:
            if held.name:
                self.viewer.remove_preview(held.name)
                held.name = ""
        for tile in dataset.tiles.values():
            self.viewer.guard(tile.path, None)
        dataset.tiles.clear()
        self.viewer.remove(dataset.name)

    def _put_previews_on(self, dataset: Dataset) -> None:
        for held in dataset.previews:
            # One that the tile on disk has taken over from stays off.
            if not held.name and not held.released:
                held.name = self.viewer.add_preview(dataset.name, held.preview, held.preview.channel())

    # -- one look at the disk ----------------------------------------------------------

    @contextmanager
    def _in_turn(self):
        """Hold the library for a caller that must not be kept waiting: the microscope
        handing over a frame or a stack, the operator's click in the list.

        A look at the disk (poll) can take long for a large acquisition on a slow drive.
        It works tile by tile and lets go of the library in between; this tells it that
        somebody is waiting, so that it really makes way and does not simply carry on.
        """
        with self._waiting_lock:
            self._waiting += 1
        try:
            self._lock.acquire()
        finally:
            with self._waiting_lock:
                self._waiting -= 1
        try:
            yield
        finally:
            self._lock.release()

    def _make_way(self) -> None:
        """Between two tiles of a look, with the library let go: wait a moment for
        whoever is waiting to take their turn."""
        until = time.monotonic() + 0.2
        while self._waiting and time.monotonic() < until:
            time.sleep(0.0005)

    def poll(self) -> None:
        """Bring the list and the viewer up to date with the disk.

        The disk is looked at tile by tile, and the library is let go between two tiles.
        The camera's frames and the operator's clicks need it too, and listing a large
        tile on a network drive can take seconds: held for the whole look, the live view
        stood still and the list did not answer for as long as the look took.
        """
        with self._lock:
            if self._closed:
                return
            now = self.clock()
            self._list_watched()
            datasets = list(self.datasets)
        began = time.monotonic()
        for dataset in datasets:
            with self._lock:
                if self._closed:
                    self._let_previews_go()
                    return  # closed while this look was going on
                # A run paused by Live off is left where it is, on disk too.
                wanted = dataset in self.datasets and dataset.shown and (self.collecting or not dataset.in_run)
                paths = dataset.tile_paths() if wanted else []
            for path in paths:
                self._make_way()
                with self._lock:
                    if self._closed:
                        self._let_previews_go()
                        return
                    if dataset not in self.datasets or not dataset.shown:
                        break  # taken off the list or hidden while this look was going on
                    hurried = self.look_budget_s is not None and time.monotonic() - began > self.look_budget_s
                    self._look_at(dataset, path, now, hurried=hurried)
        for dataset in datasets:
            if not self._read_places(dataset, began):
                break
        with self._lock:
            if self._closed:
                return
            self._open_from_files(now)
            self._hand_over(now)
            self._offer()
            self._point(now)

    def _read_places(self, dataset: Dataset, began: float) -> bool:
        """Learn where the tiles of an acquisition that is not shown lie, a few with every
        look, and return whether there is still time for more in this look.

        The panel draws an acquisition's positions as a map, and for one that is not shown
        nothing was known but the tiles' names: a hundred of them had no map until the
        acquisition was put on screen. A tile's place is in its description, two small
        files. They are read here, in the time a look has left over once the tiles on
        screen have been seen to, and each tile is read once.
        """
        while True:
            with self._lock:
                if self._closed or dataset not in self.datasets or dataset.shown or dataset.path is None:
                    return True
                if dataset._places_to_read is None:
                    dataset._places_to_read = list(reversed(dataset.tile_paths()))
                if not dataset._places_to_read:
                    return True
                path = dataset._places_to_read.pop()
            try:
                place = store_place(read_store(path))
            except (NotAStore, NotSupported):
                place = {}  # not a tile the viewer reads: listed by its name alone
            with self._lock:
                dataset.listed_places[position_name(path)] = place
            self._make_way()
            if self.look_budget_s is not None and time.monotonic() - began > self.look_budget_s:
                return False

    def _look(self, dataset: Dataset, now: float) -> None:
        """Look at every tile of an acquisition in one go (for one just shown or opened)."""
        for path in dataset.tile_paths():
            self._look_at(dataset, path, now)

    def _look_at(self, dataset: Dataset, path: Path, now: float, *, hurried: bool = False) -> None:
        """Look at one tile on disk, as closely as its state calls for.

        ``hurried``: this look has taken long already, and only what cannot wait is done.
        A tile that is being written cannot wait; a new tile and a quiet one can.
        """
        tile = dataset.tiles.get(path)
        if tile is None:
            if not hurried:
                self._add_tile(dataset, path, now)
            return
        if tile.in_a_file:
            # A file is whole or it is not there. It is only asked, now and then, whether
            # it has changed (written again, or still growing under a writer that the
            # viewer is not told about); if so its description is read afresh.
            growing = dataset.path == self._top or time.time() - tile.store.stamp[0] / 1e9 < RECENT_S
            if hurried or now - tile.looked_at < (DOZING_S if growing else ASLEEP_S):
                return
            tile.looked_at = now
            try:
                store = read_store(path)
            except NotAStore:
                return
            if store.stamp != tile.store.stamp:
                tile.describe(store)
                self.viewer.add(path, layer=dataset.name, channel=channel_of(store), measure=False)
            return
        only_its_shape = False
        if tile.awake(now):
            # The stack the microscope is acquiring into this tile right now is on
            # screen from the camera's frames, and its files are kept from the page
            # until it is whole: there is nothing to gain from watching them land.
            # An earlier stack of the same tile that is waiting to be handed over
            # from its preview is another matter: that needs the tile looked at.
            mine = [held for held in dataset.previews if held.store == path and not held.released]
            fed = (
                dataset.running is not None
                and any(held.ended is None for held in mine)
                and not any(held.ended is not None for held in mine)
            )
            if fed and now - tile.looked_at < FED_EVERY_S:
                return
            thorough = not fed and now - tile.listed_at >= AWAKE_THOROUGH_S
            depth = None
        else:
            if hurried:
                return
            if tile.confirm_at is not None and now >= tile.confirm_at and tile.progress is not None:
                tile.confirm_at = None
                if newest_change(path / tile.progress.level.path) != tile.settled_newest:
                    # A file has landed since the tile was taken for a finished one: it
                    # is being written (by a computer whose clock is behind). Looked at
                    # at once, and closely from here on.
                    tile.woken_at = now
                    tile.looked_at = -1e9
                    return self._look_at(dataset, path, now)
            # May it still be growing? The newest acquisition of the watched
            # folder may, and so may one the microscope is running, or one
            # whose files are young; one opened from last year hardly.
            growing = dataset.path == self._top or dataset.in_run or tile.recent()
            if dataset.in_run and dataset.previews:
                # The microscope says which tile it writes next (begin_stack wakes
                # it), so the others of its run need no watching: a glance now and
                # then is enough, in case something reached them by another road.
                every = ASLEEP_S
                only_its_shape = True
            elif tile.whole():
                # Every stack it has room for is there. Only the store itself can
                # still grow, by a time point, and its description says when.
                every = DOZING_S if growing else ASLEEP_S
                only_its_shape = True
            else:
                every = DOZING_S if growing else ASLEEP_S
            if now - tile.looked_at < every:
                return
            thorough = growing and not only_its_shape and now - tile.listed_at >= DOZING_THOROUGH_S
            depth = None if thorough else PROBE_DEPTH
        tile.looked_at = now
        if thorough:
            tile.listed_at = now
        try:
            store = read_store(path)
        except NotAStore:
            return
        if store.shape != tile.store.shape:
            tile.describe(store)
            tile.woken_at = now
            self.viewer.add(path, layer=dataset.name, channel=channel_of(store), measure=False)
            only_its_shape = False
        elif store.stamp != tile.store.stamp:
            # The store's description was written anew though its shape is the same: the
            # microscope has begun a stack in it, over one that was there (the same time
            # point acquired again). Its files are about to change, so it is watched.
            tile.store = store
            tile.woken_at = now
            only_its_shape = False
        if only_its_shape:
            return
        files = tile.landing.look(now, thorough=thorough, depth=depth)
        if not files:
            return
        tile.woken_at = now
        if tile.progress is None:
            tile.describe(store)
        if tile.progress is not None:
            tile.progress.add(files)
        kept = [file for file in files if tile.held_back(file)] if tile.held else []
        if kept:
            for held in dataset.previews:
                if held.store == path and not held.released and held.stack in tile.held:
                    held.files.extend(f for f in kept if tile.progress.stack_of(f) == held.stack)
            kept_back = set(kept)
            files = [file for file in files if file not in kept_back]
        self.viewer.landed(path, files)

    def _add_tile(self, dataset: Dataset, path: Path, now: float) -> None:
        try:
            store = read_store(path)
        except NotAStore:
            return  # being created at this very moment: looked at again next time
        landing = Landing(path, phase=random.randrange(1000))
        tile = Tile(path=path, store=store, landing=landing, progress=None, looked_at=now, listed_at=now)
        tile.describe(store)
        tile.in_a_file = filestack.virtual(path) is not None
        settled = 0 if tile.in_a_file else self._settled_since(dataset, tile)
        if tile.in_a_file:
            files = []
        elif settled:
            landing.take_as_read(settled)
            tile.progress.take_as_whole()
            tile.settled_newest = settled
            tile.confirm_at = now + CONFIRM_SETTLED_S
            files = []
        else:
            files = landing.look(now, thorough=True)  # what is there already: nothing has read it yet
        if tile.progress is not None:
            tile.progress.add(files)
        if not settled and not tile.in_a_file and (not files or time.time() - landing.newest_s < AWAKE_S):
            # A tile that has just appeared, or was written moments ago, is one to
            # keep an eye on: its next files land any second.
            tile.woken_at = now
        dataset.tiles[path] = tile
        for held in dataset.previews:
            if held.store == path and not held.released:
                held.stack = tile.stack_key(held.preview.stack.time_point, held.preview.stack.channel_index)
                if tile.progress is not None:
                    held.files.extend(f for f in files if tile.progress.stack_of(f) == held.stack)
        self._hold(dataset, tile)
        self.viewer.guard(path, tile.held_back)
        # Its contrast is measured from its data only when nobody is writing it:
        # for one that is being written the page sets it from what is on screen.
        self.viewer.add(
            path, layer=dataset.name, channel=channel_of(store), measure=not self._is_live(dataset, now)
        )

    @staticmethod
    def _settled_since(dataset: Dataset, tile: Tile) -> int:
        """When the last file of a tile was written, if the tile can be taken in without
        listing its folders; 0 if it has to be looked at.

        A tile the microscope is acquiring, or one written within the last minutes, is
        looked at properly. For any other, three cheap signs have to agree. The store's
        description is old. So is the newest file, found by following the folders changed
        last in the finest copy (``newest_change``). And every stack the store has room
        for ends in its last file, so nothing more is expected to land. An old tile that
        is not complete (the run was stopped, or it is still being copied here) fails the
        third, and is listed and watched as before.
        """
        path, store, progress = tile.path, tile.store, tile.progress
        if progress is None or dataset.in_run:
            return 0
        if any(held.store == path and not held.released for held in dataset.previews):
            return 0
        wall = time.time()
        if wall - store.stamp[0] / 1e9 < SETTLED_S:
            return 0
        newest = newest_change(path / progress.level.path)
        if newest <= 0 or wall - newest / 1e9 < SETTLED_S:
            return 0
        if not all((path / progress.last_file(stack)).is_file() for stack in progress.stacks()):
            return 0
        return newest

    @staticmethod
    def _hold(dataset: Dataset, tile: Tile) -> None:
        """Work out which stacks of a tile are shown from a preview and so kept back."""
        tile.held = {
            held.stack
            for held in dataset.previews
            if held.store == tile.path and held.stack is not None and not held.released
        }

    # -- the stack being acquired, fed in by the microscope ------------------------------

    def begin_stack(self, stack: Stack) -> None:
        """The microscope is about to acquire a stack: show it from memory as it comes."""
        with self._in_turn():
            if self._closed or not self.collecting:
                return
            now = self.clock()
            folder = stack.folder.expanduser().resolve() if stack.folder is not None else None
            dataset = self._at(folder) if folder is not None else None
            saved_as = stack.file.expanduser().resolve() if stack.file is not None else None
            if dataset is None and folder is None:
                dataset = next(
                    (d for d in self.datasets if d.path is None and d.name == stack.acquisition), None
                )
            if dataset is None:
                name = plain_name(folder) if folder is not None else stack.acquisition
                dataset = self._new(name, folder, started=self._start_time())
            self.end_stack()
            if not dataset.in_run:
                # A run begins: it is what a live view shows, even one the operator
                # had ticked off after an earlier run. (Ticked off during a run, it
                # stays off for the rest of it, through every stack and time point.)
                # A time lapse saved as a folder per time point moves on to the next
                # folder without the run having ended: the one before is done, and
                # what the operator ticked off stays off.
                before = [other for other in self.datasets if other.in_run]
                for other in before:
                    other.in_run = False
                    other.over = True
                dataset.in_run = True
                dataset.hidden = any(other.hidden for other in before)
                self._show_newest(dataset)
                self._sort()
            dataset.over = False
            dataset.running = stack
            if saved_as is not None:
                dataset.files.add(saved_as)
            store = stack.store.expanduser().resolve() if stack.store is not None else None
            earlier = list(dataset.previews)
            preview = PreviewStack(stack)
            held = Held(preview=preview, name="", layer=dataset.name, began=now, store=store)
            dataset.previews.append(held)
            tile = dataset.tiles.get(store) if store is not None else None
            if tile is not None:
                held.stack = tile.stack_key(stack.time_point, stack.channel_index)
                tile.woken_at = now
                # Looked at with the very next look: a time point appended changes the
                # store's shape, and the page has to be given the store anew.
                tile.looked_at = -1e9
                tile.landing.wake(now)
                if tile.progress is not None:
                    # Whatever an earlier try left of this stack is written over.
                    tile.progress.forget(held.stack)
                self._hold(dataset, tile)
            for old in earlier:
                # The same stack once more -- stopped and started again -- takes
                # the place of the earlier try, which lets go only now that the
                # new one holds the stack's files back.
                before = old.preview.stack
                same_place = old.store == store if store is not None else (
                    old.store is None and before.origin_um == stack.origin_um
                )
                if same_place and (before.time_point, before.channel) == (stack.time_point, stack.channel):
                    self._drop(old)
            if dataset.shown:
                held.name = self.viewer.add_preview(dataset.name, preview, preview.channel())
            self._plane_at = -1e9
            self._offer()

    def add_plane(self, index: int, frame) -> None:
        """A frame of the stack being acquired: its plane number and its pixels as written."""
        with self._in_turn():
            if self._closed or not self.collecting:
                return
            now = self.clock()
            held = self._acquiring()
            # A little under the pace the window hands frames over at, so that the
            # two do not take turns in passing a frame over.
            if held is None or now - self._plane_at < 0.9 / PLANES_PER_S:
                return
            self._plane_at = now
            preview = held.preview
            unlit = preview.window is None
            first = preview.top < 0
            files = preview.add(index, frame)
            if first and files:
                self._offer()  # no longer waiting for its first planes
            if not held.name:
                return
            if unlit and preview.window is not None:
                self.viewer.set_window(held.layer, preview.stack.channel, preview.window, only_if_unset=True)
            self.viewer.preview_landed(held.name, files)
            self._point(now)

    def end_stack(self) -> None:
        """The microscope has acquired the stack; the writer may still be saving it."""
        with self._in_turn():
            now = self.clock()
            for dataset in self.datasets:
                if dataset.running is None:
                    continue
                dataset.running = None
                for held in dataset.previews:
                    if held.ended is None:
                        held.ended = now
                        files = held.preview.finish()
                        if held.name:
                            self.viewer.preview_landed(held.name, files)

    def end_run(self) -> None:
        """The microscope has finished the whole run: nothing more is coming."""
        with self._in_turn():
            self.end_stack()
            for dataset in self.datasets:
                if dataset.in_run:
                    dataset.in_run = False
                    dataset.over = True  # what still lands is the writer finishing
                    if dataset.files:
                        self._to_open_from_files.append((self.clock() + GIVE_UP_S, dataset))
            if self._closed:
                return
            self._offer()
            self._point(self.clock())

    def _open_from_files(self, now: float) -> None:
        """A run saved as TIFF, raw or HDF5 files has ended: show it from those files.

        While it ran it was on screen from the camera's frames, thinned out to fit in
        memory. Its files are whole now, and hold every plane at full size. As soon as
        they can be read the run is opened from them, and the frames kept in memory are
        let go. If they cannot be read within a minute and a half, the run stays as it
        is, from memory.
        """
        for due, dataset in list(self._to_open_from_files):
            if dataset not in self.datasets or dataset.in_run:
                self._to_open_from_files.remove((due, dataset))
                continue
            runs = {}
            for file in dataset.files:
                run = filestack.run_at(file) if file.is_file() else None
                if run is not None:
                    runs[run.path] = run
            ready = bool(runs)
            try:
                for run in runs.values():
                    members = filestack.members_of(run.path)
                    ready = ready and bool(members)
                    for member in members:
                        read_store(member)
            except ValueError:  # NotAStore or NotSupported: not readable (yet)
                ready = False
            if not ready:
                if now >= due:
                    self._to_open_from_files.remove((due, dataset))
                continue
            self._to_open_from_files.remove((due, dataset))
            shown, by_hand, hidden = dataset.shown, dataset.by_hand, dataset.hidden
            dataset.files = set()
            self.remove(dataset.name)
            for run in sorted(runs.values(), key=lambda one: one.started):
                opened = self._at(run.path) or self._new(run.name, run.path, started=run.started)
                opened.over = True
                opened.by_hand, opened.hidden = by_hand, hidden
                if shown:
                    opened.shown = True
                    self._look(opened, now)
            self._sort()

    def _acquiring(self) -> Held | None:
        for dataset in self.datasets:
            if dataset.running is not None:
                for held in reversed(dataset.previews):
                    if held.ended is None:
                        return held
        return None

    def _hand_over(self, now: float) -> None:
        """Give finished stacks to the page from disk, and take their previews away after."""
        for dataset in self.datasets:
            for held in list(dataset.previews):
                if held.ended is None or held.released or held.store is None:
                    continue
                tile = dataset.tiles.get(held.store)
                if tile is None or held.stack is None or tile.progress is None:
                    # Its tile has not appeared on disk (yet), or is not shown.
                    if not dataset.shown or now - held.ended >= GIVE_UP_S:
                        self._drop(held)
                    continue
                whole = tile.progress.complete(held.stack) and now - tile.landing.changed_at >= SETTLE_S
                if whole or now - held.ended >= GIVE_UP_S:
                    self._release(dataset, tile, held, now)
        for due, held in list(self._retiring):
            if now >= due:
                self._drop(held)
        # Finished previews stay only within a budget; the oldest go first. Those
        # with nothing on disk to take their place are all there is of their
        # stacks, so the ones that have a tile go before them.
        finished = [(d, h) for d in self.datasets for h in d.previews if h.ended is not None]
        total = sum(held.preview.bytes for _, held in finished)
        for dataset, held in sorted(finished, key=lambda pair: (pair[1].store is None, pair[1].began)):
            if total <= PREVIEW_BYTES:
                break
            total -= held.preview.bytes
            tile = dataset.tiles.get(held.store) if held.store is not None else None
            if tile is not None and not held.released and held.stack is not None:
                self._release(dataset, tile, held, now)
            self._drop(held)

    def _release(self, dataset: Dataset, tile: Tile, held: Held, now: float) -> None:
        """Let the page have a stack from disk, and its preview go once that is drawn."""
        held.released = True
        self._hold(dataset, tile)
        files, held.files = held.files, []
        self._pass_on(dataset, tile, held, files)
        if held.name:
            self.viewer.retire_preview(held.name)
        self._retiring.append((now + HAND_OVER_S, held))

    def _pass_on(self, dataset: Dataset, tile: Tile, held: Held, files: list[str]) -> None:
        """What was kept back for a preview that lets go: named to the page, unless the
        same stack is being acquired once more -- then it stays kept back, for the
        preview that shows it now."""
        if held.stack in tile.held:
            for other in dataset.previews:
                if other is not held and not other.released and (other.store, other.stack) == (held.store, held.stack):
                    other.files.extend(files)
                    return
        # A file written over by a second try was kept back twice.
        self.viewer.landed(tile.path, list(dict.fromkeys(files)))

    def _drop(self, held: Held) -> None:
        """Let a preview go for good."""
        self._retiring = [(due, other) for due, other in self._retiring if other is not held]
        if held.name:
            self.viewer.remove_preview(held.name)
            held.name = ""
        for dataset in self.datasets:
            if held in dataset.previews:
                dataset.previews.remove(held)
                tile = dataset.tiles.get(held.store) if held.store is not None else None
                if tile is not None and not held.released:
                    held.released = True
                    self._hold(dataset, tile)
                    files, held.files = held.files, []
                    self._pass_on(dataset, tile, held, files)

    # -- what the panel lists, and where a live view looks --------------------------------

    def _is_live(self, dataset: Dataset, now: float) -> bool:
        if dataset.running is not None or dataset.in_run:
            return True
        if dataset.over:
            return False
        return any(
            tile.landing.grew and now - tile.landing.changed_at < LIVE_S
            for tile in dataset.tiles.values()
        )

    def _offer(self) -> None:
        now = self.clock()
        offered = []
        for dataset in self.datasets:
            live = self._is_live(dataset, now)
            entry = {
                "name": dataset.name,
                "shown": dataset.shown,
                "live": live,
                "paused": live and not self.collecting,
                "removable": not dataset.in_run,
                "tiles": self._tile_count(dataset) if dataset.shown else None,
                "note": self._note(dataset),
                "positions": self._positions(dataset),
                "channels": self._channels(dataset),
                # Shown, but with nothing to draw yet: the page says what it waits for.
                "empty": dataset.shown and self._holds_nothing(dataset),
            }
            offered.append(entry)
            if dataset.shown:
                # While it is being written nobody knows its brightness: the page
                # sets the contrast from what is on screen.
                self.viewer.set_auto(dataset.name, live)
        if offered != self._offered:
            self._offered = offered
            self.viewer.offer(offered)

    @staticmethod
    def _positions(dataset: Dataset) -> list[dict]:
        """Its positions, by tile, for the panel to list and go to: each with its name and,
        when known, its place in micrometres (``um``: x, y and z, each low and high). Of
        the run being acquired, each also says whether it is ``done``, ``now`` being
        acquired, or ``next``. Where a next one lies is known when the microscope said
        so with the stack it began (``Stack.places``, from its acquisition table)."""
        found: dict[str, dict] = {}
        if dataset.shown:
            for tile in dataset.tiles.values():
                found.setdefault(position_name(tile.path), {"um": store_place(tile.store)})
        else:
            if not dataset.listed_positions:
                # Kept once found: a run just finished may not show its tiles yet.
                dataset.listed_positions = sorted({position_name(path) for path in dataset.tile_paths()}, key=_position_order)
            for name in dataset.listed_positions:
                place = dataset.listed_places.get(name)
                found.setdefault(name, {"um": place} if place else {})
        for held in dataset.previews:
            stack = held.preview.stack
            if stack.tile:
                found.setdefault(stack.tile, {}).setdefault("um", stack_place(stack))
        if dataset.in_run:
            # Of each tile, the channels whose stack the microscope has finished, at its newest time point.
            finished: dict[str, tuple[int, set[str]]] = {}
            for held in dataset.previews:
                stack = held.preview.stack
                point, channels = finished.get(stack.tile, (stack.time_point, set()))
                if stack.time_point > point:
                    point, channels = stack.time_point, set()
                if held.ended is not None and stack.time_point == point:
                    channels.add(stack.channel)
                finished[stack.tile] = (point, channels)
            last = dataset.previews[-1].preview.stack if dataset.previews else None
            wanted = len(last.channels) if last is not None else 1
            # The tile being acquired; between two of its stacks, the one last begun, until it is whole.
            now = dataset.running.tile if dataset.running is not None else (
                last.tile if last is not None and len(finished.get(last.tile, (0, set()))[1]) < wanted else None
            )
            for name, position in found.items():
                done = len(finished.get(name, (0, set()))[1])
                if name == now:
                    position["state"] = "now"
                elif done >= wanted:
                    position["state"] = "done"
                elif done:
                    position["state"] = "part"
                    position["acquired"] = done
                else:
                    position["state"] = "next"
            total = max((held.preview.stack.tiles for held in dataset.previews), default=0)
            for number in range(1, total + 1):
                found.setdefault(f"Tile {number}", {"state": "next"})
            # Where the tiles lie that have not begun, from what the microscope last said.
            for name, (x0, x1, y0, y1, z0, z1) in (last.places if last is not None else ()):
                if name in found:
                    found[name].setdefault("um", {"x": [x0, x1], "y": [y0, y1], "z": [z0, z1]})
        return [{"name": name, **found[name]} for name in sorted(found, key=_position_order)]

    @staticmethod
    def _channels(dataset: Dataset) -> list[str]:
        """Its channels, in the order they are saved: as the microscope said, as the tiles
        shown hold them, or read once from its first tile."""
        for held in reversed(dataset.previews):
            return list(held.preview.stack.channels)
        labels: list[str] = []
        stores = [tile.store for tile in dataset.tiles.values()] if dataset.shown else []
        if not stores and not dataset.listed_channels:
            # Kept once found: a run just finished may not have its description on disk yet.
            for path in dataset.tile_paths()[:1]:
                try:
                    dataset.listed_channels = [channel.label for channel in read_store(path).channels]
                except (NotAStore, NotSupported, OSError):
                    pass
        if not stores:
            return list(dataset.listed_channels or [])
        for store in stores:
            for channel in store.channels:
                if channel.label not in labels:
                    labels.append(channel.label)
        return labels

    @staticmethod
    def _tile_count(dataset: Dataset) -> int:
        """How many tiles are shown: stores at different places. A run saved as one
        store per tile and channel has several stores to a tile. A run with nothing
        on disk the viewer reads (TIFF, HDF5, raw) is shown from the camera's frames:
        its tiles are the places of its previews."""
        places = set()
        for tile in dataset.tiles.values():
            store = tile.store
            places.add(
                tuple(
                    round(store.translation[index], 3)
                    for index, axis in enumerate(store.axes)
                    if axis.name in ("z", "y", "x")
                )
            )
        if not places:
            places = {tuple(round(value, 3) for value in held.preview.stack.origin_um) for held in dataset.previews}
        return len(places)

    @staticmethod
    def _holds_nothing(dataset: Dataset) -> bool:
        if any(held.preview.top >= 0 for held in dataset.previews):
            return False
        return not any(tile.landing.holds_data for tile in dataset.tiles.values())

    @staticmethod
    def _note(dataset: Dataset) -> str:
        stack = dataset.running
        if stack is None:
            return ""
        parts = []
        if stack.tile:
            parts.append(f"{stack.tile} of {stack.tiles}" if stack.tiles else stack.tile)
        parts.append(stack.channel)
        return " · ".join(parts)

    def _point(self, now: float) -> None:
        """Say where the newest data is, for a view that follows the acquisition."""
        if not self.collecting:
            # Live off: nothing comes in, so there is nothing to follow.
            self.viewer.look_at_newest()
            return
        held = self._acquiring()
        if held is not None and held.name:
            depth = held.preview.newest_um
            if depth is not None:
                preview = held.preview
                self.viewer.look_at_newest(
                    z=depth,
                    t=float(preview.stack.time_point),
                    preview=held.name,
                    chunk=[0, 0, preview.top, preview.stack.channel_index, 0],
                )
                return
        newest: tuple[float, Tile] | None = None
        for dataset in self.datasets:
            if not dataset.shown or dataset.running is not None or dataset.over:
                continue
            for tile in dataset.tiles.values():
                at = tile.landing.changed_at
                if tile.landing.grew and now - at < LIVE_S and (newest is None or at > newest[0]):
                    newest = (at, tile)
        if newest is not None:
            where = _newest_plane(newest[1])
            if where is not None:
                self.viewer.look_at_newest(**where)
                return
        # Between two stacks of a run the view stays where it is, and a run counts
        # as going on: only when nothing is fed or written any more is it over.
        if held is None and not any(self._is_live(dataset, now) for dataset in self.datasets):
            self.viewer.look_at_newest()


_TILE_NUMBER = re.compile(r"Tile(\d+)")


def position_name(path: Path) -> str:
    """A tile's position as the panel names it: "Tile 1" for a store called ``..._Tile0_...``,
    as the microscope names the tile it acquires; otherwise the store's own name."""
    in_a_file = filestack.position_name(path)
    if in_a_file is not None:
        return in_a_file
    found = _TILE_NUMBER.search(path.name)
    return f"Tile {int(found.group(1)) + 1}" if found else plain_name(path)


def _position_order(name: str) -> tuple:
    found = re.fullmatch(r"Tile (\d+)", name)
    return (0, int(found.group(1)), "") if found else (1, 0, name)


def store_place(store: Store) -> dict[str, list[float]]:
    """Where a store lies, in micrometres: x, y and z, each from low to high."""
    place = {}
    for index, axis in enumerate(store.axes):
        if axis.name not in ("x", "y", "z"):
            continue
        base, factor = axis.si
        to_um = factor * 1e6 if base == "m" else 1.0
        low = store.translation[index] * to_um
        high = (store.translation[index] + store.shape[index] * store.scale[index]) * to_um
        place[axis.name] = [round(low, 6), round(high, 6)]
    return place


def stack_place(stack: Stack) -> dict[str, list[float]]:
    """Where a stack being acquired lies, in micrometres, from what the microscope said of it."""
    z, y, x = stack.origin_um
    voxel_z, voxel_y, voxel_x = stack.voxel_um
    rows, columns = stack.frame
    return {
        "x": [round(x, 6), round(x + columns * voxel_x, 6)],
        "y": [round(y, 6), round(y + rows * voxel_y, 6)],
        "z": [round(z, 6), round(z + stack.planes * voxel_z, 6)],
    }


def _newest_plane(tile: Tile) -> dict[str, float] | None:
    """Where the last complete plane of the stack a tile is being written into lies."""
    progress = tile.progress
    if progress is None or progress.newest is None or progress.newest in tile.held:
        return None
    planes = progress.planes(progress.newest)
    if planes <= 0:
        return None
    store = tile.store
    z = store.axis_index("z")
    _, factor = store.axes[z].si
    where = {"z": (store.translation[z] + (planes - 1) * store.scale[z]) * factor * 1e6}
    if any(axis.name == "t" for axis in store.axes):
        where["t"] = float(progress.newest[0])
    return where
