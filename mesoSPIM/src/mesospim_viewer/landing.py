"""Which files of a store have landed on disk, and how far a stack has got.

The microscope's writers create a store's arrays when a stack starts and then
add chunk files over the minutes that follow, each written once and never
changed (zarr writes a file under another name and renames it; so does the
shard writer). So following a store that is being written comes down to one
question, asked every second: *which files are new?* The page is told exactly
those, and reads exactly the chunks that come from them (see
``source/src/engine/worker_landed.js``).

:class:`Landing` answers the question cheaply. It remembers each folder's
modification time and lists a folder again only when that has moved, so a
store of thousands of chunk files costs a few hundred ``stat`` calls a look;
a part of the store that has gone quiet is looked at only now and then, and
what it remembers of that part shrinks to a count. A store of a million files
is watched with the memory of the few folders that are still growing.

Three kinds of look, for three ages of a store:

- the ordinary look, every second, for a store that is being written;
- a *probe* (``depth``), for one that has gone quiet: only the folders near
  the top are looked at, which is where a new channel or time point shows;
- a *thorough* look, which lists every folder whatever its modification time
  says, for file systems whose folder times cannot be trusted.

A store that was finished long ago is not looked into at all when it is first
met (:func:`newest_change`, :meth:`Landing.take_as_read`). Listing every folder
of a two-hundred-tile acquisition from last month, only to learn that nothing
is new, kept a network drive busy for minutes.

:class:`Progress` turns the landed files of the finest copy into how many
planes of each stack are complete, which is where a live view should be looking.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .omezarr import Level, Store

# A folder changed this shortly before the newest change seen in the store is
# listed again whatever its modification time says: some file systems count
# that time in whole seconds, and a file added within the same second would
# otherwise be missed until the next one. Measured against the store's own
# newest time, not this computer's clock, which a file server need not share.
HOT_S = 3.0
# A part of the store with no new file for this long is looked at only every
# COLD_EVERY-th time, and what is remembered of its files shrinks to a count.
COLD_S = 30.0
COLD_EVERY = 10

# Not chunk files: metadata, and files still being written under another name.
_METADATA = {"zarr.json", ".zarray", ".zattrs", ".zgroup", ".zmetadata"}


def _is_chunk_file(name: str) -> bool:
    return not (name in _METADATA or name.startswith(".") or name.endswith((".partial", ".tmp")))


def newest_change(folder: str | Path, steps: int = 8) -> int:
    """Roughly when the last file landed anywhere below ``folder``, in nanoseconds (0 for
    a folder that cannot be read).

    Found without listing everything. A folder's modification time moves when something
    is added to it, so of the folders inside one, the one changed last is where the
    writer was last at work. Following that one down, a step at a time, ends at the
    newest files after a handful of listings, where a full walk of a large tile takes
    thousands. The answer can be a little early when two stacks were written side by
    side; it is used only to tell "finished long ago" from "written lately".
    """
    newest = 0
    here = os.fspath(folder)
    for _ in range(steps):
        following = None
        try:
            with os.scandir(here) as entries:
                for entry in entries:
                    try:
                        modified = entry.stat(follow_symlinks=False).st_mtime_ns
                        is_folder = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if not is_folder and not _is_chunk_file(entry.name):
                        continue
                    newest = max(newest, modified)
                    if is_folder and (following is None or modified >= following[0]):
                        following = (modified, entry.path)
        except OSError:
            break
        if following is None:
            break
        here = following[1]
    return newest


@dataclass
class _Folder:
    modified: int = -1  # st_mtime_ns when last listed
    changed_at: float = 0.0  # when this folder, or one below it, last gained a file
    folders: list[str] = field(default_factory=list)
    # Its files as (modified, size) by name while it may still gain some; once
    # it has gone quiet only how many there were and the newest of them.
    files: dict[str, tuple[int, int]] | None = field(default_factory=dict)
    count: int = 0
    newest: int = 0


class Landing:
    """The files that have appeared in a store's folder since the last look."""

    def __init__(self, root: str | Path, *, phase: int = 0) -> None:
        self.root = Path(root)
        self._folders: dict[str, _Folder] = {}
        # Stores looked at together take their closer looks in turn (``phase``).
        self._looks = phase
        self._first = True
        # Files written up to this time were there when the store was taken in without
        # a look (take_as_read). The first real look does not report them as new.
        self._read_until = 0
        self._latest = 0  # the newest modification time seen in the store, in ns
        self.count = 0  # chunk files seen so far
        self.changed_at = 0.0  # when a file last landed, on the monotonic clock
        self.grew = False  # whether a file has landed since the first look

    @property
    def holds_data(self) -> bool:
        """Whether any chunk file has been seen, or is known to be there."""
        return self.count > 0 or self._read_until > 0

    def take_as_read(self, newest: int) -> None:
        """Take the store in as it is, without listing its folders.

        For a store finished long ago, whose newest file is from ``newest`` (see
        :func:`newest_change`). Nothing of it is listed until there is a reason to. The
        first look that does list it then reports only what was written after
        ``newest``, since the page has shown everything older from the start.
        """
        self._read_until = max(int(newest), 1)
        self._latest = max(self._latest, int(newest))

    @property
    def newest_s(self) -> float:
        """When the newest file seen was written, as a timestamp (0 before any)."""
        return self._latest / 1e9

    def wake(self, now: float | None = None) -> None:
        """Files are about to land again (the microscope says so): no part of the
        store counts as quiet, and the next looks are ordinary ones all the way down."""
        now = time.monotonic() if now is None else now
        for folder in self._folders.values():
            folder.changed_at = now

    def look(
        self, now: float | None = None, *, thorough: bool = False, depth: int | None = None
    ) -> list[str]:
        """The chunk files that are new or have changed, as paths below the root with ``/``.

        ``thorough`` lists every folder again, whatever its modification time
        says. ``depth`` looks only at the folders that many steps below the
        root, and below them only at what turns out to be new: a probe for a
        store that has gone quiet. Otherwise the quiet parts are looked at every
        ``COLD_EVERY`` -th time only.
        """
        now = time.monotonic() if now is None else now
        self._looks += 1
        closer = thorough or self._first or self._looks % COLD_EVERY == 0
        landed: list[str] = []
        self._walk("", now, closer, thorough or self._first, depth, 0, landed)
        if landed:
            self.changed_at = now
            self.grew = self.grew or not self._first or self._read_until > 0
        if self._first and not self._read_until:
            # (A store that was taken as read is listed for the first time because it is
            # being written again. All of it is kept in mind for now, so that a file
            # written over in any part of it is seen with the next look.)
            self._forget_what_was_there(now)
        self._first = False
        return landed

    def _forget_what_was_there(self, now: float) -> None:
        """After the first look: of the parts of the store that were written long before
        its newest file, keep only the count.

        The first look lists everything, and every file's time and size were kept until
        that part of the store had been looked at again and found quiet. For a store
        opened from disk that never happened. A large acquisition then cost hundreds of
        megabytes, and tidying up that much memory made the whole program pause for up to
        half a second at a time. A folder whose newest file is well older than the
        store's newest cannot be the one still being written.
        """
        for folder in self._folders.values():
            if folder.files is not None and folder.count and self._latest - folder.newest > COLD_S * 1e9:
                folder.files = None
                folder.changed_at = min(folder.changed_at, now - COLD_S - 1.0)

    def _walk(
        self,
        relative: str,
        now: float,
        closer: bool,
        every: bool,
        depth: int | None,
        level: int,
        landed: list[str],
    ) -> float:
        """Look at one folder and those below it; return when anything in it last changed.

        ``closer`` looks at folders that have gone quiet too; ``every`` lists
        them whatever their modification time says.
        """
        folder = self._folders.get(relative)
        new = folder is None
        if new:
            folder = self._folders[relative] = _Folder(changed_at=now)
        quiet = now - folder.changed_at > COLD_S
        if quiet and folder.files is not None:
            folder.files = None  # only the count is kept of a part that has gone quiet
        if not new and depth is not None and level > depth:
            return folder.changed_at
        if quiet and not closer and depth is None:
            return folder.changed_at
        path = self.root / relative if relative else self.root
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            return folder.changed_at
        self._latest = max(self._latest, modified)
        hot = self._latest - modified < HOT_S * 1e9
        found_new = set()
        if every or new or modified != folder.modified or hot:
            folder.modified = modified
            try:
                with os.scandir(path) as entries:
                    found = list(entries)
            except OSError:
                found = []
            known = set(folder.folders)
            folders = []
            files: dict[str, tuple[int, int]] = {}
            for entry in found:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        name = f"{relative}/{entry.name}" if relative else entry.name
                        folders.append(name)
                        if name not in known:
                            found_new.add(name)
                        continue
                    if not _is_chunk_file(entry.name):
                        continue
                    about = entry.stat()
                except OSError:
                    continue
                files[entry.name] = (about.st_mtime_ns, about.st_size)
            folder.folders = folders
            fresh = self._fresh(folder, files)
            if self._first and self._read_until:
                fresh = [name for name in fresh if files[name][0] > self._read_until]
            if fresh:
                prefix = f"{relative}/" if relative else ""
                landed.extend(prefix + name for name in fresh)
                folder.changed_at = now
                self.count += len(files) - folder.count
            folder.count = len(files)
            if files:
                folder.newest = max(folder.newest, max(seen[0] for seen in files.values()))
                self._latest = max(self._latest, folder.newest)
            folder.files = files if now - folder.changed_at <= COLD_S else None
        for name in folder.folders:
            # Below a folder that has just appeared everything is new.
            below = None if (depth is not None and name in found_new) else depth
            folder.changed_at = max(
                folder.changed_at,
                self._walk(name, now, closer, every, below, level + 1, landed),
            )
        return folder.changed_at

    @staticmethod
    def _fresh(folder: _Folder, files: dict[str, tuple[int, int]]) -> list[str]:
        """Which of a folder's files are new or have changed since it was last listed."""
        if folder.files is not None:
            return [name for name, seen in files.items() if folder.files.get(name) != seen]
        # Its files were forgotten when it went quiet. What was written since is
        # newer than the newest of them; failing that, a changed count says that
        # something came by another road (copied in, say), and all are named.
        newer = [name for name, seen in files.items() if seen[0] > folder.newest]
        if newer or len(files) == folder.count:
            return newer
        return list(files)


WHOLE = None  # stands for a slab of which every file has landed


@dataclass
class Progress:
    """How many planes of each stack of a store are on disk, from its landed files.

    A stack is one time point of one channel. Its planes land a slab at a time
    -- as many planes as one file of the finest copy is deep -- and a slab
    counts once every file across the sensor is there.
    """

    store: Store
    level: Level  # the finest copy
    # (time point, channel) -> slab index -> the places of its files that have
    # landed, or WHOLE once all of them have
    slabs: dict[tuple[int, int], dict[int, set | None]] = field(default_factory=dict)
    newest: tuple[int, int] | None = None  # the stack that last gained a file
    # Stacks taken to be on disk in full without their files having been counted: those
    # of a store that was finished long before it was first met (take_as_whole).
    settled: set[tuple[int, int]] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.reshape(self.store, self.level)

    def reshape(self, store: Store, level: Level) -> None:
        """Take in the store's description as it is now (a time point appended, a
        stack cut short), keeping what is known of its files."""
        self.store, self.level = store, level
        names = [axis.name for axis in store.axes]
        self._t = names.index("t") if "t" in names else None
        self._c = names.index("c") if "c" in names else None
        self._z = names.index("z")
        across = 1
        for axis, name in enumerate(names):
            if name in ("y", "x"):
                across *= level.count(axis)
        self._across = across
        self._prefix = level.path + "/"

    def take_as_whole(self) -> None:
        """Count every stack the store has room for as complete, without a look at its
        files. For a store finished long ago. A stack that gains a file afterwards is
        being written again, and is counted from its files from then on."""
        self.settled = set(self.stacks())

    def stacks(self) -> list[tuple[int, int]]:
        """Every stack the store has room for: each time point of each channel."""
        points = self.level.shape[self._t] if self._t is not None else 1
        channels = self.level.shape[self._c] if self._c is not None else 1
        return [(point, channel) for point in range(points) for channel in range(channels)]

    def last_file(self, stack: tuple[int, int]) -> str:
        """The file of a stack's last slab that lies in the far corner of the sensor, as a
        path below the store. The writer fills a stack from its first plane to its last,
        so in a store that has been quiet for a while this file being there says, to a
        very good approximation, that the stack is there in full."""
        index = [self.level.count(axis) - 1 for axis in range(len(self.level.shape))]
        if self._t is not None:
            index[self._t] = stack[0]
        if self._c is not None:
            index[self._c] = stack[1]
        return self._prefix + self.level.file_of(tuple(index))

    def forget(self, stack: tuple[int, int]) -> None:
        """A stack is about to be written again: what is known of its files no longer holds."""
        self.slabs.pop(stack, None)
        self.settled.discard(stack)

    def stack_of(self, file: str) -> tuple[int, int] | None:
        """The (time point, channel) a chunk file of any copy belongs to."""
        index = self._index(file, any_level=True)
        return None if index is None else self._stack(index)

    def add(self, files: list[str]) -> None:
        """Take in files that have landed; one named twice counts once."""
        for file in files:
            index = self._index(file)
            if index is None:
                continue
            stack = self._stack(index)
            self.settled.discard(stack)
            slabs = self.slabs.setdefault(stack, {})
            at = index[self._z]
            places = slabs.get(at, set())
            if places is not WHOLE:
                places.add(index)
                slabs[at] = WHOLE if len(places) >= self._across else places
            self.newest = stack

    def planes(self, stack: tuple[int, int]) -> int:
        """How many planes of a stack are complete, counted from its first."""
        if stack in self.settled:
            return self.level.shape[self._z]
        slabs = self.slabs.get(stack)
        if not slabs:
            return 0
        top = max(slabs)
        # The slab on top counts once it is whole; the ones beneath it are, since
        # the writer finishes a slab before it starts the next.
        whole = top + 1 if slabs[top] is WHOLE else top
        return min(whole * self.level.files[self._z], self.level.shape[self._z])

    def complete(self, stack: tuple[int, int]) -> bool:
        """Whether every plane of a stack is on disk."""
        return self.planes(stack) >= self.level.shape[self._z] > 0

    def _index(self, file: str, any_level: bool = False) -> tuple[int, ...] | None:
        if any_level:
            _, _, name = file.partition("/")
        elif file.startswith(self._prefix):
            name = file[len(self._prefix) :]
        else:
            return None
        return self.level.index_of(name)

    def _stack(self, index: tuple[int, ...]) -> tuple[int, int]:
        return (
            index[self._t] if self._t is not None else 0,
            index[self._c] if self._c is not None else 0,
        )
