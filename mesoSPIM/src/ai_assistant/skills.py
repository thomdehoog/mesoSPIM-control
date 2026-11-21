"""Skills: short procedures in Markdown that the model loads when a request matches one.

A skill is one file with a head and a body:

    ---
    name: centring
    description: Move the sample to the middle of the image.
    tools: look, move_relative, calibrate
    ---
    When it applies, the steps with the tool for each, when it is done, what to report.

The prompt lists only each skill's name and description; `load_skill` returns the body when the
model decides a request matches.

There are two kinds of skill. **System skills** belong to the microscope: they live in a `skills`
folder next to its config file, everyone who uses the microscope sees them, and the assistant can
read them but never change them. **Personal skills** belong to one person: they live in that
person's own folder, only that person sees them, and the assistant can save and change them once
the operator has confirmed the text. The setup window lists both kinds, and a chat gets exactly
the ones that were ticked when it started.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    tools: tuple[str, ...]
    body: str
    path: Path | None = None
    kind: str = "system"            # "system" or "personal"


SYSTEM, PERSONAL = "system", "personal"
DELETED_FOLDER = "deleted"          # where a removed personal skill is kept, so a mistake can be undone


@dataclass(frozen=True)
class Entry:
    """One line of the setup window's skill lists: a file in one of the two skills folders.

    `key` is the name the assistant knows the skill by. It is the skill's own name, except when a
    personal skill has the same name as a system skill: the personal one is then known as
    "name (personal)", so that both can be listed and loaded without confusion.
    `problem` says, in a sentence, why a file cannot be used (it then has no `skill`).
    `missing_tools` names calls the skill uses that this version of mesoSPIM no longer has: the
    skill is out of date and would fail half way, so the window marks it."""

    key: str
    kind: str
    path: Path
    skill: Skill | None = None
    problem: str | None = None
    missing_tools: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return self.skill is not None

    @property
    def description(self) -> str:
        return self.skill.description if self.skill is not None else (self.problem or "")


def parse(text: str, path: Path | None = None) -> Skill:
    """One skill from its file text; ValueError says what is wrong with it."""
    where = path.name if path is not None else "skill"
    if len(text) > config.SKILL_MAX_CHARS:
        raise ValueError(f"{where}: {len(text)} characters, more than {config.SKILL_MAX_CHARS}")
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---" or "---" not in (line.strip() for line in lines[1:]):
        raise ValueError(f"{where}: starts with a head between two '---' lines")
    end = 1 + [line.strip() for line in lines[1:]].index("---")
    head = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep:
            head[key.strip().lower()] = value.strip()
    name, description = head.get("name", ""), head.get("description", "")
    if not name or not description:
        raise ValueError(f"{where}: the head needs a name and a description")
    tools = tuple(t.strip() for t in head.get("tools", "").split(",") if t.strip())
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        raise ValueError(f"{where}: has no steps after the head")
    return Skill(name, description, tools, body, path)


def load(folder, kind=SYSTEM) -> tuple[dict[str, Skill], list[str]]:
    """Every skill in the folder by name, and a line for each file that could not be used."""
    skills, problems = {}, []
    if folder is None or not Path(folder).is_dir():
        return skills, problems
    for path in sorted(Path(folder).glob("*.md")):
        try:
            skill = parse(path.read_text(encoding="utf-8"), path)
            skill = Skill(skill.name, skill.description, skill.tools, skill.body, path, kind)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            problems.append(f"skill not loaded: {error}")
            logger.warning("skill %s not loaded: %s", path, error)
            continue
        if skill.name in skills:
            problems.append(f"skill not loaded: {path.name} repeats the name {skill.name!r}")
            continue
        skills[skill.name] = skill
    return skills, problems


def folder_of(cfg) -> Path | None:
    """The microscope's skills folder: `skills` next to its config file, when it has one."""
    config_file = getattr(cfg, "__file__", None)
    return Path(config_file).parent / config.SKILLS_FOLDER if config_file else None


def unknown_tools(skills: dict[str, Skill], offered) -> list[str]:
    """A line for each skill that names a tool the assistant does not offer."""
    offered = set(offered)
    return [f"skill {s.name}: no tool {', '.join(missing)}" for s in skills.values()
            if (missing := [t for t in s.tools if t not in offered])]


# --- the two folders together: what the setup window lists and a chat is given ---

def _entries(folder, kind, offered) -> list[Entry]:
    """One entry per skill file in a folder, usable or not, in the order of their file names."""
    entries, seen = [], set()
    if folder is None or not Path(folder).is_dir():
        return entries
    for path in sorted(Path(folder).glob("*.md")):
        try:
            skill = parse(path.read_text(encoding="utf-8"), path)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            entries.append(Entry(path.stem, kind, path, problem=str(error)))
            continue
        if skill.name in seen:
            entries.append(Entry(path.stem, kind, path,
                                 problem=f"{path.name}: another file in this folder is already named {skill.name!r}"))
            continue
        seen.add(skill.name)
        skill = Skill(skill.name, skill.description, skill.tools, skill.body, path, kind)
        missing = tuple(tool for tool in skill.tools if tool not in offered) if offered is not None else ()
        entries.append(Entry(skill.name, kind, path, skill, missing_tools=missing))
    return entries


def library(system_folder, personal_folder, offered=None) -> list[Entry]:
    """Every skill file of both folders, system skills first. `offered` is the list of calls the
    assistant has; with it, each skill that names a call outside that list is marked as out of
    date. Read again each time the setup window is shown, so a file added by hand appears without
    restarting mesoSPIM."""
    offered = set(offered) if offered is not None else None
    system = _entries(system_folder, SYSTEM, offered)
    taken = {entry.key for entry in system if entry.usable}
    personal = []
    for entry in _entries(personal_folder, PERSONAL, offered):
        if entry.usable and entry.key in taken:
            entry = Entry(f"{entry.key} (personal)", entry.kind, entry.path, entry.skill,
                          missing_tools=entry.missing_tools)
        personal.append(entry)
    return system + personal


def chosen(entries, switched_off=()) -> dict[str, Skill]:
    """The skills a chat is given: every usable entry that the operator has not unticked, by the
    name the assistant knows it by."""
    switched_off = set(switched_off)
    return {entry.key: entry.skill for entry in entries if entry.usable and entry.key not in switched_off}


def text_of(name, description, body, tools=()) -> str:
    """A skill file's text from its parts: the head between two '---' lines, then the steps."""
    head = [f"name: {' '.join(str(name).split())}", f"description: {' '.join(str(description).split())}"]
    if tools:
        head.append("tools: " + ", ".join(tools))
    return "---\n" + "\n".join(head) + "\n---\n" + str(body).strip() + "\n"


def _file_name(name) -> str:
    """A file name made from a skill's name: lower case, with a hyphen for anything that is not a
    letter or a digit, so it is valid on every operating system."""
    stem = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    if not stem:
        raise ValueError("the skill's name needs at least one letter or digit")
    return stem[:60] + ".md"


def _write(path, text):
    """Write a skill file in one step: to a second file first, which then takes the place of the
    real one, so that a crash half way never leaves half a skill behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    draft = path.with_suffix(".tmp")
    draft.write_text(text, encoding="utf-8")
    os.replace(draft, path)


def save_personal(folder, name, description, body, tools=()) -> Skill:
    """Save a new personal skill and return it. Raises ValueError, with a sentence the operator
    can act on, when the skill is not valid (no name, no description, no steps, too long) or when
    a personal skill of that name exists already (changing one is `change_personal`)."""
    if folder is None:
        raise ValueError("there is no folder for personal skills")
    text = text_of(name, description, body, tools)
    skill = parse(text)                                   # the same check a file gets when it is read
    path = Path(folder) / _file_name(skill.name)
    existing, _ = load(folder, PERSONAL)
    if skill.name in existing or path.exists():
        raise ValueError(f"a personal skill named {skill.name!r} exists already; change that one, or pick another name")
    _write(path, text)
    return Skill(skill.name, skill.description, skill.tools, skill.body, path, PERSONAL)


def change_personal(skill, description=None, body=None, tools=None) -> Skill:
    """Save a changed version of a personal skill under the same name, and return it. Whatever is
    not given stays as it was. Raises ValueError for a system skill, which the assistant may never
    change, and for a version that is not valid."""
    if skill.kind != PERSONAL or skill.path is None:
        raise ValueError(f"{skill.name!r} is a system skill; only personal skills can be changed here")
    text = text_of(skill.name,
                   skill.description if description is None else description,
                   skill.body if body is None else body,
                   skill.tools if tools is None else tools)
    changed = parse(text)
    _write(skill.path, text)
    return Skill(changed.name, changed.description, changed.tools, changed.body, skill.path, PERSONAL)


def remove_personal(path) -> Path:
    """Take a personal skill out of the list. The file is moved to a `deleted` folder next to it
    and not erased, so a mistaken click can be undone by moving it back. Returns where it went."""
    path = Path(path)
    bin_folder = path.parent / DELETED_FOLDER
    bin_folder.mkdir(parents=True, exist_ok=True)
    target = bin_folder / path.name
    if target.exists():                                   # an earlier skill of the same name is kept too
        target = bin_folder / f"{path.stem}-{time.strftime('%Y%m%d-%H%M%S')}{path.suffix}"
    os.replace(path, target)
    return target
