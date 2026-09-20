import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.model import HostSnapshot
from milonga.core.names import DeviceName, ServerName
from milonga.ui.context import AppContext, Target
from milonga.ui.mainwindow import MainWindow
from milonga.ui.models.tree import NodeKind, TreeNode
from milonga.ui.navigator import Scope
from milonga.ui.search import SearchDialog, parse_target
from milonga.ui.theme import Theme, Tokens

DEVICE = Target.device(DeviceName.parse("sys/tg_test/1"))
SERVER = Target.server(ServerName.parse("TangoTest/test"))


@pytest.fixture
async def window(context: AppContext, tokens: Tokens) -> MainWindow:
    main = MainWindow(context, tokens, Theme.LIGHT)
    await main.idle()
    return main


async def test_the_system_overview_opens_at_startup(window: MainWindow) -> None:
    assert window.tabs.count() == 1
    assert window.tabs.tabText(0) == "System"


async def test_opening_a_target_adds_one_tab(window: MainWindow) -> None:
    window.open_target(DEVICE)
    window.open_target(DEVICE)
    await window.idle()
    assert window.tabs.count() == 2
    assert window.tabs.tabText(1) == "sys/tg_test/1"


async def test_two_targets_are_two_tabs(window: MainWindow) -> None:
    window.open_target(DEVICE)
    window.open_target(SERVER)
    await window.idle()
    assert window.tabs.count() == 3
    assert {DEVICE, SERVER} <= set(window.open_panels)


async def test_closing_a_tab_forgets_the_panel(window: MainWindow) -> None:
    window.open_target(DEVICE)
    await window.idle()
    window.tabs.tabCloseRequested.emit(1)
    await window.idle()
    assert window.tabs.count() == 1
    assert DEVICE not in window.open_panels


async def test_navigator_activation_opens_a_panel(window: MainWindow) -> None:
    window.navigator.targetActivated.emit(SERVER)
    await window.idle()
    assert SERVER in window.open_panels


async def test_journal_records_the_session(window: MainWindow, context: AppContext) -> None:
    before = window.journal_model.rowCount()
    context.journal.write("put_device_property sys/tg_test/1 Foo=1")
    assert window.journal_model.rowCount() == before + 1
    assert "put_device_property" in window.journal_model.data(
        window.journal_model.index(before, 2)
    )


async def test_inspector_follows_the_selection(window: MainWindow) -> None:
    node = TreeNode(NodeKind.DEVICE, "phi", DeviceName.parse("id09/motor/phi"), detail="motor")
    window.navigator.nodeSelected.emit(node)
    assert window.inspector.open_button.isEnabled()
    assert window.inspector.open_button.text() == "Open device panel"
    window.navigator.nodeSelected.emit(None)
    assert not window.inspector.open_button.isEnabled()
    assert window.inspector.open_button.text() == "Select something to open"


async def test_the_open_button_names_the_panel_it_opens(window: MainWindow) -> None:
    host = TreeNode(NodeKind.HOST, "id09-srv-02", HostSnapshot("id09-srv-02"))
    window.navigator.nodeSelected.emit(host)
    assert window.inspector.open_button.text() == "Open host panel"
    domain = TreeNode(NodeKind.DOMAIN, "sys", "sys")
    window.navigator.nodeSelected.emit(domain)
    assert not window.inspector.open_button.isEnabled()


async def test_inspector_open_button_opens_the_panel(window: MainWindow) -> None:
    node = TreeNode(NodeKind.DEVICE, "phi", DeviceName.parse("id09/motor/phi"))
    window.navigator.nodeSelected.emit(node)
    window.inspector.open_button.click()
    await window.idle()
    assert window.tabs.count() == 2


async def test_refresh_reloads_the_current_panel(
    window: MainWindow, backend: FakeBackend
) -> None:
    window.open_target(DEVICE)
    await window.idle()
    calls = len(backend.call_log)
    window.refresh_current()
    await window.idle()
    assert len(backend.call_log) > calls


async def test_theme_toggle_asks_for_the_other_theme(window: MainWindow) -> None:
    seen: list[object] = []
    window.themeToggled.connect(seen.append)
    window._toggle_theme()
    assert seen == [Theme.DARK]


async def test_scope_switch_reloads_the_tree(window: MainWindow) -> None:
    window.navigator.set_scope(Scope.HOSTS)
    await window.idle()
    assert window.navigator.model.rowCount() == 2


async def test_search_finds_devices_servers_and_classes(
    context: AppContext, tokens: Tokens
) -> None:
    dialog = SearchDialog(context, tokens)
    targets = await dialog._collect("motor")
    names = {target.name for target in targets}
    assert "id09/motor/phi" in names
    assert any(target.kind.value == "class" for target in targets)


async def test_search_resolves_aliases(context: AppContext, tokens: Tokens) -> None:
    dialog = SearchDialog(context, tokens)
    targets = await dialog._collect("phi")
    assert any(target.name == "id09/motor/phi" for target in targets)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sys/tg_test/1", "sys/tg_test/1"),
        ("TangoTest/test", "TangoTest/test"),
        ("nonsense text", None),
    ],
)
def test_typed_names_resolve_without_the_database(text: str, expected: str | None) -> None:
    target = parse_target(text)
    assert (target.name if target else None) == expected
