"""Print the demo instrument's position and settings over Remote Control TCP (read-only)."""
import json, os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), *[".."] * 4)))
from mesoSPIM.test.remote_control.support.clients import RemoteControl
c = RemoteControl("127.0.0.1", 42000, "smart_mesospim", timeout=30)
pos = c.call("get_position")
st = c.call("get_state_all", keys=["intensity", "filter", "laser", "zoom", "shutterconfig", "state"])
acq = c.call("get_acquisition_list").get("acquisitions") or []
print(json.dumps({"x": pos.get("x"), "y": pos.get("y"), **st, "rows": [(r.get("filename"), r.get("z_end"), r.get("folder")) for r in acq]}))
c.close()
