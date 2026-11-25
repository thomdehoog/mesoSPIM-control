"""Search through the mesoSPIM documentation and the operator's saved conversations.

The assistant cannot hold the whole documentation, or every earlier conversation, in its head at
once: that would fill its memory before the operator has typed anything. So it looks things up
when it needs them, the way a person would. It asks a question in a few words, gets back a
handful of short excerpts with the place each one came from, and reads one of them in full only
when an excerpt is not enough.

The search is a ranked word search: it finds the passages that contain the words asked for and
puts the best matches first. It uses the small database that comes with Python (SQLite), so it
needs nothing installed, no second API key and no internet connection. It does not understand
meaning the way the model does; a question works best when it uses the words the documentation
would use ("light-sheet alignment", "ETL calibration").

What is searched:

- **The documentation**: the text files in the folder that the config file's `documentation`
  line points to. Each file is cut into sections at its headings.
- **Saved conversations**: the files in the operator's own conversations folder, one passage per
  turn. These exist only when the facility has turned saving on.

Maintainer (2026):
    Thom de Hoog
    Center for Microscopy and Image Analysis
    thom.dehoog@zmb.uzh.ch
    thomdehoog@gmail.com
"""

import logging
import re
import sqlite3
from pathlib import Path

from . import conversations

logger = logging.getLogger(__name__)

DOCUMENTATION, CONVERSATIONS, BOTH = "documentation", "conversations", "both"
DOC_SUFFIXES = (".md", ".rst", ".txt")
RESULTS = 5                     # excerpts returned by one search
EXCERPT_WORDS = 40              # about how many words an excerpt shows around the match
READ_MAX_CHARS = 6000           # one passage read in full is cut here, so it cannot fill the context
SECTION_MAX_CHARS = 4000        # a very long section is cut into pieces of this size for searching

_MD_HEADING = re.compile(r"^#{1,6}\s+(.*\S)\s*$")
_RST_UNDERLINE = re.compile(r"^([=\-~^\"'`#*+])\1{2,}\s*$")
_WORD = re.compile(r"\w+", re.UNICODE)


def sections_of(text, suffix=".md"):
    """A documentation file cut at its headings: a list of (heading, text of that section). Text
    before the first heading is a section with an empty heading. Markdown headings start with
    '#'; in reStructuredText a heading is a line with a line of ===, --- or ~~~ under it."""
    sections, heading, lines = [], "", []

    def close():
        body = "\n".join(lines).strip()
        if body:
            sections.append((heading, body))

    rows = text.splitlines()
    index = 0
    while index < len(rows):
        row = rows[index]
        found = None
        if suffix == ".md":
            match = _MD_HEADING.match(row)
            if match:
                found, step = match.group(1), 1
        elif row.strip() and index + 1 < len(rows) and _RST_UNDERLINE.match(rows[index + 1]) \
                and len(rows[index + 1].strip()) >= len(row.strip()):
            found, step = row.strip(), 2
        if found is not None:
            close()
            heading, lines = found, []
            index += step
            continue
        lines.append(row)
        index += 1
    close()
    return sections


def _pieces(text):
    """A long text in pieces of at most SECTION_MAX_CHARS, cut between paragraphs where it can."""
    if len(text) <= SECTION_MAX_CHARS:
        return [text]
    pieces, current = [], ""
    for paragraph in text.split("\n\n"):
        while len(paragraph) > SECTION_MAX_CHARS:            # one enormous paragraph: cut it as it comes
            pieces.append(paragraph[:SECTION_MAX_CHARS])
            paragraph = paragraph[SECTION_MAX_CHARS:]
        if current and len(current) + len(paragraph) + 2 > SECTION_MAX_CHARS:
            pieces.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces


def documentation_passages(folder):
    """Every section of every documentation file as (place, title, text). The place is the file's
    path inside the folder and the number of the section, like "user_guide.rst#3": it is what a
    search result names, and what `read_passage` takes to read that section in full."""
    passages = []
    folder = Path(folder) if folder is not None else None
    if folder is None or not folder.is_dir():
        return passages
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DOC_SUFFIXES:
            continue
        if any(part.startswith(("_", ".")) for part in path.relative_to(folder).parts):
            continue                                         # _static, _build and hidden folders are not text to read
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            logger.warning("AI Assistant search: %s could not be read: %s", path, error)
            continue
        name = path.relative_to(folder).as_posix()
        number = 0
        for heading, body in sections_of(text, path.suffix.lower()):
            for piece in _pieces(body):
                number += 1
                passages.append((f"{name}#{number}", f"{name}: {heading}" if heading else name, piece))
    return passages


def conversation_passages(folder):
    """Every turn of every saved conversation as (place, title, text). The place is the file's
    name and the turn's number, like "20261118-140211.json#2"."""
    passages = []
    for found in conversations.listing(folder):
        document = conversations.read(found["path"])
        if document is None:
            continue
        for number, turn in enumerate(document["turns"], start=1):
            passages.append((f"{found['path'].name}#{number}",
                             f"Conversation of {found['started']}: {found['title']}",
                             conversations.turn_text(turn)))
    return passages


def _question(query):
    """The words of a question in the form the database's search takes: each word in quotes, any
    of them enough to match, so punctuation in the question can never be read as a command."""
    words = [word for word in _WORD.findall(str(query or "")) if len(word) > 1]
    return " OR ".join(f'"{word}"' for word in words)


def search(query, where=BOTH, documentation=None, conversation_folder=None, saving=True, limit=RESULTS):
    """Look for `query` and return a dict the assistant can read:

        {"results": [{"place", "source", "title", "excerpt"}, ...], "notes": [...]}

    `where` is "documentation", "conversations" or "both". `documentation` and
    `conversation_folder` are the two folders; either may be None when it does not exist on this
    computer. `saving` says whether conversations are being saved. The notes say, in plain
    sentences, what could not be searched and why, so that an empty answer is never mistaken for
    "nothing was ever written about this"."""
    notes, passages = [], []
    if where not in (DOCUMENTATION, CONVERSATIONS, BOTH):
        return {"error": {"code": "validation",
                          "message": "where must be 'documentation', 'conversations' or 'both'"}}
    match = _question(query)
    if not match:
        return {"error": {"code": "validation", "message": "the question needs at least one word"}}
    if where in (DOCUMENTATION, BOTH):
        found = documentation_passages(documentation)
        if not found:
            notes.append("The documentation is not available on this computer, so it was not searched.")
        passages += [(DOCUMENTATION, *passage) for passage in found]
    if where in (CONVERSATIONS, BOTH):
        found = conversation_passages(conversation_folder)
        if not found:
            notes.append("No earlier conversation is saved." if saving else
                         "Conversations are not saved on this microscope, so there are none to search.")
        passages += [(CONVERSATIONS, *passage) for passage in found]
    if not passages:
        return {"results": [], "notes": notes}

    # A new, empty database in memory for every search: a few hundred passages are indexed in a
    # few hundredths of a second, and the answer always reflects the files as they are now.
    database = sqlite3.connect(":memory:")
    try:
        database.execute("CREATE VIRTUAL TABLE passages USING fts5(source UNINDEXED, place UNINDEXED, title, text)")
        database.executemany("INSERT INTO passages VALUES (?, ?, ?, ?)", passages)
        # bm25 ranks the passages; a word in the title counts three times a word in the text.
        rows = database.execute(
            "SELECT source, place, title, snippet(passages, 3, '', '', ' ... ', ?) FROM passages "
            "WHERE passages MATCH ? ORDER BY bm25(passages, 0, 0, 3.0, 1.0) LIMIT ?",
            (EXCERPT_WORDS, match, int(limit))).fetchall()
    except sqlite3.Error as error:
        logger.warning("AI Assistant search failed for %r: %s", query, error)
        return {"error": {"code": "execution", "message": f"the search failed: {error}"}}
    finally:
        database.close()
    results = [{"place": place, "source": source, "title": title, "excerpt": " ".join(excerpt.split())}
               for source, place, title, excerpt in rows]
    if not results:
        notes.append("Nothing matched those words. Try other words for the same thing.")
    else:
        notes.append("read_passage with a place gives that passage in full.")
    return {"results": results, "notes": notes}


def read_passage(place, documentation=None, conversation_folder=None):
    """One passage in full, by the place a search result named. Returns
    {"place", "title", "text"}, the text cut at READ_MAX_CHARS with a note when it was longer, or
    an error the assistant can read when there is no such place."""
    name, _, number = str(place or "").rpartition("#")
    if not name or not number.isdigit():
        return {"error": {"code": "validation", "message": "a place looks like 'file.md#3', as a search result gives it"}}
    wanted = f"{name}#{int(number)}"
    found = conversation_passages(conversation_folder) if name.endswith(conversations.SUFFIX) \
        else documentation_passages(documentation)
    for where, title, text in found:
        if where == wanted:
            result = {"place": where, "title": title, "text": text[:READ_MAX_CHARS]}
            if len(text) > READ_MAX_CHARS:
                result["note"] = f"cut at {READ_MAX_CHARS} of {len(text)} characters"
            return result
    return {"error": {"code": "validation", "message": f"no passage {wanted!r}; search again for the current places"}}
