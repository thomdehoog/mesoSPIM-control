/**
 * The rule that lets one overview picture stand in for the tiles of an
 * acquisition while the view is zoomed out.
 *
 * With every tile of a large acquisition in view, each is a source of its own
 * that the engine sets up, loads and draws for. From that far away one small
 * picture of the whole acquisition shows the same, and Python offers one: an
 * extra source in each channel's layer (overview.py). The engine has to know
 * which sources are tiles and which is the overview, and from which zoom on
 * the overview is as sharp as the screen. That is kept here, and the build's
 * edits to the engine ask for it by a source's address
 * (`globalThis.mesospimOverviewRule`, see scripts/neuroglancer.mjs).
 *
 * A tile's rule is switched on only once the overview's own picture is on
 * screen. Until then the tiles go on being drawn, so that the picture is never
 * empty while the overview loads.
 */

// By address below the server: "data/3" -> { across, volume }, "overview/ab12" -> { overview: true }.
const rules = new Map();
// By layer name: what Python last said, and the tiles' rules still to be switched on.
const layers = new Map();
const ADDRESS = /\/(data|overview)\/([^/.|]+)/;
const SWITCH_AFTER_MS = 1200; // at the earliest: the engine's counts lag a little
const SWITCH_BY_MS = 15000; // at the latest, whatever the counts say

// How many time points a store really holds, by address. The page is told that a store
// has room for more (so that one more time point is not a new store to it), and the
// engine, which reads ahead of a view that moves through time, must not read ahead
// into time points that are not there.
const times = new Map();
const timesOfLayer = new Map(); // by layer name: what was last said, as text

const keyOf = (url) => {
  const found = typeof url === "string" ? ADDRESS.exec(url) : null;
  return found === null ? null : `${found[1]}/${found[2]}`;
};

globalThis.mesospimOverviewRule = (source) => {
  const key = keyOf(source?.parameters?.url);
  if (key === null) return undefined;
  const rule = rules.get(key);
  const held = times.get(key);
  if (rule === undefined && held === undefined) return undefined;
  return held === undefined ? rule : { ...rule, times: held };
};

/** Take in how many time points a layer's stores really hold (`spec._times`). */
export function reflectTimes(viewer, spec) {
  const keys = (spec.source ?? []).map((source) => keyOf(source.url)).filter((key) => key !== null && key.startsWith("data/"));
  const said = JSON.stringify([spec._times ?? null, keys]);
  if (timesOfLayer.get(spec.name) === said) return;
  timesOfLayer.set(spec.name, said);
  for (const key of keys) {
    if (spec._times > 0) times.set(key, spec._times);
    else times.delete(key);
  }
  readAgain(viewer.layerManager.getLayerByName(spec.name));
}

// The engine reads a source's rule when it makes its list of the source's copies, and
// makes that list anew when the source's transform is said to have changed.
function readAgain(managed) {
  for (const renderLayer of managed?.layer?.renderLayers ?? []) {
    renderLayer.transform?.changed?.dispatch?.();
  }
}

function pictureIsUp(managed) {
  let needed = 0;
  let available = 0;
  for (const renderLayer of managed.layer?.renderLayers ?? []) {
    const progress = renderLayer.layerChunkProgressInfo;
    if (!progress) continue;
    needed += progress.numVisibleChunksNeeded;
    available += progress.numVisibleChunksAvailable;
  }
  return needed > 0 && available >= needed;
}

/** Take in what Python says about a layer's overview (`{ key, tiles }`, or nothing). */
export function reflectOverview(viewer, name, wanted) {
  const said = JSON.stringify(wanted ?? null);
  const entry = layers.get(name);
  if ((entry?.said ?? "null") === said) return;
  for (const key of entry?.keys ?? []) rules.delete(key);
  if (!wanted) {
    layers.delete(name);
  } else {
    // The overview may be drawn from the start; the tiles step back once it is up.
    rules.set(wanted.key, { overview: true });
    layers.set(name, { said, keys: [wanted.key], pending: wanted.tiles, since: performance.now() });
  }
  readAgain(viewer.layerManager.getLayerByName(name));
}

export function forgetOverview(name) {
  const entry = layers.get(name);
  for (const key of entry?.keys ?? []) rules.delete(key);
  layers.delete(name);
  timesOfLayer.delete(name);
}

export function watchOverviews(viewer) {
  setInterval(() => {
    const now = performance.now();
    for (const [name, entry] of layers) {
      if (!entry.pending) continue;
      const managed = viewer.layerManager.getLayerByName(name);
      if (!managed?.layer) continue;
      const waited = now - entry.since;
      const loaded = (managed.layer.dataSources ?? []).every((source) => source.loadState !== undefined);
      if (waited < SWITCH_AFTER_MS) continue;
      if (!((loaded && pictureIsUp(managed)) || !managed.visible || waited > SWITCH_BY_MS)) continue;
      for (const [key, [across, volume]] of Object.entries(entry.pending)) {
        rules.set(key, { across, volume });
        entry.keys.push(key);
      }
      entry.pending = null;
      readAgain(managed);
    }
  }, 200);
}

/** For tests and for looking at: the rules in force, by address. */
export function overviewRules() {
  return Object.fromEntries(rules);
}
