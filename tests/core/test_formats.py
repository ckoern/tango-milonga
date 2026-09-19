"""The sample strings here were read from a running Tango database."""

from datetime import datetime

import pytest

from milonga.core.backend.formats import (
    format_logging_target,
    log_level_from_int,
    log_level_to_int,
    parse_db_date,
    parse_logging_target,
    parse_polling_status,
    parse_server_info,
    tango_release,
)
from milonga.core.enums import LogLevel, LogTargetType, PollableKind
from milonga.core.model import LoggingTarget
from milonga.core.names import ServerName

STARTER_POLLING = (
    "Polled command name = State\nPolling period (mS) = 1000\nPolling ring buffer depth = 10\n"
    "Time needed for the last commands (HostState + RunningServers + StoppedServers + Servers"
    " + State) reading (mS) = 0.046\nData not updated since 625 mS\n"
    "Delta between last records (in mS) = 1000, 1000, 1000, 1000",
    "Polled attribute name = HostState\nPolling period (mS) = 1000\n"
    "Polling ring buffer depth = 10\nTime needed for the last attributes (HostState + "
    "RunningServers + StoppedServers + Servers + State) reading (mS) = 0.046\n"
    "Data not updated since 625 mS\nDelta between last records (in mS) = 1000, 1000, 1000, 1000",
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("19th September 2026 at 16:42:41", datetime(2026, 9, 19, 16, 42, 41)),
        ("1st March 2025 at 08:05:00", datetime(2025, 3, 1, 8, 5, 0)),
        ("22nd July 2024 at 23:59:59", datetime(2024, 7, 22, 23, 59, 59)),
        ("19/09/2026 16:09:49", datetime(2026, 9, 19, 16, 9, 49)),
        ("", None),
        ("?", None),
        ("not a date", None),
    ],
)
def test_both_database_date_spellings(text: str, expected: datetime | None) -> None:
    assert parse_db_date(text) == expected


def test_an_unassigned_server_answers_blanks() -> None:
    info = parse_server_info(["TangoTest/test", " ", " ", " "])
    assert info.name == ServerName("TangoTest", "test")
    assert info.host == ""
    assert info.level == 0
    assert not info.controlled


def test_a_controlled_server() -> None:
    info = parse_server_info(["TangoTest/test", "desktop-h2ai4s9", "1", "3"])
    assert info.host == "desktop-h2ai4s9"
    assert info.level == 3
    assert info.controlled
    assert not parse_server_info(["TangoAccessControl/1", "", "0", "0"]).controlled


def test_polling_status_blocks() -> None:
    command, attribute = parse_polling_status(STARTER_POLLING)
    assert command.name == "State" and command.kind is PollableKind.COMMAND
    assert attribute.name == "HostState" and attribute.kind is PollableKind.ATTRIBUTE
    assert attribute.period_ms == 1000
    assert attribute.ring_depth == 10
    assert attribute.last_read_ms == 625.0
    assert attribute.delta_ms == 1000.0
    assert attribute.polled
    assert parse_polling_status(["unrelated text"]) == ()


def test_logging_targets_with_and_without_a_type() -> None:
    assert parse_logging_target("file::/tmp/log") == LoggingTarget(LogTargetType.FILE, "/tmp/log")
    telemetry = parse_logging_target("telemetry_logs_appender")
    assert telemetry.target_type is LogTargetType.OTHER
    assert str(telemetry) == "telemetry_logs_appender"
    assert format_logging_target(telemetry) == "telemetry_logs_appender"
    assert format_logging_target(LoggingTarget(LogTargetType.CONSOLE)) == "console"
    assert parse_logging_target("weird::x").target_type is LogTargetType.OTHER


def test_log_levels_round_trip() -> None:
    for level in LogLevel:
        assert log_level_from_int(log_level_to_int(level)) is level
    assert log_level_from_int(0) is LogLevel.OFF
    assert log_level_from_int(99) is LogLevel.OFF


@pytest.mark.parametrize(("packed", "text"), [(1000, "10.0.0"), (935, "9.3.5"), (0, "")])
def test_tango_release_numbers(packed: int, text: str) -> None:
    assert tango_release(packed) == text
