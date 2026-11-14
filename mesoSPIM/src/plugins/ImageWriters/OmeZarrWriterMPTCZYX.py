import os
import re
from pathlib import Path
import logging
logger = logging.getLogger(__name__)
import numpy as np
from typing import Union

import multiprocessing as mp

from mesoSPIM.src.plugins.ImageWriterApi import (
    WriterCapabilities, WriteRequest, API_VERSION, FileNaming, FinalizeImage
)
from mesoSPIM.src.plugins.ImageWriters.OmeZarrWriterMP import OMEZarrWriterMP
from mesoSPIM.src.utils.acquisitions import AcquisitionList

# Install zarr via pip if needed
from mesoSPIM.src.plugins.utils import install_and_import
install_and_import('zarr', version='3.1.3')
import zarr

from mesoSPIM.src.plugins.support_files.ImageWriters.OmeZarrWriterMP.omezarr_writer import (
    PyramidSpec, ChunkScheme, plan_levels, compute_xy_only_levels, FlushPad, BloscCodec, BloscShuffle,
    CHANNEL_COLORS,
)
from mesoSPIM.src.plugins.support_files.ImageWriters.OmeZarrWriterMP.omezarr_writer_tczyx import (
    TCZYX, omezarr_writer_worker_tczyx,
)

# The time index mesoSPIM writes into file names while running a time lapse. It
# goes in front of the LAST suffix, so 'Sample.ome.zarr' becomes
# 'Sample.ome_Time003.zarr' (see mesoSPIM_AcquisitionManagerWindow.append_time_index_to_filenames).
# Only a mark at the end of the name counts, with nothing but suffixes ('.ome', '.zarr')
# after it. A sample called 'Mouse_Time2h' keeps its name: there the digits are followed
# by more of the name.
_TIME_SUFFIX = re.compile(r'_Time(\d+)(?=(?:\.[A-Za-z][A-Za-z0-9]*)*$)')


class OMEZarrWriterMPTCZYX(OMEZarrWriterMP):
    '''
    A Multiprocess OME-Zarr Image Writer Plugin that writes ONE (t, c, z, y, x) store per tile.

    This is the MP_OME_Zarr_Writer with a different layout on disk and nothing else changed:
    the frames travel through the same shared-memory ring buffer to a writer process that
    runs the same live multiscale pipeline (Live3DPyramidWriter, untouched, writing through
    a window onto [t, c] of the store's arrays; see omezarr_writer_tczyx.py).

    Layout
        <folder>/<Sample>.ome.zarr/Mag{mag}_Tile{tile}_Sh{shutter}_Rot{rot}.ome.zarr
    Every channel of a tile is one index along c, and every time point of a time lapse is
    one index along t. A channel is a laser line together with its filter, in the order
    they first appear in the acquisition table; it is named after the laser ("488"), with
    the filter added when a laser is used with more than one. Tiles and channels are
    counted over all rows of the table that are saved under the same name, so a single row
    run on its own (Run selected acquisition) lands at the same place as in a full run. The
    '_Time###' suffix mesoSPIM appends to file names between time points is read to find t
    and stripped from the path, so all time points of a run land in the same stores.
    Channel names and colours go into the omero block, the stage position into the
    coordinateTransformations, as with the other OME-Zarr writers.

    Chunks and shards never span channels or time points: every chunk and every shard
    covers exactly one (t, c). Which channels and time points still come is not known
    when a store is created, so keeping them out of every shard means a later stack
    only ever ADDS files; nothing already on disk is rewritten.

    A shard is written in one go. The live pipeline buffers complete z-chunks over the
    full sensor and writes each as one assignment, so a shard whose z equals the chunk's z
    is one write. A shard deeper than a chunk would be written in several parts, each
    reading the shard back and rewriting it, which is very slow: this writer therefore
    refuses a shard deeper than the base chunk. Shards are off by default (one file per
    chunk), which measured as fast as sharding for this layout; switch them on to limit
    the number of files.

    Not supported here: the BigStitcher XML (a (z, y, x)-per-stack description; use the
    MP_OME_Zarr_Writer if you need it) and 'write_cache' (moving a finished stack into a
    store that already holds other channels would nest the folders).

    OPTIONAL: Place the following entry into the mesoSPIM configuration file and change as needed

    MP_OME_Zarr_TCZYX_Writer = {
        'ome_version': '0.5', # 0.4 (zarr v2), 0.5 (zarr v3, sharding supported)
        'generate_multiscales': True, #True, False. False: only the primary data is saved. True: multiscale data is generated
        'compression': 'zstd', # None, 'zstd', 'lz4'
        'compression_level': 5, # 1-9
        'shards': None, # None (one file per chunk) or a (z,y,x) tuple; z must equal base_chunks z. Ignored if ome_version "0.4"
        'base_chunks': (64,256,256), # Tuple specifying starting chunk size (multiscale level 0). Bigger chunks, less files (axes: z,y,x)
        'target_chunks': (64,64,64), # Tuple specifying ending chunk size (multiscale highest level). Bigger chunks, less files (axes: z,y,x)

        # Multiprocess options
        'ring_buffer_size': 512, # The number of frames buffered into shared memory for the MP writer.
        }
    '''

    def __init__(self):
        super().__init__()
        # The writer process of each store still running, so that the next channel or
        # time point of a tile waits for the previous one before opening the same store.
        self._store_processes: dict[str, mp.Process] = {}
        # The queues of every process still running. finalize() drops the writer's own
        # references straight away; if the parent's were the last ones, the queues'
        # semaphores would be unlinked while a child that is still starting up (a short
        # stack, a slow disk) tries to attach to them, and it would die on the spot.
        self._queues_in_use: list = []

    @classmethod
    def name(cls) -> str:
        return 'MP_OME_Zarr_TCZYX_Writer'

    @classmethod
    def capabilities(cls):
        return WriterCapabilities(
            dtype=["uint16"],
            ndim=[5],
            supports_chunks=True,
            supports_compression=True,
            supports_multiscale=True,
            supports_overwrite=False,
            streaming_safe=True,
        )

    @classmethod
    def file_extensions(cls) -> Union[None, str, list[str]]:
        # '.zarr' as well: with the time mark in place the name ends in '_Time003.zarr'.
        return ['.ome.zarr', '.zarr']

    @classmethod
    def file_names(cls):
        return FileNaming(
            # Passed to filename_wizard for selection of file formats in UI
            FormatSelectionOption = 'MP OME Zarr TCZYX: ~.ome.zarr', # Selection Box Test when selecting file format
            WindowTitle = "Autogenerate OME Zarr File Names",
            WindowSubTitle = "One (t, c, z, y, x) OME Zarr store per tile, inside one '.ome.zarr' folder",
            WindowDescription = cls.name(), # Unique description to register with ui
            IncludeMag = True,
            IncludeTile = False,
            IncludeChannel = False,    # channels are the c axis of each tile's store
            IncludeFilter = False,
            IncludeShutter = False,
            IncludeRotation = False,
            IncludeSuffix = None,      # Str suffix to be appended to the end of filenames
            SingleFileFormat = True,   # Will all tiles be written into 1 file (.h5 for example)
            IncludeAllChannelsInSingleFileFormat = False,
        )

    @classmethod
    def wizard_parameters(cls):
        '''The settings of this writer: the defaults used by open(), and the rows the
        file-naming wizard shows so that they can be changed for one acquisition.

        {key: (default, help text)}. This writer has its own list because it differs from
        the MP_OME_Zarr_Writer it is built on: shards are off unless asked for, and it has
        no BigStitcher XML and no write cache. Without this list the wizard would offer the
        other writer's rows and quietly switch its shards on.
        '''
        return {
            'ome_version': ('0.5', "'0.4' = zarr v2, no sharding. '0.5' = zarr v3, sharding supported"),
            'generate_multiscales': (True, 'False saves only the full-resolution data'),
            'compression': ('zstd', "None, 'zstd' or 'lz4'. zstd-5 costs almost no speed and saves disk"),
            'compression_level': (5, '1-9'),
            'shards': (None, 'None = one file per chunk. Or a shard size (z,y,x) whose z equals the chunk z, to have fewer files. Ignored for ome_version 0.4'),
            'base_chunks': ((64, 256, 256), 'Chunk size (z,y,x) at multiscale level 0. Bigger chunks = fewer files and better IO'),
            'target_chunks': ((64, 64, 64), 'Chunk size (z,y,x) at the smallest multiscale level'),
            'ring_buffer_size': (512, 'Frames buffered in shared memory for the writer process'),
        }

    @staticmethod
    def split_time_index(uri: str) -> tuple[str, int]:
        '''The acquisition path without its '_Time###' mark, and the time index (0 without one).

        Only the name changes: the folder part is kept as given, separators included, so the
        same path comes back on every operating system.'''
        name = Path(uri).name
        matches = list(_TIME_SUFFIX.finditer(name))
        if not matches:
            return uri, 0
        last = matches[-1]
        folder = uri[:len(uri) - len(name)]
        return folder + name[:last.start()] + name[last.end():], int(last.group(1))

    @staticmethod
    def channel_label(laser: str) -> str:
        '''"488 nm" -> "488"'''
        return laser[:-3] if laser.endswith(' nm') else laser

    def rows_of_this_acquisition(self, acq, acq_list) -> AcquisitionList:
        '''All rows of the acquisition table that are saved under the same name as ``acq``.

        These rows together make up one acquisition on disk, and a stack's tile number and
        channel number are its place among them. They are looked up in the whole table as
        the operator sees it, not only in the list that is being run. "Run selected
        acquisition" runs a list of a single row: counted within that list alone, every
        row would be tile 0 and channel 0, and each would be written over the one before.

        The whole table is mesoSPIM's own (the program's shared state). Where there is
        none, as in a test or a script that drives the writer directly, or where ``acq``
        is not one of its rows, the list that was handed over is used.
        '''
        table = acq_list
        try:
            from mesoSPIM.src.mesoSPIM_State import mesoSPIM_StateSingleton
            state = mesoSPIM_StateSingleton.instance   # the one that exists; calling the class would reset it
            whole = state['acq_list'] if state is not None else None
            if whole is not None and any(row is acq or row == acq for row in whole):
                table = whole
        except Exception:
            logger.debug(f'{self.name()}: the whole acquisition table could not be read', exc_info=True)

        def saved_at(row) -> str:
            return self.split_time_index(str(row['folder']) + '/' + str(row['filename']))[0]

        here = saved_at(acq)
        rows = [row for row in table if saved_at(row) == here]
        return AcquisitionList(rows) if rows else acq_list

    @classmethod
    def channels_of(cls, rows) -> tuple[list[tuple[str, str]], list[str]]:
        '''The channels of an acquisition and their names, in the order they first appear.

        A channel is a laser line together with its filter. Two rows with the same laser
        and different filters are two channels; told apart by the laser alone, the second
        would be written over the first. A channel is named after its laser ("488"). Only
        where a laser is used with more than one filter is the filter added ("488 Empty"),
        so the usual acquisition keeps its short names.
        '''
        channels: list[tuple[str, str]] = []
        for row in rows:
            channel = (str(row['laser']), str(row['filter']))
            if channel not in channels:
                channels.append(channel)
        lasers = [laser for laser, _filter in channels]
        labels = [
            cls.channel_label(laser) if lasers.count(laser) == 1 else f'{cls.channel_label(laser)} {filter_name}'
            for laser, filter_name in channels
        ]
        return channels, labels

    def open(self, req: WriteRequest) -> None:
        assert self.compatible_suffix(req), f'URI suffix not compatible with {self.name()}'

        #############################################################
        ####  Defaults, overridden by the config file / wizard  ###
        #############################################################
        p = {key: default for key, (default, _help) in self.wizard_parameters().items()}
        given = req.writer_config_file_values or {}
        p.update({key: value for key, value in given.items() if key in p})
        ome_version = p['ome_version']
        generate_multiscales = p['generate_multiscales']
        compression = p['compression']
        compression_level = p['compression_level']
        shards = p['shards']
        base_chunks = p['base_chunks']
        target_chunks = p['target_chunks']
        ring_buffer_size = p['ring_buffer_size']
        for unsupported in ('write_big_stitcher_xml', 'write_cache'):
            if given.get(unsupported):
                logger.warning(f"{self.name()}: '{unsupported}' is not supported by this writer and is ignored")

        # The full-resolution data is written in z-chunks this deep (the chunk depth starts at
        # base_chunks z and may not exceed target_chunks z). A shard has to be exactly that deep.
        chunk_depth = min(int(base_chunks[0]), int(target_chunks[0]))
        if shards is not None and ome_version != '0.4' and int(shards[0]) != chunk_depth:
            raise ValueError(
                f"{self.name()}: a shard must be exactly one z-chunk deep so that it is written in one go "
                f"(shards z={shards[0]}, z-chunk={chunk_depth} from base_chunks z={base_chunks[0]} and "
                f"target_chunks z={target_chunks[0]}); a deeper shard would be rewritten for every chunk landing in it"
            )

        # Save req so metadata_file_info can see it
        self.req = req
        acq = req.acq
        acq_list = req.acq_list

        # Logic for naming files and metadata
        # Define descriptive group name for this tile: the channel is an index inside the store
        mag = acq['zoom'][:-1]  # remove x at end
        rot = acq['rot']
        shutter_id = acq['shutterconfig']
        shutter_id = 0 if shutter_id == 'Left' else 1
        # The stack's place in its acquisition: which tile, and which channel of that tile.
        rows = self.rows_of_this_acquisition(acq, acq_list)
        tile = rows.get_tile_index(acq)
        group_name = f'Mag{mag}_Tile{tile}_Sh{shutter_id}_Rot{rot}.ome.zarr'
        self.omezarr_group_name = group_name

        # Where this stack lands: the store of its tile, at (t, c)
        self.acquisition_path, time_index = self.split_time_index(req.uri)
        channels, channel_labels = self.channels_of(rows)
        self.channel_label_of_this_stack = channel_labels[channels.index((str(acq['laser']), str(acq['filter'])))]
        channel_colors = [CHANNEL_COLORS.get(self.channel_label(laser), '') for laser, _filter in channels]
        # For the Data viewer, so that its preview of this stack carries the same tile
        # number and channel names as the store on disk.
        self.tile_index_of_this_stack = tile
        self.tile_count_of_this_acquisition = rows.get_n_tiles()
        self.rows_of_this_stacks_acquisition = rows
        self.current_acquire_file_path = self.acquisition_path + '/' + self.omezarr_group_name
        self.metadata_file_path = req.uri + '_' + self.omezarr_group_name + '_meta.txt'

        # create the acquisition group on the first stack of the run
        if time_index == 0 and acq == acq_list[0]:
            zarr_version = 2 if ome_version == "0.4" else 3
            zarr.open_group(self.acquisition_path, mode="a", zarr_version=zarr_version)
        self.xml_writer = None

        # An earlier stack of this same store (another channel, or the previous time point)
        # may still be finishing in the background: its trim of z and this stack's growth of t
        # must not meet. Other tiles keep running in the background as before.
        self._wait_for_store(self.current_acquire_file_path)

        # A stack finds its channel in the store by the channel's name. A store that is
        # there already keeps the channels it has, in their order, and the channels of this
        # run that it does not have yet are added after them. So a tile acquired again after
        # the table was changed (a laser added, a row taken out) still puts every stack in
        # its own channel, and nothing lands on another channel's data.
        store_shape, in_store = self._store_as_it_is(self.current_acquire_file_path)
        known = [str(channel.get('label', '')) for channel in in_store]
        added = [label for label in channel_labels if label not in known]
        if in_store and added:
            logger.info(f"{self.name()}: {group_name} gets {len(added)} more channel(s): {', '.join(added)}")
        colour_of = dict(zip(channel_labels, channel_colors))
        colour_of.update({str(channel.get('label', '')): str(channel.get('color', '')) for channel in in_store})
        channel_labels = known + added
        channel_colors = [colour_of.get(label, '') for label in channel_labels]
        channel_index = channel_labels.index(self.channel_label_of_this_stack)
        self.channel_labels_of_this_acquisition = tuple(channel_labels)

        px_size_zyx = (req.z_res, req.y_res, req.x_res)

        # ZARR Writer setup
        Z_EST, Y, X = (req.shape[0], req.shape[2], req.shape[1])
        if store_shape is not None and store_shape[2] < Z_EST:
            # The store is made deeper for this stack (omezarr_writer_tczyx.py). It was
            # shallower because an earlier stack of the tile was stopped, or was planned
            # with a shorter z range; those stacks then end in empty planes.
            logger.info(f"{self.name()}: {group_name} is {store_shape[2]} planes deep and is made "
                        f"{Z_EST} planes deep for this stack")

        xy_levels = compute_xy_only_levels(px_size_zyx)
        if generate_multiscales:
            levels = plan_levels(Y, X, Z_EST, xy_levels, min_dim=64)
        else:
            levels = 1

        spec = PyramidSpec(
            z_size_estimate=Z_EST,  # big upper bound; we'll truncate at the end
            y=Y, x=X, levels=levels,
        )

        shard_shape = tuple(shards) if shards is not None else None
        scheme = ChunkScheme(base=tuple(base_chunks), target=tuple(target_chunks))

        compressor = compression
        if compression:
            compressor = BloscCodec(cname=compression, clevel=compression_level, shuffle=BloscShuffle.bitshuffle)

        tczyx = TCZYX(
            t=time_index,
            c=channel_index,
            n_channels=len(channel_labels),
            channel_labels=tuple(channel_labels),
            # The colour goes by the laser line, with or without a filter in the name.
            channel_colors=tuple(channel_colors),
        )

        # Setup multiprocessing ring buffer
        self._create_shared_ringbuffer(ring_buffer_size, req.shape[1], req.shape[2])
        shm_name = self._shm.name

        # --- Create queues ---
        ctx = mp.get_context("spawn")
        self._work_q = ctx.Queue(maxsize=ring_buffer_size)
        self._free_q = ctx.Queue(maxsize=ring_buffer_size)

        # Initialize free-slot queue with all indices
        for i in range(ring_buffer_size):
            self._free_q.put(i)

        # --- Spawn writer process, which owns Live3DPyramidWriter ---
        writer_kwargs = dict(
            spec=spec,
            voxel_size=px_size_zyx,
            path=self.current_acquire_file_path,
            ingest_queue_size=256,
            max_workers=2,
            max_inflight_chunks=8,
            chunk_scheme=scheme,
            compressor=compressor,
            shard_shape=shard_shape,
            flush_pad=FlushPad.DUPLICATE_LAST,
            async_close=False, # Force sync close to ensure all data is written before proceeding, sync not compatible with Multiprocess
            translation=(acq['z_start'], acq['y_pos'], acq['x_pos']),
            ome_version=ome_version,
            tczyx=tczyx,
        )

        # The writer process sets this once it holds the shared memory. finalize() waits for
        # it before letting go of its own handle: on Windows the memory disappears with its
        # last handle, so a stack that ends before the process has started (a very short
        # stack, or Stop right after this point) would otherwise be lost.
        self._writer_attached = ctx.Event()
        self._writer_proc = ctx.Process(
            target=omezarr_writer_worker_tczyx,  # From omezarr_writer_tczyx.py
            args=(shm_name, (Y, X), ring_buffer_size, writer_kwargs, self._work_q, self._free_q,
                  self._writer_attached),
            daemon=True,
        )

        self._writer_proc.start()

        # remember this writer as "in the background", and as the one holding this store
        self._background_writers.append((self._writer_proc, shm_name))
        self._store_processes[self.current_acquire_file_path] = self._writer_proc
        self._queues_in_use.append((self._work_q, self._free_q))
        logger.debug("Added a new writer process to the list, total writer processes running: %d", len(self._background_writers))

        self.omezarr_writer = None  # keep attribute for compatibility

        self.metadata_file_info()

    def finalize(self, finalize_image: FinalizeImage) -> None:
        super().finalize(finalize_image)
        acq = finalize_image.acq
        acq_list = finalize_image.acq_list
        if acq == acq_list[-1]:
            # The end of a time point: let every store settle before the next time point
            # (or the end of the run) so nothing is left half-written in the background.
            self._wait_for_background_writers()
            self._store_processes.clear()
            self._queues_in_use.clear()

    def _store_as_it_is(self, store_path: str) -> tuple[tuple | None, list[dict]]:
        '''What a tile's store holds already: the shape of its full-resolution array and the
        channels it names, each as its entry in the omero block (``label``, ``color``). An
        empty answer, ``(None, [])``, for a store that is not there yet or cannot be read.'''
        try:
            if not os.path.isdir(store_path):
                return None, []
            root = zarr.open_group(store_path, mode='r')
            shape = tuple(root['0'].shape) if '0' in root else None
            attrs = dict(root.attrs)
            omero = attrs.get('ome', {}).get('omero') or attrs.get('omero') or {}
            channels = [dict(channel) for channel in omero.get('channels', []) if isinstance(channel, dict)]
        except Exception:
            logger.debug(f'{self.name()}: could not read the existing store {store_path}', exc_info=True)
            return None, []
        if shape is None or len(shape) != 5:
            return None, []
        return shape, channels

    def _wait_for_store(self, store_path: str) -> None:
        '''Block until the writer process last given this store has finished.'''
        proc = self._store_processes.pop(store_path, None)
        if proc is None or not proc.is_alive():
            return
        logger.info(f'{self.name()}: waiting for the previous stack of {Path(store_path).name} to finish')
        proc.join()

    def metadata_file_info(self) -> str:
        """
        Return the file name for the current metadata file.
        One metadata file per stack: the store name says which tile, the suffix which channel,
        and the time index stays in req.uri.
        """
        # A channel name may hold a filter's name, with spaces or a slash: not in a file name.
        channel = re.sub(r'[^A-Za-z0-9.-]+', '_', self.channel_label_of_this_stack)
        self.metadata_file = self.req.uri + f'_{self.omezarr_group_name}_Ch{channel}_meta.txt'
        self.metadata_file_describes_this_path = Path(self.current_acquire_file_path).as_posix()

        # Placeholder prior to adding data processing plugins
        path = Path(self.req.uri + f'_{self.omezarr_group_name}_Ch{channel}')
        self.MIP_path = path.with_name('MAX_' + path.name + '.tif').as_posix()
