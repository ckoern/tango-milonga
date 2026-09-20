"""The navigator: Jive's five trees and Astor's host tree as one widget."""

from collections.abc import Sequence

from PyQt6.QtCore import QModelIndex, QPoint, QSortFilterProxyModel, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QLineEdit,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from milonga.compat import StrEnum
from milonga.core.commands import (
    CreateDevice,
    CreateServer,
    DeleteDevice,
    DeleteServer,
    RenameDevice,
    RenameServer,
    SetDeviceAlias,
)
from milonga.core.enums import StateCategory
from milonga.core.model import HostSnapshot, ServerSnapshot
from milonga.core.names import DeviceName, ServerName
from milonga.core.tasks import gather_limited
from milonga.ui.context import AppContext, Target
from milonga.ui.dialogs import NameDialog
from milonga.ui.menus import SEPARATOR, MenuEntry, MenuItems, popup
from milonga.ui.models.delegate import NodeDelegate
from milonga.ui.models.tree import LazyTreeModel, NodeKind, TreeNode
from milonga.ui.process import ProcessActions
from milonga.ui.tasks import TaskRunner
from milonga.ui.theme import Tokens, host_state_category, run_state_category
from milonga.ui.wizards import AddDeviceDialog, AddHostDialog, NewServerDialog
from milonga.ui.write import WriteAction


class Scope(StrEnum):
    HOSTS = "Hosts"
    SERVERS = "Servers"
    DEVICES = "Devices"
    CLASSES = "Classes"
    ALIASES = "Aliases"
    PROPERTIES = "Free properties"


TAB_LABELS: dict[Scope, str] = {
    Scope.HOSTS: "Hosts",
    Scope.SERVERS: "Servers",
    Scope.DEVICES: "Devices",
    Scope.CLASSES: "Classes",
    Scope.ALIASES: "Aliases",
    Scope.PROPERTIES: "Objects",
}


class ScopeLoader:
    """Builds tree nodes for a scope, one level at a time."""

    def __init__(self, context: AppContext, scope: Scope = Scope.DEVICES) -> None:
        self._context = context
        self.scope = scope

    async def roots(self) -> list[TreeNode]:
        backend = self._context.backend
        match self.scope:
            case Scope.HOSTS:
                hosts = await self._context.control.controlled_hosts()
                arranged = self._context.groups.arrange(hosts)
                return [
                    TreeNode(
                        NodeKind.GROUP, group, members, detail=str(len(members)), expandable=True
                    )
                    for group, members in arranged.items()
                ]
            case Scope.SERVERS:
                servers = await backend.get_server_list()
                executables: dict[str, list[ServerName]] = {}
                for server in servers:
                    executables.setdefault(server.exec_name, []).append(server)
                return [
                    TreeNode(
                        NodeKind.SERVER_EXEC,
                        name,
                        instances,
                        detail=str(len(instances)),
                        expandable=True,
                    )
                    for name, instances in sorted(executables.items())
                ]
            case Scope.DEVICES:
                return [
                    TreeNode(NodeKind.DOMAIN, domain, domain, expandable=True)
                    for domain in await backend.get_device_domain_list()
                ]
            case Scope.CLASSES:
                return [
                    TreeNode(NodeKind.CLASS, name, name, expandable=True)
                    for name in await backend.get_class_list()
                ]
            case Scope.ALIASES:
                return [
                    TreeNode(NodeKind.ALIAS, alias, alias)
                    for alias in await backend.get_device_alias_list()
                ]
            case Scope.PROPERTIES:
                return [
                    TreeNode(NodeKind.OBJECT, obj, obj, expandable=True)
                    for obj in await backend.get_object_list()
                ]

    async def children(self, node: TreeNode) -> list[TreeNode]:
        backend = self._context.backend
        match node.kind:
            case NodeKind.GROUP:
                snapshots = await self._context.control.host_snapshots(
                    node.payload, groups=dict.fromkeys(node.payload, node.label)
                )
                return [self._host_node(snapshot) for snapshot in snapshots]
            case NodeKind.HOST:
                snapshot = node.payload
                if isinstance(snapshot, HostSnapshot):
                    return [self._server_node(server) for server in snapshot.servers]
                return []
            case NodeKind.SERVER_EXEC:
                infos = await gather_limited(node.payload, backend.get_server_info)
                return [
                    TreeNode(
                        NodeKind.SERVER,
                        info.name.instance,
                        info.name,
                        detail=self._server_detail(info.host, info.level),
                        tooltip=str(info.name),
                        expandable=True,
                    )
                    for info in infos
                ]
            case NodeKind.SERVER:
                devices = await backend.get_device_list_for_server(node.payload)
                return [self._device_node(device, full=True) for device in devices]
            case NodeKind.DOMAIN:
                families = await backend.get_device_family_list(node.payload)
                return [
                    TreeNode(NodeKind.FAMILY, family, (node.payload, family), expandable=True)
                    for family in families
                ]
            case NodeKind.FAMILY:
                domain, family = node.payload
                members = await backend.get_device_member_list(domain, family)
                return [
                    self._device_node(DeviceName(domain, family, member), label=member)
                    for member in members
                ]
            case NodeKind.CLASS:
                devices = await backend.get_device_list_for_class(node.payload)
                return [self._device_node(device, full=True) for device in devices]
            case NodeKind.OBJECT:
                entries = await backend.get_properties(node.payload)
                return [
                    TreeNode(
                        NodeKind.PROPERTY,
                        entry.name,
                        (node.payload, entry.name),
                        detail=_preview(entry.values),
                        tooltip="\n".join(entry.values),
                    )
                    for entry in entries
                ]
            case _:
                return []

    def _host_node(self, snapshot: HostSnapshot) -> TreeNode:
        running = snapshot.running_count
        total = len(snapshot.servers)
        detail = "unreachable" if snapshot.error else f"{running}/{total}"
        return TreeNode(
            NodeKind.HOST,
            snapshot.name,
            snapshot,
            detail=detail,
            category=host_state_category(snapshot.state),
            expandable=not snapshot.error,
            tooltip=snapshot.error.message if snapshot.error else "",
        )

    def _server_node(self, server: ServerSnapshot) -> TreeNode:
        level = server.info.level
        return TreeNode(
            NodeKind.SERVER,
            str(server.name),
            server.name,
            detail=f"L{level}" if level else "-",
            category=run_state_category(server.run_state),
            expandable=True,
        )

    @staticmethod
    def _server_detail(host: str, level: int) -> str:
        return f"{host} · L{level}" if host else "unassigned"

    @staticmethod
    def _device_node(device: DeviceName, *, label: str = "", full: bool = False) -> TreeNode:
        return TreeNode(
            NodeKind.DEVICE,
            label or (str(device) if full else device.member),
            device,
            tooltip=str(device),
        )


def node_path(node: TreeNode) -> tuple[str, ...]:
    """The labels from the root down to this node."""
    labels: list[str] = []
    current: TreeNode | None = node
    while current is not None:
        labels.append(current.label)
        current = current.parent
    return tuple(reversed(labels))


def _host_of(node: TreeNode) -> str | None:
    parent = node.parent
    if parent is not None and isinstance(parent.payload, HostSnapshot):
        return parent.payload.name
    return None


def _preview(values: Sequence[str]) -> str:
    if not values:
        return ""
    text = values[0] if len(values) == 1 else f"{values[0]} +{len(values) - 1}"
    return text if len(text) <= 28 else f"{text[:27]}…"


def target_of(node: TreeNode) -> Target | None:
    match node.kind:
        case NodeKind.DEVICE:
            return Target.device(node.payload)
        case NodeKind.SERVER:
            return Target.server(node.payload)
        case NodeKind.CLASS:
            return Target.device_class(node.payload)
        case NodeKind.OBJECT:
            return Target.free_object(node.payload)
        case NodeKind.HOST:
            return Target.host(node.payload.name)
        case _:
            return None


class ScopeSelector(QWidget):
    """The scopes as tabs that wrap.

    Six labels in a row need about 440 pixels; the navigator is narrower than
    that, and a scrolling tab bar would cost the click the tabs save.
    """

    currentChanged = pyqtSignal(int)
    COLUMNS = 3

    def __init__(self, labels: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QToolButton] = []
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        for position, label in enumerate(labels):
            button = QToolButton(self)
            button.setText(label)
            button.setCheckable(True)
            button.setProperty("scopeTab", "true")
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._group.addButton(button, position)
            self._buttons.append(button)
            grid.addWidget(button, position // self.COLUMNS, position % self.COLUMNS)
        for column in range(self.COLUMNS):
            grid.setColumnStretch(column, 1)
        self._group.idClicked.connect(self._clicked)

    def setTabToolTip(self, index: int, text: str) -> None:
        self._buttons[index].setToolTip(text)

    def currentIndex(self) -> int:
        return int(self._group.checkedId())

    def setCurrentIndex(self, index: int) -> None:
        if index == self.currentIndex() or not 0 <= index < len(self._buttons):
            return
        self._buttons[index].setChecked(True)
        self.currentChanged.emit(index)

    def _clicked(self, index: int) -> None:
        self.currentChanged.emit(index)


class ScopePage(QWidget):
    """One scope's tree, kept while other tabs are shown."""

    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        scope: Scope,
        runner: TaskRunner,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.scope = scope
        self.loader = ScopeLoader(context, scope)
        self.loaded = False
        self._runner = runner

        self.model = LazyTreeModel(self.loader.children, runner, self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setRecursiveFilteringEnabled(True)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self.view = QTreeView(self)
        self.view.setModel(self.proxy)
        self.view.setHeaderHidden(True)
        self.view.setItemDelegate(NodeDelegate(tokens, self.view))
        self.view.setUniformRowHeights(True)
        self.view.setExpandsOnDoubleClick(False)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    def refresh(self) -> None:
        self.loaded = True
        self._runner.run(
            self.loader.roots(),
            on_result=self.model.set_roots,
            label=f"load {self.scope.value.lower()}",
        )

    def node_at(self, index: QModelIndex) -> TreeNode | None:
        return self.model.node(self.proxy.mapToSource(index))


class Navigator(QWidget):
    """One tab per scope, each with its own tree, loaded the first time it shows."""

    targetActivated = pyqtSignal(object)
    nodeSelected = pyqtSignal(object)

    def __init__(self, context: AppContext, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._tokens = tokens
        self._runner = TaskRunner(self, context.journal, context="navigator")

        self.filter_box = QLineEdit(self)
        self.filter_box.setPlaceholderText("Filter loaded nodes…")
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.textChanged.connect(self._apply_filter)

        self.tab_bar = ScopeSelector([TAB_LABELS[scope] for scope in Scope], self)
        self.stack = QStackedWidget(self)
        self.pages: dict[Scope, ScopePage] = {}
        for scope in Scope:
            page = ScopePage(context, tokens, scope, self._runner, self.stack)
            page.view.clicked.connect(self._clicked)
            page.view.doubleClicked.connect(self._activated)
            page.view.customContextMenuRequested.connect(self._context_menu)
            selection = page.view.selectionModel()
            if selection is not None:
                selection.currentChanged.connect(self._current_changed)
            self.pages[scope] = page
            self.stack.addWidget(page)
            self.tab_bar.setTabToolTip(list(Scope).index(scope), scope.value)
        self._select(Scope.DEVICES)
        self.tab_bar.currentChanged.connect(self._tab_changed)

        self.write = WriteAction(context, tokens, self._runner, self)
        self.write.done.connect(self.refresh_all)
        self.process = ProcessActions(context, self._runner, self)
        self.process.done.connect(self.refresh)
        self.process.failed.connect(lambda report: context.journal.report(report, "navigator"))
        self.write.failed.connect(lambda report: context.journal.report(report, "navigator"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self.tab_bar)
        layout.addWidget(self.filter_box)
        layout.addWidget(self.stack, 1)

    @property
    def page(self) -> ScopePage:
        widget = self.stack.currentWidget()
        assert isinstance(widget, ScopePage)
        return widget

    @property
    def scope(self) -> Scope:
        return self.page.scope

    @property
    def model(self) -> LazyTreeModel:
        return self.page.model

    @property
    def proxy(self) -> QSortFilterProxyModel:
        return self.page.proxy

    @property
    def view(self) -> QTreeView:
        return self.page.view

    def set_scope(self, scope: Scope) -> None:
        self.tab_bar.setCurrentIndex(list(Scope).index(scope))
        self._ensure_loaded()

    def _select(self, scope: Scope) -> None:
        self.tab_bar.setCurrentIndex(list(Scope).index(scope))
        self.stack.setCurrentWidget(self.pages[scope])

    def refresh(self) -> None:
        """Reload the tab in view."""
        self.page.refresh()

    def refresh_all(self) -> None:
        """After a write every scope may be out of date; the others reload when shown."""
        for page in self.pages.values():
            page.loaded = False
        self.refresh()

    async def idle(self) -> None:
        await self._runner.idle()

    def node_at(self, index: QModelIndex) -> TreeNode | None:
        return self.page.node_at(index)

    def _ensure_loaded(self) -> None:
        if not self.page.loaded:
            self.page.refresh()

    def _tab_changed(self, index: int) -> None:
        self.stack.setCurrentWidget(self.pages[list(Scope)[index]])
        self._apply_filter(self.filter_box.text())
        self._ensure_loaded()
        self.nodeSelected.emit(self.node_at(self.view.currentIndex()))

    def _apply_filter(self, text: str) -> None:
        self.page.proxy.setFilterFixedString(text)

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self.nodeSelected.emit(self.node_at(current))

    # ------------------------------------------------------------ create and remove

    def create_here(self) -> None:
        """What “New…” means depends on the scope being browsed."""
        if self.scope is Scope.HOSTS:
            self.add_host()
        else:
            self.new_server()

    def _context_menu(self, point: QPoint) -> None:
        popup(self.view, point, self.context_items(self.node_at(self.view.indexAt(point))))

    def context_items(self, node: TreeNode | None) -> MenuItems:
        writable = not self._context.read_only
        items: list[MenuEntry | None] = []
        target = target_of(node) if node is not None else None
        if target is not None:
            items.append(MenuEntry("Open", lambda: self.targetActivated.emit(target)))
        if node is not None and node.expandable:
            tiles = self.tiles_target(node)
            items.append(
                MenuEntry("Open members as tiles", lambda: self.targetActivated.emit(tiles))
            )
        if items:
            items.append(SEPARATOR)
        if node is not None:
            items += self._process_items(node, writable)
            items += self._database_items(node, writable)
        if self.scope is Scope.HOSTS:
            items.append(MenuEntry("Add controlled host…", self.add_host, writable))
        else:
            items.append(MenuEntry("New server…", self.new_server, writable))
        return items

    def _process_items(self, node: TreeNode, writable: bool) -> list[MenuEntry | None]:
        """Processes are acted on where the host is known: under a host."""
        if node.kind is NodeKind.HOST and isinstance(node.payload, HostSnapshot):
            snapshot = node.payload
            return [
                MenuEntry("Start all levels", lambda: self.process.start_all(snapshot), writable),
                MenuEntry("Stop all levels", lambda: self.process.stop_all(snapshot), writable),
                SEPARATOR,
            ]
        host = _host_of(node)
        if node.kind is not NodeKind.SERVER or host is None:
            return []
        servers = (node.payload,)
        return [
            MenuEntry("Start", lambda: self.process.start(host, servers), writable),
            MenuEntry("Stop", lambda: self.process.stop(host, servers), writable),
            MenuEntry("Restart", lambda: self.process.restart(host, servers), writable),
            SEPARATOR,
        ]

    def _database_items(self, node: TreeNode, writable: bool) -> list[MenuEntry | None]:
        match node.kind:
            case NodeKind.SERVER:
                server = node.payload
                return [
                    MenuEntry("Add device…", lambda: self._add_device(server), writable),
                    MenuEntry("Rename server…", lambda: self._rename_server(server), writable),
                    MenuEntry("Delete server…", lambda: self._delete_server(server), writable),
                    SEPARATOR,
                ]
            case NodeKind.DEVICE:
                device = node.payload
                return [
                    MenuEntry("Rename device…", lambda: self._rename_device(device), writable),
                    MenuEntry("Set alias…", lambda: self._set_alias(device), writable),
                    MenuEntry("Delete device…", lambda: self._delete_device(device), writable),
                    SEPARATOR,
                ]
            case _:
                return []

    def new_server(self) -> None:
        self._runner.run(
            self._context.control.controlled_hosts(), on_result=self._ask_new_server
        )

    def _ask_new_server(self, hosts: tuple[str, ...]) -> None:
        dialog = NewServerDialog(hosts, self._tokens, self)
        if not dialog.exec():
            return
        server, host = dialog.server(), dialog.host_name()
        then = (lambda: self._bring_up(host, server)) if dialog.start_after() else None
        self.write.execute(
            [CreateServer(server, dialog.registrations(), host, dialog.startup_level())],
            then=then,
        )

    def _bring_up(self, host: str, server: ServerName) -> None:
        """Start a new server where it was registered, so that host's Starter has it.

        The Starter lists the servers that have run on its host and rebuilds
        that list when asked, so it is told once the server is up.
        """

        async def run() -> None:
            control = self._context.control
            await control.start_and_confirm(host, server)
            await control.update_servers_info(host)
            self._context.journal.write(f"start {server} on {host}")

        self._runner.run(
            run(),
            on_result=lambda _: self.refresh_all(),
            on_error=lambda report: self._context.journal.report(report, f"start {server}"),
        )

    def add_host(self) -> None:
        dialog = AddHostDialog(self._tokens, self)
        if not dialog.exec():
            return
        self.write.execute(
            [
                CreateServer(
                    dialog.server(), (dialog.registration(),), dialog.host_name(), level=0
                )
            ]
        )

    def _add_device(self, server: ServerName) -> None:
        async def gather() -> tuple[tuple[str, ...], bool]:
            classes = await self._context.backend.get_class_list()
            return classes, await self._context.control.is_running(server)

        self._runner.run(
            gather(), on_result=lambda found: self._ask_add_device(server, *found)
        )

    def _ask_add_device(
        self, server: ServerName, classes: tuple[str, ...], running: bool
    ) -> None:
        dialog = AddDeviceDialog(server, classes, self._tokens, self, running=running)
        if not dialog.exec():
            return
        then = (lambda: self._reload(server)) if dialog.reload_after() else None
        self.write.execute([CreateDevice(dialog.registration())], then=then)

    def _reload(self, server: ServerName) -> None:
        async def run() -> None:
            await self._context.control.reload_server(server)
            self._context.journal.write(f"reload {server}")

        self._runner.run(
            run(),
            on_result=lambda _: self.refresh_all(),
            on_error=lambda report: self._context.journal.report(report, f"reload {server}"),
        )

    def _rename_server(self, server: ServerName) -> None:
        dialog = NameDialog("Rename server", "New name", str(server), self)
        if not dialog.exec() or dialog.name() == str(server):
            return
        self.write.execute([RenameServer(server, ServerName.parse(dialog.name()))])

    def _delete_server(self, server: ServerName) -> None:
        self.write.execute([DeleteServer(server)])

    def _rename_device(self, device: DeviceName) -> None:
        dialog = NameDialog("Rename device", "New name", str(device), self)
        if not dialog.exec() or dialog.name() == str(device):
            return
        self.write.execute([RenameDevice(device, DeviceName.parse(dialog.name()))])

    def _set_alias(self, device: DeviceName) -> None:
        self._runner.run(
            self._context.backend.get_alias_from_device(device),
            on_result=lambda alias: self._ask_alias(device, alias or ""),
        )

    def _ask_alias(self, device: DeviceName, alias: str) -> None:
        dialog = NameDialog("Device alias", "Alias (empty removes it)", alias, self)
        if dialog.exec():
            self.write.execute([SetDeviceAlias(device, dialog.name())])

    def _delete_device(self, device: DeviceName) -> None:
        self.write.execute([DeleteDevice(device)])

    def _clicked(self, index: QModelIndex) -> None:
        """A single click opens and closes a branch."""
        node = self.node_at(index)
        if node is not None and (node.expandable or node.loaded):
            self.view.setExpanded(index, not self.view.isExpanded(index))

    def _activated(self, index: QModelIndex) -> None:
        """A double click opens what the node holds: the members of a branch as
        tiles, or the panel of a leaf."""
        node = self.node_at(index)
        if node is None:
            return
        if node.expandable:
            self.targetActivated.emit(self.tiles_target(node))
            return
        if node.kind is NodeKind.ALIAS:
            self._runner.run(
                self._context.backend.get_device_from_alias(node.payload),
                on_result=lambda device: self.targetActivated.emit(Target.device(device)),
                label=f"resolve alias {node.payload}",
            )
            return
        target = target_of(node)
        if target is not None:
            self.targetActivated.emit(target)

    def tiles_target(self, node: TreeNode) -> Target:
        return Target.tiles(self.scope.name, node_path(node))


UNKNOWN_CATEGORY = StateCategory.UNKNOWN
