"""Wire format of the Starter device's ``Servers`` attribute.

Each line describes one controlled server. Field separation is tolerant
(tabs or runs of spaces) and missing trailing fields fall back to defaults,
because Starter releases differ in what they append.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from milonga.core.enums import ServerRunState
from milonga.core.names import ServerName

_SPLIT = re.compile(r"[\t]+|\s{2,}|\s+")

_STATE_ALIASES: dict[str, ServerRunState] = {
    "ON": ServerRunState.RUNNING,
    "RUNNING": ServerRunState.RUNNING,
    "ALARM": ServerRunState.RUNNING,
    "MOVING": ServerRunState.STARTING,
    "STARTING": ServerRunState.STARTING,
    "INIT": ServerRunState.STARTING,
    "FAULT": ServerRunState.NOT_RESPONDING,
    "NOT_RESPONDING": ServerRunState.NOT_RESPONDING,
    "OFF": ServerRunState.STOPPED,
    "STOPPED": ServerRunState.STOPPED,
    "UNKNOWN": ServerRunState.UNKNOWN,
}

_STATE_NAMES: dict[ServerRunState, str] = {
    ServerRunState.RUNNING: "ON",
    ServerRunState.STARTING: "MOVING",
    ServerRunState.NOT_RESPONDING: "FAULT",
    ServerRunState.STOPPED: "OFF",
    ServerRunState.UNKNOWN: "UNKNOWN",
}


@dataclass(frozen=True, slots=True)
class ServerLine:
    """Parsed form of one ``Servers`` line."""

    name: ServerName
    run_state: ServerRunState
    controlled: bool = False
    level: int = 0


def format_server_line(line: ServerLine) -> str:
    state = _STATE_NAMES[line.run_state]
    return f"{line.name}\t{state}\t{int(line.controlled)}\t{line.level}"


def parse_server_line(text: str) -> ServerLine | None:
    """Return ``None`` for a line that does not carry a server name."""
    fields = [field for field in _SPLIT.split(text.strip()) if field]
    if not fields or "/" not in fields[0]:
        return None
    name = ServerName.parse(fields[0])
    state = (
        _STATE_ALIASES.get(fields[1].upper(), ServerRunState.UNKNOWN)
        if len(fields) > 1
        else (ServerRunState.UNKNOWN)
    )
    controlled = _as_bool(fields[2]) if len(fields) > 2 else False
    level = _as_int(fields[3]) if len(fields) > 3 else 0
    return ServerLine(name, state, controlled, level)


def parse_server_lines(lines: Iterable[object] | str | None) -> tuple[ServerLine, ...]:
    """Accept what a backend hands back: a spectrum of strings, or one blob."""
    if lines is None:
        return ()
    items = lines.splitlines() if isinstance(lines, str) else lines
    parsed = (parse_server_line(str(line)) for line in items)
    return tuple(line for line in parsed if line is not None)


def _as_bool(text: str) -> bool:
    return text.strip().lower() in {"1", "true", "yes", "controlled"}


def _as_int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return 0
