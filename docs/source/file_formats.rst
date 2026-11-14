Output File Formats
======================

mesoSPIM-control writes acquired image data through a pluggable **image
writer** system. Each acquisition entry in the Acquisition Manager selects
its own writer via the file-naming wizard, so a single acquisition list can
mix formats across rows if needed.

.. tip::

   Not sure which one to pick?

   * **Tiff_Writer** (ImageJ-TIFF) for the broadest compatibility with
     external viewers and stitchers — the safest default if you're not sure
     what your downstream tools support.
   * **MP_OME_Zarr_Writer** for new acquisitions on a fast (NVMe/SSD) system
     — natively saves multi-resolution pyramids with compression, and won't
     stall acquisition on disk I/O.
   * **H5_BDV_Writer** for the easiest BigStitcher stitching workflow. See
     the caveats below before relying on it for Imaris.

At a glance
-------------

.. list-table::
   :widths: 20 12 14 14 14 26
   :header-rows: 1

   * - Writer
     - Extension
     - Files per acquisition
     - Pyramid
     - Compression
     - Notes
   * - ``MP_OME_Zarr_Writer``
     - ``.ome.zarr``
     - One store (per-tile subgroups)
     - Yes
     - zstd / lz4
     - Writes in a background process; recommended default
   * - ``MP_OME_Zarr_TCZYX_Writer``
     - ``.ome.zarr``
     - One ``(t, c, z, y, x)`` store per tile
     - Yes
     - zstd / lz4
     - Channels along ``c``, time points appended along ``t``; no BigStitcher XML
   * - ``H5_BDV_Writer``
     - ``.h5``
     - One file (all tiles)
     - Not in practice :sup:`†`
     - gzip / lzf / none
     - Easiest BigStitcher stitching workflow; Imaris-compatible only for a single-tile, uncompressed, pyramid-free dataset
   * - ``Tiff_Writer``
     - ``.tif`` / ``.tiff``
     - One per tile/channel
     - No
     - No
     - ImageJ-compatible; broadest compatibility with external viewers/stitchers
   * - ``Big_Tiff_Writer``
     - ``.btf`` / ``.tf2`` / ``.tf8``
     - One per tile/channel
     - No
     - No
     - Same as TIFF, in the BigTIFF container format
   * - ``RAW_Writer``
     - ``.raw``
     - One per tile/channel
     - No
     - No
     - Uncompressed uint16 binary dump, no metadata

:sup:`†` ``H5_BDV_Writer`` accepts a pyramid (``subsamp``) option, but
generating it during acquisition is slow enough (see below) that it's not
used in practice.

Selecting a writer
---------------------

Pick the writer for each acquisition entry in the **file-naming wizard**.
The ``plugins['first_image_writer']`` setting in the config file only
controls which writer is pre-selected/listed first there — it does not
restrict which writers are available:

.. code-block:: python

   plugins = {
       'path_list': ["../src/plugins"],
       'first_image_writer': 'MP_OME_Zarr_Writer',
   }

See :doc:`configuration` for the full ``plugins`` config reference.

OME-Zarr (``MP_OME_Zarr_Writer``)
---------------------------------

``MP_OME_Zarr_Writer`` writes `OME-Zarr <https://ngff.openmicroscopy.org/>`_ —
one ``.ome.zarr`` store per acquisition, with each tile in its own subgroup.
It offloads writing to a separate background process via a shared-memory
ring buffer, so a slow disk can't stall acquisition. This is the
**recommended writer for new acquisitions** on systems with fast (NVMe/SSD)
storage.

It saves multi-resolution pyramids natively, with compression, and can
optionally emit an XML file for drag-and-drop stitching in BigStitcher.

Each tile subgroup holds one channel as a ``(z, y, x)`` image. So that other
software can tell the channels apart without reading the file names, each
subgroup also carries an ``omero`` block with that one channel: its laser line
as the label (for example ``"488"``) and a matching false colour. The
:doc:`Data viewer <data_viewer>` reads it to show each channel as its own row.
Data written before this was added has no ``omero`` block.

.. note::

   The single-core ``OME_Zarr_Writer`` was removed in 1.27.0. Acquisition
   lists that name it are written with ``MP_OME_Zarr_Writer``, and a config
   file that still has an ``OME_Zarr_Writer`` dictionary, names it as
   ``first_image_writer``, or sets ``async_finalize`` (its option) gets a
   warning at startup. Move those settings to ``MP_OME_Zarr_Writer``.

.. tip::

   Data already saved as TIFF can be converted to the same OME-Zarr layout
   afterwards with the :doc:`TIFF to OME-Zarr converter <tiff_to_omezarr>`
   (**Utils → Convert TIFF stacks to OME-Zarr**).

.. code-block:: python

   MP_OME_Zarr_Writer = {
       'ome_version': '0.5',          # '0.4' (zarr v2) or '0.5' (zarr v3, sharding supported)
       'generate_multiscales': True,  # False: only save the original resolution
       'compression': 'zstd',         # None, 'zstd', 'lz4'
       'compression_level': 5,        # 1-9
       'shards': (64, 6000, 6000),    # max shard size (z,y,x); ignored if ome_version == '0.4'
       'base_chunks': (64, 256, 256),   # starting chunk size (level 0), (z,y,x)
       'target_chunks': (64, 64, 64),   # chunk size at the highest pyramid level, (z,y,x)

       # BigStitcher-specific
       'write_big_stitcher_xml': True,       # ome_version '0.4' and '0.5'
       'flip_xyz': (True, True, False),      # match BigStitcher coordinates to mesoSPIM axes
       'transpose_xy': False,                # swap X/Y if tile positions come out wrong

       # background process
       'ring_buffer_size': 512,  # frames buffered in shared memory; lower (e.g. 16) for demo/simulation mode
       'write_cache': None,      # optional fast local scratch path, moved to the final folder once a tile finishes
   }

.. note::

   The BigStitcher XML is written for both ``ome_version: '0.4'`` (zarr v2)
   and ``'0.5'`` (zarr v3, sharding). It records the zarr version, so
   BigStitcher opens the container with the matching reader (verified with
   sharded 0.5 data in BigStitcher 3.0.8).

.. important::

   Bigger chunks/shards generally mean fewer, more efficient files; very
   small chunks can noticeably slow down writing on some hardware. The
   defaults above are a reasonable starting point — benchmark on your own
   hardware before changing them for a real acquisition.

MP_OME_Zarr_TCZYX_Writer
-------------------------

The multi-process writer with a different layout on disk: **one
``(t, c, z, y, x)`` store per tile** inside the acquisition's ``.ome.zarr``
group, at ``Mag{mag}_Tile{tile}_Sh{shutter}_Rot{rot}.ome.zarr``. Every channel
of a tile is one index along ``c``, and every time point of a timelapse is
appended along ``t`` of the same store -- the ``_Time###`` suffix mesoSPIM puts
on file names between time points is read for ``t`` and stripped from the path.
Channel names and colours go into the ``omero`` block, the stage position into
the coordinate transformations. A viewer opens each tile as one multichannel,
multi-time-point image.

A channel is a laser line together with its filter, in the order they first
appear in the acquisition table. It is named after the laser (``488``). When
one laser is used with more than one filter, the filter is added to the name
(``488 515LP``), so the two are kept apart.

Tiles and channels are counted over all rows of the acquisition table that are
saved under the same file name. A single row run on its own with *Run selected
acquisition* therefore lands in the same store, at the same channel, as it
would in a run of the whole list.

No chunk or shard spans a channel or a time point: every one covers exactly one
``(t, c)``, so a later stack only ever adds files and nothing on disk is
rewritten. A shard is always exactly one z-chunk deep -- the live pipeline
writes one z-chunk over the full sensor per assignment, so such a shard is one
write; a deeper shard would be read back and rewritten for every chunk landing
in it, and the writer refuses it. Shards are off by default (one file per
chunk); ``mesoSPIM/test/benchmark_omezarr_shards.py`` measures the difference on
your hardware.

.. code-block:: python

   MP_OME_Zarr_TCZYX_Writer = {
       'ome_version': '0.5',            # '0.4' (zarr v2) or '0.5' (zarr v3, sharding supported)
       'generate_multiscales': True,
       'compression': 'zstd',
       'compression_level': 5,
       'shards': None,                  # None, or (z, y, x) with z == base_chunks z
       'base_chunks': (64, 256, 256),
       'target_chunks': (64, 64, 64),
       'ring_buffer_size': 512,
   }

Not supported by this writer: the BigStitcher XML and ``write_cache``.

.. note::

   **A tile's store grows; it is never cut.** Acquiring into a store that is
   already there adds to it:

   * A stack with more planes than the store has makes the store deeper. This
     is what happens when a run is stopped and then started again under the
     same name, or when the channels of a tile have different z ranges. The
     shorter stacks then read as empty planes after their own last plane.
   * A stack finds its channel by the channel's name. A channel the store
     does not have yet is added after the ones it has. So changing the
     acquisition table between two runs (a laser added, a row taken out)
     cannot put a stack on another channel's data.
   * A time point of a timelapse is appended along ``t``.

   Nothing already on disk is rewritten for any of this: every chunk holds one
   time point of one channel, so growing only changes the store's description.

   A stack that ends early is marked in the store itself. Everything after its
   last real plane is empty (zero), and the store's attributes list it under
   ``mesospim_incomplete_stacks`` with its time point, its channel and the
   number of planes it holds. Acquiring that stack again in full removes the
   entry.

H5_BDV_Writer
----------------

Writes all tiles of the acquisition into a **single** ``.h5`` file in
`BigDataViewer <https://imagej.net/plugins/bdv/>`_ format, plus a companion
BigDataViewer/BigStitcher XML — the **easiest stitching workflow in
BigStitcher** of any of the writers here.

.. code-block:: python

   H5_BDV_Writer = {
       'subsamp': ((1, 1, 1),),         # e.g. ((1,1,1), (1,4,4)) for a 2-level (z,y,x) pyramid
       'compression': None,             # None, 'gzip', 'lzf'
       'flip_xyz': (True, True, False), # match BigStitcher coordinates to mesoSPIM axes
       'transpose_xy': False,           # swap X/Y if tile positions come out wrong
   }

.. warning::

   ``subsamp`` (multiscale pyramid) is technically available but **not used
   in practice** — generating pyramids and/or compressing on the fly during
   acquisition can slow writing down by **5-10×**, which most acquisitions
   can't afford. Leave both at their defaults (no subsampling, no
   compression) unless you've specifically benchmarked otherwise.

.. important::

   **Imaris can only open these files if the dataset has a single tile
   (one position, no mosaic), with no compression and no multiscale
   pyramid.**

Tiff_Writer / Big_Tiff_Writer
---------------------------------

One plain, uncompressed, ImageJ-compatible TIFF stack per tile, no pyramid.
``Tiff_Writer`` (regular TIFF) and ``Big_Tiff_Writer`` (the BigTIFF
container format) are both **not limited to 4 GB per file** — pick either;
``Tiff_Writer``'s plain ImageJ-TIFF output has the broadest compatibility
with external viewers and stitchers, and is a safe default if you're unsure
what your downstream tools support. Filenames from both follow the
BigStitcher auto-loader naming convention, so individual-TIFF acquisitions
can still be imported into BigStitcher.

RAW_Writer
-------------

The simplest possible option: a single uncompressed ``uint16`` binary file
per tile (no header, no metadata), written via a direct memory-mapped array.
Useful for custom downstream pipelines that read raw binary directly, or as
a baseline for debugging I/O performance issues in the other writers.

Writer capabilities
-----------------------

.. list-table::
   :widths: 25 15 15 15 15 15
   :header-rows: 1

   * - Writer
     - Chunking
     - Compression
     - Multiscale
     - Overwrite existing
     - dtypes
   * - ``MP_OME_Zarr_Writer``
     - Yes
     - Yes
     - Yes
     - No
     - uint16
   * - ``MP_OME_Zarr_TCZYX_Writer``
     - Yes
     - Yes
     - Yes
     - No
     - uint16
   * - ``H5_BDV_Writer``
     - No
     - No :sup:`*`
     - No :sup:`*`
     - No
     - uint8, uint16, float32
   * - ``Tiff_Writer``
     - No
     - No
     - No
     - No
     - uint8, uint16, float32
   * - ``Big_Tiff_Writer``
     - No
     - No
     - No
     - No
     - uint8, uint16, float32
   * - ``RAW_Writer``
     - No
     - No
     - No
     - No
     - uint16

:sup:`*` ``H5_BDV_Writer`` declares ``supports_compression=False`` and
``supports_multiscale=False`` in its plugin capabilities, but its
``compression`` and ``subsamp`` config keys (above) do work at runtime — a
known inconsistency between the declared capability flags and actual
behavior, not a documentation error.

For the plugin architecture itself (writing a new writer, the
``ImageWriter`` interface, discovery rules), see :doc:`plugins`.
