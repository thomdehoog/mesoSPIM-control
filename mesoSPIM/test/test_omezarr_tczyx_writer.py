"""
The MP_OME_Zarr_TCZYX_Writer: one (t, c, z, y, x) store per tile, channels along c,
time points appended along t, no chunk or shard ever spanning either.

Run from the repository root:
    python -m pytest mesoSPIM/test/test_omezarr_tczyx_writer.py

Needs numpy, zarr>=3, psutil and indexed (what the writer itself needs); no Qt, no hardware.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

zarr = pytest.importorskip("zarr")

from mesoSPIM.src.plugins.ImageWriterApi import WriteRequest, WriteImage, FinalizeImage
from mesoSPIM.src.plugins.ImageWriters.OmeZarrWriterMPTCZYX import OMEZarrWriterMPTCZYX
from mesoSPIM.src.utils.acquisitions import Acquisition, AcquisitionList

PLANES, Y, X = 20, 256, 192         # small, but big enough for a second pyramid level; frames arrive as (X, Y)
# The writers store a frame as it arrives: the store's y is the request's X and its x the request's Y
# (see 'Z_EST, Y, X = (req.shape[0], req.shape[2], req.shape[1])' in the MP writer).
LASERS = ('488 nm', '561 nm')
TILES = ((100.0, 200.0), (100.0, 296.0))   # (x_pos, y_pos) in um


def acquisition_list(folder: Path, time_index: int) -> AcquisitionList:
    acqs = []
    for x_pos, y_pos in TILES:
        for laser in LASERS:
            acqs.append(Acquisition(
                x_pos=x_pos, y_pos=y_pos, z_start=10.0, z_end=10.0 + 5.0 * (PLANES - 1), z_step=5.0,
                planes=PLANES, laser=laser, filter='Empty', zoom='1x', shutterconfig='Left',
                folder=str(folder), filename=f'Sample.ome_Time{time_index:03d}.zarr',  # as mesoSPIM names a time point
                image_writer_plugin=OMEZarrWriterMPTCZYX.name(),
            ))
    return AcquisitionList(acqs)


def frame(t: int, c: int, tile: int, z: int) -> np.ndarray:
    """A plane whose values say where it belongs."""
    value = 1000 * t + 100 * c + 10 * tile + z
    return np.full((X, Y), value, dtype=np.uint16)


def write_time_point(folder: Path, time_index: int, config: dict) -> None:
    """Drive the plugin the way mesoSPIM_ImageWriter does: a fresh writer per time point,
    open/write_frame/finalize per stack."""
    acq_list = acquisition_list(folder, time_index)
    writer = OMEZarrWriterMPTCZYX()
    for acq in acq_list:
        tile = acq_list.get_tile_index(acq)
        c = acq_list.find_value_index(acq['laser'], 'laser')
        req = WriteRequest(
            uri=os.path.join(acq['folder'], acq['filename']),
            shape=(PLANES, Y, X), dtype='uint16', axes='ZYX',
            x_res=1.0, y_res=1.0, z_res=5.0, unit='microns',
            num_tiles=acq_list.get_n_tiles(), num_channels=acq_list.get_n_lasers(),
            num_rotations=acq_list.get_n_angles(), num_shutters=acq_list.get_n_shutter_configs(),
            acq=acq, acq_list=acq_list, writer_config_file_values=config,
        )
        writer.open(req)
        for z in range(PLANES):
            writer.write_frame(WriteImage(
                image=frame(time_index, c, tile, z), current_image_counter=z, tile_number=tile,
                laser=acq['laser'], shutter=acq['shutterconfig'], rot=acq['rot'],
                x_res=1.0, y_res=1.0, z_res=5.0, acq=acq, acq_list=acq_list,
            ))
        writer.finalize(FinalizeImage(acq=acq, acq_list=acq_list))


def files_under(root: Path) -> dict:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in root.rglob('*') if p.is_file()}


@pytest.mark.parametrize("config", [
    pytest.param({'ome_version': '0.5', 'shards': None, 'base_chunks': (8, 64, 64), 'target_chunks': (8, 32, 32)}, id="v3-chunks"),
    pytest.param({'ome_version': '0.5', 'shards': (8, 192, 256), 'base_chunks': (8, 64, 64), 'target_chunks': (8, 32, 32)}, id="v3-shards"),
    pytest.param({'ome_version': '0.4', 'base_chunks': (8, 64, 64), 'target_chunks': (8, 32, 32)}, id="v2"),
])
def test_two_time_points_of_two_tiles_and_channels(tmp_path, config):
    write_time_point(tmp_path, 0, config)
    acquisition = tmp_path / 'Sample.ome.zarr'
    stores = sorted(p.name for p in acquisition.iterdir() if p.is_dir())
    assert stores == ['Mag1_Tile0_Sh0_Rot0.ome.zarr', 'Mag1_Tile1_Sh0_Rot0.ome.zarr']
    assert not list(tmp_path.glob('*Time*')), "the time mark never reaches the disk"

    before = files_under(acquisition)
    write_time_point(tmp_path, 1, config)
    after = files_under(acquisition)

    # Appending a time point only adds files: nothing written for t=0 is touched again,
    # which is what keeps every shard a single write.
    rewritten = [name for name, stamp in before.items() if after.get(name) != stamp and not name.endswith(('zarr.json', '.zarray', '.zattrs', '.zgroup'))]
    assert rewritten == [], rewritten
    assert len(after) > len(before)

    v2 = config['ome_version'] == '0.4'
    for tile, store in enumerate(stores):
        root = zarr.open_group(acquisition / store, mode='r')
        attrs = dict(root.attrs)
        ome = attrs if v2 else attrs['ome']
        multiscale = ome['multiscales'][0]
        assert (multiscale.get('version') if v2 else ome['version']) == config['ome_version']
        assert [axis['name'] for axis in multiscale['axes']] == ['t', 'c', 'z', 'y', 'x']
        assert multiscale['axes'][1]['type'] == 'channel'
        transforms = multiscale['datasets'][0]['coordinateTransformations']
        assert transforms[0]['scale'] == [1.0, 1.0, 5.0, 1.0, 1.0]
        assert transforms[1]['translation'] == [0.0, 0.0, 10.0, TILES[tile][1], TILES[tile][0]]
        assert [channel['label'] for channel in ome['omero']['channels']] == ['488', '561']
        assert ome['omero']['channels'][0]['color'] == '00FF66'

        level0 = root['0']
        assert level0.shape == (2, 2, PLANES, X, Y)
        assert level0.chunks[:2] == (1, 1)
        if config.get('shards'):
            assert level0.shards[:2] == (1, 1)
            assert level0.shards[2] == level0.chunks[2], "a shard is one z-chunk deep: one write"
        for t in range(2):
            for c in range(2):
                expected = np.stack([frame(t, c, tile, z) for z in range(PLANES)])
                assert np.array_equal(level0[t, c], expected), (tile, t, c)
        level1 = root['1']
        assert level1.shape[:2] == (2, 2) and level1.chunks[:2] == (1, 1)
        assert level1[1, 1].max() > 0, "the coarser level was written for the last stack too"


def test_the_time_suffix_is_read_and_stripped():
    split = OMEZarrWriterMPTCZYX.split_time_index
    # mesoSPIM's own placing of the mark, before the last suffix
    assert split('/data/run/Sample.ome_Time003.zarr') == ('/data/run/Sample.ome.zarr', 3)
    assert split('/data/run/Sample.ome.zarr') == ('/data/run/Sample.ome.zarr', 0)
    assert split('/data/run/Sample_Time003.ome.zarr') == ('/data/run/Sample.ome.zarr', 3)
    assert split(r'C:\data\run\Sample.ome_Time012.zarr') == (r'C:\data\run\Sample.ome.zarr', 12)
    # '_Time' inside a sample's own name is part of the name, not a time point
    assert split('/data/run/Mouse_Time2h.ome.zarr') == ('/data/run/Mouse_Time2h.ome.zarr', 0)
    assert split('/data/run/Mouse_Time2h.ome_Time004.zarr') == ('/data/run/Mouse_Time2h.ome.zarr', 4)
    assert OMEZarrWriterMPTCZYX.channel_label('488 nm') == '488'


def test_a_shard_deeper_than_a_chunk_is_refused(tmp_path):
    acq_list = acquisition_list(tmp_path, 0)
    acq = acq_list[0]
    req = WriteRequest(
        uri=os.path.join(acq['folder'], acq['filename']), shape=(PLANES, Y, X), dtype='uint16', axes='ZYX',
        x_res=1.0, y_res=1.0, z_res=5.0, num_tiles=2, num_channels=2, num_rotations=1, num_shutters=1,
        acq=acq, acq_list=acq_list,
        writer_config_file_values={'shards': (16, 192, 256), 'base_chunks': (8, 64, 64), 'target_chunks': (8, 32, 32)},
    )
    with pytest.raises(ValueError, match="one z-chunk deep"):
        OMEZarrWriterMPTCZYX().open(req)


def _request(folder: Path, config: dict, row: int = 0, planes: int = PLANES) -> WriteRequest:
    acq_list = acquisition_list(folder, 0)
    acq = acq_list[row]
    return WriteRequest(
        uri=os.path.join(acq['folder'], acq['filename']), shape=(planes, Y, X), dtype='uint16', axes='ZYX',
        x_res=1.0, y_res=1.0, z_res=5.0, num_tiles=2, num_channels=2, num_rotations=1, num_shutters=1,
        acq=acq, acq_list=acq_list, writer_config_file_values=config,
    )


SMALL_CHUNKS = {'base_chunks': (8, 64, 64), 'target_chunks': (8, 32, 32)}


def test_a_shard_is_compared_with_the_chunk_depth_really_used(tmp_path):
    """The z-chunk of the full-resolution data is the smaller of base and target z. With base
    z 16 and target z 8 the chunks are 8 deep, so a shard 16 deep would be written in two parts."""
    config = {'shards': (16, 192, 256), 'base_chunks': (16, 64, 64), 'target_chunks': (8, 32, 32)}
    with pytest.raises(ValueError, match="one z-chunk deep"):
        OMEZarrWriterMPTCZYX().open(_request(tmp_path, config))


def test_the_wizard_offers_this_writers_own_settings():
    """The file-naming wizard shows these rows and hands their values to open(). The
    MP_OME_Zarr_Writer's rows would switch shards on and offer settings this writer ignores."""
    from mesoSPIM.src.plugins.ImageWriters.OmeZarrWriterMP import OMEZarrWriterMP

    rows = OMEZarrWriterMPTCZYX.wizard_parameters()
    assert rows['shards'][0] is None, "shards are off unless asked for"
    for unsupported in ('write_big_stitcher_xml', 'flip_xyz', 'transpose_xy', 'write_cache'):
        assert unsupported in OMEZarrWriterMP.wizard_parameters() and unsupported not in rows
    for key, (default, _help) in rows.items():
        assert key in OMEZarrWriterMP.wizard_parameters(), key


def test_a_stack_that_ends_at_once_is_still_written(tmp_path):
    """Stop pressed right after a stack was prepared: finalize() follows open() before the
    writer process has started. The writer has to wait until the process holds the shared
    memory, because on Windows the memory is gone once the microscope's side lets go of it."""
    writer = OMEZarrWriterMPTCZYX()
    request = _request(tmp_path, SMALL_CHUNKS)
    writer.open(request)
    attached, process = writer._writer_attached, writer._writer_proc
    assert attached is not None, "the writer process is given an event to set once it has attached"
    writer.finalize(FinalizeImage(acq=request.acq, acq_list=request.acq_list))
    assert attached.is_set(), "finalize() returned before the writer process had the shared memory"
    process.join(60)
    assert process.exitcode == 0
    writer._wait_for_background_writers()
    store = tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr'
    assert zarr.open_group(store, mode='r')['0'].shape[:2] == (1, 2)


def _write_stack(writer, request, planes: int) -> None:
    writer.open(request)
    for z in range(planes):
        writer.write_frame(WriteImage(
            image=frame(0, 0, 0, z), current_image_counter=z, tile_number=0,
            laser=request.acq['laser'], shutter=request.acq['shutterconfig'], rot=request.acq['rot'],
            x_res=1.0, y_res=1.0, z_res=5.0, acq=request.acq, acq_list=request.acq_list,
        ))
    writer.finalize(FinalizeImage(acq=request.acq, acq_list=request.acq_list))
    writer._wait_for_background_writers()


def test_a_writer_process_that_dies_does_not_hang_the_acquisition(tmp_path, caplog):
    """The writer process of a stack dies halfway (a full disk, a folder that has gone).
    The microscope's side used to wait for it forever, with the Stop button frozen too. It
    now drops the rest of that stack, says so once, and writes the next stack as usual."""
    import logging
    import time

    writer = OMEZarrWriterMPTCZYX()
    request = _request(tmp_path, SMALL_CHUNKS)
    writer.open(request)
    process = writer._writer_proc
    image = lambda z: WriteImage(
        image=frame(0, 0, 0, z), current_image_counter=z, tile_number=0,
        laser=request.acq['laser'], shutter=request.acq['shutterconfig'], rot=request.acq['rot'],
        x_res=1.0, y_res=1.0, z_res=5.0, acq=request.acq, acq_list=request.acq_list,
    )
    writer.write_frame(image(0))
    assert writer.failure is None
    process.kill()
    process.join(30)

    started = time.monotonic()
    with caplog.at_level(logging.ERROR):
        for z in range(1, 3 * writer._ring_size):          # more frames than the buffer has places
            writer.write_frame(image(z))
    assert time.monotonic() - started < 30, "write_frame() waited for a process that is gone"
    said = [record for record in caplog.records if 'ended unexpectedly' in record.getMessage()]
    assert len(said) == 1, "the loss is reported once, not once per frame"
    assert 'NOT SAVING' in writer.failure, "the operator's status line is given a sentence"

    started = time.monotonic()
    writer.finalize(FinalizeImage(acq=request.acq, acq_list=request.acq_list))
    assert time.monotonic() - started < 30, "finalize() waited for a process that is gone"
    assert writer.failure is None, "the sentence belongs to the stack that was lost"

    # The next stack, the tile's other channel, gets a new process and is written whole.
    following = _request(tmp_path, SMALL_CHUNKS, row=1)
    _write_stack(writer, following, planes=PLANES)
    store = tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr'
    assert zarr.open_group(store, mode='r')['0'][0, 1, -1].max() > 0


def test_stop_does_not_hang_on_a_writer_process_that_has_died(tmp_path):
    """Stop is pressed after the writer process has died with its queue full. abort() used
    to wait forever for room in that queue."""
    import time

    writer = OMEZarrWriterMPTCZYX()
    request = _request(tmp_path, SMALL_CHUNKS)
    writer.open(request)
    writer._writer_attached.wait(60)
    writer._writer_proc.kill()
    writer._writer_proc.join(30)
    for slot in range(writer._ring_size):                   # what write_frame() leaves behind
        writer._work_q.put(writer._free_q.get(timeout=5))
    started = time.monotonic()
    writer.abort()
    assert time.monotonic() - started < 30
    assert writer._shm is None


def test_running_again_over_a_stopped_run_writes_the_whole_stack(tmp_path):
    """A stack stopped at plane 7 leaves a store 7 planes deep. Run again under the same
    name, the full stack used to be cut to those 7 planes without a word. The store is now
    made as deep as the stack needs."""
    _write_stack(OMEZarrWriterMPTCZYX(), _request(tmp_path, SMALL_CHUNKS), planes=7)   # stopped early
    store = tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr'
    assert zarr.open_group(store, mode='r')['0'].shape[2] == 7
    _write_stack(OMEZarrWriterMPTCZYX(), _request(tmp_path, SMALL_CHUNKS), planes=PLANES)
    root = zarr.open_group(store, mode='r')
    assert root['0'].shape == (1, 2, PLANES, X, Y)
    assert np.array_equal(root['0'][0, 0], np.stack([frame(0, 0, 0, z) for z in range(PLANES)]))
    assert root['1'][0, 0, -1].max() > 0, "the coarser copy was made deeper and written to its last plane too"
    assert 'mesospim_incomplete_stacks' not in root.attrs, "the stack is whole now"


def test_a_deeper_stack_makes_the_store_deeper_and_the_shorter_one_ends_empty(tmp_path):
    """Two channels of a tile with different z ranges: twelve planes, then twenty. The
    store takes the deeper one in full; the shorter channel reads as empty after its own
    last plane, not as copies of it."""
    table = _rows(tmp_path, (100.0, '488 nm', 'Empty'), (100.0, '561 nm', 'Empty'))
    writer = OMEZarrWriterMPTCZYX()
    _run(writer, table[0], table, 1000, planes=12, expected=12)
    _run(writer, table[1], table, 2000, planes=PLANES, expected=PLANES)
    root = zarr.open_group(tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr', mode='r')
    assert root['0'].shape[:3] == (1, 2, PLANES)
    assert np.array_equal(root['0'][0, 1, :, 0, 0], 2000 + np.arange(PLANES)), "the deeper stack in full"
    assert np.array_equal(root['0'][0, 0, :12, 0, 0], 1000 + np.arange(12))
    assert not root['0'][0, 0, 12:].any(), "nothing after the shorter stack's last plane"
    assert 'mesospim_incomplete_stacks' not in root.attrs, "both stacks are as long as they were planned"


def test_a_stack_finds_its_channel_by_name_when_the_table_has_changed(tmp_path):
    """A tile with 488 and 561 on disk. The table is changed: 561 is taken out and 640
    added. Counted in the new table, 640 would be channel 1 and land on the 561 data. It is
    given a channel of its own, and a second 488 stack still lands on 488."""
    before = _rows(tmp_path, (100.0, '488 nm', 'Empty'), (100.0, '561 nm', 'Empty'))
    writer = OMEZarrWriterMPTCZYX()
    for value, acq in zip((1000, 2000), before):
        _run(writer, acq, before, value)
    after = _rows(tmp_path, (100.0, '488 nm', 'Empty'), (100.0, '640 nm', 'Empty'))
    writer = OMEZarrWriterMPTCZYX()
    _run(writer, after[1], after, 3000)
    _run(writer, after[0], after, 1500)
    root = zarr.open_group(tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr', mode='r')
    channels = root.attrs['ome']['omero']['channels']
    assert [channel['label'] for channel in channels] == ['488', '561', '640']
    assert channels[2]['color'] == 'FF33FF' and channels[1]['color'] == 'FFBF1A', "each keeps its colour"
    assert root['0'].shape[:3] == (1, 3, PLANES) and root['1'].shape[1] == 3
    assert [int(root['0'][0, c, 0, 0, 0]) for c in range(3)] == [1500, 2000, 3000]
    assert writer.channel_labels_of_this_acquisition == ('488', '561', '640'), "what the Data viewer is told"


def test_every_channel_is_given_a_colour_and_a_contrast(tmp_path):
    """The OME-Zarr rules ask every channel for a colour and four contrast numbers, and
    programs that check a file refuse one without them. A laser line the writer has no
    colour for used to be written with neither."""
    table = _rows(tmp_path, (100.0, '488 nm', 'Empty'), (100.0, '594 nm', 'Empty'), (100.0, 'Lamp', 'Empty'))
    writer = OMEZarrWriterMPTCZYX()
    for value, acq in zip((1000, 2000, 3000), table):
        _run(writer, acq, table, value)
    root = zarr.open_group(tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr', mode='r')
    channels = root.attrs['ome']['omero']['channels']
    assert [channel['label'] for channel in channels] == ['488', '594', 'Lamp']
    for channel in channels:
        assert len(channel['color']) == 6 and int(channel['color'], 16) >= 0
        assert channel['window'] == {'min': 0, 'max': 65535, 'start': 0, 'end': 65535}
    assert channels[0]['color'] == '00FF66'
    assert len({channel['color'] for channel in channels}) == 3, "three channels, three colours"
    assert writer.channel_colour_of_this_stack == channels[2]['color'], "what the Data viewer's preview is told"


def test_a_colour_follows_the_wavelength():
    from mesoSPIM.src.plugins.support_files.ImageWriters.OmeZarrWriterMP.omezarr_writer import (
        CHANNEL_COLORS, channel_colour,
    )
    for line, colour in CHANNEL_COLORS.items():
        assert channel_colour(line) == colour and channel_colour(f'{line} 525/50') == colour
    rgb = lambda colour: tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))
    red, green, blue = rgb(channel_colour('473'))
    assert blue > red and green > red, "a 473 nm line reads blue-green"
    red, green, blue = rgb(channel_colour('594'))
    assert red > green > blue, "a 594 nm line reads orange-red"
    assert channel_colour('532') != channel_colour('561') != channel_colour('594')
    assert channel_colour('850') == 'FFFFFF', "past the red end there is no colour to give"
    assert channel_colour('Lamp', 0) != channel_colour('LED', 1), "names without a number take colours in turn"


def test_a_contrast_someone_chose_survives_a_new_channel(tmp_path):
    """A later stack adds a channel and the store's channel list is written again. What
    the list said about the channels it had, a contrast set in another program say, stays."""
    table = _rows(tmp_path, (100.0, '488 nm', 'Empty'))
    writer = OMEZarrWriterMPTCZYX()
    _run(writer, table[0], table, 1000)
    store = tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr'
    root = zarr.open_group(store, mode='a')
    ome = dict(root.attrs['ome'])
    ome['omero']['channels'][0]['window'] = {'min': 0, 'max': 65535, 'start': 120, 'end': 4000}
    root.attrs['ome'] = ome
    more = _rows(tmp_path, (100.0, '488 nm', 'Empty'), (100.0, '561 nm', 'Empty'))
    _run(writer, more[1], more, 2000)
    channels = zarr.open_group(store, mode='r').attrs['ome']['omero']['channels']
    assert [channel['label'] for channel in channels] == ['488', '561']
    assert channels[0]['window']['end'] == 4000
    assert channels[1]['window']['end'] == 65535


def test_the_smallest_copy_of_a_camera_frame_is_a_few_dozen_pixels():
    """What a viewer loads to show a hundred tiles at once is each tile's smallest copy."""
    from mesoSPIM.src.plugins.ImageWriters import OmeZarrWriterMPTCZYX as plugin
    from mesoSPIM.src.plugins.support_files.ImageWriters.OmeZarrWriterMP.omezarr_writer import (
        level_factors, plan_levels,
    )
    levels = plan_levels(5056, 2960, 2000, 1, min_dim=plugin.OVERVIEW_MIN_PIXELS)
    _zf, yf, xf = level_factors(levels - 1, 1)
    smallest = (-(-5056 // yf), -(-2960 // xf))
    assert 16 <= min(smallest) < 32 and max(smallest) < 64, smallest


def test_a_store_made_with_fewer_copies_keeps_that_number(tmp_path):
    """A store from an earlier version of the writer has fewer, larger copies. A stack
    added now follows it. Copies that hold only some of the channels would be worse."""
    from mesoSPIM.src.plugins.ImageWriters import OmeZarrWriterMPTCZYX as plugin

    writer = OMEZarrWriterMPTCZYX()
    was = plugin.OVERVIEW_MIN_PIXELS
    plugin.OVERVIEW_MIN_PIXELS = 64                    # as the earlier version planned
    try:
        _write_stack(writer, _request(tmp_path, SMALL_CHUNKS, row=0), planes=PLANES)
    finally:
        plugin.OVERVIEW_MIN_PIXELS = was
    store = tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr'
    before = sorted(zarr.open_group(store, mode='r').array_keys())
    _write_stack(writer, _request(tmp_path, SMALL_CHUNKS, row=1), planes=PLANES)
    root = zarr.open_group(store, mode='r')
    assert sorted(root.array_keys()) == before == ['0', '1']
    assert len(root.attrs['ome']['multiscales'][0]['datasets']) == 2

    fresh = tmp_path / 'fresh'
    fresh.mkdir()
    _write_stack(OMEZarrWriterMPTCZYX(), _request(fresh, SMALL_CHUNKS, row=0), planes=PLANES)
    made = zarr.open_group(fresh / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr', mode='r')
    assert len(list(made.array_keys())) > 2, "a new store goes on to smaller copies"
    assert min(made[str(len(list(made.array_keys())) - 1)].shape[3:]) >= 16


@pytest.mark.parametrize("config", [
    SMALL_CHUNKS,
    {**SMALL_CHUNKS, 'shards': (8, 192, 256)},
    {**SMALL_CHUNKS, 'ome_version': '0.4'},
], ids=['chunks', 'shards', 'zarr2'])
def test_the_data_viewer_can_tell_a_finished_tile_without_listing_it(tmp_path, config):
    """The Data viewer takes an old tile in without listing its folders when every stack
    ends in its last file. It works that file's name out from the store's description, so
    the name has to be right for every way this writer lays a store out."""
    from mesoSPIM.src.mesospim_viewer import Progress, read_store
    from mesoSPIM.src.mesospim_viewer.omezarr import read_levels

    writer = OMEZarrWriterMPTCZYX()
    _write_stack(writer, _request(tmp_path, config, row=0), planes=PLANES)
    store_path = tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr'
    store = read_store(store_path)
    progress = Progress(store, read_levels(store)[0])
    assert progress.stacks() == [(0, 0), (0, 1)]
    assert (store_path / progress.last_file((0, 0))).is_file()
    assert not (store_path / progress.last_file((0, 1))).is_file(), "the second channel is not acquired yet"
    _write_stack(writer, _request(tmp_path, config, row=1), planes=PLANES)
    assert (store_path / progress.last_file((0, 1))).is_file()


def test_every_stack_begun_in_a_store_that_is_there_is_noted_in_its_description(tmp_path):
    """A stack acquired a second time changes neither the store's shape nor its channels.
    The note the writer leaves rewrites the store's description file, and that is how the
    Data viewer learns, from one small file, that the store is being written again."""
    from mesoSPIM.src.mesospim_viewer import read_store

    writer = OMEZarrWriterMPTCZYX()
    _write_stack(writer, _request(tmp_path, SMALL_CHUNKS, row=0), planes=PLANES)
    store_path = tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr'
    assert 'mesospim_last_stack' not in zarr.open_group(store_path, mode='r').attrs
    before = read_store(store_path).stamp
    _write_stack(writer, _request(tmp_path, SMALL_CHUNKS, row=1), planes=PLANES)
    noted = zarr.open_group(store_path, mode='r').attrs['mesospim_last_stack']
    assert (noted['t'], noted['c']) == (0, 1) and noted['began']
    after = read_store(store_path).stamp
    assert after != before
    _write_stack(writer, _request(tmp_path, SMALL_CHUNKS, row=1), planes=PLANES)      # the same stack once more
    assert read_store(store_path).shape == (1, 2, PLANES, X, Y)
    assert read_store(store_path).stamp != after or \
        zarr.open_group(store_path, mode='r').attrs['mesospim_last_stack']['began'] == noted['began']


def test_a_store_that_fits_is_not_reported(tmp_path, caplog):
    import logging
    writer = OMEZarrWriterMPTCZYX()
    with caplog.at_level(logging.ERROR):
        _write_stack(writer, _request(tmp_path, SMALL_CHUNKS, row=0), planes=PLANES)
        _write_stack(writer, _request(tmp_path, SMALL_CHUNKS, row=1), planes=PLANES)   # the tile's second channel
    assert 'already exists' not in caplog.text


def test_a_description_file_in_use_is_tried_again(tmp_path):
    """On Windows a store's description file cannot be replaced at the instant a reader,
    such as the Data viewer, has it open. The writer waits a moment and tries again
    instead of losing the stack."""
    from mesoSPIM.src.plugins.support_files.ImageWriters.OmeZarrWriterMP import omezarr_writer_tczyx as tczyx

    calls = []

    def busy_twice():
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        return "done"

    assert tczyx.when_free(busy_twice, pause=0) == "done" and len(calls) == 3

    def always_busy():
        raise PermissionError(13, "in use")

    with pytest.raises(PermissionError):
        tczyx.when_free(always_busy, tries=3, pause=0)

    # And the real thing: growing a store by a time point goes through it.
    array = zarr.open_group(str(tmp_path / "t.zarr"), mode="w", zarr_format=3).create_array(
        name="0", shape=(1, 1, 4, 8, 8), chunks=(1, 1, 4, 8, 8), dtype="uint16")
    real_resize, failures = type(array).resize, [PermissionError(13, "in use")]

    def resize_refused_once(self, shape):
        if failures:
            raise failures.pop()
        return real_resize(self, shape)

    window = tczyx.StackWindow(array, t=0, c=0, fresh=True)
    type(array).resize = resize_refused_once
    try:
        window.resize((2, 8, 8))
    finally:
        type(array).resize = real_resize
    assert array.shape == (1, 1, 2, 8, 8) and not failures


def _rows(folder: Path, *rows) -> AcquisitionList:
    """An acquisition table: one row per (x_pos, laser, filter)."""
    return AcquisitionList([
        Acquisition(
            x_pos=x_pos, y_pos=200.0, z_start=10.0, z_end=10.0 + 5.0 * (PLANES - 1), z_step=5.0, planes=PLANES,
            laser=laser, filter=filter_name, zoom='1x', shutterconfig='Left',
            folder=str(folder), filename='Sample.ome.zarr', image_writer_plugin=OMEZarrWriterMPTCZYX.name(),
        )
        for x_pos, laser, filter_name in rows
    ])


def _run(writer, acq, acq_list, value: int, planes: int = PLANES, expected: int = PLANES) -> None:
    """Write one stack of planes that all hold ``value`` (plus the plane number)."""
    request = WriteRequest(
        uri=os.path.join(acq['folder'], acq['filename']), shape=(expected, Y, X), dtype='uint16', axes='ZYX',
        x_res=1.0, y_res=1.0, z_res=5.0, num_tiles=1, num_channels=1, num_rotations=1, num_shutters=1,
        acq=acq, acq_list=acq_list, writer_config_file_values=SMALL_CHUNKS,
    )
    writer.open(request)
    for z in range(planes):
        writer.write_frame(WriteImage(
            image=np.full((X, Y), value + z, dtype=np.uint16), current_image_counter=z, tile_number=0,
            laser=acq['laser'], shutter=acq['shutterconfig'], rot=acq['rot'],
            x_res=1.0, y_res=1.0, z_res=5.0, acq=acq, acq_list=acq_list,
        ))
    writer.finalize(FinalizeImage(acq=acq, acq_list=acq_list))
    writer._wait_for_background_writers()


def test_one_laser_with_two_filters_is_two_channels(tmp_path):
    """Told apart by the laser alone, the second filter's stack was written over the first."""
    table = _rows(tmp_path, (100.0, '488 nm', 'Empty'), (100.0, '488 nm', '515LP'), (100.0, '561 nm', 'Empty'))
    assert OMEZarrWriterMPTCZYX.channels_of(table)[1] == ['488 Empty', '488 515LP', '561']
    writer = OMEZarrWriterMPTCZYX()
    metadata_files = []
    for value, acq in zip((1000, 2000, 3000), table):
        _run(writer, acq, table, value)
        metadata_files.append(Path(writer.metadata_file).name)
    root = zarr.open_group(tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr', mode='r')
    channels = root.attrs['ome']['omero']['channels']
    assert [channel['label'] for channel in channels] == ['488 Empty', '488 515LP', '561']
    assert channels[0]['color'] == channels[1]['color'] == '00FF66', "the colour goes by the laser"
    assert root['0'].shape[:2] == (1, 3)
    assert [int(root['0'][0, c, 0, 0, 0]) for c in range(3)] == [1000, 2000, 3000]
    # A metadata file's name holds the channel; a filter's name must not break it.
    assert 'Ch488_515LP_meta' in metadata_files[1] and not any(' ' in name for name in metadata_files)


def test_rows_run_one_at_a_time_land_where_a_full_run_puts_them(tmp_path):
    """'Run selected acquisition' runs a list of one row. Counted within that list every
    row was tile 0, channel 0, and each was written over the one before. Its place is now
    looked up in the whole table, which mesoSPIM keeps in its shared state."""
    from mesoSPIM.src.mesoSPIM_State import mesoSPIM_StateSingleton

    table = _rows(tmp_path, (100.0, '488 nm', 'Empty'), (100.0, '561 nm', 'Empty'), (900.0, '488 nm', 'Empty'),
                  (900.0, '561 nm', 'Empty'))
    other = _rows(tmp_path, (5000.0, '640 nm', 'Empty'))
    other[0]['filename'] = 'Another.ome.zarr'      # a row saved elsewhere does not count
    whole = AcquisitionList(list(table) + list(other))
    state = mesoSPIM_StateSingleton.instance or mesoSPIM_StateSingleton()
    before = state['acq_list']
    state['acq_list'] = whole
    try:
        writer = OMEZarrWriterMPTCZYX()
        for value, row in ((3000, 2), (2000, 1), (4000, 3), (1000, 0)):     # one row at a time, in any order
            _run(writer, whole[row], AcquisitionList([whole[row]]), value)
    finally:
        state['acq_list'] = before
    acquisition = tmp_path / 'Sample.ome.zarr'
    assert sorted(p.name for p in acquisition.iterdir() if p.is_dir()) == [
        'Mag1_Tile0_Sh0_Rot0.ome.zarr', 'Mag1_Tile1_Sh0_Rot0.ome.zarr']
    for tile, values in enumerate(((1000, 2000), (3000, 4000))):
        root = zarr.open_group(acquisition / f'Mag1_Tile{tile}_Sh0_Rot0.ome.zarr', mode='r')
        assert [channel['label'] for channel in root.attrs['ome']['omero']['channels']] == ['488', '561']
        assert [int(root['0'][0, c, 0, 0, 0]) for c in range(2)] == list(values)


def test_a_stack_stopped_in_a_later_channel_ends_in_empty_planes_and_is_noted(tmp_path):
    """Stop in the second channel at plane 11 of 20, with chunks 8 planes deep. The chunk
    being filled was completed with five copies of the last plane, which looked like data.
    They are empty now, and the store says which stack is incomplete."""
    table = _rows(tmp_path, (100.0, '488 nm', 'Empty'), (100.0, '561 nm', 'Empty'))
    writer = OMEZarrWriterMPTCZYX()
    _run(writer, table[0], table, 1000)
    _run(writer, table[1], table, 2000, planes=11)
    store = tmp_path / 'Sample.ome.zarr' / 'Mag1_Tile0_Sh0_Rot0.ome.zarr'
    root = zarr.open_group(store, mode='r')
    stack = root['0'][0, 1]
    assert stack.shape[0] == PLANES
    assert [int(plane[0, 0]) for plane in stack[:11]] == [2000 + z for z in range(11)]
    assert not stack[11:].any(), "nothing after the last real plane looks like data"
    assert np.array_equal(root['0'][0, 0, :, 0, 0], 1000 + np.arange(PLANES)), "the first channel is untouched"
    assert root.attrs['mesospim_incomplete_stacks'] == [
        {'t': 0, 'c': 1, 'planes_written': 11, 'planes_expected': PLANES}]

    # Acquired again in full, the stack is whole and the note is gone.
    _run(OMEZarrWriterMPTCZYX(), table[1], table, 2000)
    root = zarr.open_group(store, mode='r')
    assert np.array_equal(root['0'][0, 1, :, 0, 0], 2000 + np.arange(PLANES))
    assert 'mesospim_incomplete_stacks' not in root.attrs


def test_the_plugin_registers_beside_the_mp_writer():
    from mesoSPIM.src.plugins.ImageWriterApi import ImageWriter
    assert isinstance(OMEZarrWriterMPTCZYX(), ImageWriter)
    assert OMEZarrWriterMPTCZYX.name() == 'MP_OME_Zarr_TCZYX_Writer'
    assert OMEZarrWriterMPTCZYX.file_names().IncludeChannel is False


def test_the_fast_shard_writer_puts_a_stack_at_its_time_point_and_channel(tmp_path):
    """The fast writer compresses and writes whole shards itself, which is what lets the
    microscope save sharded OME-Zarr at full speed. A tczyx store holds one stack per time
    point and channel, so the writer has to put each shard there and nowhere else."""
    from zarr.codecs import BloscCodec
    from mesoSPIM.src.plugins.support_files.ImageWriters.OmeZarrWriterMP.omezarr_writer import FastShardWriter

    root = zarr.open_group(str(tmp_path / "tile.ome.zarr"), mode="w", zarr_format=3)
    array = root.create_array(
        name="0", shape=(2, 2, 8, 40, 24), chunks=(1, 1, 4, 16, 16), shards=(1, 1, 8, 32, 32),
        dtype="uint16", compressors=[BloscCodec(cname="zstd", clevel=5)],
    )
    writer = FastShardWriter.for_array(array, lead=(1, 0))
    assert writer is not None, "a sharded, compressed tczyx array takes the fast path"
    assert writer.can_write(0, 8) and not writer.can_write(0, 4)

    stack = np.arange(8 * 40 * 24, dtype=np.uint16).reshape(8, 40, 24)
    writer.write(0, stack)

    assert np.array_equal(array[1, 0], stack), "the stack is read back where it was written"
    for t, c in ((0, 0), (0, 1), (1, 1)):
        assert not array[t, c].any(), f"time point {t}, channel {c} is left untouched"


def test_without_a_place_the_fast_shard_writer_refuses_a_tczyx_array(tmp_path):
    """Written without a time point and channel, a shard would land in the wrong place."""
    from zarr.codecs import BloscCodec
    from mesoSPIM.src.plugins.support_files.ImageWriters.OmeZarrWriterMP.omezarr_writer import FastShardWriter

    root = zarr.open_group(str(tmp_path / "tile.ome.zarr"), mode="w", zarr_format=3)
    array = root.create_array(
        name="0", shape=(2, 2, 8, 40, 24), chunks=(1, 1, 4, 16, 16), shards=(1, 1, 8, 32, 32),
        dtype="uint16", compressors=[BloscCodec(cname="zstd", clevel=5)],
    )
    assert FastShardWriter.for_array(array) is None


def test_a_sharded_tczyx_store_is_written_through_the_fast_shard_writer(tmp_path):
    """Two channels of one time point, written as the writer process writes them but in one
    process: each stack takes the fast path and is read back at its own channel."""
    from zarr.codecs import BloscCodec
    from mesoSPIM.src.plugins.support_files.ImageWriters.OmeZarrWriterMP.omezarr_writer import (
        ChunkScheme, PyramidSpec,
    )
    from mesoSPIM.src.plugins.support_files.ImageWriters.OmeZarrWriterMP.omezarr_writer_tczyx import (
        TCZYX, Live3DPyramidWriterTCZYX,
    )

    planes, y, x = 16, 64, 48
    path = tmp_path / "Mag1_Tile0_Sh0_Rot0.ome.zarr"
    stacks = {}
    for c in (0, 1):
        writer = Live3DPyramidWriterTCZYX(
            PyramidSpec(z_size_estimate=planes, y=y, x=x, levels=2),
            TCZYX(t=0, c=c, n_channels=2, channel_labels=("488", "561"), channel_colors=("00FF66", "FFBF1A")),
            path=str(path), chunk_scheme=ChunkScheme(base=(8, 32, 32), target=(8, 32, 32)),
            compressor=BloscCodec(cname="zstd", clevel=5), shard_shape=(8, 64, 64),
            async_close=False, max_inflight_chunks=1,
        )
        assert writer.fast_writers[0] is not None, "level 0 takes the fast path"
        rng = np.random.default_rng(c)
        stacks[c] = rng.integers(0, 4000, size=(planes, y, x), dtype=np.uint16)
        for plane in stacks[c]:
            writer.push_slice(plane)
        writer.close()

    level0 = zarr.open_group(str(path), mode="r")["0"]
    assert level0.shape == (1, 2, planes, y, x)
    for c in (0, 1):
        assert np.array_equal(level0[0, c], stacks[c]), f"channel {c} is read back as written"
