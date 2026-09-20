"""What every right-click menu offers, and that its entries act."""

import pytest
from PyQt6.QtCore import QModelIndex

from milonga.core.backend.fake import FakeBackend
from milonga.core.commands import PropertyTarget, PutProperties
from milonga.core.enums import ServerRunState
from milonga.core.model import PropertyEntry
from milonga.core.names import DeviceName, ServerName
from milonga.ui.context import AppContext, Target
from milonga.ui.dialogs import ConfirmDialog, DiffDialog
from milonga.ui.mainwindow import MainWindow
from milonga.ui.menus import entry, labels
from milonga.ui.models.tree import NodeKind, TreeNode
from milonga.ui.navigator import Navigator, Scope
from milonga.ui.panels.device import DevicePanel
from milonga.ui.panels.host import HostPanel
from milonga.ui.panels.overview import OverviewPanel
from milonga.ui.property_editor import PropertyEditor
from milonga.ui.tasks import TaskRunner
from milonga.ui.theme import Theme, Tokens

HOST = "id09-srv-02"
VACUUM = ServerName.parse("Vacuum/id09-front")
TANGOTEST = ServerName.parse("TangoTest/test")
TEST_DEVICE = DeviceName.parse("sys/tg_test/1")


@pytest.fixture
def accept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ConfirmDialog, "exec", lambda self: 1)
    monkeypatch.setattr(DiffDialog, "exec", lambda self: 1)


# ------------------------------------------------------------------ host panel


@pytest.fixture
async def host_panel(context: AppContext, tokens: Tokens) -> HostPanel:
    panel = HostPanel(context, tokens, Target.host(HOST))
    panel.refresh()
    await panel.idle()
    return panel


def _server_node(panel: HostPanel, name: ServerName) -> tuple[QModelIndex, TreeNode]:
    for row in range(panel.model.rowCount()):
        parent = panel.model.index(row, 0)
        for child in range(panel.model.rowCount(parent)):
            index = panel.model.index(child, 0, parent)
            node = panel.model.node(index)
            if node is not None and node.payload == name:
                return index, node
    raise AssertionError(name)


async def test_a_server_in_the_host_panel_offers_its_process_actions(
    host_panel: HostPanel,
) -> None:
    _index, node = _server_node(host_panel, VACUUM)
    assert labels(host_panel.context_items(node)) == [
        "Start",
        "Stop",
        "Restart",
        "Hard kill",
        "Read log",
        "Startup level…",
        "Open server panel",
    ]


async def test_starting_from_the_menu_starts_the_server(
    host_panel: HostPanel, backend: FakeBackend
) -> None:
    index, node = _server_node(host_panel, VACUUM)
    host_panel.view.setCurrentIndex(index)
    entry(host_panel.context_items(node), "Start").action()
    await host_panel.idle()
    assert backend.servers[VACUUM].run_state is ServerRunState.RUNNING


async def test_a_startup_level_offers_to_start_and_stop_it(
    host_panel: HostPanel, backend: FakeBackend, accept: None
) -> None:
    level_two = host_panel.model.node(host_panel.model.index(1, 0))
    assert level_two is not None and level_two.label == "Level 2"
    items = host_panel.context_items(level_two)
    assert labels(items) == ["Start level 2", "Stop level 2"]
    entry(items, "Stop level 2").action()
    await host_panel.idle()
    assert backend.servers[ServerName.parse("IcePAP/id09")].run_state is ServerRunState.STOPPED
    assert backend.servers[TANGOTEST].run_state is ServerRunState.RUNNING


async def test_opening_a_server_from_the_host_panel(
    host_panel: HostPanel, context: AppContext
) -> None:
    opened: list[Target] = []
    context.open_target = opened.append
    _index, node = _server_node(host_panel, TANGOTEST)
    entry(host_panel.context_items(node), "Open server panel").action()
    assert opened == [Target.server(TANGOTEST)]


async def test_a_read_only_session_disables_process_entries(
    read_only_context: AppContext, tokens: Tokens
) -> None:
    panel = HostPanel(read_only_context, tokens, Target.host(HOST))
    panel.refresh()
    await panel.idle()
    _index, node = _server_node(panel, VACUUM)
    items = panel.context_items(node)
    assert not entry(items, "Start").enabled
    assert entry(items, "Read log").enabled
    await panel.aclose()


# ------------------------------------------------------------------- navigator


@pytest.fixture
async def navigator(context: AppContext, tokens: Tokens) -> Navigator:
    widget = Navigator(context, tokens)
    widget.set_scope(Scope.HOSTS)
    await widget.idle()
    return widget


async def _host_node(navigator: Navigator) -> TreeNode:
    group = navigator.proxy.index(0, 0, QModelIndex())
    navigator.proxy.fetchMore(group)
    await navigator.idle()
    for row in range(navigator.proxy.rowCount(group)):
        index = navigator.proxy.index(row, 0, group)
        node = navigator.node_at(index)
        if node is not None and node.label == HOST:
            navigator.proxy.fetchMore(index)
            await navigator.idle()
            return node
    raise AssertionError(HOST)


async def test_a_server_under_a_host_can_be_started_from_the_navigator(
    navigator: Navigator, backend: FakeBackend
) -> None:
    host = await _host_node(navigator)
    vacuum = next(child for child in host.children or [] if child.payload == VACUUM)
    items = navigator.context_items(vacuum)
    assert labels(items)[:5] == [
        "Open",
        "Open members as tiles",
        "Start",
        "Stop",
        "Restart",
    ]
    entry(items, "Start").action()
    await navigator.idle()
    assert backend.servers[VACUUM].run_state is ServerRunState.RUNNING


async def test_a_host_offers_its_levels_and_open(navigator: Navigator) -> None:
    host = await _host_node(navigator)
    items = labels(navigator.context_items(host))
    assert items[0] == "Open"
    assert {"Start all levels", "Stop all levels", "Add controlled host…"} <= set(items)


async def test_a_server_outside_the_host_tree_has_no_process_entries(
    navigator: Navigator,
) -> None:
    navigator.set_scope(Scope.SERVERS)
    await navigator.idle()
    server = TreeNode(NodeKind.SERVER, "test", TANGOTEST)
    items = labels(navigator.context_items(server))
    assert "Start" not in items
    assert {"Open", "Add device…", "Delete server…", "New server…"} <= set(items)


async def test_open_from_the_menu_emits_the_target(navigator: Navigator) -> None:
    opened: list[Target] = []
    navigator.targetActivated.connect(opened.append)
    device = TreeNode(NodeKind.DEVICE, "1", TEST_DEVICE)
    entry(navigator.context_items(device), "Open").action()
    assert opened == [Target.device(TEST_DEVICE)]


# ---------------------------------------------------------------- device panel


@pytest.fixture
async def device_panel(context: AppContext, tokens: Tokens) -> DevicePanel:
    panel = DevicePanel(context, tokens, Target.device(TEST_DEVICE))
    await panel.idle()
    return panel


def _spec(panel: DevicePanel, name: str) -> object:
    return next(spec for spec in panel.values.specs if spec.name == name)


async def test_a_writable_attribute_offers_writing(device_panel: DevicePanel) -> None:
    items = labels(device_panel.attribute_items(_spec(device_panel, "double_scalar")))  # type: ignore[arg-type]
    assert items == ["Write…", "Configure…", "Poll…", "Copy name"]
    read_only = labels(device_panel.attribute_items(_spec(device_panel, "throughput")))  # type: ignore[arg-type]
    assert "Write…" not in read_only


async def test_a_command_without_an_argument_runs_from_the_menu(
    device_panel: DevicePanel, context: AppContext
) -> None:
    void = next(row for row in device_panel.commands.rows if row.name == "DevVoid")
    items = device_panel.command_items(void)
    assert labels(items)[0] == "Execute"
    entry(items, "Execute").action()
    await device_panel.idle()
    assert "command sys/tg_test/1/DevVoid" in context.journal.entries[-1].summary


async def test_a_command_with_an_argument_asks_for_it(device_panel: DevicePanel) -> None:
    echo = next(row for row in device_panel.commands.rows if row.name == "DevDouble")
    items = device_panel.command_items(echo)
    assert labels(items)[0] == "Execute…"
    entry(items, "Execute…").action()
    assert device_panel.command_bar.name.text() == "DevDouble"


async def test_polling_can_be_stopped_from_the_menu(
    device_panel: DevicePanel, backend: FakeBackend, accept: None
) -> None:
    from milonga.core.enums import PollableKind

    await backend.set_polling(TEST_DEVICE, "double_scalar", PollableKind.ATTRIBUTE, 500)
    device_panel.refresh()
    await device_panel.idle()
    polled = device_panel.polling.rows[0]
    entry(device_panel.polling_items(polled), "Stop polling").action()
    await device_panel.idle()
    assert await backend.get_polling(TEST_DEVICE) == ()


# ------------------------------------------------------------- property editor


async def test_the_property_menu_offers_undoing_a_pending_edit(
    context: AppContext, tokens: Tokens
) -> None:
    editor = PropertyEditor(
        context, tokens, PropertyTarget.device("id09/motor/phi"), TaskRunner(None, context.journal)
    )
    editor.refresh()
    await editor._runner.idle()
    position = editor.model.row_named("Velocity")
    row = editor.model.rows[position]
    assert "Undo this edit" not in labels(editor.context_items(row))
    editor.model.set_values(position, ("9",))
    items = editor.context_items(editor.model.rows[position])
    assert labels(items)[:3] == ["Edit values…", "Rename…", "Delete"]
    entry(items, "Undo this edit").action()
    assert editor.model.rows[position].values == ("2.5",)
    assert labels(editor.context_items(None)) == ["Add…"]


# -------------------------------------------------------------------- overview


async def test_a_host_card_offers_open_and_levels(
    context: AppContext, tokens: Tokens
) -> None:
    overview = OverviewPanel(context, tokens, Target.system())
    overview.refresh()
    await overview.idle()
    items = overview.card_items(HOST)
    assert labels(items) == ["Open host panel", "Start all levels", "Stop all levels"]
    unreachable = overview.card_items("id09-vac-01")
    assert not entry(unreachable, "Start all levels").enabled
    await overview.aclose()


# --------------------------------------------------------------------- journal


async def test_a_journal_entry_offers_undo_when_it_can(
    context: AppContext, tokens: Tokens, backend: FakeBackend
) -> None:
    window = MainWindow(context, tokens, Theme.LIGHT)
    await window.idle()
    command = PutProperties(
        PropertyTarget.device("id09/motor/phi"), (PropertyEntry("Velocity", ("4",)),)
    )
    await context.commands.run([command])
    context.journal.write(command.summary, "", command)
    written = context.journal.entries[-1]
    assert labels(window.journal_items(written)) == ["Undo", "Copy"]
    assert labels(window.journal_items(context.journal.entries[0])) == ["Copy"]
    entry(window.journal_items(written), "Undo").action()
    await window.idle()
    (velocity,) = await backend.get_device_properties(
        DeviceName.parse("id09/motor/phi"), ["Velocity"]
    )
    assert velocity.values == ("2.5",)
