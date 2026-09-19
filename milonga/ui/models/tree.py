"""A tree whose children are fetched when a branch is opened.

Nothing is read from the database until a node is expanded, so a control
system with ten thousand devices costs one query at startup.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, QObject, Qt, pyqtSignal

from milonga.core.enums import StateCategory
from milonga.core.errors import ErrorReport
from milonga.ui.tasks import TaskRunner


class NodeKind(StrEnum):
    GROUP = "group"
    HOST = "host"
    SERVER_EXEC = "server_exec"
    SERVER = "server"
    CLASS = "class"
    DOMAIN = "domain"
    FAMILY = "family"
    DEVICE = "device"
    ALIAS = "alias"
    OBJECT = "object"
    PROPERTY = "property"
    MESSAGE = "message"


@dataclass(slots=True)
class TreeNode:
    kind: NodeKind
    label: str
    payload: Any = None
    detail: str = ""
    category: StateCategory | None = None
    expandable: bool = False
    tooltip: str = ""
    parent: "TreeNode | None" = None
    children: "list[TreeNode] | None" = None
    loading: bool = False

    @property
    def row(self) -> int:
        if self.parent is None or self.parent.children is None:
            return 0
        return self.parent.children.index(self)

    @property
    def loaded(self) -> bool:
        return self.children is not None


@dataclass(slots=True)
class _Root:
    children: list[TreeNode] = field(default_factory=list)


type ChildLoader = Callable[[TreeNode], Awaitable[Sequence[TreeNode]]]

NODE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
CATEGORY_ROLE = NODE_ROLE + 1
DETAIL_ROLE = NODE_ROLE + 2


class LazyTreeModel(QAbstractItemModel):
    """Holds nodes; asks ``loader`` for children the first time a node opens."""

    loadFailed = pyqtSignal(object)

    def __init__(
        self, loader: ChildLoader, runner: TaskRunner, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._loader = loader
        self._runner = runner
        self._roots: list[TreeNode] = []

    # ------------------------------------------------------------------ contents

    def set_roots(self, nodes: Sequence[TreeNode]) -> None:
        self.beginResetModel()
        self._roots = list(nodes)
        for node in self._roots:
            node.parent = None
        self.endResetModel()

    def node(self, index: QModelIndex) -> TreeNode | None:
        if not index.isValid():
            return None
        pointer = index.internalPointer()
        return pointer if isinstance(pointer, TreeNode) else None

    def index_of(self, node: TreeNode) -> QModelIndex:
        return self.createIndex(node.row, 0, node)

    def reload(self, index: QModelIndex) -> None:
        node = self.node(index)
        if node is None or node.children is None:
            return
        self.beginRemoveRows(index, 0, len(node.children) - 1)
        node.children = None
        self.endRemoveRows()
        self.fetchMore(index)

    # -------------------------------------------------------------- model basics

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        siblings = self._children(self.node(parent))
        return self.createIndex(row, column, siblings[row])

    def parent(self, index: QModelIndex = QModelIndex()) -> QModelIndex:  # type: ignore[override]
        node = self.node(index)
        if node is None or node.parent is None:
            return QModelIndex()
        return self.createIndex(node.parent.row, 0, node.parent)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.column() > 0:
            return 0
        return len(self._children(self.node(parent)))

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:
        node = self.node(parent)
        if node is None:
            return bool(self._roots)
        return node.expandable or bool(node.children)

    def canFetchMore(self, parent: QModelIndex) -> bool:
        node = self.node(parent)
        return node is not None and node.expandable and not node.loaded and not node.loading

    def fetchMore(self, parent: QModelIndex) -> None:
        node = self.node(parent)
        if node is None or node.loading or node.loaded:
            return
        node.loading = True
        self._runner.run(
            self._loader(node),
            on_result=lambda children: self._children_loaded(node, children),
            on_error=lambda report: self._load_failed(node, report),
            label=f"load {node.label}",
        )

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        node = self.node(index)
        if node is None:
            return None
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return node.label
            case Qt.ItemDataRole.ToolTipRole:
                return node.tooltip or None
            case _ if role == NODE_ROLE:
                return node
            case _ if role == CATEGORY_ROLE:
                return node.category
            case _ if role == DETAIL_ROLE:
                return node.detail
            case _:
                return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        node = self.node(index)
        if node is not None and node.kind is NodeKind.MESSAGE:
            return Qt.ItemFlag.ItemIsEnabled
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    async def idle(self) -> None:
        await self._runner.idle()

    # ------------------------------------------------------------------ internals

    def _children(self, node: TreeNode | None) -> list[TreeNode]:
        if node is None:
            return self._roots
        return node.children or []

    def _children_loaded(self, node: TreeNode, children: Sequence[TreeNode]) -> None:
        node.loading = False
        index = self.index_of(node)
        if not children:
            node.children = []
            node.expandable = False
            self.dataChanged.emit(index, index)
            return
        self.beginInsertRows(index, 0, len(children) - 1)
        node.children = list(children)
        for child in node.children:
            child.parent = node
        self.endInsertRows()

    def _load_failed(self, node: TreeNode, report: ErrorReport) -> None:
        node.loading = False
        message = TreeNode(NodeKind.MESSAGE, report.message, category=StateCategory.FAULT)
        self._children_loaded(node, [message])
        self.loadFailed.emit(report)
