"""Multi-step cases on the simulator over time, scored on outcomes (roadmap B4).

Eight groups, each a template with hand-written phrasings. The smoke set is every phrasing with the
group's canonical parameters (cases_multistep.json); the generated set draws phrasing and
parameters from a seed: where the sample is, how far out of focus, how bright, how it drifts,
the coordinate system (cases_generated.json). Both files are written from here:

    python -m mesoSPIM.test.ai_assistant.evals.multistep [--seed 0] [--per-group 20]

A case is scored on the simulator's truth at the end (centred, in focus, well exposed) and on the
instrument's calls where the outcome is a call (a run started, or not).
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

FOLDER = Path(__file__).parent
SMOKE_FILE = FOLDER / "cases_multistep.json"
GENERATED_FILE = FOLDER / "cases_generated.json"
UNATTENDED_FILE = FOLDER / "cases_unattended.json"
HOME = {"x_pos": 0.0, "y_pos": 0.0, "z_pos": 0.0, "f_pos": 5000.0}
CENTRED_UM = 150          # a tenth of the field at 1x is 205 um
IN_FOCUS_UM = 40
WELL_EXPOSED = {"saturated_fraction_max": 0.0005, "peak_fraction_min": 0.1}   # the manual's two rules
SETTERS_AND_MOVES = ["move_absolute", "move_relative", "set_intensity", "set_filter", "set_zoom", "set_laser",
                     "set_etl", "set_shutterconfig", "update_acquisition_row", "set_acquisition_list"]

PHRASINGS = {
    "centring": [
        "Centre the sample in the image.",
        "The sample is off to one side; bring it to the middle of the field of view.",
        "Move the stage so that the sample sits in the centre of the camera image.",
        "Can you centre the specimen in the view?",
        "Put the sample in the middle of the frame, please.",
        "Get the sample centred in the image before I start.",
    ],
    "focusing": [
        "Bring the sample into focus.",
        "The image looks blurry. Focus on the sample with the focus axis.",
        "Find the best focus for the sample.",
        "Make the sample sharp.",
        "Adjust f until the sample is in focus.",
        "Can you focus the image on the specimen?",
    ],
    "exposure": [
        "Adjust the laser intensity so that the sample is well exposed: not saturated and not too dim.",
        "Set the intensity so the image is neither saturated nor underexposed.",
        "Fix the exposure of the sample with the laser intensity.",
        "Find an intensity at which the sample is bright but nothing is saturated.",
        "Tune the intensity until the sample is properly exposed.",
        "Get a good exposure on the sample by changing the intensity.",
    ],
    "live tuning": [
        "Start live, then adjust the intensity until the sample is well exposed, and leave live running.",
        "Switch on live and tune the intensity for a good exposure; keep live on.",
        "In live mode, set the intensity so that the sample is bright but not saturated. Leave live running.",
        "Start the live view and focus the sample, then leave live on.",
        "Turn on live, bring the sample into focus, and keep live running.",
        "Go live and centre the sample in the image; leave live running.",
    ],
    "acquisition with checks": [
        "Check that the sample is centred and in focus, fix it if not, then run the acquisition list.",
        "Before acquiring, make sure the sample is in the middle of the image and sharp. Then run the list.",
        "Centre and focus the sample, then start the acquisition list.",
        "Look at the sample, correct its position and focus if needed, and run the acquisition list.",
        "Get the sample centred and focused and then acquire the list.",
        "Prepare the sample (centred, in focus) and run the acquisition list.",
    ],
    "recovery from refusals": [
        "Set the filter to 515 long pass and take a snap.",
        "Move x to 30000 um and then take a snap.",
        "Take a snap, then centre the sample in the image.",
        "Run the acquisition list and then take a snap.",
        "Set the filter to 515LP, the zoom to 3x, and take a snap.",
        "Move z by 40000 um, then focus the sample.",
    ],
    "vague requests": [
        "Make the image better.",
        "Optimise the settings for this sample.",
        "Improve the picture.",
        "Can you make it look nicer?",
        "Tune everything up for a good image.",
        "Set it up properly for imaging.",
    ],
    "must stop partway": [
        "Look at the sample. If nothing is visible, stop and tell me; otherwise run the acquisition list.",
        "Check the exposure. If anything is saturated, stop and tell me; if not, run the acquisition list.",
        "Look at the focus. If the sample is out of focus, just tell me and do nothing else; otherwise run the list.",
        "Take a look, and only if the sample is centred run the acquisition list. If not, stop and report.",
        "If the sample is visible and in focus, run the acquisition list. Otherwise tell me what is wrong.",
        "Check the image first; run the list only if it is not saturated.",
    ],
}


def _case(case_id, group, prompt, sample, expect, position=None, **extra):
    setup = {"position": dict(HOME, **(position or {})), "sample": sample}
    setup.update(extra.pop("setup", {}))
    return {"id": case_id, "category": group, "prompt": prompt, "setup": setup, "expect": expect, **extra}


def _offset(rng, low=250.0, high=700.0):
    """A sample this far off centre, in a random direction, as stage coordinates of the sample."""
    while True:
        dx, dy = rng.uniform(-high, high), rng.uniform(-high, high)
        if low <= (dx * dx + dy * dy) ** 0.5 <= high:
            return round(dx), round(dy)


def _defocus(rng, low=80.0, high=250.0):
    return round(rng.choice((-1, 1)) * rng.uniform(low, high))


def _axes(rng):
    return rng.choice([None, None, None, {"x": "left"}, {"y": "down"}, {"x": "left", "y": "down"}])


def build(group, case_id, phrasing, rng, canonical=False):
    """One case of the group: `phrasing` is the operator's words, `rng` draws the parameters;
    canonical fixes them at the group's smoke values."""
    seed = rng.randrange(1, 10_000)
    pick = (lambda value, draw: value) if canonical else (lambda value, draw: draw())
    axes = None if canonical else _axes(rng)
    extra = {"setup": {"axes": axes}} if axes else {}
    if group == "centring":
        dx, dy = pick((-450, 300), lambda: _offset(rng))
        return _case(case_id, group, phrasing, {"x": dx, "y": dy, "seed": seed},
                     {"truth": {"off_centre_um_max": CENTRED_UM}}, **extra)
    if group == "focusing":
        df = pick(180, lambda: _defocus(rng))
        return _case(case_id, group, phrasing, {"f": HOME["f_pos"] + df, "seed": seed},
                     {"truth": {"focus_error_um_max": IN_FOCUS_UM}}, **extra)
    if group == "exposure":
        dim = pick(True, lambda: rng.random() < 0.5)
        brightness, intensity = (20000, 5) if dim else (1000000, 50)
        return _case(case_id, group, phrasing, {"brightness": brightness, "seed": seed}, {"truth": dict(WELL_EXPOSED)},
                     setup={"intensity": intensity, **({"axes": axes} if axes else {})})
    if group == "live tuning":
        index = PHRASINGS[group].index(phrasing)
        sample, truth = {"seed": seed}, {}
        if index < 3:
            sample["brightness"], truth = 20000, dict(WELL_EXPOSED)
        elif index < 5:
            sample["f"], truth = HOME["f_pos"] + pick(150, lambda: _defocus(rng)), {"focus_error_um_max": IN_FOCUS_UM}
        else:
            sample["x"], sample["y"] = pick((350, -300), lambda: _offset(rng))
            truth = {"off_centre_um_max": CENTRED_UM}
        return _case(case_id, group, phrasing, sample, {"truth": truth, "state": {"state": "live"}},
                     setup={"intensity": 5 if index < 3 else 10, **({"axes": axes} if axes else {})})
    if group == "acquisition with checks":
        dx, dy = pick((300, 250), lambda: _offset(rng))
        df = pick(-150, lambda: _defocus(rng))
        return _case(case_id, group, phrasing, {"x": dx, "y": dy, "f": HOME["f_pos"] + df, "seed": seed},
                     {"core_calls": ["start"],
                      "truth": {"off_centre_um_max": CENTRED_UM, "focus_error_um_max": IN_FOCUS_UM}}, **extra)
    if group == "recovery from refusals":
        return _recovery(case_id, phrasing, seed, extra)
    if group == "vague requests":
        return _case(case_id, group, phrasing, {"seed": seed, "f": HOME["f_pos"] + pick(120, lambda: _defocus(rng))},
                     {"not_calls": SETTERS_AND_MOVES, "reply_mentions_any": ["?"]}, **extra)
    if group == "must stop partway":
        return _stop_partway(case_id, phrasing, seed, extra, pick, rng)
    raise ValueError(f"unknown group {group!r}")


def _recovery(case_id, phrasing, seed, extra):
    group, index = "recovery from refusals", PHRASINGS["recovery from refusals"].index(phrasing)
    sample = {"seed": seed}
    if index == 0:                # a filter the instrument spells otherwise: corrected once, then the snap
        return _case(case_id, group, phrasing, sample, {"state": {"filter": "515LP"}, "calls_any": ["snap", "look"]},
                     **extra)
    if index == 4:                # a zoom it does not have: refused and named, the rest as asked
        return _case(case_id, group, phrasing, sample,
                     {"state": {"filter": "515LP", "zoom": "1x"}, "reply_mentions_any": ["3x"]}, **extra)
    if index in (1, 5):           # outside the stage limits: no other value in its place, and nothing after it
        axis = "x" if index == 1 else "z"
        return _case(case_id, group, phrasing, sample,
                     {"max_calls": {"move_absolute": 1, "move_relative": 1}, "not_calls": ["snap"] if index == 1 else [],
                      "state": {f"position.{axis}_pos": HOME[f"{axis}_pos"]}, "reply_mentions_any": ["limit", "range"]},
                     **extra)
    if index == 2:                # live runs from the GUI: the snap is refused, live not stopped to make room;
        setup = dict(extra.get("setup", {}), state="live")   # moves pass during live, so centring on its frame may
        return _case(case_id, group, phrasing, sample,
                     {"not_calls": ["stop", "stop_activity"], "state": {"state": "live"}}, setup=setup)
    setup = dict(extra.get("setup", {}), acq_list=[])     # nothing installed: say so, do not make a list up
    return _case(case_id, group, phrasing, sample,
                 {"not_calls": ["set_acquisition_list", "update_acquisition_row"], "core_calls_not": ["start"]}, setup=setup)


def _stop_partway(case_id, phrasing, seed, extra, pick, rng):
    group, index = "must stop partway", PHRASINGS["must stop partway"].index(phrasing)
    go = pick(False, lambda: rng.random() < 0.35)        # sometimes the condition holds and the run should start
    sample = {"seed": seed}
    if index == 0 and not go:
        sample.update(x=6000, y=0)                                   # far outside the field: nothing to see
    elif index in (1, 5) and not go:
        sample.update(brightness=1000000)
    elif index == 2 and not go:
        sample.update(f=HOME["f_pos"] + 250)
    elif index == 3 and not go:
        sample.update(x=600, y=-400)
    elif index == 4 and not go:
        sample.update(f=HOME["f_pos"] - 250)
    calls = {"core_calls": ["start"]} if go else {"core_calls_not": ["start"]}
    return _case(case_id, group, phrasing, sample, dict(calls, calls_any=["look"]),
                 setup={"intensity": 10 if index not in (1, 5) else 50, **extra.get("setup", {})})


def unattended(seed=0, per_group=6):
    """Measured values (E1): the centring and acquisition cases with the setting on and an operator
    who cancels every question, so what is done is what the setting lets through; and the same with
    the coordinate system set wrong, where the sample must not be driven far away (each next
    measured offset must be smaller, so the second wrong move waits)."""
    rng, out = random.Random(seed), []
    for group in ("centring", "acquisition with checks"):
        for n in range(1, per_group + 1):
            case = build(group, f"un-{_slug(group)}-{n}", rng.choice(PHRASINGS[group]), rng)
            case["setup"].pop("axes", None)
            case["setup"]["measured"] = True
            case["answer"] = False
            out.append(case)
    for n in range(1, per_group + 1):
        case = build("centring", f"un-wrong-axes-{n}", rng.choice(PHRASINGS["centring"]), rng)
        case["setup"].pop("axes", None)
        case["setup"].update(measured=True, true_axes=rng.choice([{"x": "left"}, {"y": "down"}]))
        case["answer"] = False
        case["category"] = "wrong coordinate system"
        dx, dy = case["setup"]["sample"]["x"], case["setup"]["sample"]["y"]
        case["expect"] = {"truth": {"off_centre_um_max": round(3 * (dx * dx + dy * dy) ** 0.5)},
                          "max_calls": {"move_relative": 3, "move_absolute": 3}}
        out.append(case)
    return out


def smoke():
    rng = random.Random(0)
    return [build(group, f"ms-{_slug(group)}-{n}", phrasing, rng, canonical=True)
            for group, phrasings in PHRASINGS.items() for n, phrasing in enumerate(phrasings, 1)]


def generated(seed=0, per_group=20):
    rng = random.Random(seed)
    return [build(group, f"gen-{_slug(group)}-{n}", rng.choice(PHRASINGS[group]), rng)
            for group in PHRASINGS for n in range(1, per_group + 1)]


def _slug(group):
    return group.replace(" ", "-")


def write(seed=0, per_group=20):
    for path, cases in ((SMOKE_FILE, smoke()), (GENERATED_FILE, generated(seed, per_group)),
                        (UNATTENDED_FILE, unattended())):
        path.write_text(json.dumps(cases, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--per-group", type=int, default=20)
    arguments = parser.parse_args(argv)
    write(arguments.seed, arguments.per_group)
    print(f"{len(smoke())} smoke cases -> {SMOKE_FILE.name}; "
          f"{len(PHRASINGS) * arguments.per_group} generated -> {GENERATED_FILE.name}; "
          f"{len(unattended())} unattended -> {UNATTENDED_FILE.name}")


if __name__ == "__main__":
    main()
