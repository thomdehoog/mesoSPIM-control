"""The evaluation without the model: a recorded model run of every case, replayed offline.

A recording keeps, per case, what the model answered to each request (its tool calls and text),
what the eyes answered, the input tokens the provider counted, and the score. Replaying a case plays
those answers back through the real agent, tools, guard and dispatcher on a fresh simulated
instrument, so a change to the code shows as a changed score or a changed request size, with no
model and no scripts written by hand. The size is an offline estimate: the characters of the
instructions, the tool definitions and the messages of every request, over four; images are not
counted (the main model never gets one).

    python -m mesoSPIM.test.ai_assistant.evals.replay [--cases FILE] [--only id,id] [--update]

prints the score and the estimated tokens per case against the recording; --update writes the
estimates into the recording as the new baseline. Recording a run is run.py's --record.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mesoSPIM.src.ai_assistant import assistant as ai
from mesoSPIM.test.ai_assistant.evals import harness

RECORDED = Path(__file__).with_name("recorded")
REPLAY_ENDED = "[the recording has no further answer]"
GROWTH_ALLOWED = 0.03     # a case may grow this much in estimated tokens before the suite says so


def recording_file(cases_file):
    return RECORDED / Path(cases_file).name


def load_recording(cases_file):
    path = recording_file(cases_file)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_recording(cases_file, recording):
    RECORDED.mkdir(exist_ok=True)
    text = json.dumps(dict(sorted(recording.items())), ensure_ascii=False, indent=1)
    recording_file(cases_file).write_text(text + "\n", encoding="utf-8")


def _parts(response):
    """A model response as data: its tool calls and its text, in order."""
    out = []
    for part in response.parts:
        kind = type(part).__name__
        if kind == "ToolCallPart":
            out.append({"tool": part.tool_name, "args": part.args_as_dict()})
        elif kind == "TextPart" and part.content:
            out.append({"text": part.content})
    return out


def _response(parts):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    return ModelResponse(parts=[ToolCallPart(p["tool"], p["args"]) if "tool" in p else TextPart(p["text"])
                                for p in parts])


def recorder(model):
    """The model, keeping each of its responses and the tokens the provider counted."""
    from pydantic_ai.models.wrapper import WrapperModel

    class Recorder(WrapperModel):
        def __init__(self, wrapped):
            super().__init__(wrapped)
            self.responses, self.input_tokens = [], 0

        async def request(self, messages, model_settings, model_request_parameters):
            response = await super().request(messages, model_settings, model_request_parameters)
            self.responses.append(_parts(response))
            usage = response.usage
            # pydantic-ai types the counts through a price table; a model not in it (gemini-3.5)
            # keeps them only per modality in the details
            details = usage.details or {}
            self.input_tokens += usage.input_tokens or sum(v for k, v in details.items() if k.endswith("_prompt_tokens"))
            return response
    return Recorder(model)


def record_case(case, model_factory, endpoint, profile=None, attempts=1, retries=2, retry_wait=None):
    """Run the case on the real model up to `attempts` times, until a run passes, and return the
    last run's trace with its recording under "recording"."""
    for _ in range(max(1, attempts)):
        main = recorder(model_factory())
        eyes = recorder(model_factory()) if endpoint.vision else None
        trace = harness.run_case(case, main, endpoint, profile, retries=retries, retry_wait=retry_wait, vision_model=eyes)
        trace["failures"] = harness.score(case, trace)
        if not trace["error"] and not trace["failures"]:
            break
    trace["recording"] = {
        "model": endpoint.model, "vision": endpoint.vision, "profile": profile, "responses": main.responses,
        "eyes": eyes.responses if eyes is not None else [], "failures": trace["failures"],
        "provider_tokens": {"input": main.input_tokens, "requests": len(main.responses),
                            "eyes_input": eyes.input_tokens if eyes is not None else 0},
    }
    return trace


def request_tokens(messages, info):
    """The estimated input tokens of one request: everything the model is sent, over four."""
    tools = [{"name": t.name, "description": t.description, "parameters": t.parameters_json_schema}
             for t in info.function_tools]
    sent = [info.instructions or "", json.dumps(tools)]
    for message in messages:
        for part in message.parts:
            kind = type(part).__name__
            if kind == "ToolCallPart":
                sent.append(part.tool_name + part.args_as_json_str())
            elif kind == "ToolReturnPart":
                sent.append(part.model_response_str())
            elif kind == "RetryPromptPart":
                sent.append(part.model_response())
            elif isinstance(getattr(part, "content", None), str):
                sent.append(part.content)
    return sum(len(text) for text in sent) // 4


def replayed(responses, sizes=None):
    """A model that gives the recorded responses in order, noting each request's size in `sizes`."""
    from pydantic_ai.models.function import FunctionModel
    steps = iter(responses)

    def play(messages, info):
        if sizes is not None:
            sizes.append(request_tokens(messages, info))
        return _response(next(steps, [{"text": REPLAY_ENDED}]))
    return FunctionModel(play)


def replay_case(case, recorded):
    """The case replayed from its recording: the trace, its failures and the estimated tokens."""
    endpoint = ai.Endpoint(provider="Replay", kind="openai-compatible", model=recorded["model"],
                           vision=recorded["vision"])
    sizes = []
    eyes = replayed(recorded["eyes"]) if recorded["vision"] else None
    trace = harness.run_case(case, replayed(recorded["responses"], sizes), endpoint, recorded["profile"], retries=0, vision_model=eyes)
    trace["failures"] = harness.score(case, trace)
    trace["tokens"] = sum(sizes)
    trace["requests"] = len(sizes)
    return trace


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", default=str(harness.CASES_FILE))
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument("--update", action="store_true", help="write the estimated tokens into the recording")
    arguments = parser.parse_args(argv)
    recording = load_recording(arguments.cases)
    cases = [c for c in harness.load_cases(arguments.cases) if c["id"] in recording]
    if arguments.only:
        cases = [c for c in cases if c["id"] in arguments.only.split(",")]
    changed, total, baseline = [], 0, 0
    print(f"{'case':<36} {'requests':>8} {'tokens':>7} {'baseline':>8} {'change':>7}  score")
    for case in cases:
        recorded = recording[case["id"]]
        trace = replay_case(case, recorded)
        before = recorded.get("tokens")
        growth = (trace["tokens"] - before) / before if before else 0.0
        same = trace["failures"] == recorded["failures"]
        if not same or growth > GROWTH_ALLOWED:
            changed.append(case["id"])
        total, baseline = total + trace["tokens"], baseline + (before or 0)
        print(f"{case['id']:<36} {trace['requests']:>8} {trace['tokens']:>7} {before or '-':>8} {growth:>+7.1%}  "
              f"{'pass' if not trace['failures'] else 'FAIL'}{'' if same else ' (changed: ' + '; '.join(trace['failures']) + ')'}")
        if arguments.update:
            recorded["tokens"] = trace["tokens"]
    print(f"\n{len(cases)} cases, {total} estimated tokens in all"
          + (f" against {baseline} ({(total - baseline) / baseline:+.1%})" if baseline else "")
          + (f"; changed: {', '.join(changed)}" if changed else ""))
    if arguments.update:
        save_recording(arguments.cases, recording)
    return 1 if changed and not arguments.update else 0


if __name__ == "__main__":
    sys.exit(main())
