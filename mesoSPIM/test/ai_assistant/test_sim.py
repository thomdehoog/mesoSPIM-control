"""The simulator over time (evals/sim.py): the picture follows the stage and the light, and runs,
time lapses, drift and bleaching follow its clock, through the real dispatcher."""
import numpy as np
import pytest

from mesoSPIM.src.remote_control.frame import frame_stats
from mesoSPIM.test.ai_assistant.evals import focus_check
from mesoSPIM.test.ai_assistant.evals import harness
from mesoSPIM.test.ai_assistant.evals.sim import START, SampleInstrument
from mesoSPIM.test.ai_assistant.test_evals import SCRIPTED, scripted


def instrument(axes=None, **spec):
    core = SampleInstrument(axes)
    core.state["position"].update(x_pos=0.0)
    core.state["position_absolute"].update(x_pos=0.0)          # no zero offset: as the harness sets a position
    core.place_sample(dict({"x": 0.0}, **spec))
    return core


def test_focus_follows_the_stage():
    core = instrument(f=1000.0)
    peaks = {}
    for f in (400, 800, 1000, 1200, 1600):
        core.state["position"]["f_pos"] = float(f)
        peaks[f] = float(core.render(noise=False).max())
    assert max(peaks, key=peaks.get) == 1000
    assert peaks[800] < peaks[1000] and peaks[400] < peaks[800] and peaks[1600] < peaks[1200]
    assert core.truth()["focus_error_um"] == 600.0


@pytest.mark.parametrize("axes, sign", [(None, 1), ({"x": "left", "y": "down"}, -1)])
def test_the_sample_sits_where_x_and_y_put_it_in_the_case_coordinates(axes, sign):
    core = instrument(axes)
    def centroid():
        return frame_stats(core.render(noise=False))["signal_centroid"]
    centre = centroid()
    core.state["position"]["x_pos"] = 500.0
    assert sign * (centroid()["col"] - centre["col"]) > 0.2          # 500 um of 2048: a quarter of the field
    core.state["position"].update(x_pos=0.0, y_pos=500.0)
    assert sign * (centre["row"] - centroid()["row"]) > 0.2          # positive y carries it up
    assert core.truth()["off_centre_um"] == 500.0


def test_brightness_follows_intensity_and_exposure_up_to_full_scale():
    core = instrument(brightness=20000.0)
    core.state["intensity"] = 10
    dim = float(core.render(noise=False).max())
    core.state["intensity"] = 20
    assert float(core.render(noise=False).max()) == pytest.approx(2 * dim - 100, rel=0.02)   # background stays
    core.state["camera_exposure_time"] = 0.2
    core.state["intensity"] = 100
    assert core.render(noise=False).max() == 65535 and core.truth()["saturated_fraction"] > 0


def test_a_run_takes_its_time_and_holds_the_gate_meanwhile():
    core = instrument()
    acceptor = harness.SimulatedAcceptor(core)
    acceptor.dispatch("set_acquisition_list", {"acquisitions": [{"z_start": 0, "z_end": 990, "z_step": 10}]})
    acceptor.dispatch("run_acquisition_list", {})
    progress = acceptor.dispatch("get_progress", {})
    assert progress["operation"]["status"] == "processing" and progress["state"] == "run_acquisition_list"
    with pytest.raises(Exception, match="busy"):
        acceptor.dispatch("move_relative", {"deltas": {"x": 10}})
    core.wait(10)
    assert acceptor.dispatch("get_progress", {})["operation"]["status"] == "completed"
    assert core.state["state"] == "idle" and 4 < core.elapsed() < 20      # 100 planes of 20 ms and a row's set-up


def test_a_time_lapse_runs_its_points_on_the_clock_and_is_active_between_them():
    core = instrument()
    acceptor = harness.SimulatedAcceptor(core)
    acceptor.dispatch("time_lapse_start", {"timepoints": 3, "interval_sec": 60})
    core.wait(30)
    assert core.timelapse_active and core.time_counter == 1 and core.state["state"] == "idle"
    with pytest.raises(Exception, match="busy"):                     # between points, as on the instrument
        acceptor.dispatch("set_intensity", {"intensity": 20})
    core.wait(170)
    assert not core.timelapse_active and core.time_counter == 3
    assert acceptor.dispatch("get_progress", {})["operation"]["status"] == "completed"


def test_drift_and_bleaching_switch_on_per_case():
    core = instrument(drift_um_per_min={"x": 20.0, "f": -5.0}, bleach=2.0)
    before = float(core.render(noise=False).max())
    core.wait(600)
    truth = core.truth()
    assert truth["offset_um"] == [-200.0, 0.0] and truth["focus_error_um"] == 50.0
    for _ in range(20):
        core.snap()
    assert core.truth()["bleached"] > 0.05 and float(core.render(noise=False).max()) < before
    steady = instrument()
    steady.wait(600)
    assert steady.truth()["off_centre_um"] == 0.0 and steady.truth()["bleached"] == 0.0


def test_live_shows_the_sample_as_it_is_now():
    core = instrument()
    acceptor = harness.SimulatedAcceptor(core)
    core.set_state("live")
    core.state["position"]["x_pos"] = 500.0
    frame = acceptor.dispatch("get_frame", {"include_image": False})
    assert frame["stats"]["signal_centroid"]["col"] > 0.7


def test_a_case_on_the_simulator_fires_its_schedules_and_scores_the_outcome():
    """A schedule set in the first turn fires as a turn of its own each minute while the case's
    time runs, and the score reads the simulator's truth at the end."""
    case = {"id": "sim", "prompt": "snap every minute",
            "setup": {"sample": {"x": 0.0}, "position": {"x_pos": 300.0}, "run_for_s": 190},
            "expect": {"calls": ["schedule"], "min_calls": {"snap": 3}, "truth": {"off_centre_um_max": 100}}}
    model = scripted((("schedule", {"name": "snaps", "instruction": "take a snap", "every_seconds": 60}), "Scheduled."),
                     *[(("snap", {}), "Snapped.")] * 3)
    trace = harness.run_case(case, model, SCRIPTED)
    assert [t["tool"] for t in trace["tools"]] == ["schedule", "snap", "snap", "snap"]
    assert trace["truth"]["elapsed_s"] >= 190 and trace["truth"]["off_centre_um"] == 300.0
    assert harness.score(case, trace) == ["off_centre_um is 300.0, expected at most 100"]
    assert harness.check_cases([case]) == []


@pytest.mark.parametrize("light", [
    {}, {"intensity": 2}, {"intensity": 100, "camera_exposure_time": 0.2}, {"zoom": "2x"}, {"hot_pixel": True}])
def test_the_focus_measure_peaks_clearly_at_the_sharp_frame(light):
    """B3 on simulated focus series across +-300 um: dim, saturated, zoomed in, with a hot pixel."""
    core = instrument(f=1000.0)
    hot = light.pop("hot_pixel", False)
    if "zoom" in light:
        core.set_zoom(light.pop("zoom"))
    core.state.set_parameters(light)
    frames = {}
    for f in range(700, 1301, 20):
        core.state["position"]["f_pos"] = float(f)
        frames[f] = core.render()
        if hot:
            frames[f][17, 33] = 65535
    report = focus_check.check_series(frames, sharp=1000)
    assert report["clear"] and report["falls_both_sides"] and report["peak_off_sharp_um"] <= 40, report


def test_the_focus_check_reads_a_recorded_series_from_a_folder(tmp_path):
    import tifffile
    core = instrument(f=0.0)
    for f in (-300, -100, 0, 100, 300):
        core.state["position"]["f_pos"] = float(f)
        tifffile.imwrite(tmp_path / f"f_{f}.tif", core.render())
    assert sorted(focus_check.load_series(tmp_path)) == [-300, -100, 0, 100, 300]
    assert focus_check.main([str(tmp_path), "--sharp", "0"]) == 0


def test_the_clock_starts_on_a_fixed_morning():
    assert SampleInstrument().clock() == START and np.isfinite(START)
