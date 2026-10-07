"""Load and validate trace.toml, the single user configuration file.

Every key is optional. Unknown keys raise ConfigError naming the key, so a typo
never silently falls back to a default.
"""

import os
import re
import tomllib
from dataclasses import dataclass, field, replace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PRESETS = ("default", "tiger-method")
LANGUAGES = ("zh", "en")
OUTREACH = ("note", "note+email")
TASKS = ("calibrate", "feedback", "daily", "g", "audit")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

DEFAULT_TASKS = {
    "calibrate": "09:00",
    "feedback": ["12:30", "20:30"],
    "daily": "19:30",
    "g": "20:30",
    "audit": "Sun 22:15",
}

_TOP_KEYS = ("timezone", "data_dir", "preset", "language", "outreach", "git_remote", "tasks", "credentials")
_CREDENTIAL_KEYS = ("email", "notion")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(ValueError):
    """Raised when trace.toml is unreadable or invalid."""


@dataclass(frozen=True)
class Config:
    timezone: str = "America/Chicago"
    data_dir: str = "~/existence-trace-data"
    preset: str = "default"
    language: str = "zh"
    outreach: str = "note"
    # Git URL that checkpoints push to. None (default) keeps history local only.
    git_remote: str | None = None
    # Task name -> list of times. Daily tasks use "HH:MM"; audit uses "Ddd HH:MM".
    tasks: dict = field(default_factory=lambda: {k: _as_list(v) for k, v in DEFAULT_TASKS.items()})
    # Credential name -> file path or None. Secrets live in those files, never here.
    credentials: dict = field(default_factory=lambda: {k: None for k in _CREDENTIAL_KEYS})
    source: str = ""

    @property
    def email_enabled(self):
        return self.outreach == "note+email"


def _as_list(value):
    return list(value) if isinstance(value, list) else [value]


def _check_unknown(table, allowed, where):
    for key in table:
        if key not in allowed:
            raise ConfigError(
                f"unknown key {where}{key!r} in trace.toml; allowed: {', '.join(allowed)}"
            )


def _check_choice(name, value, choices):
    if value not in choices:
        raise ConfigError(f"{name} must be one of {', '.join(choices)}; got {value!r}")


def _check_str(name, value):
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a non-empty string; got {value!r}")


def _check_time(task, value):
    if not isinstance(value, str):
        raise ConfigError(f"tasks.{task} time must be a string; got {value!r}")
    if task == "audit":
        parts = value.split()
        if len(parts) != 2 or parts[0] not in WEEKDAYS or not _HHMM.match(parts[1]):
            raise ConfigError(f"tasks.audit must look like 'Sun 22:15'; got {value!r}")
    elif not _HHMM.match(value):
        raise ConfigError(f"tasks.{task} must be 'HH:MM' (24h); got {value!r}")


def _resolve_path(value, base_dir):
    path = os.path.expanduser(value)
    if not os.path.isabs(path):
        path = os.path.join(base_dir, path)
    return os.path.normpath(path)


def from_dict(raw, base_dir="."):
    """Build a validated Config from a parsed TOML mapping.

    Relative paths resolve against base_dir (the directory of trace.toml).
    """
    _check_unknown(raw, _TOP_KEYS, "")
    defaults = Config()
    values = {}

    for name in ("timezone", "data_dir", "preset", "language", "outreach"):
        value = raw.get(name, getattr(defaults, name))
        _check_str(name, value)
        values[name] = value

    _check_choice("preset", values["preset"], PRESETS)
    _check_choice("language", values["language"], LANGUAGES)
    _check_choice("outreach", values["outreach"], OUTREACH)
    try:
        ZoneInfo(values["timezone"])
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"timezone {values['timezone']!r} is not a known IANA zone") from None
    values["data_dir"] = _resolve_path(values["data_dir"], base_dir)

    git_remote = raw.get("git_remote")
    if git_remote is not None:
        _check_str("git_remote", git_remote)
    values["git_remote"] = git_remote

    tasks_raw = raw.get("tasks", {})
    if not isinstance(tasks_raw, dict):
        raise ConfigError("[tasks] must be a table")
    _check_unknown(tasks_raw, TASKS, "tasks.")
    tasks = {}
    for task in TASKS:
        times = _as_list(tasks_raw.get(task, DEFAULT_TASKS[task]))
        if not times:
            raise ConfigError(f"tasks.{task} needs at least one time")
        for t in times:
            _check_time(task, t)
        tasks[task] = times
    values["tasks"] = tasks

    creds_raw = raw.get("credentials", {})
    if not isinstance(creds_raw, dict):
        raise ConfigError("[credentials] must be a table")
    _check_unknown(creds_raw, _CREDENTIAL_KEYS, "credentials.")
    creds = {}
    for name in _CREDENTIAL_KEYS:
        value = creds_raw.get(name)
        if value is not None:
            _check_str(f"credentials.{name}", value)
            value = _resolve_path(value, base_dir)
        creds[name] = value
    values["credentials"] = creds

    if values["outreach"] == "note+email" and creds["email"] is None:
        raise ConfigError("outreach = 'note+email' requires credentials.email")

    return Config(**values)


def load(path):
    """Read trace.toml at path and return a validated Config."""
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from None
    base_dir = os.path.dirname(os.path.abspath(path))
    return replace(from_dict(raw, base_dir), source=os.path.abspath(path))
