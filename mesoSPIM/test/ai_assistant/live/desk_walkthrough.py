"""A live walk through the tools a chat has beyond the microscope's calls, with a real model on
the simulated instrument: the status, the search over the documentation, a skill made from a
conversation and confirmed, that skill found and loaded in the next chat, an earlier conversation
found by search, and an unticked skill left alone.

    ANTHROPIC_API_KEY=... python -m mesoSPIM.test.ai_assistant.live.desk_walkthrough [--model NAME]
    GEMINI_API_KEY=... python -m mesoSPIM.test.ai_assistant.live.desk_walkthrough --provider Gemini \
        --model gemma-4-26b-a4b-it

The second line tries an open model that Google also serves, such as Gemma 4 26B. That shows how
well the model uses the tools before anyone sets it up on a computer of their own; it says
nothing about how fast it would be there.

It prints, per message, the tools the model called and the start of its reply, and ends with one
line per check: PASS or FAIL. Nothing is written outside a temporary folder. It costs a few cents.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from pathlib import Path

from mesoSPIM.test.ai_assistant.evals import harness      # first: it installs the Qt substitute
from mesoSPIM.src.ai_assistant import assistant as ai
from mesoSPIM.src.ai_assistant import config, conversations, skills

DOCS = Path(__file__).resolve().parents[4] / "docs" / "source"


class Chat:
    """One chat on a fresh simulated instrument, with a desk as the setup tab would fill it."""

    def __init__(self, endpoint, root, switched_off=(), confirm=True):
        self.core = harness.SimulatedInstrument()
        self.acceptor = harness.SimulatedAcceptor(self.core)
        self.store = ai.SessionStore()
        self.asked, self.history, self.tokens = [], [], 0
        entries = skills.library(root / "config" / "skills", root / "home" / "skills")
        self.recorder = conversations.Recorder(root / "home" / "conversations", endpoint.model)

        def answer(kind, details):
            self.asked.append((kind, details))
            return confirm
        self.desk = ai.Desk(skills=skills.chosen(entries, switched_off),
                            switched_off=tuple(e.key for e in entries if e.usable and e.key in switched_off),
                            personal_skills=root / "home" / "skills", documentation=DOCS,
                            conversations=root / "home" / "conversations", saving=True, confirm=answer,
                            status=lambda: {"provider": endpoint.provider, "model": endpoint.model,
                                            "context_tokens": self.tokens, "context_max_tokens": 900_000,
                                            "skills": sorted(self.desk.skills), "conversations_saved": True})
        self.agent = ai.build_agent(self.acceptor, threading.Event(), endpoint=endpoint, store=self.store,
                                    desk=self.desk)

    def say(self, text, label=None):
        result = self.agent.run_sync(ai.with_state(self.acceptor, text, self.store), message_history=self.history)
        self.store.finish(result.new_messages(), result.output)
        self.history = result.all_messages()
        self.tokens = ai.last_request_tokens(result.new_messages()) or self.tokens
        self.recorder.save(self.store.turns)
        calls = [call["tool"] for call in ai.turn_trace(result.new_messages())]
        print(f"\n> {label or text}\n  tools: {calls or 'none'}\n  reply: {' '.join(result.output.split())[:300]}")
        return calls, result.output

    def close(self):
        self.acceptor.close()
        self.acceptor.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="Anthropic", choices=["Anthropic", "Gemini", "OpenAI"])
    parser.add_argument("--model", default="")
    arguments = parser.parse_args()
    endpoint = ai.Endpoint.from_preset(arguments.provider, model=arguments.model)
    if not endpoint.api_key:
        raise SystemExit(f"set {config.PROVIDERS[arguments.provider]['key_env']} first")
    root = Path(tempfile.mkdtemp(prefix="mesospim-desk-"))
    (root / "config" / "skills").mkdir(parents=True)
    for name in ("centring", "exposure"):                # two of the evaluation's skills, as system skills
        source = Path(harness.__file__).parent / "skills" / f"{name}.md"
        (root / "config" / "skills" / f"{name}.md").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    checks = []
    saved_config = (ai.config.WAIT_CAP_S, ai.config.POLL_INTERVAL_S)
    ai.config.WAIT_CAP_S, ai.config.POLL_INTERVAL_S = harness.WAIT_CAP_S, 0.0
    try:
        first = Chat(endpoint, root)
        calls, reply = first.say("How full is your context, and which skills do you have in this chat?")
        checks.append(("get_status answers a question about the assistant itself", "get_status" in calls))
        calls, reply = first.say("How do I start the Remote Control so that a script can connect, and what is the "
                                 "token for? Look it up in the documentation.")
        checks.append(("search is used for a question about how mesoSPIM works", "search" in calls))
        calls, reply = first.say("Set the laser intensity to 15 percent, set the exposure time to 20 ms, and then "
                                 "take a snap. This is how I always start with a new sample.")
        checks.append(("a small procedure is carried out", {"set_intensity", "snap"} <= set(calls) or "set_state" in calls))
        calls, reply = first.say(config.CREATE_SKILL_REQUEST, label="[Create skill]")
        files = list((root / "home" / "skills").glob("*.md"))
        checks.append(("Create skill calls save_skill and the operator is asked", "save_skill" in calls
                       and bool(first.asked)))
        checks.append(("the skill is written as a valid personal skill", len(files) == 1
                       and bool(skills.load(root / "home" / "skills", "personal")[0])))
        if first.asked:
            print("\n  the skill the operator was shown:")
            for key in ("name", "description", "tools", "steps"):
                print(f"    {key}: {first.asked[0][1].get(key)!r}"[:400])
        first.close()

        second = Chat(endpoint, root)                      # the next chat: the new skill is ticked
        personal = [key for key, skill in second.desk.skills.items() if skill.kind == "personal"]
        calls, reply = second.say("I have put in a new sample. Please start it the way I always do.")
        checks.append(("the next chat loads the new personal skill for a matching request",
                       calls[:1] == ["load_skill"] and bool(personal)))
        calls, reply = second.say("What did we look up in the documentation last time? Check our earlier conversation.")
        checks.append(("an earlier conversation is found by search", "search" in calls
                       and "remote control" in reply.lower()))
        second.close()

        third = Chat(endpoint, root, switched_off={"centring"})   # centring exists but is not ticked
        calls, reply = third.say("Use the centring skill to centre my sample.")
        loaded = [call for call in ai.turn_trace(third.history) if call["tool"] == "load_skill"
                  and "instructions" in str(call.get("result"))]
        checks.append(("an unticked skill is not loaded", not loaded))
        declined = Chat(endpoint, root, confirm=False)
        declined.say("Set the laser intensity to 30 percent, set the exposure time to 50 ms, take a snap and tell me "
                     "whether it is saturated. I do this every morning as my daily check.")
        calls, reply = declined.say(config.CREATE_SKILL_REQUEST, label="[Create skill, and the operator says no]")
        checks.append(("a declined skill is asked about and not written", bool(declined.asked)
                       and len(list((root / "home" / "skills").glob("*.md"))) == len(files)))
        third.close()
        declined.close()
    finally:
        ai.config.WAIT_CAP_S, ai.config.POLL_INTERVAL_S = saved_config
    print()
    for what, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {what}")
    print(f"\n{sum(passed for _, passed in checks)} of {len(checks)} checks passed with {endpoint.model}")
    return 0 if all(passed for _, passed in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
