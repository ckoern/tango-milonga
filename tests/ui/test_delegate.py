"""The state dot is painted from what Qt hands back, not from what we stored."""

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QModelIndex, QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QTreeView

from milonga.core.enums import StateCategory
from milonga.ui.models.delegate import DOT_SIZE, NodeDelegate, category_of
from milonga.ui.models.tree import LazyTreeModel, NodeKind, TreeNode
from milonga.ui.tasks import TaskRunner
from milonga.ui.theme import Tokens, category_color

WIDTH = 220
HEIGHT = 24


async def _loader(node: TreeNode) -> list[TreeNode]:
    return []


def _rows(node: TreeNode) -> tuple[LazyTreeModel, QModelIndex]:
    """The model is handed back with the index: it owns the row the index points at."""
    model = LazyTreeModel(_loader, TaskRunner())
    model.set_roots([node])
    return model, model.index(0, 0, QModelIndex())


@pytest.fixture
def delegate(app: QApplication, light_tokens: Tokens) -> Iterator[NodeDelegate]:
    """Yielded, not returned: Qt deletes the delegate with the view it hangs on."""
    view = QTreeView()
    yield NodeDelegate(light_tokens, view)


@pytest.fixture
def rows(app: QApplication) -> tuple[LazyTreeModel, QModelIndex]:
    return _rows(TreeNode(NodeKind.DEVICE, "id09/motor/phi", category=StateCategory.FAULT))


def test_a_category_survives_the_trip_through_qt(
    rows: tuple[LazyTreeModel, QModelIndex],
) -> None:
    _model, index = rows
    assert category_of(index) is StateCategory.FAULT


def test_the_dot_is_painted_in_the_category_colour(
    rows: tuple[LazyTreeModel, QModelIndex], delegate: NodeDelegate, light_tokens: Tokens
) -> None:
    _model, index = rows
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ffffff"))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, WIDTH, HEIGHT)
    option.state = option.state | QStyle.StateFlag.State_Enabled
    painter = QPainter(image)
    delegate.paint(painter, option, index)
    painter.end()

    wanted = category_color(light_tokens, StateCategory.FAULT)
    middle = HEIGHT // 2
    painted = {image.pixelColor(x, middle).name() for x in range(2, 2 + DOT_SIZE)}
    assert wanted.name() in painted


def test_a_node_without_a_category_has_no_dot(
    app: QApplication, delegate: NodeDelegate
) -> None:
    _model, plain = _rows(TreeNode(NodeKind.GROUP, "Beamline"))
    assert category_of(plain) is None
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, WIDTH, HEIGHT)
    assert delegate.sizeHint(option, plain).width() < WIDTH
