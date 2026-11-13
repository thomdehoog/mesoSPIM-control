"""The viewer under an operator's hands: clicked around in at random, and watched frame by frame.

Two kinds of test that the others do not have.

*Clicking around.* A session with several acquisitions side by side, and a long run of
actions picked at random: names, eyes and maps in the list, the picture pointed at,
clicked, dragged and zoomed, 2D and 3D, the sliders, the channels, the window resized.
After every action the page must still answer, must have raised no error, and what it
shows must agree with itself (one acquisition selected, the channel rows its own, Fit
offered exactly while the view is the operator's, and so on). The run is repeated with
several seeds; a seed that fails is printed with the actions that led there.

*Frame by frame.* A picture that goes black for a frame between two good ones is a
flicker the eye catches and a test that looks every half second does not. Here the page
itself notes every frame the engine draws, with the time and how much of the picture is
lit, while the view is switched, moved and zoomed. No frame in between may be black, and
the longest pause between two frames is reported.

The frames are drawn in software on a machine without a graphics card, so the pauses
measured are longer than on the microscope's PC. What carries over is whether a frame
is black, and whether the page's own work holds the picture up.
"""

from __future__ import annotations

import random
import threading
import time

import pytest

from mesoSPIM.src.mesospim_viewer import Library
from mesoSPIM.src.mesospim_viewer.demo import write_tile

from .conftest import PICTURE, WHERE
from .stores import an_acquisition
from .tools import until

# Notes every frame the engine draws: when, in which layout, and how many of a few dozen
# lines across the picture are lit. Reading whole pictures would slow the very thing watched.
RECORD = """() => {
  if (window.__frames) return;
  const rec = window.__frames = { on: false, list: [] };
  const display = window.viewer.display, gl = display.gl, canvas = display.canvas;
  display.updateFinished.add(() => {
    if (!rec.on) return;
    const panel = document.querySelector('.neuroglancer-rendered-data-panel');
    if (!panel) return;
    const box = panel.getBoundingClientRect(), whole = canvas.getBoundingClientRect();
    const sx = canvas.width / whole.width, sy = canvas.height / whole.height;
    const x = Math.round((box.left - whole.left) * sx), w = Math.max(1, Math.round(box.width * sx));
    const h = Math.round(box.height * sy), bottom = canvas.height - Math.round((box.top - whole.top) * sy) - h;
    const row = new Uint8Array(w * 4);
    let lit = 0;
    const LINES = 32;
    for (let k = 0; k < LINES; ++k) {
      gl.readPixels(x, bottom + Math.floor(h * (k + 0.5) / LINES), w, 1, gl.RGBA, gl.UNSIGNED_BYTE, row);
      for (let i = 0; i < row.length; i += 4) if (Math.max(row[i], row[i + 1], row[i + 2]) > 48) lit++;
    }
    rec.list.push({ t: performance.now(), lit, layout: window.mesospim.layout() });
  });
}"""
START = "() => { window.__frames.list = []; window.__frames.on = true; }"
STOP = "() => { window.__frames.on = false; return window.__frames.list; }"

# What the panel and the picture show, gathered in one go.
STATE = """() => {
  const q = (s) => [...document.querySelectorAll(s)];
  const fit = document.querySelector('#fit');
  return {
    selected: q('.acquisition.selected').map(a => a.dataset.name),
    open: q('.acquisition.open').map(a => a.dataset.name),
    maps: q('.tile-map').length,
    framed: q('.tile-map .position.framed').length,
    listed: q('.acquisition').map(a => a.dataset.name),
    shown: q('.acquisition.shown').map(a => a.dataset.name),
    channels: q('.card.channels .channel').filter(r => r.offsetParent !== null).map(r => r.dataset.layer),
    layout: window.mesospim.layout(),
    panel: document.querySelector('.mesospim-panel') !== null,
    volumeCard: !(document.querySelector('.card.volume')?.hidden ?? true),
    fitOffered: !!fit && !fit.disabled, framing: window.mesospim.framing.value,
    layers: window.viewer.layerManager.managedLayers.length,
    sideways: document.querySelector('.panel-body') ? document.querySelector('.panel-body').scrollWidth - document.querySelector('.panel-body').clientWidth : 0,
  };
}"""
ALIVE = """() => new Promise(done => { let n = 0; const t0 = performance.now();
  const tick = () => { n++; performance.now() - t0 < 600 ? requestAnimationFrame(tick) : done(n); }; requestAnimationFrame(tick); })"""
# The middle of an element on the page, or null when it is not there or not to be seen.
MIDDLE = """([selector, which]) => {
  const all = [...document.querySelectorAll(selector)].filter(e => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && !e.disabled;
  });
  if (!all.length) return null;
  const r = all[Math.floor(which * all.length) % all.length].getBoundingClientRect();
  return [r.left + r.width / 2, r.top + r.height / 2];
}"""


class Session:
    """Three acquisitions side by side, a library that looks at them twice a second as the
    window's timer does, and the page."""

    def __init__(self, pages, viewers, tmp_path, *, width=1100, height=700) -> None:
        data = tmp_path / "data"
        # "wolverine": two by two tiles. Beside it "overview": three in a row. Below, a
        # time lapse of two tiles with three time points.
        plans = {
            "wolverine": ([(0, 0), (0, 144), (144, 0), (144, 144)], 1),
            "overview": [(0, 600 + 144 * i) for i in range(3)],
            "timelapse": ([(500, 0), (500, 144)], 3),
        }
        for seed, (name, plan) in enumerate(plans.items()):
            places, timepoints = plan if isinstance(plan, tuple) else (plan, 1)
            time.sleep(0.05)
            acquisition = an_acquisition(data, name)
            for tile, (y, x) in enumerate(places):
                write_tile(acquisition / f"Mag1_Tile{tile}_Sh0_Rot0.ome.zarr", origin_um=(0, y, x),
                           seed=seed * 10 + tile, timepoints=timepoints)
        self.view = viewers(ui="simple")
        self.library = Library(self.view)
        self.library.watch(data)
        for name in plans:
            self.library.show(name)
        self.mishaps: list[str] = []
        self.over = threading.Event()

        def look() -> None:
            while not self.over.is_set():
                try:
                    self.library.poll()
                except Exception as mishap:  # noqa: BLE001 -- told to the test
                    self.mishaps.append(repr(mishap))
                self.over.wait(0.5)

        threading.Thread(target=look, daemon=True).start()
        self.view.fit()
        self.page, self.errors = pages.open(self.view.start(), width=width, height=height)
        pages.drawn(self.page, layers=6, timeout_s=60)
        self.page.evaluate(RECORD)

    def close(self) -> None:
        self.over.set()

    def picture(self) -> dict:
        return self.page.evaluate(
            "() => { const r = document.querySelector('.neuroglancer-rendered-data-panel').getBoundingClientRect();"
            " return { x: r.left, y: r.top, width: r.width, height: r.height }; }"
        )

    def press(self, selector: str, which: float = 0.0) -> bool:
        """Click the middle of an element, if it is there: without waiting for one that is not."""
        at = self.page.evaluate(MIDDLE, [selector, which])
        if at is None:
            return False
        self.page.mouse.click(at[0], at[1])
        return True


@pytest.fixture
def session(pages, viewers, tmp_path):
    held = Session(pages, viewers, tmp_path)
    yield held
    held.close()


# -- clicking around --------------------------------------------------------------------------------


def _actions(session: Session, rng: random.Random) -> dict:
    page = session.page

    def on_picture() -> tuple[float, float]:
        box = session.picture()
        return (box["x"] + box["width"] * rng.uniform(0.08, 0.92), box["y"] + box["height"] * rng.uniform(0.12, 0.85))

    def point() -> None:
        page.mouse.move(*on_picture(), steps=3)

    def click_picture() -> None:
        page.mouse.click(*on_picture())

    def drag() -> None:
        x, y = on_picture()
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + rng.uniform(-150, 150), y + rng.uniform(-100, 100), steps=4)
        page.mouse.up()

    def zoom() -> None:
        page.mouse.move(*on_picture())
        page.mouse.wheel(0, rng.choice([-400, -200, 200, 400]))

    def step_planes() -> None:
        page.mouse.move(*on_picture())
        page.keyboard.down("Shift")
        page.mouse.wheel(0, rng.choice([-100, 100]))
        page.keyboard.up("Shift")

    def slider() -> None:
        page.evaluate(
            """([which, part]) => {
              const inputs = [...document.querySelectorAll('.axis-slider:not([hidden]) input')];
              if (!inputs.length) return;
              const input = inputs[Math.floor(which * inputs.length) % inputs.length];
              const low = Number(input.min), high = Number(input.max);
              input.value = String(Math.round(low + part * (high - low)));
              input.dispatchEvent(new Event('input', { bubbles: true }));
              input.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            [rng.random(), rng.random()],
        )

    def resize() -> None:
        page.set_viewport_size({"width": rng.choice([820, 960, 1100, 1280]), "height": rng.choice([600, 700, 780])})

    def hover_cell() -> None:
        at = page.evaluate(MIDDLE, [".tile-map .position", rng.random()])
        if at is not None:
            page.mouse.move(at[0], at[1], steps=2)

    return {
        "name": lambda: session.press(".acquisition .name", rng.random()),
        "eye": lambda: session.press(".acquisition .eye", rng.random()),
        "fold": lambda: session.press(".acquisition .fold", rng.random()),
        "map cell": lambda: session.press(".tile-map .position", rng.random()),
        "hover cell": hover_cell,
        "point": point,
        "click picture": click_picture,
        "drag": drag,
        "zoom": zoom,
        "step planes": step_planes,
        "slider": slider,
        "2D": lambda: session.press('.segmented.view button[data-layout="xy"]'),
        "3D": lambda: session.press('.segmented.view button[data-layout="3d"]'),
        "fit": lambda: session.press("#fit"),
        "channel eye": lambda: session.press(".card.channels .channel .eye", rng.random()),
        "auto": lambda: session.press(".card.channels .channel .auto", rng.random()),
        "channel name": lambda: session.press(".card.channels .channel .label", rng.random()),
        "projection": lambda: session.press(".card.volume .segmented button[data-mode]", rng.random()),
        "look from": lambda: session.press(".card.volume .segmented button[data-look]", rng.random()),
        "help": lambda: session.press("#help"),
        "fold panel": lambda: session.press(".panel-head .fold") or session.press("#fold"),
        "resize": resize,
    }


def _check(session: Session, done: list[str]) -> None:
    """What must hold whatever was clicked."""
    said = f"after {done[-12:]}"
    assert not session.errors, f"the page raised {session.errors[:2]} {said}"
    assert not session.mishaps, f"looking at the disk failed with {session.mishaps[:2]} {said}"
    state = session.page.evaluate(STATE)
    assert len(state["selected"]) <= 1 and len(state["open"]) <= 1, f"{state} {said}"
    assert state["maps"] <= 1 and state["framed"] <= 1, f"{state} {said}"
    assert state["layout"] in ("xy", "3d"), f"{state} {said}"
    if state["panel"]:  # folded away, the panel shows nothing; what it holds is checked when it is back
        assert sorted(state["listed"]) == ["overview", "timelapse", "wolverine"], f"an acquisition went missing: {state} {said}"
    if state["selected"]:
        assert all(layer.startswith(state["selected"][0]) for layer in state["channels"]), (
            f"the channel rows are not the selected acquisition's: {state} {said}"
        )
    assert state["sideways"] <= 1, f"the panel scrolls sideways by {state['sideways']} px {said}"


@pytest.mark.timeout(900)
@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_clicking_around_at_random_never_breaks_the_viewer(session, seed):
    rng = random.Random(seed)
    actions = _actions(session, rng)
    # Back to the flat view more often than into the volume: without a graphics card a
    # frame of the volume takes seconds to draw, and the run would be spent waiting.
    names = list(actions) + ["2D", "2D", "2D"]
    done: list[str] = []
    for _ in range(55):
        name = rng.choice(names)
        done.append(name)
        actions[name]()
        time.sleep(rng.choice([0.02, 0.1, 0.3]))
        _check(session, done)
    # What settles after the last click must hold too.
    time.sleep(1.5)
    _check(session, done)
    state = session.page.evaluate(STATE)
    if state["panel"]:
        assert state["volumeCard"] == (state["layout"] == "3d"), f"{state} after {done[-12:]}"
    assert state["fitOffered"] == (not state["framing"]), f"Fit and the view disagree: {state} after {done[-12:]}"

    # And the viewer is still a viewer: everything shown again, flat, fitted, and drawn.
    page = session.page
    page.set_viewport_size({"width": 1100, "height": 700})
    if page.evaluate("() => document.querySelector('.mesospim-panel') === null"):
        session.press("#fold")
    for name in ("wolverine", "overview", "timelapse"):
        session.library.show(name)
    session.press('.segmented.view button[data-layout="xy"]')
    page.evaluate("""() => { for (const eye of document.querySelectorAll('.card.channels .channel .eye.off')) eye.click(); }""")
    time.sleep(0.5)
    session.press("#fit")
    assert page.evaluate(ALIVE) >= 3, "the page no longer draws frames"
    lit = until(lambda: page.evaluate(PICTURE)["lit"], lambda seen: seen > 1500, timeout_s=40, every_s=0.5)
    assert lit > 1500, f"after {done[-12:]} the picture stays dark ({lit} lit pixels)"
    assert until(lambda: page.evaluate(STATE)["shown"], lambda shown: len(shown) == 3, 10) is not None
    assert sorted(page.evaluate(STATE)["listed"]) == ["overview", "timelapse", "wolverine"]
    _check(session, done)


# -- frame by frame ---------------------------------------------------------------------------------


def _recorded(page, act, settle_s: float = 1.5) -> list[dict]:
    page.evaluate(START)
    act()
    time.sleep(settle_s)
    return page.evaluate(STOP)


def _pauses(frames: list[dict]) -> float:
    return max((b["t"] - a["t"] for a, b in zip(frames, frames[1:])), default=0.0)


def test_moving_and_zooming_the_flat_picture_draws_no_black_frame(session):
    """Tiles that are in memory stay on the picture while it is moved and zoomed: no
    frame in between is black. The coarser copy of a tile is drawn until the finer one
    has arrived, so zooming in does not blank it either."""
    page = session.page
    box = session.picture()
    middle = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    before = page.evaluate(PICTURE)["lit"]
    assert before > 1500

    def act() -> None:
        page.mouse.move(*middle)
        for _ in range(4):
            page.mouse.wheel(0, -150)
            time.sleep(0.12)
        page.mouse.down()
        for step in range(8):
            page.mouse.move(middle[0] + 12 * step, middle[1] + 6 * step)
            time.sleep(0.03)
        page.mouse.up()
        for _ in range(4):
            page.mouse.wheel(0, 150)
            time.sleep(0.12)

    frames = _recorded(page, act)
    assert len(frames) >= 12, f"only {len(frames)} frames were drawn while the picture was moved and zoomed"
    black = [frame for frame in frames if frame["lit"] == 0]
    assert not black, f"{len(black)} of {len(frames)} frames were black"
    print(f"\n  moved and zoomed: {len(frames)} frames, longest pause {_pauses(frames):.0f} ms")
    assert not session.errors, session.errors


def test_switching_to_the_volume_and_back_shows_the_flat_picture_at_once(session):
    """Back from 3D, the plane that was shown is in memory still: the first flat frame
    drawn has the picture on it, and none after it is black."""
    page = session.page
    session.press('.segmented.view button[data-layout="3d"]')
    until(lambda: page.evaluate(PICTURE)["lit"], lambda seen: seen > 300, timeout_s=40, every_s=0.5)
    time.sleep(1.0)

    began = page.evaluate("() => performance.now()")
    frames = _recorded(page, lambda: session.press('.segmented.view button[data-layout="xy"]'), settle_s=2.0)
    flat = [frame for frame in frames if frame["layout"] == "xy"]
    assert flat, "no flat frame was drawn after the switch"
    assert flat[0]["lit"] > 0, "the first frame back in 2D was black"
    assert not [frame for frame in flat if frame["lit"] == 0], "a black frame after the switch back to 2D"
    print(f"\n  3D to 2D: first flat frame {flat[0]['t'] - began:.0f} ms after the click, lit at once")

    # And into 3D again, now that the volume has been drawn once: its data is in memory.
    began = page.evaluate("() => performance.now()")
    frames = _recorded(page, lambda: session.press('.segmented.view button[data-layout="3d"]'), settle_s=3.0)
    volume = [frame for frame in frames if frame["layout"] == "3d"]
    assert volume, "no frame of the volume was drawn after the switch"
    first_lit = next((frame for frame in volume if frame["lit"] > 0), None)
    assert first_lit is not None, "the volume never appeared"
    print(f"  2D to 3D: first frame {volume[0]['t'] - began:.0f} ms after the click, "
          f"the volume on it {first_lit['t'] - began:.0f} ms after the click")
    assert first_lit["t"] - began < 3000, "the volume took more than three seconds to come back"
    after = volume[volume.index(first_lit):]
    assert not [frame for frame in after if frame["lit"] == 0], "the volume went black again after it had appeared"
    assert not session.errors, session.errors


def test_zooming_the_volume_keeps_it_on_the_picture(session):
    """Zoomed in and out in 3D, the volume is drawn coarser while it moves and sharp once
    it stops (panel.js). It must not vanish in between."""
    page = session.page
    session.press('.segmented.view button[data-layout="3d"]')
    until(lambda: page.evaluate(PICTURE)["lit"], lambda seen: seen > 300, timeout_s=40, every_s=0.5)
    time.sleep(1.5)
    box = session.picture()
    middle = (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    def act() -> None:
        page.mouse.move(*middle)
        for delta in (-200, -200, -200, 200, 200, 200):
            page.mouse.wheel(0, delta)
            time.sleep(0.15)

    frames = _recorded(page, act, settle_s=2.5)
    assert len(frames) >= 6, f"only {len(frames)} frames were drawn while the volume was zoomed"
    black = [frame for frame in frames if frame["lit"] == 0]
    assert not black, f"{len(black)} of {len(frames)} frames of the volume were black while it was zoomed"
    print(f"\n  volume zoomed: {len(frames)} frames, longest pause {_pauses(frames):.0f} ms")
    assert not session.errors, session.errors


def test_going_from_tile_to_tile_on_the_map_keeps_a_picture_on_screen(session):
    """A click on a cell of the map goes to that tile. On the way the picture is never
    black: the coarse copy of the tile, drawn in the overview, is there to start from."""
    page = session.page
    session.press('.acquisition[data-name="wolverine"] .name')
    until(lambda: page.evaluate(STATE)["maps"], lambda maps: maps == 1)
    time.sleep(1.0)

    def act() -> None:
        for which in (0.1, 0.6, 0.35, 0.9):
            session.press(".tile-map .position", which)
            time.sleep(0.5)

    frames = _recorded(page, act, settle_s=2.0)
    assert len(frames) >= 4
    black = [frame for frame in frames if frame["lit"] == 0]
    assert not black, f"{len(black)} of {len(frames)} frames were black while going from tile to tile"
    assert page.evaluate(STATE)["framed"] == 1, "the tile gone to is marked in the map"
    print(f"\n  tile to tile: {len(frames)} frames, longest pause {_pauses(frames):.0f} ms")
    assert not session.errors, session.errors


def test_hiding_and_showing_a_channel_and_pointing_at_tiles_does_not_flicker(session):
    """One channel hidden and shown again, and the pointer moved across the tiles so that
    their boxes come and go: the other channel stays on the picture throughout."""
    page = session.page
    session.press('.acquisition[data-name="wolverine"] .name')
    until(lambda: page.evaluate(STATE)["channels"], lambda rows: len(rows) == 2)
    session.press("#fit")
    time.sleep(1.0)
    box = session.picture()

    def act() -> None:
        session.press(".card.channels .channel .eye", 0.0)
        time.sleep(0.3)
        for step in range(12):
            page.mouse.move(box["x"] + box["width"] * (0.15 + 0.06 * step), box["y"] + box["height"] * 0.4)
            time.sleep(0.05)
        session.press(".card.channels .channel .eye", 0.0)
        time.sleep(0.3)

    frames = _recorded(page, act)
    black = [frame for frame in frames if frame["lit"] == 0]
    assert not black, f"{len(black)} of {len(frames)} frames were black"
    assert not session.errors, session.errors
