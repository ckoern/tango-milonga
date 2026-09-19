"""Wire format of the Starter device's ``Servers`` attribute.

A Tango 10 Starter sends ``name<TAB>state<TAB>controlled<TAB>level<TAB>alive``.
``alive`` is 1 while the process exists. The state alone misleads: a controlled
server that was stopped on purpose is reported ``FAULT`` with ``alive`` 0,
and ``MOVING`` means a start or a stop is under way. Separation is tolerant
and missing trailing fields fall back to defaults.
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
    "MOVING": ServerRunState.CHANGING,
    "INIT": ServerRunState.CHANGING,
    "FAULT": ServerRunState.NOT_RESPONDING,
    "NOT_RESPONDING": ServerRunState.NOT_RESPONDING,
    "OFF": ServerRunState.STOPPED,
    "STOPPED": ServerRunState.STOPPED,
    "UNKNOWN": ServerRunState.UNKNOWN,
}

_STATE_NAMES: dict[ServerRunState, str] = {
    ServerRunState.RUNNING: "ON",
    ServerRunState.CHANGING: "MOVING",
    ServerRunState.NOT_RESPONDING: "FAULT",
    ServerRunState.STOPPED: "FAULT",
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
    alive = int(line.run_state not in (ServerRunState.STOPPED, ServerRunState.UNKNOWN))
    return f"{line.name}\t{state}\t{int(line.controlled)}\t{line.level}\t{alive}"


def parse_server_line(text: str) -> ServerLine | None:
    """Return ``None`` for a line that does not carry a server name."""
    fields = [field for field in _SPLIT.split(text.strip()) if field]
    if not fields or "/" not in fields[0]:
        return None
    name = ServerName.parse(fields[0])
    reported = fields[1].upper() if len(fields) > 1 else ""
    alive = _as_bool(fields[4]) if len(fields) > 4 else None
    controlled = _as_bool(fields[2]) if len(fields) > 2 else False
    level = _as_int(fields[3]) if len(fields) > 3 else 0
    return ServerLine(name, _run_state(reported, alive), controlled, level)


def _run_state(reported: str, alive: bool | None) -> ServerRunState:
    if reported == "MOVING":
        return ServerRunState.CHANGING
    if alive is False:
        return ServerRunState.STOPPED
    return _STATE_ALIASES.get(reported, ServerRunState.UNKNOWN)


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
