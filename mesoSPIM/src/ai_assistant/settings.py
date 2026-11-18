"""What the microscope's config file says about the AI Assistant, and each person's own choices.

There are two kinds of setting, kept in two places.

**The facility's settings** are in the microscope's config file, in one block:

    ai_assistant = {
        'system_prompt':      '',                    # a text file with the assistant's instructions
        'system_skills':      'skills',              # the skills that belong to this microscope
        'personal_skills':    '~/mesoSPIM/skills',   # each person's own skills
        'documentation':      '',                    # the folder with the mesoSPIM documentation
        'save_conversations': False,                 # keep each conversation as a file?
        'vision':             True,                  # may camera images be sent to the provider?
        'address':            '',                    # a model reached at your own address
        'look_bin':           2,                     # how much a camera image is shrunk before it is sent
        'focus_metric':       'laplacian',           # how sharpness is measured: 'laplacian' or 'dct_shannon'
        'axes':               {'x': 'right', 'y': 'up', 'z': 'toward the camera'},
        'request_interval_s': 0,                     # a pause between requests, for a provider with a tight limit
    }

Every line is optional. A line that is left out takes the value shown here. A path that does not
start at the root of the disk is read from the folder the config file is in, and `~` stands for
the home folder of whoever is logged in to the computer. That is how one line, shared by
everyone, gives each person a folder of their own.

**Each person's own choices** (which skills are ticked) are kept in a small file next to their
personal skills, so they are remembered the next time that person starts mesoSPIM.

Maintainer (2026):
    Thom de Hoog
    Center for Microscopy and Image Analysis
    thom.dehoog@zmb.uzh.ch
    thomdehoog@gmail.com
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

CONFIG_KEY = "ai_assistant"                     # the name of the block in the microscope's config file
CHOICES_FILE = "assistant_choices.json"         # each person's ticks, next to their personal skills
CONVERSATIONS_FOLDER = "conversations"          # saved conversations, next to the personal skills
DEFAULT_PERSONAL_SKILLS = "~/mesoSPIM/skills"
# The documentation as it sits in a copy of the mesoSPIM repository: three folders up from here.
DEFAULT_DOCUMENTATION = Path(__file__).resolve().parents[3] / "docs" / "source"
DEFAULT_SYSTEM_PROMPT = Path(__file__).parent / "manual.md"


@dataclass(frozen=True)
class Settings:
    """The facility's settings for the AI Assistant, with every path worked out in full.

    `problems` lists, in plain sentences, anything in the config file that could not be used (a
    value of the wrong kind, a file that is not there). The assistant still starts, with the
    usual value in its place, and the window shows these sentences so that the facility can fix
    the line."""

    system_prompt: Path = DEFAULT_SYSTEM_PROMPT
    system_skills: Path | None = None
    personal_skills: Path = Path(DEFAULT_PERSONAL_SKILLS)
    documentation: Path | None = None
    save_conversations: bool = False
    vision: bool = True
    address: str = ""
    look_bin: int = config.LOOK_BIN
    focus_metric: str = config.FOCUS_METRIC
    axes: dict = field(default_factory=lambda: dict(config.DEFAULT_AXES))
    request_interval_s: float = 0.0
    problems: tuple = ()

    @property
    def personal_folder(self) -> Path:
        """The folder that holds everything personal: the skills folder's parent."""
        return self.personal_skills.parent

    @property
    def conversations(self) -> Path:
        """Where this person's saved conversations are kept."""
        return self.personal_folder / CONVERSATIONS_FOLDER

    @property
    def choices_file(self) -> Path:
        return self.personal_folder / CHOICES_FILE


def _path(text, base):
    """A path from the config file: `~` is the home folder of whoever is logged in, and a path
    that does not start at the root of the disk is read from `base`."""
    path = Path(os.path.expanduser(str(text)))
    if not path.is_absolute() and base is not None:
        path = Path(base) / path
    return path


def read(cfg) -> Settings:
    """The settings from the microscope's config (the module mesoSPIM loaded at start), with the
    usual value for every line that is missing. Never raises: a line that cannot be used is
    named in `problems` and its usual value is taken."""
    block = getattr(cfg, CONFIG_KEY, None)
    problems = []
    if block is None:
        block = {}
    elif not isinstance(block, dict):
        problems.append(f"The config file's {CONFIG_KEY} must be a block in curly brackets; it is ignored.")
        block = {}
    config_file = getattr(cfg, "__file__", None)
    base = Path(config_file).parent if config_file else None

    def value(key, kinds, default):
        given = block.get(key, default)
        if isinstance(given, bool) and bool not in (kinds if isinstance(kinds, tuple) else (kinds,)):
            given = None                           # True is not a number or a path here
        if given is None or not isinstance(given, kinds):
            if key in block:
                problems.append(f"The config file's {CONFIG_KEY}['{key}'] cannot be used; the usual value is taken.")
            return default
        return given

    prompt_text = value("system_prompt", str, "")
    system_prompt = _path(prompt_text, base) if prompt_text else DEFAULT_SYSTEM_PROMPT
    if not system_prompt.is_file():
        problems.append(f"The system prompt file {system_prompt} was not found; the one that comes with "
                        "mesoSPIM is used.")
        system_prompt = DEFAULT_SYSTEM_PROMPT

    skills_text = value("system_skills", str, config.SKILLS_FOLDER)
    system_skills = _path(skills_text, base) if (base is not None or Path(skills_text).is_absolute()) else None

    documentation_text = value("documentation", str, "")
    documentation = _path(documentation_text, base) if documentation_text else DEFAULT_DOCUMENTATION
    if not documentation.is_dir():
        if documentation_text:
            problems.append(f"The documentation folder {documentation} was not found; the assistant cannot "
                            "search the documentation.")
        documentation = None

    focus_metric = value("focus_metric", str, config.FOCUS_METRIC)
    if focus_metric not in ("laplacian", "dct_shannon"):
        problems.append(f"The config file's {CONFIG_KEY}['focus_metric'] must be 'laplacian' or 'dct_shannon'.")
        focus_metric = config.FOCUS_METRIC

    from ..remote_control import config as rc_config
    look_bin = value("look_bin", int, config.LOOK_BIN)
    if look_bin not in rc_config.FRAME_BINS:
        problems.append(f"The config file's {CONFIG_KEY}['look_bin'] must be one of {list(rc_config.FRAME_BINS)}.")
        look_bin = config.LOOK_BIN

    # The two settings that had a line of their own before this block existed are still read.
    axes = dict(config.DEFAULT_AXES)
    given_axes = block.get("axes", getattr(cfg, config.AXES_CONFIG_KEY, None))
    if isinstance(given_axes, dict):
        for axis, choices in config.AXIS_CHOICES.items():
            if given_axes.get(axis) in choices:
                axes[axis] = given_axes[axis]
            elif axis in given_axes:
                problems.append(f"The config file's axes['{axis}'] must be one of {list(choices)}.")
    elif given_axes is not None:
        problems.append("The config file's axes must be a block in curly brackets.")
    interval = block.get("request_interval_s", getattr(cfg, config.REQUEST_INTERVAL_CONFIG_KEY, 0)) or 0
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval < 0:
        problems.append("The config file's request_interval_s must be a number of seconds, 0 or more.")
        interval = 0

    return Settings(
        system_prompt=system_prompt,
        system_skills=system_skills,
        personal_skills=_path(value("personal_skills", str, DEFAULT_PERSONAL_SKILLS), base),
        documentation=documentation,
        save_conversations=value("save_conversations", bool, False),
        vision=value("vision", bool, True),
        address=value("address", str, "").strip(),
        look_bin=look_bin,
        focus_metric=focus_metric,
        axes=axes,
        request_interval_s=float(interval),
        problems=tuple(problems),
    )


# --- each person's own choices ---

def read_choices(settings: Settings) -> dict:
    """This person's remembered choices; empty when there are none yet or the file cannot be read."""
    try:
        choices = json.loads(settings.choices_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return choices if isinstance(choices, dict) else {}


def write_choices(settings: Settings, choices: dict) -> bool:
    """Remember this person's choices for next time. Returns False, after a line in the log, when
    the file could not be written (a full disk, a folder that may not be written to): the choices
    then hold for this session only, and nothing else is affected."""
    try:
        settings.personal_folder.mkdir(parents=True, exist_ok=True)
        # Written to a second file first and then put in place in one step, so that a crash
        # half way never leaves a broken file behind.
        draft = settings.choices_file.with_suffix(".tmp")
        draft.write_text(json.dumps(choices, indent=2), encoding="utf-8")
        os.replace(draft, settings.choices_file)
        return True
    except OSError as error:
        logger.warning("AI Assistant: the choices could not be remembered in %s: %s", settings.choices_file, error)
        return False
