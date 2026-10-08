"""Show: the Coordinate system box is locked while the AI Assistant is connected.

Starts demo mesoSPIM, connects the AI Assistant tab (placeholder key: Connect makes no network
call), checks the axis boxes are disabled, disconnects, flips x, connects again, and checks the new
axes reach the worker and the system prompt. Rebuilding the agent happens on the next turn, which
needs a model; that part is in ../run-logs/step2_run1.log (step c).

    python repro_axes_box.py

Exit code 0: as documented below. Exit code 1: different.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from demo_app import connect_without_model, pump, start_demo  # noqa: E402


def main():
    app, window = start_demo()
    from mesoSPIM.src.ai_assistant import assistant as ai
    from mesoSPIM.src.ai_assistant import config as aiconfig
    tab = window.ai_assistant
    box = tab.axis_boxes["x"]
    before = box.currentText()
    other = next(choice for choice in aiconfig.AXIS_CHOICES["x"] if choice != before)

    connected = connect_without_model(tab)
    locked = connected and not any(b.isEnabled() for b in tab.axis_boxes.values())
    print(f"connected={connected}; axis boxes enabled while connected: "
          f"{ {axis: b.isEnabled() for axis, b in tab.axis_boxes.items()} }")

    tab.on_disconnect()
    pump(0.5)
    unlocked = all(b.isEnabled() for b in tab.axis_boxes.values())
    print(f"after Disconnect, axis boxes enabled: {unlocked}")
    box.setCurrentText(other)
    reconnected = connect_without_model(tab)
    worker = tab._worker
    prompt = ai.build_system_prompt(profile=worker._profile, axes=worker.axes)
    in_prompt = f"toward the {other} of the image" in prompt
    print(f"x changed {before!r} -> {other!r}; after Connect: worker.axes['x']={worker.axes['x']!r}, "
          f"prompt says 'toward the {other} of the image': {in_prompt}")

    tab.on_disconnect()
    box.setCurrentText(before)
    window.close()
    pump(1)
    ok = locked and unlocked and reconnected and worker.axes["x"] == other and in_prompt
    print("AS DOCUMENTED: locked while connected; a change made while disconnected reaches the worker and the prompt"
          if ok else "DIFFERENT from the documented behaviour")
    os._exit(0 if ok else 1)


if __name__ == "__main__":
    main()
