"""Saved conversations: one file per chat, kept only when the facility has turned saving on.

Saving is off unless the microscope's config file says `'save_conversations': True`. When it is
on, each chat with the AI Assistant is written to a file in the operator's own folder, and the
window says so. A saved conversation can be found again later with the assistant's `search`
call, which is the reason to save at all: "what did we do last time on this sample?"

What a file holds, for every turn of the chat: the time, what the operator typed, the calls the
assistant made with their results, and its reply. Camera images are never written, only the
numbers that describe them, so a file stays small. The API key is never part of a conversation.

A file is written again after every turn, so a chat that ends abruptly (a power cut, a crash) is
saved up to its last finished turn. Removing a conversation erases its file for good: it is the
operator's own record, and "removed" should mean gone.

Maintainer (2026):
    Thom de Hoog
    Center for Microscopy and Image Analysis
    thom.dehoog@zmb.uzh.ch
    thomdehoog@gmail.com
"""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SUFFIX = ".json"
TITLE_CHARS = 80                # how much of the first message names a conversation in a list


class Recorder:
    """Writes one chat to its file, again after every turn. Made when a chat starts and saving is
    on; Clear context makes a new one, so each cleared chat is a conversation of its own."""

    def __init__(self, folder, model="", clock=time.time):
        self.folder = Path(folder)
        self.model = model
        self.started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(clock()))
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))
        self.path = self.folder / (stamp + SUFFIX)
        number = 1
        while self.path.exists():           # a chat cleared and started again within the same second
            number += 1
            self.path = self.folder / f"{stamp}-{number}{SUFFIX}"
        self.problem = None         # a sentence for the window when the file could not be written

    def save(self, turns):
        """Write the chat so far. `turns` are the session's turns (assistant.SessionStore.turns).
        Returns True when the file is on disk. A failure never stops the chat: it is logged, kept
        in `problem` for the window to show, and tried again after the next turn."""
        if not turns:
            return True
        document = {
            "started": self.started,
            "model": self.model,
            "turns": [{"time": turn.get("time"), "prompt": turn.get("prompt"),
                       "tools": turn.get("tools") or [], "reply": turn.get("reply")} for turn in turns],
        }
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            # To a second file first, which then takes the place of the real one in one step, so
            # that a crash half way never leaves a broken conversation behind.
            draft = self.path.with_suffix(".tmp")
            draft.write_text(json.dumps(document, indent=1, default=str), encoding="utf-8")
            os.replace(draft, self.path)
            self.problem = None
            return True
        except OSError as error:
            self.problem = f"This conversation could not be saved in {self.folder}: {error}"
            logger.warning("AI Assistant: %s", self.problem)
            return False


def read(path):
    """One saved conversation as it was written, or None when the file cannot be read."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) and isinstance(document.get("turns"), list) else None


def title_of(document):
    """What names a conversation in a list: the start of the operator's first message."""
    for turn in document.get("turns", []):
        prompt = " ".join(str(turn.get("prompt") or "").split())
        if prompt:
            return prompt if len(prompt) <= TITLE_CHARS else prompt[:TITLE_CHARS - 1] + "…"
    return "(no message)"


def listing(folder):
    """The saved conversations in a folder, newest first, each as
    {"path", "started", "title", "turns"}. A file that cannot be read is left out."""
    found = []
    folder = Path(folder) if folder is not None else None
    if folder is None or not folder.is_dir():
        return found
    for path in sorted(folder.glob("*" + SUFFIX), reverse=True):
        document = read(path)
        if document is not None:
            found.append({"path": path, "started": document.get("started", ""), "title": title_of(document),
                          "turns": len(document["turns"])})
    return found


def remove(path):
    """Erase one saved conversation for good. It then no longer turns up in a search either.
    Returns False, after a line in the log, when the file could not be erased."""
    try:
        Path(path).unlink()
        return True
    except OSError as error:
        logger.warning("AI Assistant: the conversation %s could not be removed: %s", path, error)
        return False


def turn_text(turn):
    """One turn as plain text, for searching and for reading back: what was asked, the calls made
    with what they returned, and the reply."""
    lines = [f"Operator ({turn.get('time') or 'time unknown'}): {turn.get('prompt') or ''}"]
    for call in turn.get("tools") or []:
        lines.append(f"Call: {call.get('tool')}({json.dumps(call.get('args') or {}, default=str)})"
                     f" -> {call.get('result')}")
    if turn.get("reply"):
        lines.append(f"Assistant: {turn['reply']}")
    return "\n".join(lines)
