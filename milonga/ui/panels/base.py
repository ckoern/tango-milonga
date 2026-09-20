"""Panel base class and the table styling every panel shares."""

from collections.abc import Sequence

from PyQt6.QtCore import QAbstractItemModel, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from milonga.ui.context import AppContext, Target
from milonga.ui.models.delegate import NodeDelegate
from milonga.ui.models.tables import ObjectTableModel
from milonga.ui.tasks import TaskRunner
from milonga.ui.theme import Tokens, mono_font, set_role
from milonga.ui.widgets import ErrorBanner, HeaderBar


class Panel(QWidget):
    """A document in the centre area, identified by its target."""

    titleChanged = pyqtSignal(str)

    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        target: Target,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.tokens = tokens
        self.target = target
        self.runner = TaskRunner(self, context.journal, context=target.name)
        self.header = HeaderBar(tokens, self)
        self.banner = ErrorBanner(tokens, self)

    @property
    def title(self) -> str:
        return self.target.name

    def refresh(self) -> None:
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release anything held beyond this widget, such as subscriptions."""
        return None

    async def idle(self) -> None:
        await self.runner.idle()

    def base_layout(self) -> QVBoxLayout:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        layout.addWidget(self.header)
        layout.addWidget(self.banner)
        return layout

    def make_table(
        self,
        model: QAbstractItemModel,
        *,
        chips: Sequence[int] = (),
        stretch: Sequence[int] = (),
    ) -> QTableView:
        """A read-only table. Column roles come from the model when it declares them."""
        if isinstance(model, ObjectTableModel):
            columns = model.columns
            stretch = [index for index, column in enumerate(columns) if column.stretch > 1]
            chips = [index for index, column in enumerate(columns) if column.category is not None]

        view = QTableView(self)
        view.setModel(model)
        view.setAlternatingRowColors(True)
        view.setShowGrid(False)
        view.setWordWrap(False)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        vertical = view.verticalHeader()
        if vertical is not None:
            vertical.setVisible(False)
            vertical.setDefaultSectionSize(24)
        horizontal = view.horizontalHeader()
        if horizontal is not None:
            horizontal.setHighlightSections(False)
            for position in range(model.columnCount()):
                mode = (
                    QHeaderView.ResizeMode.Stretch
                    if position in stretch
                    else QHeaderView.ResizeMode.ResizeToContents
                )
                horizontal.setSectionResizeMode(position, mode)
        for position in chips:
            view.setItemDelegateForColumn(position, NodeDelegate(self.tokens, view))
        return view


MAX_FIELD = 72


def _shorten(value: str) -> str:
    if len(value) <= MAX_FIELD:
        return value
    keep = MAX_FIELD // 2 - 1
    return f"{value[:keep]}…{value[-keep:]}"


class InfoForm(QWidget):
    """A read-only field list, values in monospace."""

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._values: dict[str, QLabel] = {}
        self._layout = QFormLayout(self)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.setHorizontalSpacing(18)
        self._layout.setVerticalSpacing(6)

    def set_fields(self, fields: Sequence[tuple[str, str]]) -> None:
        for name, value in fields:
            label = self._values.get(name)
            if label is None:
                label = QLabel(self)
                label.setFont(mono_font())
                label.setTextInteractionFlags(
                    label.textInteractionFlags().TextSelectableByMouse
                )
                # an IOR is hundreds of characters without a break; a label that
                # sized itself to it would widen the whole window
                label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                caption = QLabel(name, self)
                set_role(caption, "role", "muted")
                self._layout.addRow(caption, label)
                self._values[name] = label
            label.setText(_shorten(value))
            label.setToolTip(value if len(value) > MAX_FIELD else "")
