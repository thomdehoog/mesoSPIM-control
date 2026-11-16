"""How much memory the viewer takes when nobody tells it (``memory.py``).

The rule is deliberately modest: half of the graphics card the page really draws on
and an eighth of the computer's memory, with a ceiling on both, and the engine's own
limits whenever something cannot be found out. Python only; the page's side is in
``test_page.py``.
"""

from __future__ import annotations

import sys
import types

import pytest

from mesoSPIM.src.mesospim_viewer import memory
from mesoSPIM.src.mesospim_viewer.viewer import Viewer


@pytest.mark.parametrize(
    "total_mb, expected",
    [
        (8_000, None),      # a small computer: the engine's own 2000 MB
        (16_000, None),     # an eighth is 2000 MB, no more than the engine takes anyway
        (32_000, 4000),
        (64_000, 8000),
        (512_000, 8000),    # never more than 8000 MB without being asked
    ],
)
def test_an_eighth_of_the_computers_memory_and_never_more_than_8000_mb(total_mb, expected):
    assert memory.system_mb(total_mb) == expected


def test_the_computers_memory_is_looked_up_when_it_is_not_given():
    found = memory.system_mb()
    assert found is None or memory.ENGINE_SYSTEM_MB < found <= memory.MOST_SYSTEM_MB


CARDS = [("Intel(R) UHD Graphics 770", 1024), ("NVIDIA GeForce RTX 3080", 10240), ("NVIDIA RTX A2000", 6144)]


@pytest.mark.parametrize(
    "renderer, expected",
    [
        # As Chromium 83, the one inside mesoSPIM's window, names a card on Windows...
        ("ANGLE (NVIDIA GeForce RTX 3080 Direct3D11 vs_5_0 ps_5_0)", ("NVIDIA GeForce RTX 3080", 4000)),
        # ...and as newer ones do, on Windows and on Linux.
        ("ANGLE (NVIDIA, NVIDIA RTX A2000 Direct3D11 vs_5_0 ps_5_0, D3D11)", ("NVIDIA RTX A2000", 3000)),
        ("ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 3080/PCIe/SSE2, OpenGL 4.5.0)", ("NVIDIA GeForce RTX 3080", 4000)),
        # The page draws on the small card of a computer that also has a large one:
        # the large one's memory is not the page's to fill.
        ("ANGLE (Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0)", None),
        # No graphics card at all, or one this computer's list does not have.
        ("Google SwiftShader", None),
        ("ANGLE (AMD Radeon RX 6800 Direct3D11 vs_5_0 ps_5_0)", None),
        ("", None),
    ],
)
def test_half_of_the_card_the_page_draws_on_and_no_other(renderer, expected):
    assert memory.card_mb(renderer, CARDS) == expected


def test_of_two_cards_with_one_name_the_smaller_counts():
    assert memory.card_mb("ANGLE (Twin Direct3D11)", [("Twin", 16000), ("Twin", 6000)]) == ("Twin", 3000)


def test_what_the_page_says_may_be_anything_at_all():
    assert memory.card_mb(None, CARDS) is None
    assert memory.card_mb(42, CARDS) is None


def test_the_cards_are_read_from_the_windows_registry(monkeypatch):
    """On Windows the cards and their memory are in the registry, each under a key of
    its own. Acted out here with a registry of two cards and one key that is no card."""
    adapters = {
        "0000": {"DriverDesc": "NVIDIA GeForce RTX 3080", "HardwareInformation.qwMemorySize": 10 * 1024**3},
        # An older driver gives the size as four bytes rather than as a number.
        "0001": {"DriverDesc": "Intel(R) UHD Graphics 770", "HardwareInformation.MemorySize": (1024**3).to_bytes(4, "little")},
        "0002": {"DriverDesc": "Microsoft Basic Display Adapter"},   # says nothing about memory
        "Properties": None,                                           # may not be opened
    }

    class Key:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def open_key(parent, name):
        if parent == "HKLM":
            return Key(adapters)
        if parent.values[name] is None:
            raise PermissionError(name)
        return Key(parent.values[name])

    def query_value(key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], 0

    registry = types.SimpleNamespace(
        HKEY_LOCAL_MACHINE="HKLM",
        OpenKey=open_key,
        QueryInfoKey=lambda key: (len(key.values), 0, 0),
        EnumKey=lambda key, index: list(key.values)[index],
        QueryValueEx=query_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    assert memory._cards_in_the_registry() == [("NVIDIA GeForce RTX 3080", 10737), ("Intel(R) UHD Graphics 770", 1073)]


def test_a_computer_that_cannot_tell_its_cards_has_none(monkeypatch):
    """Whatever goes wrong while looking, the viewer opens: it keeps the engine's limit."""
    def fails():
        raise OSError("no such tool")

    monkeypatch.setattr(memory, "_cards", None)
    monkeypatch.setattr(memory, "_cards_in_the_registry", fails)
    monkeypatch.setattr(memory, "_cards_by_nvidia_smi", fails)
    assert memory.cards() == []
    assert memory.card_mb("ANGLE (NVIDIA GeForce RTX 3080 Direct3D11)") is None


def test_a_viewer_asked_to_choose_takes_the_computers_memory_and_leaves_the_card_to_the_page(monkeypatch):
    from mesoSPIM.src.mesospim_viewer import viewer as module

    monkeypatch.setattr(module, "system_mb", lambda: 6000)
    view = Viewer(gpu_memory_mb="auto", system_memory_mb="auto")
    assert view._ui["memory"] == {"gpu": "auto", "system": 6_000_000_000}
    monkeypatch.setattr(module, "system_mb", lambda: None)
    assert Viewer(system_memory_mb="auto")._ui.get("memory") is None, "nothing found out: the engine's own limit"
    with pytest.raises(ValueError, match="positive"):
        Viewer(gpu_memory_mb="plenty")


@pytest.mark.parametrize(
    "system, expected",
    [
        (8000, {"gpu": 4_000_000_000}),   # room for all of the card's share
        (5000, {"gpu": 2_500_000_000}),   # the card's share is held to half of the memory's
        (None, {}),                       # the engine's 2000 MB of memory: its 1000 MB on the card stay
    ],
)
def test_the_cards_share_is_never_more_than_half_of_the_memorys(monkeypatch, system, expected):
    """The engine counts image data on the graphics card against the limit for the
    computer's memory as well, so a larger share of the card could never be filled."""
    from mesoSPIM.src.mesospim_viewer import viewer as module

    monkeypatch.setattr(module, "card_mb", lambda renderer: ("A large card", 4000))
    view = Viewer(gpu_memory_mb="auto", system_memory_mb=system)
    assert view._card("ANGLE (A large card Direct3D11)") == expected
