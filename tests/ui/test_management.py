import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.enums import ServerRunState
from milonga.core.names import DeviceName, ServerName, starter_device
from milonga.ui.context import AppContext, Target
from milonga.ui.dialogs import ConfirmDialog, DiffDialog, NameDialog
from milonga.ui.navigator import Navigator, Scope
from milonga.ui.panels.device import DevicePanel
from milonga.ui.panels.host import HostPanel
from milonga.ui.theme import Tokens
from milonga.ui.wizards import AddDeviceDialog, AddHostDialog, NewServerDialog, PollingDialog

MOTOR = DeviceName.parse("id09/motor/phi")
ICEPAP = ServerName.parse("IcePAP/id09")
TEST_DEVICE = DeviceName.parse("sys/tg_test/1")


@pytest.fixture
def accept_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DiffDialog, "exec", lambda self: 1)
    monkeypatch.setattr(ConfirmDialog, "exec", lambda self: 1)


@pytest.fixture
async def navigator(context: AppContext, tokens: Tokens) -> Navigator:
    widget = Navigator(context, tokens)
    widget.set_scope(Scope.DEVICES)
    await widget.idle()
    return widget


# ------------------------------------------------------------------ creating


async def test_creating_a_server_from_the_navigator(
    navigator: Navigator,
    backend: FakeBackend,
    context: AppContext,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = ServerName("Vacuum", "id09-back")
    device = DeviceName.parse("id09/vac/gauge-2")

    def fill(self: NewServerDialog) -> int:
        self.executable.setText(server.exec_name)
        self.instance.setText(server.instance)
        self.host.setCurrentText("id09-srv-02")
        self.level.setValue(3)
        item = self.devices.item(0, 0)
        assert item is not None
        item.setText(str(device))
        class_item = self.devices.item(0, 1)
        assert class_item is not None
        class_item.setText("VacuumGauge")
        return 1

    monkeypatch.setattr(NewServerDialog, "exec", fill)
    navigator.new_server()
    await navigator.idle()

    assert server in await backend.get_server_list()
    assert device in await backend.get_device_list_for_server(server)
    assert (await backend.get_server_info(server)).level == 3
    created, started = context.journal.entries[-2:]
    assert created.undoable and "create server" in created.summary
    assert started.summary == f"start {server} on id09-srv-02"


async def test_a_new_server_joins_the_starter_of_its_host(
    navigator: Navigator,
    backend: FakeBackend,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = ServerName("Vacuum", "id09-side")

    def fill(self: NewServerDialog) -> int:
        self.executable.setText(server.exec_name)
        self.instance.setText(server.instance)
        self.host.setCurrentText("id09-srv-02")
        self.level.setValue(2)
        for column, text in ((0, "id09/vac/gauge-7"), (1, "VacuumGauge")):
            item = self.devices.item(0, column)
            assert item is not None
            item.setText(text)
        return 1

    monkeypatch.setattr(NewServerDialog, "exec", fill)
    navigator.new_server()
    await navigator.idle()

    snapshot = await navigator._context.control.host_snapshot("id09-srv-02")
    (entry,) = [item for item in snapshot.servers if item.name == server]
    assert entry.info.controlled and entry.info.level == 2
    assert entry.run_state is ServerRunState.RUNNING


async def test_starting_can_be_left_for_later(
    navigator: Navigator,
    backend: FakeBackend,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = ServerName("Vacuum", "id09-later")

    def fill(self: NewServerDialog) -> int:
        self.executable.setText(server.exec_name)
        self.instance.setText(server.instance)
        self.host.setCurrentText("id09-srv-02")
        self.start_now.setChecked(False)
        for column, text in ((0, "id09/vac/gauge-8"), (1, "VacuumGauge")):
            item = self.devices.item(0, column)
            assert item is not None
            item.setText(text)
        return 1

    monkeypatch.setattr(NewServerDialog, "exec", fill)
    navigator.new_server()
    await navigator.idle()
    assert not await navigator._context.control.is_running(server)


async def test_a_device_added_to_a_running_server_is_created_by_a_reload(
    navigator: Navigator,
    backend: FakeBackend,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offered: list[bool] = []

    def fill(self: AddDeviceDialog) -> int:
        offered.append(self.reload_after())
        self.name.setText("id09/motor/omega")
        self.class_name.setCurrentText("IcePAPMotor")
        return 1

    monkeypatch.setattr(AddDeviceDialog, "exec", fill)
    navigator._add_device(ICEPAP)
    await navigator.idle()
    assert offered == [True]
    info = await backend.get_device_info(DeviceName.parse("id09/motor/omega"))
    assert info.exported
    assert context_summary(navigator)[-1] == f"reload {ICEPAP}"


def context_summary(navigator: Navigator) -> list[str]:
    return [entry.summary for entry in navigator._context.journal.entries]


async def test_adding_a_controlled_host(
    navigator: Navigator,
    backend: FakeBackend,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fill(self: AddHostDialog) -> int:
        self.host.setText("id09-srv-09")
        return 1

    monkeypatch.setattr(AddHostDialog, "exec", fill)
    navigator.add_host()
    await navigator.idle()

    assert starter_device("id09-srv-09") in await backend.get_device_list()
    assert ServerName("Starter", "id09-srv-09") in await backend.get_server_list()


async def test_adding_a_device_to_a_server(
    navigator: Navigator,
    backend: FakeBackend,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fill(self: AddDeviceDialog) -> int:
        self.name.setText("id09/motor/chi")
        self.class_name.setCurrentText("IcePAPMotor")
        return 1

    monkeypatch.setattr(AddDeviceDialog, "exec", fill)
    navigator._add_device(ICEPAP)
    await navigator.idle()
    assert DeviceName.parse("id09/motor/chi") in await backend.get_device_list_for_server(ICEPAP)


# ------------------------------------------------------------------ removing


async def test_deleting_a_device_asks_for_its_name(
    navigator: Navigator, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str | None] = []

    class Recorder:
        def __init__(
            self,
            title: str,
            message: str,
            *,
            confirm_word: str | None = None,
            parent: object = None,
        ) -> None:
            asked.append(confirm_word)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr("milonga.ui.write.ConfirmDialog", Recorder)
    navigator._delete_device(MOTOR)
    await navigator.idle()
    assert asked == ["id09/motor/phi"]
    assert MOTOR in await backend.get_device_list()


async def test_a_confirmed_delete_removes_the_device(
    navigator: Navigator, backend: FakeBackend, context: AppContext, accept_dialogs: None
) -> None:
    navigator._delete_device(MOTOR)
    await navigator.idle()
    assert MOTOR not in await backend.get_device_list()
    assert context.journal.entries[-1].undoable


async def test_deleting_a_server_removes_its_devices(
    navigator: Navigator, backend: FakeBackend, accept_dialogs: None
) -> None:
    navigator._delete_server(ICEPAP)
    await navigator.idle()
    assert ICEPAP not in await backend.get_server_list()
    assert MOTOR not in await backend.get_device_list()


async def test_renaming_and_aliasing_a_device(
    navigator: Navigator,
    backend: FakeBackend,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(NameDialog, "exec", lambda self: 1)
    monkeypatch.setattr(NameDialog, "name", lambda self: "id09/motor/phi2")
    navigator._rename_device(MOTOR)
    await navigator.idle()
    assert DeviceName.parse("id09/motor/phi2") in await backend.get_device_list()

    monkeypatch.setattr(NameDialog, "name", lambda self: "chi")
    navigator._set_alias(DeviceName.parse("id09/motor/phi2"))
    await navigator.idle()
    assert await backend.get_alias_from_device(DeviceName.parse("id09/motor/phi2")) == "chi"


# ------------------------------------------------------------------- polling


@pytest.fixture
async def device_panel(context: AppContext, tokens: Tokens) -> DevicePanel:
    panel = DevicePanel(context, tokens, Target.device(TEST_DEVICE))
    await panel.idle()
    return panel


async def test_polling_starts_from_the_dialog(
    device_panel: DevicePanel,
    backend: FakeBackend,
    context: AppContext,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(PollingDialog, "exec", lambda self: 1)
    monkeypatch.setattr(
        PollingDialog, "chosen", lambda self: ("double_scalar", self._candidates[0][1])
    )
    monkeypatch.setattr(PollingDialog, "period_ms", lambda self: 250)

    device_panel._edit_polling()
    await device_panel.idle()

    entries = await backend.get_polling(TEST_DEVICE)
    assert entries and entries[0].period_ms == 250
    assert device_panel.polling.rowCount() == 1
    assert "poll double_scalar every 250 ms" in context.journal.entries[-1].summary


async def test_polling_stops_from_the_table(
    device_panel: DevicePanel,
    backend: FakeBackend,
    accept_dialogs: None,
) -> None:
    from milonga.core.enums import PollableKind

    await backend.set_polling(TEST_DEVICE, "double_scalar", PollableKind.ATTRIBUTE, 500)
    device_panel.refresh()
    await device_panel.idle()
    assert device_panel.polling.rowCount() == 1

    device_panel.polling_view.setCurrentIndex(device_panel.polling.index(0, 0))
    device_panel._stop_polling()
    await device_panel.idle()
    assert await backend.get_polling(TEST_DEVICE) == ()


async def test_a_read_only_session_cannot_change_polling(
    read_only_context: AppContext, tokens: Tokens
) -> None:
    panel = DevicePanel(read_only_context, tokens, Target.device(TEST_DEVICE))
    await panel.idle()
    assert not panel.poll_button.isEnabled()
    assert not panel.stop_poll_button.isEnabled()
    await panel.aclose()


# ------------------------------------------------------------------ processes


async def test_the_host_panel_reports_pid_uptime_and_version(
    context: AppContext, tokens: Tokens
) -> None:
    panel = HostPanel(context, tokens, Target.host("id09-srv-02"))
    panel.refresh()
    await panel.idle()
    panel.tabs.setCurrentIndex(1)
    await panel.idle()

    assert panel.details.rowCount() == 7
    rows = [panel.details.row_at(panel.details.index(row, 0)) for row in range(7)]
    running = [row for row in rows if row is not None and row.running]
    assert len(running) == 6
    assert all(row.pid for row in running)
    assert all(row.version is not None for row in running)
    stopped = next(row for row in rows if row is not None and not row.running)
    assert str(stopped.name) == "Vacuum/id09-front"
    await panel.aclose()
