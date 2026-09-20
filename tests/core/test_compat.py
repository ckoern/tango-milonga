"""The ``StrEnum`` shim must behave like the one Python 3.11 added."""

from milonga.compat import StrEnum
from milonga.core.enums import TangoState
from milonga.ui.navigator import Scope


class Colour(StrEnum):
    RED = "red"
    BLUE = "blue"


def test_a_member_is_its_value_as_text() -> None:
    assert str(Colour.RED) == "red"
    assert f"{Colour.RED}" == "red"
    assert f"{Colour.RED:>5}" == "  red"
    assert "{}".format(Colour.RED) == "red"  # noqa: UP032


def test_a_member_is_the_string_it_holds() -> None:
    red: str = "red"
    assert red == Colour.RED
    assert isinstance(Colour.RED, str)
    assert {"red": 1}[Colour.BLUE.replace("blue", "red")] == 1
    assert sorted(Colour) == ["blue", "red"]


def test_the_enums_the_interface_shows_read_as_their_values() -> None:
    assert f"{TangoState.RUNNING}" == "RUNNING"
    assert f"{Scope.DEVICES}" == "Devices"
