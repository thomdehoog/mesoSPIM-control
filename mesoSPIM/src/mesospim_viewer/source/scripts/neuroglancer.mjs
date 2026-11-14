/**
 * What the build does to the pinned neuroglancer 2.41.2, as one Vite plugin.
 *
 * Five things, all done while the page is built and never by editing the
 * installed engine in `node_modules`: what is installed stays exactly as npm
 * put it there, so building twice, or on another machine after `npm ci`,
 * makes the same page.
 *
 * 1. The background workers. neuroglancer ships its two worker entry points
 *    (chunk_worker.bundle.js, which fetches image chunks, and
 *    async_computation.bundle.js, which decompresses them) as tiny source
 *    stubs: lists of `#src/...` imports that only a bundler resolves. Vite
 *    copies such a stub as it is, a browser cannot resolve it, the worker
 *    never starts and the picture never fills in. So both are compiled here
 *    with esbuild into self-contained files at the page's root, made for the
 *    Data viewer window's Qt WebEngine 5.15 (Chromium 83) with the shims of
 *    src/legacy_browser.js put first, and the engine is pointed at them. The
 *    chunk worker asks for "../async_computation.bundle.js" next to itself,
 *    which from the root is the root.
 *
 * 2. An opt-in transparent ground for the flat (2D) view: the viewer option
 *    `transparentBackground` of google/neuroglancer#1128. Stock neuroglancer
 *    paints the slice background opaque and finally forces the whole
 *    display's alpha to one, so a host application can never show through
 *    where nothing was imaged. With the option, the flat view is clear where
 *    it shows no data and translucent content keeps its alpha; the 3D view
 *    stays opaque. Without it the engine behaves exactly as shipped.
 *
 * 3. Channels and tiles mixed by the brighter of the two. For a layer with the
 *    default blending, what is laid over the picture replaces, colour by
 *    colour, only what is darker than itself: a structure lit in two channels
 *    shows in both colours, and two overlapping tiles do not add up along the
 *    join. Every tile of a layer is blended so, the first one too.
 *
 * 4. A source that is read again keeps showing its chunks until each one is
 *    replaced (google/neuroglancer#1126). Stock neuroglancer drops all of
 *    them at once, which blanks the picture until they are fetched again.
 *
 * 5. Many tiles. The engine draws every tile of an acquisition as a layer of
 *    its own, and did a fixed amount of work for each on every frame, whether
 *    the tile was on screen or not. With a hundred tiles that work, not the
 *    picture, decided how smoothly the view moved. Two edits:
 *
 *    - In the flat view, a tile none of whose chunks meets the picture is
 *      passed over before anything is set up for it. Before, each such tile
 *      still had its buffers switched and cleared and a histogram pass run.
 *    - In the volume view, the tiles of one channel are projected into the
 *      same buffer and that buffer is copied to the picture once. Before, the
 *      whole buffer was copied twice and cleared after every single tile. The
 *      engine's depth test already keeps the brightest value along each line
 *      of sight, across the chunks of one tile; it now does so across the
 *      tiles of a channel too. Where two tiles overlap, the join shows the
 *      brighter of the two, as in the flat view, and no longer their sum.
 *
 * The edits apply wherever a module is compiled, the page or a worker.
 *
 * Every edit is anchored on the stock text, and the build stops with a message
 * when an anchor is missing: the pinned engine version has then moved.
 */
import { build } from "esbuild";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const lib = join(here, "..", "node_modules", "neuroglancer", "lib");
const shims = join(here, "..", "src", "legacy_browser.js");
// Added to the chunk worker only: reading again just the chunks that landed.
const landed = join(here, "..", "src", "engine", "worker_landed.js");

const WORKERS = ["chunk_worker.bundle.js", "async_computation.bundle.js"];

// Each edit: the module it applies to (inside neuroglancer/lib), the stock text
// it is anchored on, and what that text becomes.
const PATCHES = [
  // google/neuroglancer#1128: the viewer option `transparentBackground`.
  {
    module: "viewer.js",
    anchor: `  "showPanelBorders"\n];`,
    replacement: () => `  "showPanelBorders",\n  "transparentBackground"\n];`,
  },
  {
    module: "viewer.js",
    anchor: `    VIEWER_UI_CONFIG_OPTIONS.map((key) => [key, new TrackableBoolean(true)])`,
    replacement: () => `    VIEWER_UI_CONFIG_OPTIONS.map((key) => [
      key,
      new TrackableBoolean(key !== "transparentBackground")
    ])`,
  },
  {
    module: "viewer.js",
    anchor: `    this.makeUI();\n    this.updateShowBorders();\n`,
    replacement: (anchor) => `${anchor}    const { transparentBackground } = this.uiConfiguration;
    const updateTransparentBackground = () => {
      display.transparentBackground = transparentBackground.value;
      display.scheduleRedraw();
    };
    updateTransparentBackground();
    this.registerDisposer(
      transparentBackground.changed.add(updateTransparentBackground)
    );
`,
  },
  {
    module: "display_context.js",
    anchor: `  force3DHistogramForAutoRange = false;\n`,
    replacement: (anchor) => `${anchor}  /**
   * If \`true\`, cross-section panels leave the canvas transparent where they show no data and keep
   * the alpha of translucent content, so that an embedding page shows through.  The 3D panel stays
   * opaque.  Intended for embedding Neuroglancer over other content.  Set through the viewer's
   * \`transparentBackground\` option.
   */
  transparentBackground = false;
`,
  },
  {
    // The display's alpha is forced to one at the end of a frame only when opaque.
    module: "display_context.js",
    anchor: `    gl.disable(gl.SCISSOR_TEST);
    this.gl.clearColor(1, 1, 1, 1);
    this.gl.colorMask(false, false, false, true);
    gl.clear(gl.COLOR_BUFFER_BIT);
    this.gl.colorMask(true, true, true, true);`,
    replacement: (anchor) => `    if (!this.transparentBackground) {\n${anchor.replace(/^/gm, "  ")}\n    }`,
  },
  {
    // The 3D view stays opaque.
    module: "perspective_view/panel.js",
    anchor: `      this.offscreenFramebuffer.colorBuffers[0 /* COLOR */].texture
    );
    return true;
  }
  drawSliceViews(renderContext) {`,
    replacement: (anchor) =>
      anchor.replace(
        "    return true;",
        `    if (this.context.transparentBackground) {
      gl.colorMask(false, false, false, true);
      gl.clearColor(0, 0, 0, 1);
      gl.clear(WebGL2RenderingContext.COLOR_BUFFER_BIT);
      gl.colorMask(true, true, true, true);
    }
    return true;`,
      ),
  },
  {
    // The flat view's background is clear, and its picture is drawn premultiplied.
    module: "sliceview/panel.js",
    anchor: `const tempVec3 = vec3.create();\n`,
    replacement: (anchor) => `function sliceViewPanelEmitPremultipliedColor(builder) {
  builder.addOutputBuffer("vec4", "out_fragColor", null);
  builder.addFragmentCode(\`
void emit(vec4 color, highp uint pickId) {
  out_fragColor = vec4(color.rgb * color.a, color.a);
}
\`);
}
${anchor}`,
  },
  {
    module: "sliceview/panel.js",
    anchor: `        sliceViewPanelEmitColor,\n        this.viewer,\n        false\n      )\n    );\n`,
    replacement: (anchor) => `${anchor}    this.premultipliedSliceViewRenderHelper = this.registerDisposer(
      SliceViewRenderHelper.get(
        this.gl,
        sliceViewPanelEmitPremultipliedColor,
        this.viewer,
        false
      )
    );
`,
  },
  {
    module: "sliceview/panel.js",
    anchor: `  sliceViewRenderHelper;\n`,
    replacement: (anchor) => `${anchor}  premultipliedSliceViewRenderHelper;\n`,
  },
  {
    module: "sliceview/panel.js",
    anchor: `    backgroundColor[3] = 1;
    this.offscreenFramebuffer.bindSingle(0 /* COLOR */);
    this.sliceViewRenderHelper.draw(`,
    replacement: () => `    backgroundColor[3] = this.context.transparentBackground ? 0 : 1;
    this.offscreenFramebuffer.bindSingle(0 /* COLOR */);
    const sliceViewRenderHelper = this.context.transparentBackground ? this.premultipliedSliceViewRenderHelper : this.sliceViewRenderHelper;
    sliceViewRenderHelper.draw(`,
  },
  // Translucent annotations and scale bars keep their alpha.
  ...["      ", "        "].map((indent) => ({
    module: "sliceview/panel.js",
    anchor: `${indent}gl.blendFunc(\n${indent}  WebGL2RenderingContext.SRC_ALPHA,\n${indent}  WebGL2RenderingContext.ONE_MINUS_SRC_ALPHA\n${indent});`,
    replacement: (anchor) => `${anchor}\n${indent}this.keepAlphaOfTranslucentContent();`,
  })),
  {
    module: "sliceview/panel.js",
    anchor: `  ensureBoundsUpdated() {\n`,
    replacement: (anchor) => `  // With a transparent background, blends alpha with (ONE, ONE_MINUS_SRC_ALPHA), so that
  // translucent content keeps its alpha.  Blending alpha with SRC_ALPHA would square it.
  keepAlphaOfTranslucentContent() {
    if (!this.context.transparentBackground) return;
    this.gl.blendFuncSeparate(
      WebGL2RenderingContext.SRC_ALPHA,
      WebGL2RenderingContext.ONE_MINUS_SRC_ALPHA,
      WebGL2RenderingContext.ONE,
      WebGL2RenderingContext.ONE_MINUS_SRC_ALPHA
    );
  }
${anchor}`,
  },
  // google/neuroglancer#1126: a source read again keeps showing its chunks. The
  // chunk worker no longer tells the page to drop every chunk of the source;
  // each chunk the page holds is flagged instead, stays on screen until the
  // chunk read again replaces it, and is expired if it is not read again.
  {
    module: "chunk_manager/backend.js",
    anchor: `  downloadAbortController = void 0;\n  initialize(key) {\n`,
    replacement: () => `  downloadAbortController = void 0;
  /**
   * Indicates that the frontend still holds a copy of this chunk from before the most recent
   * \`invalidateSourceCache\` call.  The frontend keeps displaying that copy until it is either
   * replaced by the re-downloaded chunk or expired, if the chunk is not re-downloaded.
   */
  staleFrontendCopy = false;
  initialize(key) {
`,
  },
  {
    module: "chunk_manager/backend.js",
    anchor: `    this.requestedState = ChunkState.NEW;\n    this.newRequestedState = ChunkState.NEW;\n`,
    replacement: (anchor) => `${anchor}    this.staleFrontendCopy = false;\n`,
  },
  {
    module: "chunk_manager/backend.js",
    anchor: `chunk.priorityTier === ChunkPriorityTier.RECENT) {\n`,
    replacement: (anchor) => `${anchor}      this.expireStaleFrontendCopy(chunk);\n`,
  },
  {
    module: "chunk_manager/backend.js",
    anchor: `    chunk.state = newState;
    this.adjustCapacitiesForChunk(chunk, true);
    this.addChunkToQueues_(chunk);
    this.scheduleUpdate();
  }
`,
    replacement: () => `    chunk.state = newState;
    if (chunk.staleFrontendCopy) {
      switch (newState) {
        case ChunkState.SYSTEM_MEMORY:
        case ChunkState.GPU_MEMORY:
          chunk.staleFrontendCopy = false;
          break;
        case ChunkState.QUEUED:
        case ChunkState.DOWNLOADING:
          break;
        case ChunkState.SYSTEM_MEMORY_WORKER:
          if (chunk.requestedState === ChunkState.GPU_MEMORY) break;
          this.expireStaleFrontendCopy(chunk);
          break;
        default:
          this.expireStaleFrontendCopy(chunk);
      }
    }
    this.adjustCapacitiesForChunk(chunk, true);
    this.addChunkToQueues_(chunk);
    this.scheduleUpdate();
  }
  /**
   * Tells the frontend to drop the copy of \`chunk\` retained by \`invalidateSourceCache\`, because
   * the chunk will not be re-downloaded.
   */
  expireStaleFrontendCopy(chunk) {
    if (!chunk.staleFrontendCopy) return;
    chunk.staleFrontendCopy = false;
    this.rpc.invoke("Chunk.update", {
      id: chunk.key,
      state: ChunkState.EXPIRED,
      source: chunk.source.rpcId
    });
  }
`,
  },
  {
    module: "chunk_manager/backend.js",
    anchor: `          chunk.freeSystemMemory();
          break;
      }
      this.updateChunkState(chunk, ChunkState.QUEUED);
    }
    this.rpc.invoke("Chunk.update", { source: source.rpcId });
`,
    replacement: () => `          chunk.freeSystemMemory();
          break;
        case ChunkState.SYSTEM_MEMORY:
        case ChunkState.GPU_MEMORY:
          chunk.staleFrontendCopy = true;
          break;
      }
      this.updateChunkState(chunk, ChunkState.QUEUED);
    }
`,
  },
  {
    module: "chunk_manager/frontend.js",
    anchor: `    } else if (update.id === void 0) {
      for (const chunkKey of source.chunks.keys()) {
        source.deleteChunk(chunkKey);
      }
      visibleChunksChanged = true;
`,
    replacement: () => ``,
  },
  {
    // A chunk read again replaces the copy still on screen.
    module: "chunk_manager/frontend.js",
    anchor: `        if (update.new) {\n`,
    replacement: (anchor) => `${anchor}          if (source.chunks.has(key)) {
            source.deleteChunk(key);
          }
`,
  },
  {
    // Channels and tiles are mixed by taking, for each of red, green and blue,
    // the brighter of what is already drawn and what is laid over it ("lighten").
    // Stock neuroglancer either lays a layer over the ones beneath, so a bright
    // channel hides the same structure in the channel under it, or adds them,
    // so two tiles of one acquisition that overlap come out twice as bright
    // along the join. With the brighter of the two, a structure lit in two
    // channels shows in both colours at once, an overlap of two tiles looks
    // like either tile alone, and nothing ever clips to white that was not
    // white in a channel already. The engine draws each tile of a layer on its
    // own, and stock neuroglancer draws the very first one without blending:
    // here every tile is blended, the first one too. The alpha is accumulated
    // as coverage, so a dark pixel still counts as imaged.
    module: "sliceview/volume/image_renderlayer.js",
    anchor: `    if (blendModeValue === BLEND_MODES.ADDITIVE || renderLayerNum > 0) {`,
    replacement: (anchor) => `    gl.blendEquation(gl.FUNC_ADD);
    if (blendModeValue === BLEND_MODES.DEFAULT) {
      gl.enable(gl.BLEND);
      gl.blendEquationSeparate(gl.MAX, gl.FUNC_ADD);
      gl.blendFuncSeparate(gl.ONE, gl.ONE, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
      return;
    }
${anchor}`,
  },
  {
    // The histogram of a layer is counted right after the layer is drawn, by
    // adding one for every sample: adding, whatever the layer was mixed with.
    module: "webgl/empirical_cdf.js",
    anchor: `    gl.blendFunc(WebGL2RenderingContext.ONE, WebGL2RenderingContext.ONE);`,
    replacement: (anchor) => `    gl.blendEquation(WebGL2RenderingContext.FUNC_ADD);\n${anchor}`,
  },
  {
    // ...and what the engine draws after the layers is blended as it expects.
    module: "sliceview/frontend.js",
    anchor: `      ++renderLayerNum;
    }
    gl.disable(WebGL2RenderingContext.BLEND);`,
    replacement: (anchor) => `${anchor}
    gl.blendEquation(WebGL2RenderingContext.FUNC_ADD);`,
  },
  {
    // Many tiles, the flat view: a tile with nothing in view is passed over.
    // The view is tested against each of the layer's sources exactly as the
    // drawing itself tests it, so a tile is passed over only when drawing it
    // would have put nothing on the picture. What the drawing would still have
    // done for such a tile is done here: its record of the detail on screen is
    // started afresh, so that it does not keep saying what was last drawn.
    module: "sliceview/frontend.js",
    anchor: `    for (const renderLayer of this.visibleLayerList) {
      const histogramCount = wireFrame ? 0 : renderLayer.getDataHistogramCount();
`,
    replacement: (anchor) => `    for (const renderLayer of this.visibleLayerList) {
      if (!this.hasChunksInView(renderLayer)) {
        const { renderScaleHistogram } = renderLayer;
        if (renderScaleHistogram !== void 0) {
          renderScaleHistogram.begin(
            this.chunkManager.chunkQueueManager.frameNumberCounter.frameNumber
          );
        }
        renderLayer.highestResolutionLoadedVoxelSize = void 0;
        continue;
      }
${anchor.split("\n").slice(1).join("\n")}`,
  },
  {
    module: "sliceview/frontend.js",
    anchor: `  computeHistograms(count, histogramSpecifications) {
`,
    replacement: (anchor) => `  /**
   * Whether any chunk of \`renderLayer\` intersects the current view.  A layer for which this is
   * false draws nothing, and is skipped by \`updateRendering\` before any state is set up for it.
   */
  hasChunksInView(renderLayer) {
    const layerInfo = this.visibleLayers.get(renderLayer);
    if (layerInfo === void 0) return true;
    const projectionParameters = this.projectionParameters.value;
    let found = false;
    const note = () => {
      found = true;
    };
    for (const tsource of layerInfo.visibleSources) {
      forEachPlaneIntersectingVolumetricChunk(
        projectionParameters,
        tsource.renderLayer.localPosition.value,
        tsource,
        getNormalizedChunkLayout(projectionParameters, tsource.chunkLayout),
        note
      );
      if (found) return true;
    }
    return false;
  }
${anchor}`,
  },
  {
    // Many tiles, the volume view: which layers share a buffer with the one
    // after them. They are consecutive tiles of one channel: the same controls
    // (colour and contrast) and the same way of drawing the volume.
    module: "perspective_view/panel.js",
    anchor: `      let currentTransparentRenderingState = 0 /* TRANSPARENT */;
      for (const [renderLayer, attachment] of visibleLayers) {
        if (renderLayer.isVolumeRendering) {
`,
    replacement: (anchor) => `      const sharesProjectionWithNext = /* @__PURE__ */ new Set();
      {
        let previous = null;
        for (const [renderLayer] of visibleLayers) {
          // Layers the loop below does nothing for do not come between two tiles.
          if (!renderLayer.isVolumeRendering && !renderLayer.isTransparent) continue;
          const volume = renderLayer.isVolumeRendering;
          if (previous !== null && volume && renderLayer.shaderControlState === previous.shaderControlState && renderLayer.mode.value === previous.mode.value) {
            sharesProjectionWithNext.add(previous);
          }
          previous = volume ? renderLayer : null;
        }
      }
${anchor}`,
  },
  {
    // ...and such a layer is left in the buffer for the next one to be projected
    // onto, where the depth test keeps the brighter (or, for the minimum, the
    // darker) of the two. The copies to the picture and the clearing follow
    // after the last tile of the channel.
    //
    // A blended volume is drawn straight into the picture, so there is nothing
    // to share while it moves. Once the camera rests, though, the engine draws
    // every such layer a second time, to learn what lies under the pointer, and
    // after each one copies that buffer across the whole picture and clears it.
    // For the tiles of one channel the second drawings are left in the buffer
    // together, which keeps the brightest under each pixel just as the copies
    // would, and are copied once, after the last of them.
    module: "perspective_view/panel.js",
    anchor: `          renderLayer.draw(renderContext, attachment);
          gl.enable(WebGL2RenderingContext.DEPTH_TEST);
          if (!needsSecondPickingPass && !isVolumeProjectionLayer) {
            continue;
          }
`,
    replacement: (anchor) => `${anchor}          if (isVolumeProjectionLayer && sharesProjectionWithNext.has(renderLayer)) {
            renderContext.emitter = maxProjectionEmit;
            bindMaxProjectionBuffer();
            currentTransparentRenderingState = 2 /* MAX_PROJECTION */;
            continue;
          }
          if (sharesProjectionWithNext.has(renderLayer)) {
            gl.enable(WebGL2RenderingContext.BLEND);
            gl.blendFuncSeparate(
              WebGL2RenderingContext.ONE,
              WebGL2RenderingContext.ONE,
              WebGL2RenderingContext.ZERO,
              WebGL2RenderingContext.ONE_MINUS_SRC_ALPHA
            );
            gl.depthMask(false);
            gl.depthFunc(WebGL2RenderingContext.LESS);
            currentTransparentRenderingState = 2 /* MAX_PROJECTION */;
            continue;
          }
`,
  },
  {
    // Many tiles, opening them. Every tile that finishes loading adds its
    // bounding box to the coordinate space all layers share, and the engine
    // then works out afresh how every tile already there sits in that space:
    // for the hundredth tile, ninety-nine times over, and so on for each one
    // after it. Opening 196 tiles of two channels spent 2.6 of its 9.5 seconds
    // on this alone. Yet how a tile sits in the shared space depends only on
    // the names and the scales of the axes and on the tile's own transform,
    // none of which a new tile's bounding box changes. So the result is kept,
    // and worked out again only when one of the things it is made from has
    // changed: the axes of the shared space or of the layer's own space, the
    // axes and the extent of the channel space, or what the tile says of
    // itself (its axes, its rank and its matrix). Nothing else of them is read
    // (see getRenderLayerTransform, just above it). (Whether two spaces have
    // the same axes is itself asked a few hundred thousand times while many
    // tiles open, so each space is given a mark for its axes once, and the
    // marks are compared.)
    module: "render_coordinate_transform.js",
    anchor: `export function getWatchableRenderLayerTransform(globalCoordinateSpace, localCoordinateSpace, modelToLayerTransform, subsourceEntry, channelCoordinateSpace) {
  return makeCachedDerivedWatchableValue(
    (globalCoordinateSpace2, localCoordinateSpace2, modelToLayerTransform2, channelCoordinateSpace2) => getRenderLayerTransform(
      globalCoordinateSpace2,
      localCoordinateSpace2,
      modelToLayerTransform2,
      subsourceEntry,
      channelCoordinateSpace2
    ),
    [
      globalCoordinateSpace,
      localCoordinateSpace,
      modelToLayerTransform,
      channelCoordinateSpace === void 0 ? constantWatchableValue(void 0) : channelCoordinateSpace
    ],
    renderLayerTransformsEqual
  );
`,
    replacement: () => `const axesMarkOfSpace = /* @__PURE__ */ new WeakMap();
const axesMarkByText = /* @__PURE__ */ new Map();
function axesMark(space) {
  let mark = axesMarkOfSpace.get(space);
  if (mark === void 0) {
    const text = space.names.join("\\u0001") + "\\u0002" + Array.from(space.scales).join("\\u0001");
    mark = axesMarkByText.get(text);
    if (mark === void 0) axesMarkByText.set(text, mark = { text });
    axesMarkOfSpace.set(space, mark);
  }
  return mark;
}
function sameModelTransform(a, b) {
  return a === b || a.rank === b.rank && a.sourceRank === b.sourceRank && axesMark(a.inputSpace) === axesMark(b.inputSpace) && axesMark(a.outputSpace) === axesMark(b.outputSpace) && (a.transform === b.transform || arraysEqual(a.transform, b.transform));
}
function sameChannelSpace(a, b) {
  if (a === b) return true;
  if (a === void 0 || b === void 0 || axesMark(a) !== axesMark(b)) return false;
  const { bounds: boundsA } = a, { bounds: boundsB } = b;
  return arraysEqual(boundsA.lowerBounds, boundsB.lowerBounds) && arraysEqual(boundsA.upperBounds, boundsB.upperBounds) && arraysEqual(boundsA.voxelCenterAtIntegerCoordinates, boundsB.voxelCenterAtIntegerCoordinates);
}
export function getWatchableRenderLayerTransform(globalCoordinateSpace, localCoordinateSpace, modelToLayerTransform, subsourceEntry, channelCoordinateSpace) {
  let last;
  return makeCachedDerivedWatchableValue(
    (globalCoordinateSpace2, localCoordinateSpace2, modelToLayerTransform2, channelCoordinateSpace2) => {
      if (last !== void 0 && last.global === axesMark(globalCoordinateSpace2) && last.local === axesMark(localCoordinateSpace2) && sameChannelSpace(last.channel, channelCoordinateSpace2) && sameModelTransform(last.model, modelToLayerTransform2)) {
        return last.result;
      }
      const result = getRenderLayerTransform(
        globalCoordinateSpace2,
        localCoordinateSpace2,
        modelToLayerTransform2,
        subsourceEntry,
        channelCoordinateSpace2
      );
      last = { global: axesMark(globalCoordinateSpace2), local: axesMark(localCoordinateSpace2), model: modelToLayerTransform2, channel: channelCoordinateSpace2, result };
      return result;
    },
    [
      globalCoordinateSpace,
      localCoordinateSpace,
      modelToLayerTransform,
      channelCoordinateSpace === void 0 ? constantWatchableValue(void 0) : channelCoordinateSpace
    ],
    (a, b) => a === b || renderLayerTransformsEqual(a, b)
  );
`,
  },
  {
    // The chunk worker is the one compiled here, at the page's root.
    module: "data_management_context.js",
    anchor: 'new URL("./chunk_worker.bundle.js", import.meta.url)',
    replacement: () => 'new URL("/chunk_worker.bundle.js", self.location.href)',
  },
];

/** The code of neuroglancer/lib/`module` with its edits, each one added to `applied`. */
function patchModule(module, code, applied) {
  let patched = code;
  for (const patch of PATCHES.filter((patch) => patch.module === module)) {
    if (!patched.includes(patch.anchor)) {
      throw new Error(`anchor not found in neuroglancer/lib/${module}: the pinned neuroglancer version has changed`);
    }
    patched = patched.replace(patch.anchor, patch.replacement(patch.anchor));
    applied.add(patch);
  }
  return patched;
}

/** One worker entry point, compiled into one self-contained file, as text. */
async function compileWorker(name, applied) {
  const result = await build({
    entryPoints: [join(lib, name)],
    bundle: true,
    format: "esm", // the workers are ES-module workers ({type:"module"})
    outfile: name,
    write: false,
    logLevel: "error",
    // Resolve neuroglancer's "#src/*" and "#datasource/*" subpath imports via
    // its package.json "imports" map (the "default" condition).
    conditions: ["default"],
    legalComments: "none",
    target: "chrome83",
    inject: name === "chunk_worker.bundle.js" ? [shims, landed] : [shims],
    plugins: [
      {
        name: "neuroglancer-edits",
        setup(worker) {
          worker.onLoad({ filter: /[\\/]neuroglancer[\\/]lib[\\/].*\.js$/ }, async ({ path }) => {
            const module = engineModule(path);
            if (!PATCHES.some((patch) => patch.module === module)) return undefined;
            return { contents: patchModule(module, await readFile(path, "utf8"), applied), loader: "js" };
          });
        },
      },
    ],
  });
  const text = result.outputFiles[0].text;
  if (text.length < 50 * 1024) {
    throw new Error(
      `Worker ${name} compiled to only ${Math.round(text.length / 1024)} KB -- expected a few ` +
        `hundred KB. It is probably still the unresolved stub; the viewer would load but ` +
        `never show pixels.`,
    );
  }
  return text;
}

/** The module path inside neuroglancer/lib that a Vite module id names, or null. */
function engineModule(id) {
  const path = id.split("?")[0].replace(/\\/g, "/");
  const at = path.lastIndexOf("/neuroglancer/lib/");
  return at === -1 ? null : path.slice(at + "/neuroglancer/lib/".length);
}

export function neuroglancerForQt() {
  let command = "build";
  const applied = new Set();
  return {
    name: "mesospim-neuroglancer-for-qt",
    enforce: "pre",
    configResolved(config) {
      command = config.command;
    },
    transform(code, id) {
      const module = engineModule(id);
      if (!PATCHES.some((patch) => patch.module === module)) return null;
      return { code: patchModule(module, code, applied), map: null };
    },
    async generateBundle() {
      for (const name of WORKERS) {
        this.emitFile({ type: "asset", fileName: name, source: await compileWorker(name, applied) });
      }
      const missed = PATCHES.filter((patch) => !applied.has(patch));
      if (missed.length) {
        this.error(`never reached: neuroglancer/lib/${missed.map((patch) => patch.module).join(", ")}`);
      }
    },
    configureServer(server) {
      // Under `vite dev`, the workers are compiled when first asked for.
      const compiled = new Map();
      server.middlewares.use(async (request, response, next) => {
        const name = request.url?.split("?")[0].slice(1);
        if (!WORKERS.includes(name) || command !== "serve") return next();
        try {
          if (!compiled.has(name)) compiled.set(name, await compileWorker(name, applied));
          response.setHeader("Content-Type", "text/javascript");
          response.end(compiled.get(name));
        } catch (error) {
          next(error);
        }
      });
    },
  };
}
