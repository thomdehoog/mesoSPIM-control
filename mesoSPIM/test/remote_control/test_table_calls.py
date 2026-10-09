"""The acquisition manager's buttons and wizards as Remote Control calls (plan WP3).

Every table call builds the whole new list and goes through set_acquisition_list's own accept and
install: one set of row checks for the window's features, TCP, MCP and the AI Assistant alike."""
import os

import pytest

from mesoSPIM.src.remote_control import (
    commands,  # noqa: F401  (registers the calls)
    config,
    dispatcher,
)
from mesoSPIM.src.utils.multicolor_acquisition_builder import (
    field_of_view_um,
    image_counts,
    tile_offsets,
)
from mesoSPIM.test.remote_control.support.fakes import RecordingCore
from mesoSPIM.test.remote_control.support.writers import use_tiff_writer

ROW = {"z_start": 0, "z_end": 20, "z_step": 10, "laser": "488 nm", "filter": "Empty", "zoom": "1x",
       "intensity": 10, "shutterconfig": "Left", "filename": "a.tif", "folder": "tmp"}


def table(*filenames):
    core = RecordingCore()
    dispatcher.run(core, "set_acquisition_list", {"acquisitions": [dict(ROW, filename=f) for f in filenames]})
    return core


def names(core):
    return [row["filename"] for row in core.state["acq_list"]]


def test_a_row_edit_changes_the_named_keys_and_nothing_else():
    core = table("a.tif", "b.tif")
    before = dict(core.state["acq_list"][1])
    dispatcher.run(core, "update_acquisition_row", {"row": 1, "changes": {"filename": "c.tif"}})
    assert names(core) == ["a.tif", "c.tif"] and core.state["selected_row"] == 1
    assert {k: v for k, v in core.state["acq_list"][1].items() if k != "filename"} == \
        {k: v for k, v in before.items() if k != "filename"}


@pytest.mark.parametrize("name, args", [
    ("update_acquisition_row", {"row": 0, "changes": {"intensity": 500}}),
    ("set_acquisition_list", {"acquisitions": [dict(ROW, intensity=500)]}),
])
def test_a_table_edit_is_refused_exactly_as_the_list_install_refuses_it(name, args):
    """One bottleneck: the same row checks, the same words, the list kept."""
    core = table("a.tif")
    with pytest.raises(dispatcher.ValidationError) as refused:
        dispatcher.run(core, name, args)
    assert "intensity=500" in str(refused.value) and "outside the allowed range" in str(refused.value)
    assert names(core) == ["a.tif"]


def test_rows_are_added_copied_moved_and_deleted_as_the_buttons_do():
    core = table("a.tif", "b.tif")
    dispatcher.run(core, "add_acquisition_rows", {"rows": [{"filename": "copy.tif"}], "like": 1, "at": 0})
    assert names(core) == ["copy.tif", "a.tif", "b.tif"] and core.state["acq_list"][0]["z_end"] == 20
    dispatcher.run(core, "add_acquisition_rows", {})                         # the Add button: a default row
    assert len(core.state["acq_list"]) == 4
    dispatcher.run(core, "move_acquisition_row", {"row": 0, "to": 2})
    assert names(core)[:3] == ["a.tif", "b.tif", "copy.tif"] and core.state["selected_row"] == 2
    dispatcher.run(core, "delete_acquisition_rows", {"rows": [2, 3]})
    assert names(core) == ["a.tif", "b.tif"]
    with pytest.raises(dispatcher.ValidationError, match="at least one row"):
        dispatcher.run(core, "delete_acquisition_rows", {"rows": [0, 1]})
    with pytest.raises(dispatcher.ValidationError):
        dispatcher.run(core, "delete_acquisition_rows", {"rows": [0, 0]})


def test_marking_gives_rows_the_instrument_s_values_rounded_as_the_window_does():
    core = table("a.tif", "b.tif")
    for key in ("position", "position_absolute"):           # no zero offsets
        core.state[key].update(x_pos=123.456, y_pos=-7.891, f_pos=4321.5, theta_pos=12.34)
    core.state["intensity"], core.state["laser"] = 42, "561 nm"
    dispatcher.run(core, "mark_acquisition_rows", {"rows": [1], "marks": ["xy", "rotation", "focus", "state"]})
    row = core.state["acq_list"][1]
    assert (row["x_pos"], row["y_pos"], row["rot"]) == (123.46, -7.89, 12.3)
    assert (row["f_start"], row["f_end"], row["intensity"], row["laser"]) == (4321.5, 4321.5, 42, "561 nm")
    assert core.state["acq_list"][0]["x_pos"] == 0                      # only the rows named
    with pytest.raises(dispatcher.ValidationError, match="marks"):
        dispatcher.run(core, "mark_acquisition_rows", {"rows": [0], "marks": ["colour"]})


def test_the_list_is_saved_and_loaded_in_the_window_s_format(tmp_path):
    core = table("a.tif", "b.tif")
    path = str(tmp_path / "list.csv")
    dispatcher.run(core, "save_acquisition_list", {"path": path})
    assert os.path.isfile(path)
    with pytest.raises(dispatcher.ValidationError, match="overwrite"):
        dispatcher.run(core, "save_acquisition_list", {"path": path})
    with pytest.raises(dispatcher.ValidationError, match=".csv"):
        dispatcher.run(core, "save_acquisition_list", {"path": str(tmp_path / "list.txt")})
    other = table("x.tif")
    dispatcher.run(other, "load_acquisition_list", {"path": path})
    assert names(other) == ["a.tif", "b.tif"]
    with pytest.raises(dispatcher.ValidationError, match="does not exist"):
        dispatcher.run(other, "load_acquisition_list", {"path": str(tmp_path / "none.csv")})


def test_rows_are_named_by_the_writer_s_rules():
    writer = use_tiff_writer()
    core = table("a.tif", "b.tif")
    dispatcher.run(core, "update_acquisition_row", {"row": 1, "changes": {"x_pos": 500}})
    dispatcher.run(core, "name_acquisition_rows", {"writer": writer, "description": "my sample"})
    assert all(name.startswith("my_sample_Mag1x_Tile") and name.endswith(".tiff") for name in names(core))   # its first extension
    assert names(core)[0] != names(core)[1] and {r["image_writer_plugin"] for r in core.state["acq_list"]} == {writer}
    with pytest.raises(dispatcher.ValidationError, match=f"unknown image writer .No_Writer.: one of .*{writer}"):
        dispatcher.run(core, "name_acquisition_rows", {"writer": "No_Writer"})
    assert writer in [w["name"] for w in dispatcher.run(core, "get_config", {})["image_writers"]]   # the reader of the choice


def test_focus_tracking_sets_each_row_s_focus_on_the_line_through_two_points():
    core = table("a.tif", "b.tif")
    dispatcher.run(core, "update_acquisition_row", {"row": 1, "changes": {"z_start": 50, "z_end": 100, "laser": "561 nm"}})
    dispatcher.run(core, "track_focus", {"z_1": 0, "f_1": 1000, "z_2": 100, "f_2": 1100, "laser": "561 nm"})
    rows = core.state["acq_list"]
    assert (rows[1]["f_start"], rows[1]["f_end"]) == (1050, 1100) and rows[0]["f_end"] == 0   # 488 nm left alone
    with pytest.raises(dispatcher.ValidationError, match="must differ"):
        dispatcher.run(core, "track_focus", {"z_1": 5, "f_1": 1, "z_2": 5, "f_2": 2})


def test_a_tiling_list_is_the_wizard_s_grid_named_and_checked(tmp_path):
    writer = use_tiff_writer()
    core = table("a.tif")
    core.state["position"]["theta_pos"] = 0.0
    args = {"x_start": -1000, "x_end": 1000, "y_start": 0, "y_end": 2000, "z_start": 0, "z_end": 100, "z_step": 10,
            "overlap_percent": 10, "channels": [{"laser": "488 nm", "intensity": 10, "filter": "Empty"},
                                                {"laser": "561 nm", "intensity": 20, "filter": "515LP"}],
            "folder": str(tmp_path), "writer": writer, "zoom": "1x"}
    dispatcher.run(core, "build_tiling_list", args)
    result = dispatcher.operation_snapshot(core)["result"]
    x_off, y_off = tile_offsets(*field_of_view_um(2048, 2048, core.cfg.pixelsize["1x"]), 10)
    x_n, y_n = image_counts(-1000, 1000, 0, 2000, x_off, y_off)
    rows = core.state["acq_list"]
    assert result["tiles"] == x_n * y_n and result["count"] == len(rows) == x_n * y_n * 2
    assert (rows[0]["x_pos"], rows[0]["y_pos"], rows[0]["laser"], rows[1]["laser"]) == (-1000, 0, "488 nm", "561 nm")
    assert all(row["filename"].endswith(".tiff") for row in rows) and len(set(names(core))) == len(rows)
    with pytest.raises(dispatcher.ValidationError, match="outside the allowed range"):   # the tiles go through the limits
        dispatcher.run(core, "build_tiling_list", dict(args, x_end=60000))
    with pytest.raises(dispatcher.ValidationError, match=f"at most {config.MAX_TILING_ROWS}"):
        dispatcher.run(core, "build_tiling_list", dict(args, x_start=-20000, x_end=20000, y_start=-40000, y_end=40000,
                                                       overlap_percent=90))


def test_the_snap_folder_is_set_and_the_window_shows_it(tmp_path):
    core = table("a.tif")
    dispatcher.run(core, "set_snap_folder", {"folder": str(tmp_path)})
    assert core.state["snap_folder"] == str(tmp_path)
    assert ("sig_update_gui_from_state", (), {}) in core.calls()
    with pytest.raises(dispatcher.ValidationError, match="not an existing folder"):
        dispatcher.run(core, "set_snap_folder", {"folder": str(tmp_path / "none")})
