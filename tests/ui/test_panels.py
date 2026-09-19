import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.names import DeviceName, ServerName
from milonga.ui.context import AppContext, Target
from milonga.ui.panels import (
    ClassPanel,
    DevicePanel,
    HostPanel,
    ObjectPanel,
    Panel,
    ServerPanel,
    create_panel,
)
from milonga.ui.theme import Tokens

DEVICE = DeviceName.parse("sys/tg_test/1")


async def _ready(panel: Panel) -> None:
    await panel.idle()


async def test_device_panel_shows_info_properties_and_interface(
    context: AppContext, tokens: Tokens
) -> None:
    panel = DevicePanel(context, tokens, Target.device(DEVICE))
    await _ready(panel)
    assert panel.header.chip.text() == "ON"
    assert panel.properties.model.rowCount() == 0
    assert panel.values.rowCount() == 9
    assert panel.specs.rowCount() == 9
    assert panel.commands.rowCount() == 3
    assert "Attributes (9)" in panel.tabs.tabText(0)
    assert panel.banner.isVisibleTo(panel) is False


async def test_device_panel_lists_properties(context: AppContext, tokens: Tokens) -> None:
    panel = DevicePanel(context, tokens, Target.device(DeviceName.parse("id09/motor/phi")))
    await _ready(panel)
    names = [row.name for row in panel.properties.model.rows]
    assert "Velocity" in names
    assert panel.values.rowCount() == 4


async def test_device_panel_of_a_stopped_server_stays_readable(
    context: AppContext, tokens: Tokens, backend: FakeBackend
) -> None:
    backend.stop_server("TangoTest/test")
    panel = DevicePanel(context, tokens, Target.device(DEVICE))
    await _ready(panel)
    assert panel.header.chip.text() == "NOT EXPORTED"
    assert panel.values.rowCount() == 0
    assert panel.banner.isVisibleTo(panel) is False


async def test_device_panel_reports_a_missing_device(
    context: AppContext, tokens: Tokens
) -> None:
    panel = DevicePanel(context, tokens, Target.device(DeviceName.parse("no/such/device")))
    await _ready(panel)
    assert panel.banner.isVisibleTo(panel) is True


async def test_server_panel_reads_run_state_from_the_admin_device(
    context: AppContext, tokens: Tokens
) -> None:
    panel = ServerPanel(context, tokens, Target.server(ServerName.parse("IcePAP/id09")))
    await _ready(panel)
    assert panel.devices.rowCount() == 3
    assert panel.header.chip.text() == "RUNNING"


async def test_server_panel_shows_a_stopped_server(
    context: AppContext, tokens: Tokens, backend: FakeBackend
) -> None:
    backend.stop_server("IcePAP/id09")
    panel = ServerPanel(context, tokens, Target.server(ServerName.parse("IcePAP/id09")))
    await _ready(panel)
    assert panel.header.chip.text() == "STOPPED"


async def test_class_panel_shows_class_properties_and_instances(
    context: AppContext, tokens: Tokens
) -> None:
    panel = ClassPanel(context, tokens, Target.device_class("IcePAPMotor"))
    await _ready(panel)
    assert panel.properties.model.rowCount() == 3
    assert panel.devices.rowCount() == 2


async def test_object_panel_shows_free_properties(
    context: AppContext, tokens: Tokens
) -> None:
    panel = ObjectPanel(context, tokens, Target.free_object("Milonga"))
    await _ready(panel)
    assert panel.properties.model.rowCount() == 1


async def test_host_panel_summarises_the_starter(
    context: AppContext, tokens: Tokens
) -> None:
    panel = HostPanel(context, tokens, Target.host("id09-srv-02"))
    panel.refresh()
    await _ready(panel)
    assert panel.header.chip.text() == "MIXED"
    snapshot = panel.snapshot
    assert snapshot is not None and len(snapshot.servers) == 7
    await panel.aclose()


async def test_host_panel_of_an_unreachable_host_shows_the_error(
    context: AppContext, tokens: Tokens
) -> None:
    panel = HostPanel(context, tokens, Target.host("id09-vac-01"))
    panel.refresh()
    await _ready(panel)
    assert panel.header.chip.text() == "UNREACHABLE"
    assert panel.banner.isVisibleTo(panel) is True
    await panel.aclose()


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (Target.device(DEVICE), DevicePanel),
        (Target.device_class("TangoTest"), ClassPanel),
        (Target.free_object("Milonga"), ObjectPanel),
        (Target.host("id09-srv-02"), HostPanel),
    ],
)
async def test_factory_picks_the_panel(
    context: AppContext, tokens: Tokens, target: Target, expected: type
) -> None:
    panel = create_panel(context, tokens, target)
    await _ready(panel)
    assert isinstance(panel, expected)
