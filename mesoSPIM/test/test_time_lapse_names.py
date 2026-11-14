"""
The file names of the acquisition table during and after a time lapse.

A time lapse marks every file name with the time point being acquired
('Sample_Time003.tif'). When it is over the names go back to what they were, so
that an ordinary acquisition run next is not saved as "time point 3".

Run from the repository root:
    python -m pytest mesoSPIM/test/test_time_lapse_names.py

Needs no Qt and no hardware.
"""
from mesoSPIM.src.utils.acquisitions import TimeLapseNames, with_time_index, without_time_index


def test_a_time_point_is_marked_in_front_of_the_last_ending():
    assert with_time_index('Sample.ome.zarr', 3) == 'Sample.ome_Time003.zarr'
    assert with_time_index('Sample.tif', 0) == 'Sample_Time000.tif'
    assert with_time_index('Sample.raw', 12) == 'Sample_Time012.raw'


def test_the_next_time_point_replaces_the_mark_of_the_one_before():
    assert with_time_index('Sample.ome_Time003.zarr', 4) == 'Sample.ome_Time004.zarr'
    assert with_time_index(with_time_index('Sample.h5', 0), 1) == 'Sample_Time001.h5'


def test_a_sample_name_with_time_in_it_is_kept():
    """'Mouse_Time2h' is the operator's own name. The old rule cut it at '_Time' and the
    files of a time lapse were called 'Mouse_Time000', the '2h' lost."""
    assert with_time_index('Mouse_Time2h.tif', 0) == 'Mouse_Time2h_Time000.tif'
    assert with_time_index('Mouse_Time2h_Time000.tif', 1) == 'Mouse_Time2h_Time001.tif'
    assert without_time_index('Mouse_Time2h_Time001.tif') == 'Mouse_Time2h.tif'
    assert without_time_index('Mouse_Time2h.tif') == 'Mouse_Time2h.tif'


def test_the_mark_comes_off_again():
    for plain in ('Sample.ome.zarr', 'Sample.tif', 'Sample.h5', 'Tile_3_488nm.raw'):
        assert without_time_index(with_time_index(plain, 7)) == plain
        assert without_time_index(plain) == plain


def test_the_names_go_back_when_a_time_lapse_has_run_to_its_end():
    restored = []
    names = TimeLapseNames(lambda: restored.append(True))
    for _ in range(3):
        names.time_point_started()
        assert not restored
        names.acquisition_finished()
        assert not restored, "between two time points the names stay marked"
    names.time_lapse_ended()
    assert restored == [True]


def test_a_time_lapse_stopped_in_the_middle_of_a_stack_waits_for_that_stack_to_close():
    """The stack being stopped still closes its files under the name it has. Changing the
    name before it has ended would send its last notes to a file of another name."""
    restored = []
    names = TimeLapseNames(lambda: restored.append(True))
    names.time_point_started()
    names.time_lapse_ended()                     # Stop: the time lapse is cancelled first
    assert not restored, "the acquisition is still closing its files"
    names.acquisition_finished()
    assert restored == [True]
    names.acquisition_finished()                 # a later, ordinary acquisition
    assert restored == [True], "the names are put back once"


def test_a_time_lapse_stopped_while_waiting_for_the_next_time_point_restores_at_once():
    restored = []
    names = TimeLapseNames(lambda: restored.append(True))
    names.time_point_started()
    names.acquisition_finished()
    names.time_lapse_ended()
    assert restored == [True]


def test_a_new_time_lapse_started_before_the_old_stack_closed_keeps_its_marks():
    restored = []
    names = TimeLapseNames(lambda: restored.append(True))
    names.time_point_started()
    names.time_lapse_ended()
    names.time_point_started()                   # the names have just been marked afresh
    names.acquisition_finished()
    assert not restored
    names.time_lapse_ended()
    assert restored == [True]
