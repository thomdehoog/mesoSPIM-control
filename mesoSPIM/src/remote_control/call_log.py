"""One line in the mesoSPIM log for every Remote Control call.

The log is there so that, after a problem, someone can read back what happened: who asked the
microscope to do what, with which values, and how it ended. Every call is written, whoever made
it (the AI Assistant, a script over TCP, or an outside AI program over MCP), and whether it
changed something or only read a value. The lines go into the same log file as the rest of
mesoSPIM, so one timeline shows each request next to what the microscope did.

A line reads, after the time that the log file puts in front of every line:

    Remote Control | anna | AI Assistant (claude-haiku-5-5) | set_laser {"laser": "488 nm"} | ok | 0.21 s | {}

That is: the person logged in to the computer, the client that made the call, the call with its
values, how it ended (ok, refused with the reason, or failed with the reason), how long it took,
and what came back. Camera images and other very long values are replaced by a note of their
size, so one frame does not fill the file.

Two things are never written here. The API key is not part of any call, so it cannot appear. The
conversation with the AI Assistant (what the operator typed, what the model answered) is not a
call either, so "Conversations are not saved" stays true.

Maintainer (2026):
    Thom de Hoog
    Center for Microscopy and Image Analysis
    thom.dehoog@zmb.uzh.ch
    thomdehoog@gmail.com
"""

import getpass
import json
import logging

logger = logging.getLogger(__name__)

# A single value longer than this many characters is replaced by a note of its size, and the
# values of one call, or its result, are cut at the second number. A camera frame is hundreds of
# thousands of characters; a list of acquisition rows a few thousand.
VALUE_MAX_CHARS = 200
TEXT_MAX_CHARS = 2000
UNKNOWN_CLIENT = "unknown client"

_user = None


def operator():
    """The name of the person logged in to the computer, as the operating system knows it; the
    word "unknown" on a system that cannot say."""
    global _user
    if _user is None:
        try:
            _user = getpass.getuser()
        except Exception:                      # no login name on this system: the line is still written
            _user = "unknown"
    return _user


def _shortened(value):
    """The value with every very long piece of text replaced by a note of its size."""
    if isinstance(value, str):
        return value if len(value) <= VALUE_MAX_CHARS else f"<{len(value)} characters>"
    if isinstance(value, dict):
        return {key: _shortened(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_shortened(item) for item in value]
    return value


def as_text(value):
    """A call's values, or its result, as one line of text that fits in the log."""
    try:
        text = json.dumps(_shortened(value), default=str)
    except (TypeError, ValueError):
        text = repr(value)
    if len(text) > TEXT_MAX_CHARS:
        text = text[:TEXT_MAX_CHARS] + f" ... <{len(text)} characters in all>"
    return text


def outcome_of(error):
    """How a call ended, in words: "ok", "refused: ..." when the microscope did not accept it (a
    value outside the limits, the microscope busy, a name it does not know), or "failed: ..."
    when it was accepted and then went wrong."""
    if error is None:
        return "ok"
    from .dispatcher import error_info         # here, not at the top: the dispatcher needs Qt
    code, message = error_info(error)
    refused = code in ("validation", "busy", "unknown_command")
    return f"{'refused' if refused else 'failed'}: {message}"


def record(client, name, args, seconds, result=None, error=None):
    """Write the line for one call. It must never be the reason a call fails, so a problem while
    writing is itself logged and then let go."""
    try:
        line = " | ".join((
            "Remote Control", operator(), client or UNKNOWN_CLIENT,
            f"{name} {as_text({} if args is None else args)}",
            outcome_of(error), f"{seconds:.2f} s",
        ))
        if error is None:
            line += " | " + as_text(result)
        logger.info(line)
    except Exception:
        logger.exception("Remote Control: the line for %r could not be written", name)
