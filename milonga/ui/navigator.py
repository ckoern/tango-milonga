"""The navigator: Jive's five trees and Astor's host tree as one widget."""

from collections.abc import Sequence
from enum import StrEnum

from PyQt6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QLineEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from milonga.core.enums import StateCategory
from milonga.core.model import HostSnapshot, ServerSnapshot
from milonga.core.names import DeviceName, ServerName
from milonga.core.tasks import gather_limited
from milonga.ui.context import AppContext, Target
from milonga.ui.models.delegate import NodeDelegate
from milonga.ui.models.tree import LazyTreeModel, NodeKind, TreeNode
from milonga.ui.tasks import TaskRunner
from milonga.ui.theme import Tokens, host_state_category, run_state_category


class Scope(StrEnum):
    HOSTS = "Hosts"
    SERVERS = "Servers"
    DEVICES = "Devices"
    CLASSES = "Classes"
    ALIASES = "Aliases"
    PROPERTIES = "Free properties"


class ScopeLoader:
    """Builds tree nodes for a scope, one level at a time."""

    def __init__(self, context: AppContext) -> None:
        self._context = context
        self.scope = Scope.DEVICES

    async def roots(self) -> list[TreeNode]:
        backend = self._context.backend
        match self.scope:
            case Scope.HOSTS:
                hosts = await backend.get_host_list()
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
                snapshots = await self._context.control.host_snapshots(node.payload)
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


class Navigator(QWidget):
    targetActivated = pyqtSignal(object)
    nodeSelected = pyqtSignal(object)

    def __init__(self, context: AppContext, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._loader = ScopeLoader(context)
        self._runner = TaskRunner(self, context.journal, context="navigator")

        self.scope_box = QComboBox(self)
        self.scope_box.addItems([scope.value for scope in Scope])
        self.scope_box.setCurrentText(Scope.DEVICES.value)
        self.scope_box.currentTextChanged.connect(self._scope_changed)

        self.filter_box = QLineEdit(self)
        self.filter_box.setPlaceholderText("Filter loaded nodes…")
        self.filter_box.setClearButtonEnabled(True)

        self.model = LazyTreeModel(self._loader.children, self._runner, self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setRecursiveFilteringEnabled(True)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.filter_box.textChanged.connect(self.proxy.setFilterFixedString)

        self.view = QTreeView(self)
        self.view.setModel(self.proxy)
        self.view.setHeaderHidden(True)
        self.view.setItemDelegate(NodeDelegate(tokens, self.view))
        self.view.setUniformRowHeights(True)
        self.view.setExpandsOnDoubleClick(False)
        self.view.doubleClicked.connect(self._activated)
        selection = self.view.selectionModel()
        if selection is not None:
            selection.currentChanged.connect(self._current_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self.scope_box)
        layout.addWidget(self.filter_box)
        layout.addWidget(self.view, 1)

    @property
    def scope(self) -> Scope:
        return self._loader.scope

    def set_scope(self, scope: Scope) -> None:
        self.scope_box.setCurrentText(scope.value)

    def refresh(self) -> None:
        self._runner.run(
            self._loader.roots(),
            on_result=self.model.set_roots,
            label=f"load {self._loader.scope.value.lower()}",
        )

    async def idle(self) -> None:
        await self._runner.idle()

    def node_at(self, index: QModelIndex) -> TreeNode | None:
        return self.model.node(self.proxy.mapToSource(index))

    def _scope_changed(self, text: str) -> None:
        self._loader.scope = Scope(text)
        self.model.set_roots([])
        self.refresh()

    def _current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self.nodeSelected.emit(self.node_at(current))

    def _activated(self, index: QModelIndex) -> None:
        node = self.node_at(index)
        if node is None:
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
        elif node.expandable:
            self.view.setExpanded(index, not self.view.isExpanded(index))


UNKNOWN_CATEGORY = StateCategory.UNKNOWN
