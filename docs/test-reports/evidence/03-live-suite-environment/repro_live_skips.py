"""Reproduce: `run.py live tcp` skips every test when only the brief's two variables are set.

Runs the live TCP selection twice with a clean environment (no other MESOSPIM_* variables) and only
MESOSPIM_ALLOW_DEVICE_CHANGE=1 and MESOSPIM_OPERATOR_PRESENT=1, as the test brief says:
  1. through mesoSPIM/test/remote_control/run.py live tcp, which prints only "skipped";
  2. through pytest -rs on the same files, which prints each skip's reason.
No demo has to be running: every test skips before it connects.

    python repro_live_skips.py

Exit code 1: everything skipped (the gap). Exit code 0: something ran.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, *[".."] * 4))
LIVE = os.path.join("mesoSPIM", "test", "remote_control", "live")


def environment():
    env = {key: value for key, value in os.environ.items() if not key.startswith("MESOSPIM_")}
    env.pop("PYTEST_ADDOPTS", None)
    env.update(MESOSPIM_ALLOW_DEVICE_CHANGE="1", MESOSPIM_OPERATOR_PRESENT="1")
    return env


def run(command, extra=None):
    env = environment()
    env.update(extra or {})
    result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, errors="replace")
    return result.returncode, result.stdout + result.stderr


def main():
    print("environment: only MESOSPIM_ALLOW_DEVICE_CHANGE=1 and MESOSPIM_OPERATOR_PRESENT=1\n")
    code, out = run([sys.executable, os.path.join("mesoSPIM", "test", "remote_control", "run.py"), "live", "tcp"])
    print(f"1. run.py live tcp (exit {code}):")
    for line in out.splitlines():
        if re.search(r"passed|failed|skipped|error", line):
            print("   " + line.strip())

    selection = [os.path.join(LIVE, "test_valid.py") + "::test_live_tcp_x_move_changes_position_and_restores_it",
                 os.path.join(LIVE, "test_all_commands.py"), os.path.join(LIVE, "test_adversarial.py")]
    code, out = run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", *selection],
                    {"MESOSPIM_LIVE_DEMO_TRANSPORT": "tcp", "MESOSPIM_LIVE_ADVERSARIAL_TRANSPORT": "tcp"})
    print(f"\n2. the same selection with pytest -rs (exit {code}):")
    reasons = [line.strip() for line in out.splitlines() if line.startswith("SKIPPED")]
    for line in reasons:
        print("   " + re.sub(r"\S*mesoSPIM[\\/]test[\\/]remote_control[\\/]live[\\/]", "live/", line))
    summary = next((line.strip() for line in reversed(out.splitlines()) if re.search(r"\d+ (passed|skipped|failed)", line)), "")
    print("   " + summary)

    names = sorted(set(re.findall(r"MESOSPIM_[A-Z_]+", out)))
    print(f"\nvariables named in the skip reasons: {', '.join(names)}")
    all_skipped = "skipped" in summary and not re.search(r"\d+ (passed|failed)", summary)
    print("REPRODUCED: every live test skipped" if all_skipped else "NOT REPRODUCED: some live tests ran")
    sys.exit(1 if all_skipped else 0)


if __name__ == "__main__":
    main()
