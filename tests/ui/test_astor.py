import pytest
from PySide6.QtGui import QHideEvent

from milonga.core.backend.fake import FakeBackend
from milonga.core.enums import HostState, ServerRunState, StateCategory
from milonga.core.names import ServerName
from milonga.ui.context import AppContext, Target
from milonga.ui.dialogs import ConfirmDialog, DiffDialog, LevelDialog
from milonga.ui.live_hosts import LiveHosts
from milonga.ui.models.tree import NodeKind
from milonga.ui.panels.host import HostPanel
from milonga.ui.panels.overview import OverviewPanel
from milonga.ui.theme import Tokens

HOST = "id09-srv-02"
VACUUM = ServerName.parse("Vacuum/id09-front")
TANGOTEST = ServerName.parse("TangoTest/test")


@pytest.fixture
def accept_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ConfirmDialog, "exec", lambda self: 1)
    monkeypatch.setattr(DiffDialog, "exec", lambda self: 1)


@pytest.fixture
def refuse_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ConfirmDialog, "exec", lambda self: 0)
    monkeypatch.setattr(DiffDialog, "exec", lambda self: 0)


@pytest.fixture
async def overview(context: AppContext, tokens: Tokens) -> OverviewPanel:
    panel = OverviewPanel(context, tokens, Target.system())
    panel.refresh()
    await panel.idle()
    return panel


@pytest.fixture
async def host_panel(context: AppContext, tokens: Tokens) -> HostPanel:
    panel = HostPanel(context, tokens, Target.host(HOST))
    panel.refresh()
    await panel.idle()
    return panel


def _select(panel: HostPanel, server: str) -> None:
    selection = panel.view.selectionModel()
    assert selection is not None
    for row in range(panel.model.rowCount()):
        parent = panel.model.index(row, 0)
        for child in range(panel.model.rowCount(parent)):
            index = panel.model.index(child, 0, parent)
            node = panel.model.node(index)
            if node is not None and str(node.payload) == server:
                selection.clearSelection()
                selection.select(
                    index,
                    selection.SelectionFlag.Select | selection.SelectionFlag.Rows,
                )
                return
    raise AssertionError(f"no server {server} in the tree")


# ------------------------------------------------------------------- live hosts


async def test_live_hosts_load_then_follow_events(
    context: AppContext, backend: FakeBackend
) -> None:
    live = LiveHosts(context)
    await live.watch([HOST])
    snapshot = live.snapshot(HOST)
    assert snapshot is not None and snapshot.state is HostState.MIXED

    backend.start_server(VACUUM)
    updated = live.snapshot(HOST)
    assert updated is not None and updated.state is HostState.ALL_RUNNING
    assert context.store.host(HOST) is updated
    await live.release()


async def test_live_hosts_report_an_unreachable_starter(context: AppContext) -> None:
    live = LiveHosts(context)
    await live.watch(["id09-vac-01"])
    snapshot = live.snapshot("id09-vac-01")
    assert snapshot is not None and snapshot.state is HostState.UNREACHABLE
    assert snapshot.error is not None
    await live.release()


async def test_releasing_drops_every_subscription(
    context: AppContext, backend: FakeBackend
) -> None:
    live = LiveHosts(context)
    await live.watch([HOST, "id09-srv-01"])
    assert backend.subscription_count == 2
    await live.release()
    assert backend.subscription_count == 0
    assert live.watched == frozenset()


# --------------------------------------------------------------------- overview


async def test_the_overview_shows_one_card_per_host(overview: OverviewPanel) -> None:
    assert len(overview.grid.cards) == 6
    card = overview.grid.card(HOST)
    assert card is not None
    assert card.chip.text() == "MIXED"
    assert card.left.text() == "6 running · 1 stopped"
    assert card.right.text() == "L1–3"
    assert card.title.text() == HOST


async def test_the_overview_summarises_the_control_system(
    overview: OverviewPanel,
) -> None:
    assert "6 hosts" in overview.summary.text()
    assert "1 Starter unreachable" in overview.summary.text()


async def test_an_unreachable_host_card_says_why(overview: OverviewPanel) -> None:
    card = overview.grid.card("id09-vac-01")
    assert card is not None
    assert card.chip.text() == "UNREACHABLE"
    assert card.left.text() == "Starter does not answer"
    assert "does not answer" in card.left.toolTip()


async def test_a_card_follows_the_control_system(
    overview: OverviewPanel, backend: FakeBackend
) -> None:
    card = overview.grid.card(HOST)
    assert card is not None
    backend.start_server(VACUUM)
    assert card.chip.text() == "ALL_RUNNING"
    backend.stop_server(TANGOTEST)
    assert card.chip.text() == "MIXED"


async def test_double_clicking_a_card_opens_the_host(
    overview: OverviewPanel, context: AppContext
) -> None:
    opened: list[Target] = []
    context.open_target = opened.append
    card = overview.grid.card(HOST)
    assert card is not None
    card.activated.emit(HOST)
    assert opened == [Target.host(HOST)]


async def test_hiding_the_overview_releases_its_watches(
    overview: OverviewPanel, backend: FakeBackend
) -> None:
    # the unreachable host has no event channel, so it is polled, not subscribed
    assert len(overview.live.watched) == 6
    assert backend.subscription_count == 5
    overview.hideEvent(QHideEvent())
    await overview.idle()
    assert backend.subscription_count == 0
    assert overview.live.watched == frozenset()


# -------------------------------------------------------------------- host panel


async def test_servers_are_grouped_by_startup_level(host_panel: HostPanel) -> None:
    levels = [host_panel.model.node(host_panel.model.index(row, 0)) for row in range(3)]
    assert [node.label for node in levels if node] == ["Level 1", "Level 2", "Level 3"]
    level_two = host_panel.model.index(1, 0)
    children = [
        host_panel.model.node(host_panel.model.index(row, 0, level_two))
        for row in range(host_panel.model.rowCount(level_two))
    ]
    assert {node.label for node in children if node} == {
        "IcePAP/id09",
        "LimaCCDs/pilatus",
        "Vacuum/id09-front",
    }
    stopped = next(node for node in children if node and node.label == str(VACUUM))
    assert stopped.category is StateCategory.FAULT
    assert stopped.kind is NodeKind.SERVER


async def test_starting_a_server_needs_no_confirmation(
    host_panel: HostPanel, context: AppContext, backend: FakeBackend
) -> None:
    _select(host_panel, str(VACUUM))
    host_panel._start()
    await host_panel.idle()
    assert backend.servers[VACUUM].run_state is ServerRunState.RUNNING
    assert f"start {VACUUM}" in context.journal.entries[-1].summary


async def test_stopping_a_server_asks_first(
    host_panel: HostPanel, backend: FakeBackend, refuse_dialogs: None
) -> None:
    _select(host_panel, str(TANGOTEST))
    host_panel._stop()
    await host_panel.idle()
    assert backend.servers[TANGOTEST].run_state is ServerRunState.RUNNING


async def test_a_confirmed_stop_goes_through(
    host_panel: HostPanel, backend: FakeBackend, accept_dialogs: None
) -> None:
    _select(host_panel, str(TANGOTEST))
    host_panel._stop()
    await host_panel.idle()
    assert backend.servers[TANGOTEST].run_state is ServerRunState.STOPPED


async def test_hard_kill_writes_it_in_the_log(
    host_panel: HostPanel, backend: FakeBackend, accept_dialogs: None
) -> None:
    _select(host_panel, str(TANGOTEST))
    host_panel._hard_kill()
    await host_panel.idle()
    assert backend.servers[TANGOTEST].run_state is ServerRunState.STOPPED
    assert "killed" in "\n".join(backend.servers[TANGOTEST].log)


async def test_reading_a_log_fills_the_pane(host_panel: HostPanel) -> None:
    _select(host_panel, str(VACUUM))
    host_panel._read_log()
    await host_panel.idle()
    text = host_panel.log.toPlainText()
    assert "device or resource busy" in text
    assert "auto-restart" in text


async def test_a_server_without_a_log_says_so(host_panel: HostPanel) -> None:
    _select(host_panel, str(TANGOTEST))
    host_panel._read_log()
    await host_panel.idle()
    assert "no log" in host_panel.log.toPlainText()


async def test_the_tree_follows_the_control_system(
    host_panel: HostPanel, backend: FakeBackend
) -> None:
    backend.start_server(VACUUM)
    snapshot = host_panel.snapshot
    assert snapshot is not None
    running = {
        server.name for server in snapshot.servers
        if server.run_state is ServerRunState.RUNNING
    }
    assert VACUUM in running


async def test_starting_every_level(
    host_panel: HostPanel, backend: FakeBackend, context: AppContext
) -> None:
    host_panel._start_all()
    await host_panel.idle()
    assert backend.servers[VACUUM].run_state is ServerRunState.RUNNING
    assert "start all levels" in context.journal.entries[-1].summary


async def test_stopping_every_level_asks_first(
    host_panel: HostPanel, backend: FakeBackend, refuse_dialogs: None
) -> None:
    host_panel._stop_all()
    await host_panel.idle()
    assert backend.servers[TANGOTEST].run_state is ServerRunState.RUNNING


async def test_changing_the_startup_level_goes_through_the_write_path(
    host_panel: HostPanel,
    backend: FakeBackend,
    context: AppContext,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LevelDialog, "exec", lambda self: 1)
    monkeypatch.setattr(LevelDialog, "level", lambda self: 4)
    monkeypatch.setattr(LevelDialog, "controlled", lambda self: True)
    _select(host_panel, str(VACUUM))
    host_panel._edit_level()
    await host_panel.idle()
    assert (await backend.get_server_info(VACUUM)).level == 4
    assert context.journal.entries[-1].undoable


async def test_a_read_only_session_cannot_act_on_processes(
    read_only_context: AppContext, tokens: Tokens
) -> None:
    panel = HostPanel(read_only_context, tokens, Target.host(HOST))
    panel.refresh()
    await panel.idle()
    assert not panel.start_button.isEnabled()
    assert not panel.stop_button.isEnabled()
    assert not panel.start_all_button.isEnabled()
    assert panel.log_button.isEnabled()
    await panel.aclose()
