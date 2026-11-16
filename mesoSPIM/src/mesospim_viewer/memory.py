"""How much image data the viewer may keep at hand on this computer.

The page keeps the chunks it draws on the graphics card, and the ones it drew a
moment ago in the computer's memory, so that going back to them costs nothing. The
engine's own limits for the two, 1000 MB and 2000 MB, are chosen to be safe on any
computer. A microscope's PC usually has far more room than that, and with more room
a large acquisition is drawn in finer detail (above all as a volume) and less is
read again from disk when the operator moves around.

This module looks at what the computer has and suggests limits that leave most of
it to everything else: the camera, the writers, the rest of mesoSPIM. Nothing here
is ever in the way of opening the viewer. Whatever cannot be found out is simply
left at the engine's own limit.

- The computer's memory: an eighth of it, at most 8000 MB (:func:`system_mb`).
- The graphics card: half of its memory, at most 4000 MB (:func:`card_mb`). A
  computer can have more than one graphics card (a laptop often has a small one on
  the processor and a large one beside it), so the page says which card it really
  draws on, and only that card's memory counts. If the page draws without a
  graphics card, or on one this module does not know, the engine's limit stays.
  The viewer also keeps the card's share to half of the limit for the computer's
  memory, because the engine counts what is on the card against that limit too.

The microscope's configuration can set either limit itself (``data_viewer``); what
it says is taken as it is and nothing here is asked.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

ENGINE_GPU_MB = 1000
ENGINE_SYSTEM_MB = 2000
# The most that is taken without being asked for.
MOST_GPU_MB = 4000
MOST_SYSTEM_MB = 8000
# Of the graphics card and of the computer's memory, this share is the viewer's.
GPU_SHARE = 0.5
SYSTEM_SHARE = 0.125
# Windows lists its display adapters, each with its name and memory, under this key.
ADAPTERS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"

_lock = threading.Lock()
_cards: list[tuple[str, int]] | None = None


def _round_down(megabytes: float, step: int = 500) -> int:
    return int(megabytes // step * step)


def system_mb(total_mb: float | None = None) -> int | None:
    """How many megabytes of the computer's memory the viewer may keep image data in,
    or None to leave the engine's own limit. ``total_mb`` is how much memory the
    computer has; left out, it is looked up."""
    if total_mb is None:
        try:
            import psutil

            total_mb = psutil.virtual_memory().total / 1e6
        except Exception:  # noqa: BLE001 -- psutil is missing or cannot tell: keep the engine's limit
            return None
    suggested = _round_down(min(total_mb * SYSTEM_SHARE, MOST_SYSTEM_MB))
    return suggested if suggested > ENGINE_SYSTEM_MB else None


def _cards_in_the_registry() -> list[tuple[str, int]]:
    """The display adapters Windows knows, as (name, megabytes of their own memory)."""
    import winreg

    found = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ADAPTERS_KEY) as adapters:
        for index in range(winreg.QueryInfoKey(adapters)[0]):
            try:
                with winreg.OpenKey(adapters, winreg.EnumKey(adapters, index)) as adapter:
                    name = winreg.QueryValueEx(adapter, "DriverDesc")[0]
                    size = None
                    # The first is a 64-bit number and right for any card. The second
                    # is older and cannot say more than 4 GB, so it is only a fallback.
                    for value in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                        try:
                            size = winreg.QueryValueEx(adapter, value)[0]
                            break
                        except OSError:
                            continue
                    if isinstance(size, bytes):
                        size = int.from_bytes(size, "little")
                    if isinstance(name, str) and isinstance(size, int) and size > 0:
                        found.append((name, int(size / 1e6)))
            except OSError:
                continue  # a key that is not an adapter, or one that may not be read
    return found


def _cards_by_nvidia_smi() -> list[tuple[str, int]]:
    """The NVIDIA cards, as their driver's own tool lists them."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console window flashing up on Windows
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=5, creationflags=flags,
    ).stdout
    found = []
    for line in out.strip().splitlines():
        name, _, megabytes = line.rpartition(",")
        if name.strip() and megabytes.strip().isdigit():
            found.append((name.strip(), int(int(megabytes) * 1.048576)))  # MiB, as the tool counts
    return found


def cards() -> list[tuple[str, int]]:
    """The computer's graphics cards as (name, megabytes of memory). Looked up once;
    an empty list when it cannot be found out."""
    global _cards
    with _lock:
        if _cards is None:
            found: list[tuple[str, int]] = []
            if sys.platform == "win32":
                try:
                    found = _cards_in_the_registry()
                except Exception as error:  # noqa: BLE001
                    logger.debug("the graphics cards could not be read from the registry: %s", error)
            if not found:
                try:
                    found = _cards_by_nvidia_smi()
                except Exception as error:  # noqa: BLE001 -- no NVIDIA card, or its tool is not installed
                    logger.debug("nvidia-smi did not answer: %s", error)
            _cards = found
        return list(_cards)


def card_mb(renderer: str, known: list[tuple[str, int]] | None = None) -> tuple[str, int] | None:
    """Which graphics card the page draws on and how many megabytes of it the viewer
    may fill, as (name, megabytes); None to leave the engine's own limit.

    ``renderer`` is what the page's graphics say about themselves, for example
    ``ANGLE (NVIDIA GeForce RTX 3080 Direct3D11 vs_5_0 ps_5_0)``. A card counts only if
    its name is in there, so a second card the page does not draw on is never taken for
    the one it does. ``known`` stands in for the computer's cards (for tests)."""
    if not isinstance(renderer, str) or not renderer:
        return None
    said = renderer.lower()
    matching = [(name, megabytes) for name, megabytes in (cards() if known is None else known) if name.lower() in said]
    if not matching:
        return None
    # Two cards of one name: the smaller, to be on the safe side.
    name, megabytes = min(matching, key=lambda card: card[1])
    suggested = _round_down(min(megabytes * GPU_SHARE, MOST_GPU_MB))
    return (name, suggested) if suggested > ENGINE_GPU_MB else None
