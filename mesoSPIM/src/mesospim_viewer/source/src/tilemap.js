/**
 * The tiles of an acquisition as a small map, and the link between that map and
 * the picture.
 *
 * An acquisition can have a hundred positions or more. As a list of names
 * ("Tile 73") they say nothing about where a tile lies, and a hundred of them
 * do not fit a panel. Drawn from their stage positions they are a map of the
 * specimen: every tile a cell where it really is, the part of the specimen that
 * is on screen framed on it, and during a run the cells filling in as the
 * microscope goes.
 *
 * The map and the picture are two views of the same tiles, so they answer each
 * other. Pointing at a cell draws a box around that tile in the picture, and
 * pointing at a tile in the picture lights up its cell. A click on a cell goes
 * to the tile; a click on a tile in the picture selects it, and with it its
 * acquisition, without moving the view. Dragging the picture still moves it:
 * only a press and release in one place is a click.
 *
 * Nothing here knows about acquisitions as such. The panel (panel.js) hands
 * over the positions and is told what was pointed at or clicked.
 *
 * Written for Qt WebEngine 5.15 (Chromium 83) as much as for a current browser.
 */
import { boxOnScreen, layoutOf, pointerInMicrometres, viewInMicrometres } from "./camera.js";

const SVG = "http://www.w3.org/2000/svg";
// How long the pointer rests on the picture before the tile under it is boxed:
// long enough that moving across the picture does not make boxes flicker.
const REST_MS = 120;
// A press and a release this close together, in pixels and in time, are a click.
const CLICK_PIXELS = 4;
const CLICK_MS = 500;

function svg(tag, attributes = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
  return node;
}

const hasPlace = (position) => Array.isArray(position.um?.x) && Array.isArray(position.um?.y);

/** What a position's state reads as: "acquired", "acquiring", "next", "1 of 2 channels". */
export function stateWords(position, channels) {
  if (!position.state) return "";
  const words = {
    done: "acquired", now: "acquiring", next: "next",
    part: `${position.acquired ?? 0} of ${channels || "?"} channels`,
  };
  return words[position.state] ?? position.state;
}

/**
 * Makes the map of one acquisition's positions, anew whenever the panel draws
 * its list, and keeps the one that is on the page up to date: which cell is
 * pointed at, and which part of the specimen the picture shows.
 */
export function tileMapper(viewer) {
  let current = null; // the map on the page: { cells, inView, bounds, caption, say }
  let pending = 0;

  const frameView = () => {
    pending = 0;
    if (current === null || !current.root.isConnected) return;
    const shown = viewInMicrometres(viewer);
    const { inView, bounds } = current;
    // No frame while the picture shows everything: it would only trace the map's edge.
    const all = shown !== null && shown.x[0] <= bounds.x[0] && shown.x[1] >= bounds.x[1]
      && shown.y[0] <= bounds.y[0] && shown.y[1] >= bounds.y[1];
    if (shown === null || all) {
      inView.setAttribute("visibility", "hidden");
      return;
    }
    // Kept within the map: a view that reaches past the acquisition on some sides would
    // otherwise leave a single line of its frame on the map, which reads as nothing.
    const { margin } = current;
    const x0 = Math.max(shown.x[0], bounds.x[0] - margin / 2);
    const x1 = Math.min(shown.x[1], bounds.x[1] + margin / 2);
    const y0 = Math.max(shown.y[0], bounds.y[0] - margin / 2);
    const y1 = Math.min(shown.y[1], bounds.y[1] + margin / 2);
    if (!(x1 > x0) || !(y1 > y0)) {
      inView.setAttribute("visibility", "hidden"); // the view is somewhere else altogether
      return;
    }
    inView.setAttribute("visibility", "visible");
    inView.setAttribute("x", x0);
    inView.setAttribute("y", y0);
    inView.setAttribute("width", x1 - x0);
    inView.setAttribute("height", y1 - y0);
  };
  const later = () => {
    if (!pending) pending = requestAnimationFrame(frameView);
  };
  viewer.navigationState.changed.add(later);
  viewer.layout.changed.add(later);
  window.addEventListener("resize", later);

  return {
    /**
     * The map of `positions` (each `{ name, um, state }`), or null when none of them has
     * a place yet. `marked` is the name of the position that is selected; `onHover` and
     * `onPick` are told the position pointed at (or null) and the one clicked.
     */
    build(positions, { marked, channels, note, onHover, onPick }) {
      const placed = positions.filter(hasPlace);
      if (placed.length === 0) return null;
      const xs = placed.flatMap((one) => one.um.x);
      const ys = placed.flatMap((one) => one.um.y);
      const bounds = { x: [Math.min(...xs), Math.max(...xs)], y: [Math.min(...ys), Math.max(...ys)] };
      const wide = Math.max(bounds.x[1] - bounds.x[0], 1e-6);
      const high = Math.max(bounds.y[1] - bounds.y[0], 1e-6);
      const margin = Math.max(wide, high) * 0.02;

      const root = document.createElement("div");
      root.className = "positions tile-map";
      const drawing = svg("svg", {
        viewBox: `${bounds.x[0] - margin} ${bounds.y[0] - margin} ${wide + 2 * margin} ${high + 2 * margin}`,
        preserveAspectRatio: "xMidYMid meet",
      });
      // The page's own words for the map, for someone who cannot see it.
      drawing.setAttribute("role", "group");
      drawing.setAttribute("aria-label", `Map of ${placed.length} positions`);
      const caption = document.createElement("div");
      caption.className = "map-caption";
      const said = document.createElement("span");
      said.className = "said";
      const noted = document.createElement("span");
      noted.className = "noted";
      noted.textContent = note ?? "";
      caption.append(said, noted);

      // Tiles that lie on the very same spot (the same place acquired at another angle, or
      // lit from the other side) would cover each other, and only the last could be pointed
      // at. They are drawn one inside the other instead: the first as the full tile, each
      // further one a step smaller, so that every one of them shows and can be clicked.
      const stacked = new Map();
      for (const position of placed) {
        const { x, y } = position.um;
        const grain = Math.max(Math.max(x[1] - x[0], y[1] - y[0]) * 0.02, 1e-6);
        const spot = [x[0], x[1], y[0], y[1]].map((edge) => Math.round(edge / grain)).join(" ");
        if (!stacked.has(spot)) stacked.set(spot, []);
        stacked.get(spot).push(position);
      }
      const drawnAt = new Map();
      for (const group of stacked.values()) {
        group.forEach((position, place) => {
          const { x, y } = position.um;
          const width = Math.max(x[1] - x[0], 0);
          const height = Math.max(y[1] - y[0], 0);
          const inset = (Math.min(width, height) / 2) * (place / group.length);
          drawnAt.set(position.name, {
            x: x[0] + inset, y: y[0] + inset, width: width - 2 * inset, height: height - 2 * inset,
          });
        });
      }
      const cells = new Map();
      const resting = () => {
        const position = placed.find((one) => one.name === marked);
        return position ? [position.name, stateWords(position, channels)].filter(Boolean).join(" · ") : "";
      };
      const say = (position) => {
        const words = position
          ? [position.name, stateWords(position, channels)].filter(Boolean).join(" · ")
          : resting();
        // With nothing pointed at or selected, the line says what the map is for.
        said.textContent = words || "Point at a tile, or click it to go there";
        said.classList.toggle("hint", !words);
      };
      for (const position of placed) {
        const cell = svg("g", { class: "position" });
        cell.dataset.name = position.name;
        if (position.state) cell.dataset.state = position.state;
        cell.classList.toggle("framed", position.name === marked);
        const box = svg("rect", drawnAt.get(position.name));
        const title = svg("title", { class: "label" });
        title.textContent = position.name;
        cell.append(box, title);
        cell.addEventListener("mouseenter", () => onHover(position));
        cell.addEventListener("mouseleave", () => onHover(null));
        cell.addEventListener("click", (event) => {
          event.stopPropagation();
          onPick(position);
        });
        cells.set(position.name, cell);
        drawing.appendChild(cell);
      }
      // The selected tile's outline is drawn once more on top, so that its neighbours'
      // edges do not run over it. The cells themselves keep their order.
      const chosen = placed.find((one) => one.name === marked);
      if (chosen) drawing.appendChild(svg("rect", { class: "chosen", ...drawnAt.get(chosen.name) }));
      const inView = svg("rect", { class: "in-view", visibility: "hidden" });
      drawing.appendChild(inView);
      root.append(drawing, caption);
      current = { root, cells, inView, bounds, margin, say, byName: new Map(placed.map((one) => [one.name, one])) };
      say(null);
      later();
      return root;
    },
    /** Light up the cell of the position pointed at (in the map or in the picture), or none. */
    hover(name) {
      if (current === null) return;
      for (const [cell, node] of current.cells) node.classList.toggle("pointed", cell === name);
      current.say(name === null ? null : current.byName.get(name) ?? null);
    },
  };
}

/**
 * The boxes drawn on the picture: one around the tile pointed at, one around the
 * tile that is selected. Each is given as `{ um, text }` or null, and follows
 * the picture as it is moved and zoomed. In the volume view there are none.
 */
export function tileOutlines(viewer, overlay) {
  const kinds = {};
  for (const kind of ["picked", "pointed"]) {
    const box = document.createElement("div");
    box.className = `tile-outline ${kind}`;
    box.hidden = true;
    const name = document.createElement("span");
    name.className = "name";
    box.appendChild(name);
    overlay.appendChild(box);
    kinds[kind] = { box, name, what: null };
  }
  let pending = 0;
  const place = () => {
    pending = 0;
    const frame = overlay.getBoundingClientRect();
    for (const { box, name, what } of Object.values(kinds)) {
      const at = what === null ? null : boxOnScreen(viewer, what.um);
      // Too small to be seen as a box, or off the picture altogether: not drawn.
      const off = at === null || at.width < 6 || at.height < 6
        || at.left > frame.right || at.top > frame.bottom || at.left + at.width < frame.left || at.top + at.height < frame.top;
      box.hidden = off;
      if (off) continue;
      box.style.left = `${at.left - frame.left}px`;
      box.style.top = `${at.top - frame.top}px`;
      box.style.width = `${at.width}px`;
      box.style.height = `${at.height}px`;
      name.textContent = what.text ?? "";
      name.hidden = !what.text || at.width < 60 || at.height < 24;
      name.style.maxWidth = `${Math.max(0, at.width - 12)}px`;
    }
  };
  const later = () => {
    if (!pending) pending = requestAnimationFrame(place);
  };
  viewer.navigationState.changed.add(later);
  viewer.layout.changed.add(later);
  window.addEventListener("resize", later);
  return {
    set(kind, what) {
      kinds[kind].what = what;
      later();
    },
  };
}

/**
 * Tell the panel what the operator points at and clicks in the picture.
 * `tileAt(um)` finds the tile at a place; `hover(found)` and `pick(found)` are
 * told what was found there, or null for a place where no tile lies.
 */
export function watchPicture(viewer, stage, { tileAt, hover, pick }) {
  let resting = null;
  let pressed = null;
  const look = () => {
    resting = null;
    const at = pressed === null ? pointerInMicrometres(viewer) : null;
    hover(at === null ? null : tileAt(at));
  };
  viewer.mouseState.changed.add(() => {
    clearTimeout(resting);
    resting = setTimeout(look, REST_MS);
  });
  viewer.layout.changed.add(() => hover(null));
  const onControls = (event) => event.target?.closest?.(".stage-overlay") != null;
  stage.addEventListener("mousedown", (event) => {
    pressed = null;
    if (event.button !== 0 || onControls(event)) return;
    pressed = {
      x: event.clientX, y: event.clientY, at: performance.now(),
      plain: !(event.shiftKey || event.ctrlKey || event.altKey || event.metaKey),
    };
  }, true);
  window.addEventListener("mouseup", (event) => {
    const was = pressed;
    pressed = null;
    if (was === null || event.button !== 0 || !was.plain) return;
    const moved = Math.hypot(event.clientX - was.x, event.clientY - was.y);
    if (moved > CLICK_PIXELS || performance.now() - was.at > CLICK_MS) return;
    if (layoutOf(viewer) === "3d") return;
    const at = pointerInMicrometres(viewer);
    if (at === null) return;
    // What is pointed at is said afresh with the click, not a moment after it.
    const found = tileAt(at);
    hover(found);
    pick(found);
  }, true);
}
