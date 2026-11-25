"""An experiment, not part of mesoSPIM: the evaluation as an operator's chat really is.

The recorded evaluation builds the agent without the five tools that every chat started from the
window has (get_status, search, read_passage, save_skill, edit_skill). With --desk this file
gives each case a desk as the setup tab would: one system skill and one personal skill ticked,
the repository's documentation, an empty folder of saved conversations, and an operator who
answers yes. Without --desk it runs the cases exactly as the evaluation does, so the two can be
set side by side on the same cases.

    ANTHROPIC_API_KEY=... python desk_eval.py [--desk] --provider Anthropic --cases FILE --only id,id ...
(the other arguments are those of mesoSPIM.test.ai_assistant.evals.run)
"""
import sys
import tempfile
from pathlib import Path

from mesoSPIM.test.ai_assistant.evals import harness, run
from mesoSPIM.src.ai_assistant import assistant as ai
from mesoSPIM.src.ai_assistant import skills

DESK_TOOLS = ("get_status", "search", "read_passage", "save_skill", "edit_skill", "load_skill")
READ_ONLY = ("get_status", "search", "read_passage", "load_skill")
DOCS = Path(__file__).resolve().parent / "docs" / "source"
PERSONAL = ("---\nname: overview\ndescription: Take one low-zoom snap of the whole sample for an overview.\n"
            "tools: set_zoom, snap\n---\nSet the lowest zoom, take one snap, and report what the frame shows.\n")

# The new tools that read and change nothing count as reads, as look does.
_reads = harness._reads
harness._reads = lambda name: name in READ_ONLY or _reads(name)
_check = harness.check_cases
harness.check_cases = lambda cases: [p for p in _check(cases) if not any(f"unknown tool {t}" in p for t in DESK_TOOLS)]


def with_desk():
    build = ai.build_agent

    def build_agent(acceptor, cancel, **kwargs):
        root = Path(tempfile.mkdtemp(prefix="mesospim-desk-eval-"))
        (root / "system").mkdir()
        (root / "personal").mkdir()
        centring = Path(harness.__file__).parent / "skills" / "centring.md"
        (root / "system" / "centring.md").write_text(centring.read_text(encoding="utf-8"), encoding="utf-8")
        (root / "personal" / "overview.md").write_text(PERSONAL, encoding="utf-8")
        endpoint = kwargs.get("endpoint")
        desk = ai.Desk(skills=skills.chosen(skills.library(root / "system", root / "personal")),
                       personal_skills=root / "personal", documentation=DOCS if DOCS.is_dir() else None,
                       conversations=root / "conversations", saving=True, confirm=lambda kind, details: True)
        desk.status = lambda: {"provider": getattr(endpoint, "provider", None), "model": getattr(endpoint, "model", None),
                               "context_tokens": 0, "context_max_tokens": 900_000, "skills": sorted(desk.skills),
                               "conversations_saved": True}
        kwargs.pop("skills", None)
        return build(acceptor, cancel, desk=desk, **kwargs)
    ai.build_agent = build_agent


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if "--desk" in arguments:
        arguments.remove("--desk")
        with_desk()
    sys.exit(run.main(arguments))
