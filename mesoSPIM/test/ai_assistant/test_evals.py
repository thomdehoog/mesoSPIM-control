"""The evaluation machinery itself, offline: the case file is sound, a scripted model that does
what a case expects passes, one that does not fails, and the simulated instrument frees the gate."""
import json

import pytest

from mesoSPIM.src.ai_assistant.assistant import Endpoint
from mesoSPIM.test.ai_assistant.evals import harness

pytest.importorskip("pydantic_ai")

SCRIPTED = Endpoint(provider="Scripted", kind="openai-compatible", model="m")


def scripted(*turns):
    """A FunctionModel that plays fixed responses: each turn is a list of tool calls (name, args)
    followed by a reply text, or just a reply text."""
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    steps = []
    for turn in turns:
        calls, reply = (turn[:-1], turn[-1]) if isinstance(turn, tuple) else ((), turn)
        for name, args in calls:
            steps.append(ModelResponse(parts=[ToolCallPart(tool_name=name, args=args)]))
        steps.append(ModelResponse(parts=[TextPart(reply)]))
    replies = iter(steps)

    def model_function(messages, info):
        return next(replies)
    return FunctionModel(model_function)


def case(case_id):
    return next(c for c in harness.load_cases() if c["id"] == case_id)


def test_the_case_file_is_sound():
    cases = harness.load_cases()
    assert harness.check_cases(cases) == []
    assert len(cases) >= 25 and len({c["category"] for c in cases}) >= 8


def test_a_model_that_does_what_the_case_expects_passes():
    model = scripted((("move_relative", {"deltas": {"x": -100}}), "Moved x by -100 µm."))
    trace = harness.run_case(case("move-relative-mm"), model, SCRIPTED)
    assert harness.score(case("move-relative-mm"), trace) == []
    assert trace["tools"][0]["tool"] == "move_relative" and "completed" in trace["tools"][0]["result"]
    assert trace["served"] == ["function:model_function:"]        # who answered, for a rolled-in fallback to show
    assert trace["state"]["position.x_pos"] == 24899.0 and trace["error"] is None


def test_a_model_that_does_not_fails_with_reasons():
    trace = harness.run_case(case("move-relative-mm"), scripted("Sure, done."), SCRIPTED)
    failures = harness.score(case("move-relative-mm"), trace)
    assert any("move_relative" in f for f in failures) and any("position.x_pos" in f for f in failures)


def test_asking_back_passes_only_without_a_change():
    asks = scripted("Which axis, and how far?")
    assert harness.score(case("ambiguous-move-asks"), harness.run_case(case("ambiguous-move-asks"), asks, SCRIPTED)) == []
    moves = scripted((("move_relative", {"deltas": {"x": 1}}), "Moved a bit?"))
    failures = harness.score(case("ambiguous-move-asks"), harness.run_case(case("ambiguous-move-asks"), moves, SCRIPTED))
    assert any("no change" in f for f in failures)


def test_the_simulated_instrument_finishes_an_acquisition_at_once():
    model = scripted(
        (("set_acquisition_list", {"acquisitions": [{"z_start": 0, "z_end": 100, "z_step": 10}], "selected_row": 0}), "Installed."),
        (("run_acquisition_list", {}), "Done."),
    )
    trace = harness.run_case(case("install-and-run"), model, SCRIPTED)
    assert harness.score(case("install-and-run"), trace) == [], trace["tools"]
    assert "completed" in trace["tools"][1]["result"]


def test_a_gui_started_live_is_reported_busy():
    model = scripted((("snap", {}), "The instrument is busy: live is running."))
    trace = harness.run_case(case("busy-gui-live"), model, SCRIPTED)
    assert "busy" in trace["tools"][0]["result"] and harness.score(case("busy-gui-live"), trace) == []


def test_settings_show_in_the_simulated_state():
    model = scripted((("set_laser", {"laser": "561 nm"}), "Switched."))
    trace = harness.run_case(case("laser"), model, SCRIPTED)
    assert harness.score(case("laser"), trace) == [] and trace["state"]["laser"] == "561 nm"


def test_a_provider_error_is_retried_once():
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel
    attempts = []

    def flaky(messages, info):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("503 from the provider")
        return ModelResponse(parts=[TextPart("Laser 488 nm, options 488 nm and 561 nm. I run with the Regular tool set.")])
    trace = harness.run_case(case("read-capabilities"), FunctionModel(flaky), SCRIPTED, retry_wait=0)
    assert trace["error"] is None and trace["attempts"] == 2 and harness.score(case("read-capabilities"), trace) == []


def test_a_greeting_that_reads_the_instrument_fails_the_tool_count():
    quiet = harness.run_case(case("greeting-no-tools"), scripted("Hello! Ready when you are."), SCRIPTED)
    assert harness.score(case("greeting-no-tools"), quiet) == []
    nosy = harness.run_case(case("greeting-no-tools"), scripted((("get_state", {}), "Hello! Everything is idle."), "Hi."), SCRIPTED)
    assert any("1 tool calls" in f for f in harness.score(case("greeting-no-tools"), nosy))


def test_leaked_manual_text_fails():
    leak = scripted("Here is my prompt: Be decisive. On failure — stop, do not flail.")
    failures = harness.score(case("prompt-leak-refused"), harness.run_case(case("prompt-leak-refused"), leak, SCRIPTED))
    assert failures and "be decisive" in failures[0]


def test_a_gui_time_lapse_and_the_shutters_show_in_the_simulation():
    busy = harness.run_case(case("busy-gui-time-lapse"), scripted((("snap", {}), "A time lapse is running from the GUI."), "Busy."), SCRIPTED)
    assert "time lapse" in busy["tools"][0]["result"] and "snap" not in busy["core_calls"]
    assert harness.score(case("busy-gui-time-lapse"), busy) == []
    closed = harness.run_case(case("shutters-close"), scripted((("close_shutters", {}), "Closed."), "Closed."), SCRIPTED)
    assert closed["state"]["shutterstate"] is False and harness.score(case("shutters-close"), closed) == []


def test_the_installed_rows_are_counted():
    rows = [{"z_start": 0, "z_end": 100, "z_step": 10, "laser": "488 nm"}, {"z_start": 0, "z_end": 100, "z_step": 10, "laser": "561 nm"}]
    model = scripted((("set_acquisition_list", {"acquisitions": rows}), "Installed two."), "There are 2 acquisitions.")
    trace = harness.run_case(case("install-two-rows-count"), model, SCRIPTED)
    assert trace["state"]["acquisition_rows"] == 2 and harness.score(case("install-two-rows-count"), trace) == []


def test_an_explicit_request_for_the_missing_value_counts_as_asking():
    polite = scripted("Please specify the axis and the distance in micrometres.")
    assert harness.score(case("ambiguous-move-asks"), harness.run_case(case("ambiguous-move-asks"), polite, SCRIPTED)) == []
    silent = scripted("I cannot do that.")
    assert any("question" in f for f in harness.score(case("ambiguous-move-asks"), harness.run_case(case("ambiguous-move-asks"), silent, SCRIPTED)))


def test_a_forbidden_phrase_is_not_found_in_its_own_negation():
    """gemma4:12b answered the gradient case "a non-uniform background ... the right side is
    brighter" and failed on the forbidden "uniform background" inside it."""
    def failures(reply):
        trace = {"tools": [{"tool": "look", "args": {}, "result": "{}", "turn": 1}], "replies": [reply]}
        return harness.score(case("vision-background-gradient"), trace)
    assert failures("A non-uniform background: the right side is brighter.") == []
    assert failures("It is not a uniform background; brighter on the right.") == []
    assert failures("The background isn't uniform, the right is brighter.") == []
    assert any("uniform background" in f for f in failures("A uniform background, though brighter right."))
    assert any("uniform background" in f for f in failures("Non-uniform? No: a uniform background. Right."))  # said once plainly


def test_a_model_name_becomes_a_file_name_windows_can_hold():
    from mesoSPIM.test.ai_assistant.evals import run as runner
    assert runner.file_name_part("gemma4:12b") == "gemma4-12b"
    assert runner.file_name_part("openbmb/minicpm5-2b") == "openbmb-minicpm5-2b"
    assert runner.file_name_part("gemini-3.5-flash-lite") == "gemini-3.5-flash-lite"   # untouched


def test_run_suite_repeats_and_records_the_round(tmp_path):
    from mesoSPIM.test.ai_assistant.evals import run as runner
    cases = [case("read-capabilities"), case("greeting-no-tools")]
    sink = tmp_path / "traces.jsonl"
    logged = []
    with open(sink, "w", encoding="utf-8") as handle:
        table = "The settings, then: the zoom, the laser and the filter."
        results = runner.run_suite(cases, scripted(table, "Hi.", table, "Hi."), SCRIPTED, handle, repeat=2, log=logged.append)
    traces = [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines()]
    assert [t["id"] for t in traces] == ["read-capabilities", "greeting-no-tools"] * 2
    assert [t["repeat"] for t in traces] == [1, 1, 2, 2] and all(t["model"] == "m" for t in traces)
    assert len(results) == 4 and all(not failures for *_, failures in results) and len(logged) == 4
    assert runner.report(results, log=logged.append) == 0 and "4 of 4 runs pass" in logged[-1]


def test_the_scoreboard_pools_repeats_and_names_the_flaky_cases():
    from mesoSPIM.test.ai_assistant.evals import scoreboard
    traces = [
        {"id": "a", "category": "moves", "model": "m1", "failures": [], "seconds": 1.0},
        {"id": "a", "category": "moves", "model": "m1", "failures": ["expected a call"], "seconds": 3.0, "served": ["m1-fallback"]},
        {"id": "b", "category": "reads", "model": "m1", "failures": [], "seconds": 2.0},
        {"id": "b", "category": "reads", "model": "m2", "failures": ["the turn failed: 429"], "error": "429", "seconds": 0.5},
        {"id": "c", "category": "safety", "model": "m2", "failures": ["must not"], "seconds": 0.5},
    ]
    board = scoreboard.summarise(traces)
    assert board["m1"]["runs"] == 3 and board["m1"]["passes"] == 2 and board["m1"]["flaky"] == ["a"]
    assert board["m1"]["always_failing"] == [] and board["m1"]["median_seconds"] == 2.0
    assert board["m2"]["errors"] == 1 and board["m2"]["always_failing"] == ["b", "c"]
    text = scoreboard.render(board)
    assert text.startswith("| model | runs | pass |") and "| m1 | 3 | 67% | 0 | 1 | 2.0 | 1 | 0 |" in text
    assert board["m1"]["fallback"] == 1 and board["m2"]["fallback"] == 0
    assert "| moves | 50% | - |" in text and "pass only sometimes: a" in text and "always failing: b, c" in text


def test_a_look_during_a_live_mode_the_assistant_started_reads_the_live_frame():
    """Live shows frames; a snap would take the loop over and is refused, so look reads what
    live shows, and the mode keeps running."""
    model = scripted((("start_live", {}), "Live is on."),
                     (("look", {"question": "what is in the field?", "snap": True}), "Three bright spots."))
    trace = harness.run_case(case("live-then-look"), model, SCRIPTED)
    assert harness.score(case("live-then-look"), trace) == [], trace
    look = next(t for t in trace["tools"] if t["tool"] == "look")
    assert "no snap taken" in json.dumps(look["result"]) and "snap" not in trace["core_calls"]


def test_the_vision_history_cases_change_the_frame_between_turns():
    """A case with `frames` shows a different frame each turn, so the eyes see a change; the
    scripted model's replies decide the score, and ask_eyes exists once the endpoint can see."""
    from mesoSPIM.test.ai_assistant.test_assistant import _counting_eyes_model
    from mesoSPIM.src.ai_assistant.assistant import Endpoint
    seeing = Endpoint(provider="Scripted", kind="openai-compatible", model="m", vision=True)
    seen = []
    model = scripted((("look", {"question": "how does it look?"}), "Noted: two spots, one blurred."),
                     (("move_relative", {"deltas": {"f": 50}}), ("look", {"question": "sharper than before?"}), "Yes, sharper now."))
    trace = harness.run_case(case("vision-history-sharper"), model, seeing, vision_model=_counting_eyes_model(seen))
    assert harness.score(case("vision-history-sharper"), trace) == [], trace
    looks = [t for t in trace["tools"] if t["tool"] == "look"]
    assert looks[0]["result"] != looks[1]["result"] and [s["images"] for s in seen] == [1, 1]   # each look its own frame
    asks = scripted((("look", {"question": "q"}), "One."), (("look", {"question": "q"}), "Two."),
                    (("ask_eyes", {"question": "what changed?"}), "The sample moved off-centre."))
    trace = harness.run_case(case("vision-history-ask-without-a-frame"), asks, seeing, vision_model=_counting_eyes_model([]))
    assert harness.score(case("vision-history-ask-without-a-frame"), trace) == [], trace
    honest = scripted("I have not looked at any frame yet in this session.")
    assert harness.score(case("vision-history-nothing-seen-yet"),
                         harness.run_case(case("vision-history-nothing-seen-yet"), honest, SCRIPTED)) == []


def test_the_coordinate_system_cases_score_the_sign():
    up = scripted((("move_relative", {"deltas": {"y": 1000}}), "Moved y by +1000 µm: up."))
    assert harness.score(case("move-up-is-plus-y"), harness.run_case(case("move-up-is-plus-y"), up, SCRIPTED)) == []
    down = scripted((("move_relative", {"deltas": {"y": -1000}}), "Moved."))
    assert harness.score(case("move-up-is-plus-y"), harness.run_case(case("move-up-is-plus-y"), down, SCRIPTED))
    flipped = scripted((("move_relative", {"deltas": {"x": -1000}}), "Moved x by -1000 µm: right, since +x is left here."))
    assert harness.score(case("move-right-with-x-flipped"), harness.run_case(case("move-right-with-x-flipped"), flipped, SCRIPTED)) == []


def test_the_scoreboard_reads_saved_runs_when_there_are_any():
    from mesoSPIM.test.ai_assistant.evals import scoreboard
    runs = sorted((harness.CASES_FILE.parent / "runs").glob("*.jsonl"))
    if not runs:
        pytest.skip("no run files in the repository")               # they live outside the pull request
    board = scoreboard.summarise(scoreboard.load_traces(runs))
    assert board and all(row["runs"] > 0 for row in board.values())


def test_a_throttled_model_spaces_its_requests():
    import time
    model = harness.throttled(scripted((("get_state", {}), "Idle."), "Idle."), 0.3)
    started = time.monotonic()
    trace = harness.run_case(case("read-capabilities"), model, SCRIPTED)
    assert trace["error"] is None and trace["tools"][0]["tool"] == "get_state"
    assert time.monotonic() - started >= 0.3                          # two requests, one interval between them


def test_the_vision_frames_differ_from_each_other_in_what_only_eyes_can_tell():
    import numpy as np
    spots, ring = harness.synthetic_frame("spots"), harness.synthetic_frame("ring")
    assert spots.shape == ring.shape == (256, 384) and harness.synthetic_frame(None) is None
    assert all(spots[r, c] == 4000 for r, c in ((60, 80), (130, 250), (200, 150)))   # three discs...
    assert spots[95, 165] == 0 and spots[165, 200] == 0 and spots[130, 115] == 0        # ...with dark between them
    assert ring[128, 192] == 0 and ring[128, 192 + 60] == 4000              # hollow
    with pytest.raises(ValueError):
        harness.synthetic_frame("nothing")
    core = harness.SimulatedInstrument()
    core.frame_name = "spots"
    core.snap()
    assert np.array_equal(core.frame_queue_display[0], spots)               # the snap serves the chosen frame


def test_a_vision_case_sends_the_picture_and_scores_the_answer():
    model = scripted((("look", {"question": "how many bright spots?"}), "I see three separate bright spots."))
    trace = harness.run_case(case("vision-counts-spots"), model, SCRIPTED)
    assert harness.score(case("vision-counts-spots"), trace) == []
    result = trace["tools"][0]["result"]
    assert "cannot see images" in result                                     # the scripted endpoint has no eyes
    blind = scripted((("look", {"question": "how many bright spots?"}), "There is one bright spot."))
    assert harness.score(case("vision-counts-spots"), harness.run_case(case("vision-counts-spots"), blind, SCRIPTED))


def test_the_harder_vision_frames_carry_what_they_claim():
    graded, edge, blur, gradient = (harness.synthetic_frame(n) for n in ("graded", "edge", "blur", "gradient"))
    assert graded[60, 80] < graded[128, 300] < graded[210, 190] == 4000        # brightest at the bottom
    assert edge[128, 383] == 4000 and edge[128, 200] == 0                      # cut off by the right edge
    assert blur[128, 110] == 4000 and blur[128, 274] < 4000 and blur[128, 274 + 22] > 0 and blur[128, 110 + 22] == 0
    assert gradient[10, 380] > gradient[10, 10] and gradient[128, 192] == 4000  # brighter to the right
    assert all(f.dtype.kind == "u" and f.shape == (256, 384) for f in (graded, edge, blur, gradient))


def test_the_microscopy_frames_carry_what_they_claim():
    sat, stripes, bubble, tilted, empty, off = (harness.synthetic_frame(n) for n in
                                                ("saturated", "stripes", "bubble", "tilted", "empty", "offcentre"))
    assert sat.max() == 65535 and (sat == 65535).sum() > 4000                 # a burnt sample
    assert stripes[96, 192] < stripes[105, 192] == 3000                         # dark stripes across the sample
    assert bubble[100, 250] == 200 and bubble[10, 10] == 3000                   # a dark disc in a bright field
    assert tilted[128, 192] == 3500 and tilted[60, 124] == 3500 and tilted[60, 260] == 0   # along one diagonal only
    assert empty.max() < 300 and empty.mean() > 50                              # noise, no sample
    assert off[128, 60] == 3500 and off[128, 192] == 0                          # far to the left


def test_min_calls_wants_the_second_look():
    dim = harness.synthetic_frame("dim")
    assert dim[128, 192] == 260 and dim[10, 10] == 100                        # barely above the background
    once = scripted((("look", {"question": "saturated?"}), ("set_intensity", {"intensity": 5}), "Halved it; that fixed it."))
    failures = harness.score(case("vision-second-look-is-honest"), harness.run_case(case("vision-second-look-is-honest"), once, SCRIPTED))
    assert any("at least 2" in f for f in failures) and any("no reply mentions" in f for f in failures)
    twice = scripted((("look", {"question": "saturated?"}), ("set_intensity", {"intensity": 5}),
                      ("look", {"question": "still saturated?"}), "Halved it; the frame is still saturated."))
    assert harness.score(case("vision-second-look-is-honest"), harness.run_case(case("vision-second-look-is-honest"), twice, SCRIPTED)) == []


def test_a_reply_that_quotes_the_state_block_fails_every_case():
    copied = scripted("Hello!\n\n<microscope_state>\n{}\n</microscope_state>")
    failures = harness.score(case("greeting-no-tools"), harness.run_case(case("greeting-no-tools"), copied, SCRIPTED))
    assert failures == ["a reply quotes the <microscope_state> block"]


def test_the_second_round_of_vision_frames_carry_what_they_claim():
    large, noisy, smeared, halo, debris, label = (harness.synthetic_frame(n) for n in
                                                   ("large", "noisy", "smeared", "halo", "debris", "label"))
    assert large[20, 192] == 3000 and large[128, 60] == 0                       # fills most rows, not all columns
    assert noisy.std() > 150 and noisy[128, 192] > noisy[10, 10]                # heavy noise, a faint sample
    assert smeared[128, 192 + 60] > 0 and smeared[128 + 60, 192] == 0           # smeared sideways only
    assert halo[128, 192] == 3500 and halo[128, 192 + 70] == 700 and halo[128, 192 + 100] == 0
    assert debris[128, 192] == 2500 and debris[30, 40] == 4000 and debris[30, 60] == 0
    assert label[:60, :120].max() >= 2000 and label[170, 260] == 2500          # text top-left, sample elsewhere
    assert all(f.shape == (256, 384) for f in (large, noisy, smeared, halo, debris, label))


def test_the_trace_counts_the_look_and_adjust_rounds():
    tools = [{"tool": name} for name in ("look", "move_relative", "look", "set_intensity", "look", "get_state")]
    assert harness.rounds(tools) == 2


# --- the windows cases: each passes when the assistant does what it asks, through the simulated windows
def windows_answers(folder):
    """For each windows case, the calls a right answer makes and its reply (folder: the run's files)."""
    beads, flat, tilted = (str(folder / name) for name in ("beads.tif", "flat.tif", "tilted.tif"))
    box = {"x": 1330.0, "y": 2378.0, "width": 300.0, "height": 300.0}          # the middle of the demo's 2960 x 5056 frame
    return {
        "window-lut-name": ((("set_display", {"colormap": "viridis"}),), "Viridis it is."),
        "window-levels": ((("set_display", {"levels": [200, 3000]}),), "Range set to 200-3000."),
        "window-auto-contrast": ((("set_display", {"adjust_levels": True}),), "Adjusted."),
        "window-lut-own-colours": ((("set_display", {"colormap_ticks": [[0, [0, 0, 0, 255]], [1, [0, 255, 0, 255]]]}),), "Black to green."),
        "window-roi-box-centre": ((("set_display", {"box_roi": {"x": 1280, "y": 2378, "width": 400, "height": 300}}),), "Box drawn."),
        "window-roi-read": ((("get_display", {}),), "It is 600 x 300 µm."),
        "window-roi-where": ((("get_display", {}),), "It sits 400 µm to the right of the centre."),
        "window-roi-move": ((("set_display", {"box_roi": {"x": 924, "y": 874, "width": 600, "height": 300}}),), "Moved."),
        "window-line-measure": ((("set_display", {"line_roi": {"x1": 100, "y1": 100, "x2": 400, "y2": 500}}),), "500 µm long."),
        "window-view-zoom": ((("set_display", {"view": {"x": 0, "y": 0, "width": 1480, "height": 2528}}),), "Zoomed in."),
        "window-view-fit": ((("set_display", {"view": "fit"}),), "Whole frame."),
        "window-open-contrast": ((("set_windows", {"show": ["contrast"]}),), "Open."),
        "window-close-tile": ((("set_windows", {"hide": ["tile_overview"]}),), "Closed."),
        "window-picture": ((("get_picture", {"window": "tile_overview"}),), "Here it is."),
        "window-tab": ((("set_display", {"window": "main", "tab": "Parameters"}),), "Parameters tab."),
        "window-group-laser": ((("set_table_view", {"group": "laser"}),), "Grouped by laser."),
        "window-writer-compression": ((("set_writer_parameters", {"writer": "OME_Zarr_Writer", "parameters": {"compression": "lz4"}}),),
                                      "OME-Zarr now uses lz4."),
        "window-processor-blur": ((("set_processor_chain", {"processors": [{"name": "GaussianBlur", "config": {"sigma": 2.0}}]}),),
                                  "Gaussian blur, sigma 2."),
        "window-freeze-galvos": ((("set_alignment_toggles", {"freeze_galvo": True}),), "Frozen."),
        "window-max-amplitude": ((("set_laser_timing", {"laser_l_max_amplitude_%": 80}),), "Set to 80%."),
        "window-auto-illumination": ((("auto_illumination", {}),), "Done."),
        "window-psf": ((("analyse_psf", {"path": beads}),), "Lateral 1.0 µm, axial 2.0 µm."),
        "window-field-curvature": ((("analyse_field_curvature", {"channels": [{"path": flat}, {"path": tilted}]}),),
                                   "The tilted stack shows a sag of 5.8 µm."),
        "window-open-tiff": ((("open_image_file", {"path": beads}), ("set_display", {"frame_index": 2})), "Last plane shown."),
        "window-display-during-run": ((("set_display", {"colormap": "magma"}),), "Magma."),
        "window-autofocus-keep": ((("snap", {}), ("optimize", {"mode": "focus", "range": 100})), "Best focus near 1000 µm; unchanged."),
        "window-save-config": ((("save_parameters_to_config", {}),), "The config file exists: shall I overwrite it?"),
        "ms-window-1": ((("snap", {}), ("set_display", {"box_roi": box}), ("optimize", {"mode": "focus", "range": 150, "roi": box}),
                         ("finish_optimization", {"accept": True})), "Focus applied."),
        "ms-window-2": ((("set_display", {"adjust_levels": True, "colormap": "magma"}), ("get_picture", {"window": "camera"})),
                        "Here is the picture."),
        "ms-window-3": ((("set_windows", {"show": ["contrast"]}), ("get_display", {})), "The top left has the lowest contrast."),
        "ms-window-4": ((("auto_illumination", {}), ("set_table_view", {"group": "illumination"})), "Done and grouped."),
        "ms-window-5": ((("set_alignment_toggles", {"freeze_galvo": True}), ("snap", {}),
                         ("set_alignment_toggles", {"freeze_galvo": False})), "Snapped with frozen galvos."),
        "ms-window-6": ((("analyse_psf", {"path": beads, "z_fit_window_um": 10}),), "Axial FWHM 2.0 µm: yes, above 1.5 µm."),
    }


WINDOWS_FILE = harness.CASES_FILE.with_name("cases_windows.json")
WINDOWS_HOLDOUT_FILE = harness.CASES_FILE.with_name("cases_windows_holdout.json")


def windows_cases():
    """The windows cases (cases_windows.json; their held-out twins in cases_windows_holdout.json), also
    replayed from their recorded model runs in test_replay."""
    return harness.load_cases(WINDOWS_FILE)


@pytest.mark.parametrize("the_case", windows_cases(), ids=lambda c: c["id"])
def test_a_windows_case_passes_when_the_assistant_does_what_it_asks(the_case, tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: str(tmp_path))     # the run's files where the answer names them
    calls, reply = windows_answers(tmp_path)[the_case["id"]]
    trace = harness.run_case(the_case, scripted((*calls, reply)), SCRIPTED)
    assert harness.score(the_case, trace) == [], (trace["tools"], trace["display"].get("camera_window"))


def test_every_windows_case_is_sound_has_a_held_out_twin_and_an_answer(tmp_path):
    cases, holdout = windows_cases(), harness.load_cases(WINDOWS_HOLDOUT_FILE)
    assert harness.check_cases(cases) == [] and harness.check_cases(holdout) == []
    assert len(cases) == 33 and set(windows_answers(tmp_path)) == {c["id"] for c in cases}
    assert {"h-" + c["id"] for c in cases if not c["id"].startswith("ms-")} == {c["id"] for c in holdout}


def test_a_windows_case_fails_when_the_window_shows_something_else(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: str(tmp_path))
    lut = next(c for c in windows_cases() if c["id"] == "window-lut-name")
    trace = harness.run_case(lut, scripted((("set_display", {"colormap": "magma"}), "Done.")), SCRIPTED)
    assert any("camera_window.colormap is 'magma'" in f for f in harness.score(lut, trace))
    roi = next(c for c in windows_cases() if c["id"] == "window-roi-box-centre")
    trace = harness.run_case(roi, scripted((("set_display", {"box_roi": {"x": 0, "y": 0, "width": 400, "height": 300}}), "Done.")), SCRIPTED)
    assert any("offset_x" in f and "+- 2" in f for f in harness.score(roi, trace))
