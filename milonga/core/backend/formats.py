"""Text formats the Tango database and devices answer in.

Kept free of any ``tango`` import so they can be tested without a control
system. The sample strings in the tests were read from a running database.
"""

import re
from datetime import datetime

from milonga.core.enums import LogLevel, LogTargetType, PollableKind
from milonga.core.model import LoggingTarget, PollingEntry, ServerInfo
from milonga.core.names import ServerName

_ORDINAL = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)\s+(\w+)\s+(\d{4})\s+at\s+(\d{1,2}:\d{2}:\d{2})$")
_DATE_FORMATS = ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d %B %Y %H:%M:%S")

_LOG_LEVELS: tuple[LogLevel, ...] = (
    LogLevel.OFF,
    LogLevel.FATAL,
    LogLevel.ERROR,
    LogLevel.WARNING,
    LogLevel.INFO,
    LogLevel.DEBUG,
)


def parse_db_date(text: str) -> datetime | None:
    """Accept both spellings the database uses; anything else means unknown.

    Device info dates read ``19th September 2026 at 16:42:41`` while property
    history dates read ``19/09/2026 16:09:49``.
    """
    value = text.strip()
    if not value or value == "?":
        return None
    match = _ORDINAL.match(value)
    if match is not None:
        day, month, year, clock = match.groups()
        value = f"{day} {month} {year} {clock}"
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None


def parse_server_info(fields: list[str] | tuple[str, ...]) -> ServerInfo:
    """``DbGetServerInfo`` answers ``[name, host, mode, level]`` as text.

    An unassigned server answers blanks, which the C++ client library refuses
    to parse, so the raw command is read and blanks become defaults here.
    """
    name = ServerName.parse(fields[0])
    host = fields[1].strip() if len(fields) > 1 else ""
    mode = _int_or_zero(fields[2]) if len(fields) > 2 else 0
    level = _int_or_zero(fields[3]) if len(fields) > 3 else 0
    return ServerInfo(name, host, level, controlled=mode == 1)


def _int_or_zero(text: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        return 0


_POLLED = re.compile(r"Polled (attribute|command) name = (\S+)")
_PERIOD = re.compile(r"Polling period \(mS\) = (\d+)")
_DEPTH = re.compile(r"Polling ring buffer depth = (\d+)")
_SINCE = re.compile(r"Data not updated since ([\d.]+) mS")
_DELTA = re.compile(r"Delta between last records \(in mS\) = ([\d.]+)")
_TRIGGERED = re.compile(r"Polling externally triggered", re.IGNORECASE)


def parse_polling_status(entries: list[str] | tuple[str, ...]) -> tuple[PollingEntry, ...]:
    """``polling_status()`` answers one free-text block per polled object."""
    parsed: list[PollingEntry] = []
    for text in entries:
        polled = _POLLED.search(text)
        if polled is None:
            continue
        kind = PollableKind.ATTRIBUTE if polled.group(1) == "attribute" else PollableKind.COMMAND
        parsed.append(
            PollingEntry(
                name=polled.group(2),
                kind=kind,
                period_ms=_first_int(_PERIOD, text),
                polled=True,
                ring_depth=_first_int(_DEPTH, text),
                last_read_ms=_first_float(_SINCE, text),
                delta_ms=_first_float(_DELTA, text),
                externally_triggered=bool(_TRIGGERED.search(text)),
            )
        )
    return tuple(parsed)


def _first_int(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return int(match.group(1)) if match else 0


def _first_float(pattern: re.Pattern[str], text: str) -> float:
    match = pattern.search(text)
    return float(match.group(1)) if match else 0.0


def parse_logging_target(text: str) -> LoggingTarget:
    """``type::name``, or a bare appender name such as the telemetry one."""
    kind, separator, name = text.partition("::")
    if not separator:
        return LoggingTarget(LogTargetType.OTHER, text)
    try:
        return LoggingTarget(LogTargetType(kind.strip().lower()), name.strip())
    except ValueError:
        return LoggingTarget(LogTargetType.OTHER, text)


def format_logging_target(target: LoggingTarget) -> str:
    if target.target_type is LogTargetType.OTHER:
        return target.name
    return f"{target.target_type.value}::{target.name}" if target.name else target.target_type.value


def log_level_from_int(value: int) -> LogLevel:
    return _LOG_LEVELS[value] if 0 <= value < len(_LOG_LEVELS) else LogLevel.OFF


def log_level_to_int(level: LogLevel) -> int:
    return _LOG_LEVELS.index(level)


def tango_release(version: int) -> str:
    """``get_tango_lib_version()`` packs the release as ``major*100 + minor*10 + patch``."""
    if version <= 0:
        return ""
    return f"{version // 100}.{version % 100 // 10}.{version % 10}"
