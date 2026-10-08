# 04. The AI Assistant and a Remote Control transport cannot run together

**Kind:** gap in the test brief; the behaviour is by design. **Status:** brief to correct.

## What was seen

Step 2 of the brief says: in the Remote Control tab start TCP, then in the AI Assistant tab connect
the Gemini preset. With TCP running, Connect is refused and the status line says:

    Stop the Remote Control transport to use the AI Assistant.

After Stop in the Remote Control tab, Connect works. The walkthrough and live suites of step 3, on
the other hand, need TCP or MCP running, so steps 2 and 3 need the transport in opposite states.

From the driven GUI run ([../run-logs/step2_run1.log](../run-logs/step2_run1.log), lines 16 to 19):

    PASS setup: TCP starts: TCP running on 127.0.0.1:42000
    PASS setup: Connect while TCP runs is refused: Stop the Remote Control transport to use the AI Assistant.
    PASS setup: Gemini connects after TCP stops: connected: Gemini, gemini-3.5-flash-lite

## Reproduce (no model, no key)

    python docs/test-reports/evidence/04-tcp-and-assistant-exclusive/repro_exclusive.py

Output on the test machine ([repro_output.txt](repro_output.txt)):

    Remote Control: TCP running on 127.0.0.1:42000
    Connect while TCP runs: state='idle', status line='Stop the Remote Control transport to use the AI Assistant.'
    Remote Control after Stop: stopped
    Connect after TCP stops: state='ready', status line='connected: Gemini, gemini-3.5-flash-lite'
    AS DOCUMENTED: refused while TCP runs, connects after Stop

Exit code 0 while the behaviour stays as described here.

## Where it is decided (code at 41839f2)

`start_assistant_for_core` (mesoSPIM/src/ai_assistant/assistant.py:1780) refuses while
`core._remote_control` is set, "so the assistant does not start a second controller behind the
operator's back", and leaves the reason in `core._assistant_refusal`. The tab's `_connect`
(mesoSPIM/src/ai_assistant/gui.py:670) shows it.

## Suggested change

In the brief: "Stop the Remote Control transport, then connect the AI Assistant" for step 2, and
"Disconnect the AI Assistant, then start TCP (or MCP)" for step 3. No code change.
