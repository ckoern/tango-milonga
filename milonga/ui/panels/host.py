"""Host panel: the servers a Starter controls, grouped by startup level.

A host is its startup sequence, so the servers are grouped by level rather
than alphabetically, and bulk actions walk the levels in order the way the
Starter itself does.
"""


from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QHideEvent, QShowEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from milonga.core.commands import SetServerControl
from milonga.core.enums import NOT_CONTROLLED_LEVEL, ServerRunState, StateCategory
from milonga.core.errors import ErrorReport
from milonga.core.model import HostSnapshot, ServerSnapshot
from milonga.core.names import ServerName
from milonga.core.services.diagnostics import Diagnostics
from milonga.ui.context import AppContext, Target
from milonga.ui.dialogs import LevelDialog
from milonga.ui.live_hosts import LiveHosts
from milonga.ui.menus import SEPARATOR, MenuEntry, MenuItems, popup
from milonga.ui.models.delegate import NodeDelegate
from milonga.ui.models.tables import ObjectTableModel
from milonga.ui.models.tree import LazyTreeModel, NodeKind, TreeNode
from milonga.ui.panels.base import InfoForm, Panel
from milonga.ui.panels.columns import detail_columns
from milonga.ui.process import ProcessActions
from milonga.ui.theme import Tokens, host_state_category, mono_font, run_state_category
from milonga.ui.widgets import SectionLabel
from milonga.ui.write import WriteAction


async def _no_children(_node: TreeNode) -> list[TreeNode]:
    return []


class HostPanel(Panel):
    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        target: Target,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(context, tokens, target, parent)
        self.host = target.name
        self.live = LiveHosts(context, self)
        self.live.changed.connect(self._host_changed)
        self.write = WriteAction(context, tokens, self.runner, self)
        self.write.done.connect(self.refresh)
        self.write.failed.connect(self._failed)
        self.process = ProcessActions(context, self.runner, self)
        self.process.done.connect(self.refresh)
        self.process.failed.connect(self._failed)

        self.info = InfoForm(tokens, self)
        self.model = LazyTreeModel(_no_children, self.runner, self)
        self.view = QTreeView(self)
        self.view.setModel(self.model)
        self.view.setHeaderHidden(True)
        self.view.setUniformRowHeights(True)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setItemDelegate(NodeDelegate(tokens, self.view))
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)

        self.log = QPlainTextEdit(self)
        self.log.setReadOnly(True)
        self.log.setFont(mono_font())
        self.log.setPlaceholderText("Select a server and press “Read log”")

        self.details = ObjectTableModel(detail_columns(), self)
        self.details_view = self.make_table(self.details)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.view, "Startup levels")
        self.tabs.addTab(self.details_view, "Processes")
        self.tabs.currentChanged.connect(self._tab_changed)

        layout = self.base_layout()
        layout.addWidget(self.info)
        layout.addLayout(self._controls())
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self.tabs)
        log_box = QWidget(splitter)
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(0, 6, 0, 0)
        log_layout.setSpacing(4)
        log_layout.addWidget(SectionLabel("Starter log", tokens, log_box))
        log_layout.addWidget(self.log, 1)
        splitter.addWidget(log_box)
        splitter.setSizes([460, 220])
        layout.addWidget(splitter, 1)

        self.header.set_header(self.host, "controlled host")
        self._update_buttons()

    # -------------------------------------------------------------------- chrome

    def _controls(self) -> QHBoxLayout:
        writable = not self.context.read_only
        self.start_button = self._button("Start", self._start, writable)
        self.stop_button = self._button("Stop", self._stop, writable)
        self.restart_button = self._button("Restart", self._restart, writable)
        self.kill_button = self._button("Hard kill", self._hard_kill, writable)
        self.log_button = self._button("Read log", self._read_log, True)
        self.level_button = self._button("Level…", self._edit_level, writable)
        self.start_all_button = self._button("Start all levels", self._start_all, writable)
        self.stop_all_button = self._button("Stop all levels", self._stop_all, writable)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for button in (
            self.start_button,
            self.stop_button,
            self.restart_button,
            self.kill_button,
            self.log_button,
            self.level_button,
        ):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.start_all_button)
        row.addWidget(self.stop_all_button)
        return row

    def _button(self, text: str, slot: object, enabled: bool) -> QPushButton:
        button = QPushButton(text, self)
        button.clicked.connect(slot)  # type: ignore[arg-type]
        button.setEnabled(enabled)
        return button

    # ------------------------------------------------------------------ lifecycle

    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        self.refresh()

    def hideEvent(self, a0: QHideEvent | None) -> None:
        super().hideEvent(a0)
        self.runner.run(self.live.release(), label="release host watch")

    async def aclose(self) -> None:
        await self.live.release()

    def refresh(self) -> None:
        self.banner.clear()
        self.runner.run(self.live.watch([self.host]), on_error=self._failed)
        self._load_details()

    def _tab_changed(self, index: int) -> None:
        if index == 1:
            self._load_details()

    def _load_details(self) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            return
        servers = [server.name for server in snapshot.servers]
        self.runner.run(
            Diagnostics(self.context.backend).server_details(servers),
            on_result=self.details.set_rows,
            on_error=self._failed,
        )

    @property
    def snapshot(self) -> HostSnapshot | None:
        return self.live.snapshot(self.host)

    # -------------------------------------------------------------------- display

    def _host_changed(self, _host: str) -> None:
        snapshot = self.snapshot
        if snapshot is None:
            return
        self.header.chip.set_state(
            snapshot.state.value, host_state_category(snapshot.state)
        )
        self.header.set_header(self.host, snapshot.group or "controlled host")
        if snapshot.error is not None:
            self.banner.show_error(snapshot.error, self.host)
        else:
            self.banner.clear()
        self.info.set_fields(
            [
                ("Starter", str(snapshot.starter) if snapshot.starter else "—"),
                ("Servers", str(len(snapshot.servers))),
                ("Running", str(snapshot.running_count)),
                ("Stopped", str(snapshot.stopped_count)),
                ("Startup levels", ", ".join(str(level) for level in snapshot.levels) or "—"),
            ]
        )
        self._rebuild_tree(snapshot)
        self._update_buttons()
        if self.tabs.currentIndex() == 1:
            self._load_details()

    def _rebuild_tree(self, snapshot: HostSnapshot) -> None:
        selected = {str(name) for name in self.selected_servers()}
        expanded = self._expanded_levels()
        roots: list[TreeNode] = []
        for level in (*snapshot.levels, NOT_CONTROLLED_LEVEL):
            servers = [
                server for server in snapshot.servers if server.info.level == level
            ]
            if not servers:
                continue
            children = [self._server_node(server) for server in servers]
            node = TreeNode(
                NodeKind.GROUP,
                f"Level {level}" if level else "Not controlled",
                level,
                detail=f"{len(servers)}",
                expandable=True,
            )
            node.children = children
            for child in children:
                child.parent = node
            roots.append(node)
        self.model.set_roots(roots)
        for row in range(self.model.rowCount()):
            index = self.model.index(row, 0)
            level_node = self.model.node(index)
            keep_open = not expanded or (
                level_node is not None and level_node.payload in expanded
            )
            self.view.setExpanded(index, keep_open)
        self._restore_selection(selected)

    @staticmethod
    def _server_node(server: ServerSnapshot) -> TreeNode:
        detail = server.run_state.value.lower()
        category = run_state_category(server.run_state)
        if not server.info.is_controlled and server.run_state is ServerRunState.STOPPED:
            # nobody asked for an uncontrolled server to run
            category = StateCategory.INACTIVE
        return TreeNode(
            NodeKind.SERVER,
            str(server.name),
            server.name,
            detail=detail,
            category=category,
            tooltip=f"{server.name} · level {server.info.level}",
        )

    def _expanded_levels(self) -> set[int]:
        levels: set[int] = set()
        for row in range(self.model.rowCount()):
            index = self.model.index(row, 0)
            node = self.model.node(index)
            if node is not None and self.view.isExpanded(index):
                levels.add(int(node.payload))
        return levels

    def _restore_selection(self, names: set[str]) -> None:
        selection = self.view.selectionModel()
        if selection is None or not names:
            return
        for row in range(self.model.rowCount()):
            parent = self.model.index(row, 0)
            for child in range(self.model.rowCount(parent)):
                index = self.model.index(child, 0, parent)
                node = self.model.node(index)
                if node is not None and str(node.payload) in names:
                    selection.select(
                        index, selection.SelectionFlag.Select | selection.SelectionFlag.Rows
                    )

    # -------------------------------------------------------------------- actions

    def selected_servers(self) -> tuple[ServerName, ...]:
        selection = self.view.selectionModel()
        if selection is None:
            return ()
        names: list[ServerName] = []
        for index in selection.selectedIndexes():
            node = self.model.node(index)
            if node is not None and node.kind is NodeKind.SERVER:
                names.append(node.payload)
        return tuple(dict.fromkeys(names))

    def _start(self) -> None:
        self.process.start(self.host, self.selected_servers())

    def _stop(self) -> None:
        self.process.stop(self.host, self.selected_servers())

    def _restart(self) -> None:
        self.process.restart(self.host, self.selected_servers())

    def _hard_kill(self) -> None:
        self.process.hard_kill(self.host, self.selected_servers())

    def _start_all(self) -> None:
        if self.snapshot is not None:
            self.process.start_all(self.snapshot)

    def _stop_all(self) -> None:
        if self.snapshot is not None:
            self.process.stop_all(self.snapshot)

    # ----------------------------------------------------------------- right click

    def context_items(self, node: TreeNode | None) -> MenuItems:
        writable = self.process.enabled
        if node is None:
            return [
                MenuEntry("Start all levels", self._start_all, writable),
                MenuEntry("Stop all levels", self._stop_all, writable),
            ]
        if node.kind is NodeKind.GROUP:
            level = int(node.payload)
            if not level:
                return []
            return [
                MenuEntry(
                    f"Start level {level}",
                    lambda: self.process.start_level(self.host, level),
                    writable,
                ),
                MenuEntry(
                    f"Stop level {level}",
                    lambda: self.process.stop_level(self.host, level),
                    writable,
                ),
            ]
        server = node.payload
        return [
            MenuEntry("Start", self._start, writable),
            MenuEntry("Stop", self._stop, writable),
            MenuEntry("Restart", self._restart, writable),
            MenuEntry("Hard kill", self._hard_kill, writable),
            SEPARATOR,
            MenuEntry("Read log", self._read_log),
            MenuEntry("Startup level…", self._edit_level, writable),
            SEPARATOR,
            MenuEntry(
                "Open server panel", lambda: self.context.open_target(Target.server(server))
            ),
        ]

    def _context_menu(self, point: QPoint) -> None:
        index = self.view.indexAt(point)
        popup(self.view, point, self.context_items(self.model.node(index)))

    def _read_log(self) -> None:
        servers = self.selected_servers()
        if not servers:
            return
        server = servers[0]
        self.runner.run(
            self.context.control.read_log(self.host, server),
            on_result=lambda text: self._show_log(server, text),
            on_error=self._failed,
        )

    def _show_log(self, server: ServerName, text: str) -> None:
        self.log.setPlainText(text or f"no log for {server}")

    def _edit_level(self) -> None:
        servers = self.selected_servers()
        snapshot = self.snapshot
        if not servers or snapshot is None:
            return
        current = next(
            (server for server in snapshot.servers if server.name == servers[0]), None
        )
        level = current.info.level if current else 1
        controlled = current.info.controlled if current else True
        dialog = LevelDialog(str(servers[0]), level, controlled, self)
        if not dialog.exec():
            return
        commands = [
            SetServerControl(server, self.host, dialog.level(), dialog.controlled())
            for server in servers
        ]
        self.write.execute(commands)

    def _update_buttons(self) -> None:
        snapshot = self.snapshot
        has_servers = bool(snapshot and snapshot.servers)
        for button in (self.start_all_button, self.stop_all_button):
            button.setEnabled(has_servers and not self.context.read_only)

    def _failed(self, report: ErrorReport) -> None:
        self.banner.show_error(report, self.host)
        self.context.journal.report(report, self.host)
