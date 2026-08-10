"""Tests for the OME-Zarr MP writer's shard alignment and close behavior.

Background: the Live3DPyramidWriter flushes one chunk-depth, full-XY slab per
write. That is only safe and fast if every shard is exactly one slab deep --
a deeper shard would be filled by several concurrent slab writes, each of
which makes Zarr read the whole shard file back, merge, and rewrite it. The
concurrent read-modify-writes race and silently lose planes (reproduced at
5/5 before the fix). These tests pin:

* the shard z-depth is clamped to the chunk z-depth, whatever the config says;
* data written under a previously-corrupting config now round-trips intact;
* the writer never reads shard data back while writing (the signature of the
  slow read-modify-write path);
* close() writes out frames that are still waiting in the ingest queue
  instead of dropping them.

Run:  pytest test_omezarr_shard_alignment.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest
import zarr
import zarr.storage

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[1]
        / "src/plugins/support_files/ImageWriters/OmeZarrWriterMP"
    ),
)
from omezarr_writer import ChunkScheme, Live3DPyramidWriter, PyramidSpec


@pytest.fixture()
def store_reads(monkeypatch):
    """Count shard-data read-backs from disk while the writer is writing.

    A writer that keeps its whole-shard-write promise produces ZERO reads
    during acquisition; reads are the signature of Zarr's read-modify-write
    path. The counter ignores zarr.json metadata documents, and counting can
    be switched off before the test reads the data back to verify it.
    """
    counts = {"n": 0, "active": True}
    orig_get = zarr.storage.LocalStore.get

    async def counting_get(self, key, prototype, byte_range=None):
        out = await orig_get(self, key, prototype, byte_range)
        if out is not None and counts["active"] and "zarr.json" not in key:
            counts["n"] += 1
        return out

    monkeypatch.setattr(zarr.storage.LocalStore, "get", counting_get)
    return counts


def make_stack(z: int, y: int, x: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 2**16, size=(z, y, x), dtype=np.uint16)


def run_writer(path, stack, *, shard_shape, chunk_scheme, levels=1, drain=False):
    z, y, x = stack.shape
    writer = Live3DPyramidWriter(
        PyramidSpec(z_size_estimate=z, y=y, x=x, levels=levels),
        voxel_size=(1.0, 1.0, 1.0),
        path=str(path),
        chunk_scheme=chunk_scheme,
        shard_shape=shard_shape,
        async_close=False,
        ingest_queue_size=max(64, z),
    )
    for plane in stack:
        writer.push_slice(plane)
    if drain:
        while not writer.q.empty():
            time.sleep(0.01)
    writer.close()
    return writer


MISMATCHED = dict(
    # Chunks 4 planes deep but shards requested 16 deep: before the clamp,
    # each shard was filled by 4 concurrent partial writes -- the config that
    # reproducibly corrupted data.
    shard_shape=(16, 6000, 6000),
    chunk_scheme=ChunkScheme(base=(4, 128, 128), target=(4, 64, 64)),
)


def test_shard_depth_is_clamped_to_chunk_depth(tmp_path):
    stack = make_stack(16, 256, 256)
    writer = run_writer(tmp_path / "a.ome.zarr", stack, **MISMATCHED)
    arr = writer.arrs[0]
    assert arr.shards[0] == arr.chunks[0] == 4
    # XY sharding is untouched: still one shard across the full frame.
    assert arr.shards[1:] == (256, 256)


def test_mismatched_config_roundtrips_intact(tmp_path, store_reads):
    # 5/5 runs corrupted under this config before the clamp.
    stack = make_stack(32, 256, 256, seed=1)
    run_writer(tmp_path / "a.ome.zarr", stack, **MISMATCHED)
    writes_read_back = store_reads["n"]
    store_reads["active"] = False  # verification reads below don't count
    back = zarr.open_array(store=str(tmp_path / "a.ome.zarr"), path="0")[:]
    np.testing.assert_array_equal(back, stack)
    assert writes_read_back == 0, "writer took the read-modify-write path"


def test_aligned_config_never_reads_back(tmp_path, store_reads):
    # The recommended configuration (chunk z == shard z), scaled down.
    stack = make_stack(32, 256, 256, seed=2)
    run_writer(
        tmp_path / "a.ome.zarr",
        stack,
        shard_shape=(8, 6000, 6000),
        chunk_scheme=ChunkScheme(base=(8, 128, 128), target=(8, 64, 64)),
    )
    writes_read_back = store_reads["n"]
    store_reads["active"] = False
    back = zarr.open_array(store=str(tmp_path / "a.ome.zarr"), path="0")[:]
    np.testing.assert_array_equal(back, stack)
    assert writes_read_back == 0


def test_multiscale_levels_stay_aligned(tmp_path, store_reads):
    # Three pyramid levels; every level's flush must stay whole-shard too.
    stack = make_stack(16, 128, 128, seed=3)
    writer = run_writer(
        tmp_path / "a.ome.zarr",
        stack,
        shard_shape=(4, 6000, 6000),
        chunk_scheme=ChunkScheme(base=(4, 64, 64), target=(4, 32, 32)),
        levels=3,
    )
    writes_read_back = store_reads["n"]
    store_reads["active"] = False
    for arr in writer.arrs:
        assert arr.shards[0] == arr.chunks[0]
    back = zarr.open_array(store=str(tmp_path / "a.ome.zarr"), path="0")[:]
    np.testing.assert_array_equal(back, stack)
    assert writes_read_back == 0


def test_close_writes_out_still_queued_frames(tmp_path):
    # Frames are pushed faster than the consumer thread can take them, and
    # close() is called immediately -- everything must still land on disk.
    # (Before the fix, close() set the stop event before the join, and the
    # consumer dropped whatever was still waiting in the ingest queue.)
    stack = make_stack(24, 128, 128, seed=4)
    run_writer(
        tmp_path / "a.ome.zarr",
        stack,
        shard_shape=(4, 6000, 6000),
        chunk_scheme=ChunkScheme(base=(4, 64, 64), target=(4, 32, 32)),
        drain=False,
    )
    back = zarr.open_array(store=str(tmp_path / "a.ome.zarr"), path="0")[:]
    assert back.shape == stack.shape, (
        f"close() dropped queued frames: {back.shape[0]} of {stack.shape[0]} "
        f"planes on disk"
    )
    np.testing.assert_array_equal(back, stack)


def test_stack_not_multiple_of_chunk_depth(tmp_path):
    # 14 planes with 4-deep chunks: the padded final chunk is written, then
    # the array is truncated to the true length. Data must be exact.
    stack = make_stack(14, 128, 128, seed=5)
    run_writer(
        tmp_path / "a.ome.zarr",
        stack,
        shard_shape=(4, 6000, 6000),
        chunk_scheme=ChunkScheme(base=(4, 64, 64), target=(4, 32, 32)),
    )
    back = zarr.open_array(store=str(tmp_path / "a.ome.zarr"), path="0")[:]
    np.testing.assert_array_equal(back, stack)
