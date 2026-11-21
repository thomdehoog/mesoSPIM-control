"""An experiment, not part of mesoSPIM: the evaluation with a core set of tools and a catalogue.

The model is offered about twenty everyday commands as real tools. Every other command is listed
in the instructions by name with one line, and reached through two general tools: describe_tool
returns a command's arguments as text, run_tool runs it. Nothing in mesoSPIM's own code is
changed; this file swaps the pieces in while the evaluation runs.

For the score, a run_tool call counts as a call to the command it ran, with that command's
arguments, so the cases' expectations apply as they stand. describe_tool calls are counted apart.

    GEMINI_API_KEY=... python core_catalogue_eval.py --provider Gemini --model gemma-4-26b-a4b-it ...
(the arguments are those of mesoSPIM.test.ai_assistant.evals.run)
"""
import json
import sys
import time

from mesoSPIM.test.ai_assistant.evals import harness, run
from mesoSPIM.src.ai_assistant import assistant as ai
from mesoSPIM.src.ai_assistant import config
from mesoSPIM.src.remote_control.dispatcher import COMMANDS

CORE = ["get_position", "get_state", "get_snapshot", "get_progress", "get_acquisition_list", "move_absolute",
        "move_relative", "start_live", "snap", "center_sample", "run_acquisition_list", "zero", "set_filter",
        "set_zoom", "set_laser", "set_intensity", "set_shutterconfig", "set_camera", "open_shutters",
        "close_shutters", "stop", "stop_activity"]
CATALOGUE_HEAD = ("# More commands\n\nThese commands exist but are not among your tools. To use one, call describe_tool "
                  "with its name to read its arguments, then call run_tool with the name and the arguments. It then "
                  "acts exactly like one of your tools.\n")
DESCRIBED = {"count": 0}

_everything = ai.offered_commands()
_others = [cmd for cmd in _everything if cmd.name not in CORE]
ai.offered_commands = lambda: [cmd for cmd in _everything if cmd.name in CORE]


def _line(cmd):
    return f"- {cmd.name}: {config.TOOL_DESCRIPTIONS.get(cmd.name, cmd.hint or cmd.name)}"


_prompt = ai.build_system_prompt


def build_system_prompt(*args, **kwargs):
    return _prompt(*args, **kwargs) + "\n\n" + CATALOGUE_HEAD + "\n".join(_line(cmd) for cmd in _others)


_tools = ai.build_tools


def build_tools(acceptor, cancel, on_call=None, **kwargs):
    from pydantic_ai import Tool
    made = _tools(acceptor, cancel, on_call, **kwargs)
    names = sorted(cmd.name for cmd in _others)
    clock = kwargs.get("clock", time.time)

    def describe_tool(name):
        DESCRIBED["count"] += 1
        cmd = COMMANDS.get(name)
        if cmd is None or name in CORE:
            return json.dumps({"error": {"code": "validation", "message": f"no command {name!r} in the catalogue"}})
        return json.dumps({"name": name, "kind": cmd.kind, "description": config.TOOL_DESCRIPTIONS.get(name, cmd.hint),
                           "arguments": cmd.schema})

    def run_tool(name, arguments=None):
        cmd = COMMANDS.get(name)
        if cmd is None or name in CORE:
            return json.dumps({"error": {"code": "validation", "message": f"no command {name!r} in the catalogue"}})
        return ai._tool_fn(acceptor, name, cmd.kind, cancel, on_call, clock, None, kwargs.get("on_started"))(**(arguments or {}))

    made.append(Tool.from_schema(
        describe_tool, name="describe_tool", sequential=True,
        json_schema={"type": "object", "properties": {"name": {"type": "string", "enum": names}}, "required": ["name"],
                     "additionalProperties": False},
        description="Returns the arguments of one command listed under 'More commands' in your instructions."))
    made.append(Tool.from_schema(
        run_tool, name="run_tool", sequential=True,
        json_schema={"type": "object",
                     "properties": {"name": {"type": "string", "enum": names},
                                    "arguments": {"type": "object", "additionalProperties": True,
                                                  "description": "the command's arguments, as describe_tool gave them"}},
                     "required": ["name"], "additionalProperties": False},
        description="Runs one command listed under 'More commands', with its arguments. Call describe_tool first "
                    "unless you have already read that command's arguments in this conversation."))
    return made


_trace = ai.turn_trace


def turn_trace(messages):
    """The calls of a turn as the cases expect them: a run_tool call is the command it ran."""
    out = []
    for call in _trace(messages):
        if call["tool"] == "describe_tool":
            continue
        if call["tool"] == "run_tool":
            args = call.get("args") or {}
            call = dict(call, tool=args.get("name"), args=args.get("arguments") or {}, via="run_tool")
        out.append(call)
    return out


ai.build_system_prompt, ai.build_tools, ai.turn_trace = build_system_prompt, build_tools, turn_trace

if __name__ == "__main__":
    if sys.argv[1:2] == ["--check"]:
        import threading
        acc = harness.SimulatedAcceptor(harness.SimulatedInstrument())
        tools = {t.name: t for t in ai.build_tools(acc, threading.Event())}
        prompt = ai.build_system_prompt()
        size = sum(len(json.dumps(t.function_schema.json_schema)) + len(t.description or "") for t in tools.values())
        print(len(tools), "tools;", len(_others), "in the catalogue; prompt", len(prompt), "chars; tool text", size, "chars")
        print("describe:", tools["describe_tool"].function(name="get_limits")[:200])
        print("run     :", tools["run_tool"].function(name="get_limits", arguments={})[:160])
        print("run bad :", tools["run_tool"].function(name="set_etl", arguments={"nonsense": 1})[:200])
        print("core ref:", tools["run_tool"].function(name="snap")[:120])
    else:
        code = run.main(sys.argv[1:])
        print(f"\ndescribe_tool was called {DESCRIBED['count']} times")
        sys.exit(code)
