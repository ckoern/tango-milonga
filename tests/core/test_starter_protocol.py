import pytest

from milonga.core.enums import ServerRunState
from milonga.core.names import ServerName
from milonga.core.services.starter_protocol import (
    ServerLine,
    format_server_line,
    parse_server_line,
    parse_server_lines,
)


@pytest.mark.parametrize(
    ("text", "expected_state", "expected_level"),
    [
        ("TangoTest/test\tON\t1\t3", ServerRunState.RUNNING, 3),
        ("TangoTest/test   OFF   1   2", ServerRunState.STOPPED, 2),
        ("TangoTest/test MOVING 1 1", ServerRunState.STARTING, 1),
        ("TangoTest/test\tFAULT\t0\t0", ServerRunState.NOT_RESPONDING, 0),
    ],
)
def test_parses_separator_variants(
    text: str, expected_state: ServerRunState, expected_level: int
) -> None:
    line = parse_server_line(text)
    assert line is not None
    assert line.name == ServerName("TangoTest", "test")
    assert line.run_state is expected_state
    assert line.level == expected_level


def test_tolerates_missing_trailing_fields() -> None:
    line = parse_server_line("TangoTest/test\tON")
    assert line is not None
    assert line.controlled is False
    assert line.level == 0


def test_skips_lines_without_a_server_name() -> None:
    assert parse_server_line("") is None
    assert parse_server_line("no-slash here") is None
    assert len(parse_server_lines(["TangoTest/test\tON\t1\t1", "garbage"])) == 1


def test_format_round_trips() -> None:
    line = ServerLine(ServerName("TangoTest", "test"), ServerRunState.RUNNING, True, 3)
    assert parse_server_line(format_server_line(line)) == line


def test_accepts_a_single_string_blob() -> None:
    assert len(parse_server_lines("A/1\tON\t1\t1\nB/1\tOFF\t1\t2")) == 2
