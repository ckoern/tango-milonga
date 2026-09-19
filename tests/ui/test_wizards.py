import pytest

from milonga.core.enums import PollableKind
from milonga.core.names import ServerName
from milonga.ui.theme import Tokens
from milonga.ui.wizards import AddDeviceDialog, AddHostDialog, NewServerDialog, PollingDialog

HOSTS = ("id09-srv-01", "id09-srv-02")


def _fill(dialog: NewServerDialog, row: int, name: str, class_name: str) -> None:
    for column, text in ((0, name), (1, class_name)):
        item = dialog.devices.item(row, column)
        assert item is not None
        item.setText(text)


def test_a_new_server_needs_a_name_and_a_device(tokens: Tokens) -> None:
    dialog = NewServerDialog(HOSTS, tokens)
    assert "invalid server" in dialog.problem()

    dialog.executable.setText("Vacuum")
    dialog.instance.setText("id09-back")
    assert dialog.problem() == "a server needs at least one device"

    _fill(dialog, 0, "id09/vac/gauge-2", "")
    assert dialog.problem() == "row 1: the device needs a class"

    _fill(dialog, 0, "not a device name", "VacuumGauge")
    assert "row 1" in dialog.problem()

    _fill(dialog, 0, "id09/vac/gauge-2", "VacuumGauge")
    assert dialog.problem() == ""


def test_a_new_server_collects_its_devices(tokens: Tokens) -> None:
    dialog = NewServerDialog(HOSTS, tokens)
    dialog.executable.setText("Vacuum")
    dialog.instance.setText("id09-back")
    dialog.host.setCurrentText("id09-srv-02")
    dialog.level.setValue(3)
    _fill(dialog, 0, "id09/vac/gauge-2", "VacuumGauge")
    dialog.add_row()
    _fill(dialog, 1, "id09/vac/gauge-3", "VacuumGauge")

    assert dialog.server() == ServerName("Vacuum", "id09-back")
    assert dialog.host_name() == "id09-srv-02"
    assert dialog.startup_level() == 3
    registrations = dialog.registrations()
    assert [str(item.name) for item in registrations] == [
        "id09/vac/gauge-2",
        "id09/vac/gauge-3",
    ]
    assert all(item.server == dialog.server() for item in registrations)


def test_a_server_without_a_host_is_not_controlled(tokens: Tokens) -> None:
    dialog = NewServerDialog(HOSTS, tokens)
    dialog.executable.setText("Vacuum")
    dialog.instance.setText("spare")
    dialog.level.setValue(4)
    _fill(dialog, 0, "id09/vac/gauge-9", "VacuumGauge")
    assert dialog.host_name() == ""
    assert dialog.startup_level() == 0


def test_adding_a_device_validates_its_name(tokens: Tokens) -> None:
    dialog = AddDeviceDialog(ServerName("IcePAP", "id09"), ("IcePAPMotor",), tokens)
    dialog.class_name.setCurrentText("")
    assert dialog.problem() == "the device needs a class"
    dialog.class_name.setCurrentText("IcePAPMotor")
    dialog.name.setText("nonsense")
    assert "three fields" in dialog.problem()
    dialog.name.setText("id09/motor/chi")
    assert dialog.problem() == ""
    registration = dialog.registration()
    assert str(registration.name) == "id09/motor/chi"
    assert registration.server == ServerName("IcePAP", "id09")


def test_a_controlled_host_is_a_starter_server(tokens: Tokens) -> None:
    dialog = AddHostDialog(tokens)
    assert dialog.problem() == "give a host name"
    dialog.host.setText("id09-srv-03")
    assert dialog.problem() == ""
    assert dialog.server() == ServerName("Starter", "id09-srv-03")
    assert str(dialog.registration().name) == "tango/admin/id09-srv-03"
    assert dialog.preview.text() == "tango/admin/id09-srv-03"


@pytest.mark.parametrize(
    ("period", "expected"),
    [(0, 0), (250, 250)],
)
def test_the_polling_dialog_reports_its_choice(
    tokens: Tokens, period: int, expected: int
) -> None:
    candidates = [("position", PollableKind.ATTRIBUTE), ("State", PollableKind.COMMAND)]
    dialog = PollingDialog(candidates, tokens, selected="State", period_ms=period)
    assert dialog.chosen() == ("State", PollableKind.COMMAND)
    assert dialog.period_ms() == expected
    assert dialog.problem() == ""


def test_the_polling_dialog_refuses_an_empty_device(tokens: Tokens) -> None:
    dialog = PollingDialog([], tokens)
    assert dialog.problem() == "nothing to poll"
