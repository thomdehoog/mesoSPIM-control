"""Many tiles: what the engine does per frame must depend on what is on screen, not on how many tiles there are.

The engine draws every tile of an acquisition as a layer of its own. As it comes, it does
a fixed amount of work for each on every frame: in the flat view it switches and clears
buffers and runs a histogram pass for a tile even when the tile is off the screen, and in
the volume view it copies the whole picture twice and clears it after every single tile.
With a hundred tiles that decides how smoothly the view moves. The build changes both
(``source/scripts/neuroglancer.mjs``, "Many tiles"); these tests count the engine's own
drawing calls to make sure it stays that way, and that the picture is still there.

Counting calls, not timing them: without a graphics card the times mean little, but the
number of times the engine switches a buffer is the same on any machine.
"""

from __future__ import annotations

import time

import pytest

from mesoSPIM.src.mesospim_viewer.demo import write_tile

from .conftest import PICTURE
from .test_panel import slow_without_a_graphics_card

# Counts the engine's calls to the graphics card from now on, by name.
COUNT = """() => {
  const gl = window.viewer.display.gl;
  if (gl.__counted) return;
  const counted = gl.__counted = {};
  for (const name of ['bindFramebuffer', 'clear', 'drawArrays', 'drawArraysInstanced']) {
    const stock = gl[name].bind(gl);
    counted[name] = 0;
    gl[name] = (...given) => { counted[name]++; return stock(...given); };
  }
}"""
# One frame drawn from scratch, as when the picture is being moved, and what it took.
ONE_FRAME = """() => {
  const viewer = window.viewer, counted = viewer.display.gl.__counted;
  for (const panel of viewer.display.panels) if (panel.sliceView) panel.sliceView.renderingStale = true;
  const before = { ...counted };
  viewer.display.draw();
  return Object.fromEntries(Object.keys(counted).map((name) => [name, counted[name] - before[name]]));
}"""


def _grid(folder, across: int, down: int) -> list:
    """Tiles of 160 um side by side, 144 um apart, so neighbours overlap by a strip."""
    return [
        write_tile(folder / f"tile_{row}{column}.ome.zarr", origin_um=(0, row * 144, column * 144), seed=row * across + column)
        for row in range(down) for column in range(across)
    ]


def _look_at_the_first_tile(page) -> None:
    """Zoom in until only the first tile (x and y from 0 to 160 um) is on screen."""
    page.evaluate(
        """() => {
          const n = window.viewer.navigationState, space = n.position.coordinateSpace.value;
          const at = Float32Array.from(n.position.value);
          for (const name of ['x', 'y']) at[space.names.indexOf(name)] = 60e-6 / space.scales[space.names.indexOf(name)];
          n.position.value = at;
          n.zoomFactor.value = n.zoomFactor.value / 6;
        }"""
    )


def test_tiles_off_the_screen_cost_the_flat_view_nothing(pages, viewers, tmp_path):
    """Nine tiles in two channels. Zoomed in on one of them, the engine sets up, clears
    and measures only what it draws. Before, every one of the eighteen layers had its
    buffers switched and cleared and a histogram pass run on every frame."""
    view = viewers(ui="simple")
    for tile in _grid(tmp_path, 3, 3):
        view.add(tile, layer="run")
    view.fit()
    page, errors = pages.open(view.start(), width=1000, height=700)
    pages.drawn(page, layers=2, sources=9)
    page.evaluate(COUNT)
    time.sleep(0.5)
    all_in_view = page.evaluate(ONE_FRAME)
    lit_all = page.evaluate(PICTURE)["lit"]
    assert lit_all > 1500

    _look_at_the_first_tile(page)
    pages.drawn(page, layers=2, sources=9)
    time.sleep(0.5)
    one_in_view = page.evaluate(ONE_FRAME)
    assert page.evaluate(PICTURE)["lit"] > 1500, "the tile looked at is drawn"
    # With all nine in view each layer is set up: two buffer switches a layer and more.
    assert all_in_view["bindFramebuffer"] >= 2 * 18
    # With one in view: that tile's two layers, and what a frame takes anyway.
    assert one_in_view["bindFramebuffer"] <= 14, f"buffers are still switched for tiles off the screen: {one_in_view}"
    assert one_in_view["clear"] <= 12, f"buffers are still cleared for tiles off the screen: {one_in_view}"
    assert one_in_view["drawArraysInstanced"] <= 6, f"histograms are still measured for tiles off the screen: {one_in_view}"
    assert one_in_view["bindFramebuffer"] * 2 < all_in_view["bindFramebuffer"]

    # Back out again: everything is drawn as before, nothing was dropped for good.
    page.click("#fit")
    pages.drawn(page, layers=2, sources=9)
    time.sleep(0.5)
    again = page.evaluate(ONE_FRAME)
    assert again["bindFramebuffer"] == all_in_view["bindFramebuffer"]
    assert page.evaluate(PICTURE)["lit"] == pytest.approx(lit_all, rel=0.05)
    assert not errors, errors


@slow_without_a_graphics_card
def test_the_volume_view_copies_the_picture_once_a_channel_not_once_a_tile(pages, viewers, tmp_path):
    """Four tiles in two channels, as a maximum projection. The tiles of a channel are
    projected into one buffer, which is copied to the picture once: two whole-picture
    passes a channel. Before, it was two a tile and channel, sixteen here, and the buffer
    was cleared after each. With a hundred tiles that was most of the frame."""
    view = viewers(ui="simple")
    for tile in _grid(tmp_path, 2, 2):
        view.add(tile, layer="run")
    view.fit()
    page, errors = pages.open(view.start(), width=800, height=560)
    pages.drawn(page, layers=2, sources=4)
    page.click('.segmented.view button[data-layout="3d"]')
    deadline = time.time() + 60
    while time.time() < deadline and page.evaluate(PICTURE)["lit"] < 300:
        time.sleep(0.5)
    assert page.evaluate(PICTURE)["lit"] >= 300, "the volume is drawn"
    time.sleep(2.0)
    page.evaluate(COUNT)
    frame = page.evaluate(ONE_FRAME)
    # Whole-picture passes are plain draws; the chunks themselves are drawn as instanced boxes.
    assert frame["drawArraysInstanced"] > 0, "the chunks of the volume are drawn"
    assert frame["drawArrays"] <= 2 * 2 + 8, f"the picture is still copied for every tile: {frame}"
    assert page.evaluate(PICTURE)["lit"] >= 300, "and the volume is still there"
    assert not errors, errors


@slow_without_a_graphics_card
def test_a_blended_volume_is_copied_once_a_channel_at_rest_and_can_still_be_pointed_at(pages, viewers, tmp_path):
    """Blend draws a volume straight into the picture, so on the move there is nothing to
    share. Once the camera rests the engine draws every tile a second time, to learn what
    lies under the pointer, and it used to copy that across the whole picture and clear it
    after each tile. The tiles of a channel now share the one copy. What the pointer rests
    on is still found: its place in micrometres and the value there."""
    view = viewers(ui="simple")
    for tile in _grid(tmp_path, 2, 2):
        view.add(tile, layer="run")
    view.fit()
    page, errors = pages.open(view.start(), width=800, height=560)
    pages.drawn(page, layers=2, sources=4)
    page.click('.segmented.view button[data-layout="3d"]')
    page.click('.card.volume button[data-mode="on"]')
    deadline = time.time() + 60
    while time.time() < deadline and page.evaluate(PICTURE)["lit"] < 300:
        time.sleep(0.5)
    assert page.evaluate(PICTURE)["lit"] >= 300, "the volume is drawn"
    time.sleep(2.0)   # the camera is at rest: the frame now includes the second drawing
    page.evaluate(COUNT)
    frame = page.evaluate(ONE_FRAME)
    assert frame["drawArraysInstanced"] > 0, "the chunks of the volume are drawn"
    assert frame["drawArrays"] <= 2 * 2 + 8, f"the picture is still copied for every tile: {frame}"
    # Pointing: sweep the pointer over the picture until it rests on something bright.
    box = page.evaluate("""() => { const r = document.querySelector('.neuroglancer-rendered-data-panel').getBoundingClientRect();
      return [r.left, r.top, r.width, r.height]; }""")
    found = None
    for step in range(1, 16):
        for row in (0.35, 0.5, 0.65):
            page.mouse.move(box[0] + box[2] * step / 16, box[1] + box[3] * row)
            time.sleep(0.25)
            seen = page.evaluate("""() => { const mouse = window.viewer.mouseState;
              return { active: mouse.active, layer: mouse.pickedRenderLayer !== null,
                       said: document.getElementById('readout')?.textContent ?? '' }; }""")
            if seen["active"] and seen["layer"]:
                found = seen
                break
        if found:
            break
    assert found is not None, "nothing of the blended volume could be pointed at"
    assert "µm" in found["said"], found
    assert not errors, errors


@slow_without_a_graphics_card
def test_an_overview_stands_in_for_the_tiles_when_zoomed_out(pages, viewers, tmp_path):
    """Sixteen tiles in two channels, all in view. Drawn as they are, a frame does a little
    work for each of them. With the overview, one small picture of the whole acquisition
    is drawn in their place while the view is zoomed out, so a frame costs the same as
    for a single tile. Zoomed in, the tiles themselves are drawn again, and on the way
    in and out the picture is never empty."""
    from .test_interaction import RECORD, START, STOP
    from .tools import until

    view = viewers(ui="simple")
    for tile in _grid(tmp_path, 4, 4):
        view.add(tile, layer="run")
    view.fit()
    page, errors = pages.open(view.start(), width=800, height=560)
    pages.drawn(page, layers=2, sources=16)
    time.sleep(1.0)
    as_tiles = page.evaluate(PICTURE)["lit"]
    assert as_tiles > 1000, "there is specimen on the picture"
    page.evaluate(COUNT)
    every_tile = page.evaluate(ONE_FRAME)

    assert view.offer_overview("run") is True
    in_force = "() => Object.values(window.mesospim.overviewRules()).filter((rule) => rule.across).length"
    assert until(lambda: page.evaluate(in_force), lambda count: count == 16, 40) == 16, "the tiles' rules were never switched on"
    time.sleep(1.0)
    one_picture = page.evaluate(ONE_FRAME)
    assert one_picture["bindFramebuffer"] <= 12 < every_tile["bindFramebuffer"], (
        f"zoomed out, a frame still does something for every tile: {one_picture}, before {every_tile}"
    )
    assert one_picture["drawArraysInstanced"] <= 4, f"histograms are still measured tile by tile: {one_picture}"
    as_overview = page.evaluate(PICTURE)["lit"]
    assert abs(as_overview - as_tiles) < 0.1 * as_tiles, f"the picture changed: {as_tiles} lit before, {as_overview} now"

    # Zooming in and out again: no frame in between is empty.
    page.evaluate(RECORD)
    box = page.evaluate("""() => { const r = document.querySelector('.neuroglancer-rendered-data-panel').getBoundingClientRect();
      return [r.left + r.width / 2, r.top + r.height / 2]; }""")
    page.mouse.move(*box)
    page.evaluate(START)
    for step in (-150,) * 6 + (150,) * 6:
        page.mouse.wheel(0, step)
        time.sleep(0.25)
    time.sleep(1.5)
    frames = page.evaluate(STOP)
    assert len(frames) >= 8, f"only {len(frames)} frames were drawn while zooming"
    assert not [frame for frame in frames if frame["lit"] == 0], "a frame was empty where tiles and overview change over"

    # Zoomed in on the first tile, the tiles themselves are drawn: the picture is that of
    # a viewer without any overview, and only the tiles in view cost anything.
    _look_at_the_first_tile(page)
    time.sleep(3.0)
    close_up = page.evaluate(ONE_FRAME)
    assert close_up["drawArrays"] > 0 and page.evaluate(PICTURE)["lit"] > 1000
    plain = viewers(ui="simple")
    for tile in sorted(tmp_path.glob("tile_*.ome.zarr")):
        plain.add(tile, layer="run")
    plain.fit()
    other, other_errors = pages.open(plain.start(), width=800, height=560)
    pages.drawn(other, layers=2, sources=16)
    _look_at_the_first_tile(other)
    time.sleep(3.0)
    assert page.evaluate(PICTURE)["lit"] == other.evaluate(PICTURE)["lit"], "zoomed in, the picture is the tiles' own"
    assert not errors and not other_errors, (errors, other_errors)


@slow_without_a_graphics_card
def test_the_overview_stands_in_for_the_tiles_in_the_volume_view_too(pages, viewers, tmp_path):
    from .tools import until

    view = viewers(ui="simple")
    for tile in _grid(tmp_path, 4, 4):
        view.add(tile, layer="run")
    view.fit()
    page, errors = pages.open(view.start(), width=800, height=560)
    pages.drawn(page, layers=2, sources=16)
    assert view.offer_overview("run") is True
    in_force = "() => Object.values(window.mesospim.overviewRules()).filter((rule) => rule.across).length"
    assert until(lambda: page.evaluate(in_force), lambda count: count == 16, 40) == 16
    page.click('.segmented.view button[data-layout="3d"]')
    assert until(lambda: page.evaluate(PICTURE)["lit"], lambda lit: lit >= 300, 60) >= 300, "the volume is drawn"
    time.sleep(2.0)
    page.evaluate(COUNT)
    frame = page.evaluate(ONE_FRAME)
    # Every tile used to be marched through on its own: thirty-two volumes. One per channel now.
    assert 0 < frame["drawArraysInstanced"] <= 40, f"the volume is still drawn tile by tile: {frame}"
    assert page.evaluate(PICTURE)["lit"] >= 300
    assert not errors, errors
