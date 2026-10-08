# 03. The live suite skips everything with the brief's two variables

**Kind:** gap in the test brief, and a runner that hides it. **Status:** open.

## What was seen

The brief says: set `MESOSPIM_ALLOW_DEVICE_CHANGE=1` and `MESOSPIM_OPERATOR_PRESENT=1`, then run
`python mesoSPIM/test/remote_control/run.py live tcp` (and `live mcp`). Every test skipped,
`run.py` printed only "2 skipped" and "6 skipped", and it exited 0. A run that tested nothing looks
like a pass.

## Reproduce (no model, no demo running)

    python docs/test-reports/evidence/03-live-suite-environment/repro_live_skips.py

It runs the selection with only the brief's two variables, first through `run.py`, then through
pytest with `-rs`, which shows the skip reasons. Exit code 1: everything skipped.

Output on the test machine ([repro_output.txt](repro_output.txt)), 8 October 2026:

    1. run.py live tcp (exit 0):
       2 skipped in 0.10s
       6 skipped in 0.09s
    2. the same selection with pytest -rs (exit 0):
       SKIPPED [1] live/test_valid.py:60: set MESOSPIM_LIVE_TCP_TOKEN for the live TCP server
       SKIPPED [7] ...\support\live_session.py:40: set MESOSPIM_CONFIRM_DEMO_MODE=1 to permit the DemoStage test
       8 skipped in 0.12s
    REPRODUCED: every live test skipped

Each test reports only the first variable it misses: the checks run one after another
(mesoSPIM/test/remote_control/support/live_session.py:31 to 63), so fixing one reveals the next.

## What it took to run them

With a demo mesoSPIM running and serving the transport (`tools/drive_gui.py --serve TCP`), this
environment ran all 8 TCP tests, 8 passed in 67 s
([../run-logs/step3_live_tcp.log](../run-logs/step3_live_tcp.log)):

| Variable | Value used | Read by |
| --- | --- | --- |
| MESOSPIM_ALLOW_DEVICE_CHANGE | 1 | all (named in the brief) |
| MESOSPIM_OPERATOR_PRESENT | 1 | all (named in the brief) |
| MESOSPIM_CONFIRM_DEMO_MODE | 1 | live_session.live_config |
| MESOSPIM_RUN_ALL_COMMANDS | 1 | test_all_commands.py |
| MESOSPIM_RUN_LIVE_ADVERSARIAL | 1 | test_adversarial.py |
| MESOSPIM_DEMO_ROOT | the clone's `mesoSPIM` folder | live_session.live_config |
| MESOSPIM_DEMO_ETL_CONFIG_PATH | `<DEMO_ROOT>\config\etl_parameters\ETL-parameters.csv` | live_session.live_config |
| MESOSPIM_DEMO_PROCESS_ID | PID of the running demo mesoSPIM | live_session.live_config |
| MESOSPIM_LIVE_DEMO_TRANSPORT, MESOSPIM_LIVE_ADVERSARIAL_TRANSPORT | tcp (run.py sets these) | test_all_commands.py, test_adversarial.py |
| MESOSPIM_LIVE_TCP_HOST, MESOSPIM_LIVE_TCP_PORT | 127.0.0.1, 42000 | TCP tests |
| MESOSPIM_LIVE_TCP_TOKEN | the Remote Control tab's password (default `smart_mesospim`) | TCP tests |

For MCP the same, with transport `mcp`, `MESOSPIM_LIVE_MCP_URL=http://127.0.0.1:42100/mcp` and
`MESOSPIM_LIVE_MCP_TOKEN` instead of the TCP three: 8 passed in 65 s
([../run-logs/step3_live_mcp.log](../run-logs/step3_live_mcp.log)).

The tests also write marker files (`.mesospim_demo_all_<pid>_<transport>.done` and others) to
`tempfile.gettempdir()`. On a machine where nothing may be written outside one folder, set `TEMP`
and `TMP` there for the run. The markers make a second run against the same demo PID skip; restart
the demo or delete them to run again.

## Fix to consider

- List all of these in the brief, or in `run.py --help`.
- Have `run.py live` check the variables up front and print what is missing, and exit non-zero
  when every test skipped.
