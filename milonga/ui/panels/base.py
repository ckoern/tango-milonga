"""Panel base class and the table styling every panel shares."""

from collections.abc import Sequence

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from milonga.ui.context import AppContext, Target
from milonga.ui.models.delegate import NodeDelegate
from milonga.ui.models.tables import ObjectTableModel
from milonga.ui.tasks import TaskRunner
from milonga.ui.theme import Tokens, mono_font
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

    async def idle(self) -> None:
        await self.runner.idle()

    def base_layout(self) -> QVBoxLayout:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        layout.addWidget(self.header)
        layout.addWidget(self.banner)
        return layout

    def make_table[T](self, model: ObjectTableModel[T]) -> QTableView:
        view = QTableView(self)
        view.setModel(model)
        view.setAlternatingRowColors(True)
        view.setShowGrid(False)
        view.setWordWrap(False)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setSortingEnabled(False)
        vertical = view.verticalHeader()
        if vertical is not None:
            vertical.setVisible(False)
            vertical.setDefaultSectionSize(24)
        horizontal = view.horizontalHeader()
        if horizontal is not None:
            horizontal.setHighlightSections(False)
            for position, column in enumerate(model.columns):
                mode = (
                    QHeaderView.ResizeMode.Stretch
                    if column.stretch > 1
                    else QHeaderView.ResizeMode.ResizeToContents
                )
                horizontal.setSectionResizeMode(position, mode)
        for position, column in enumerate(model.columns):
            if column.category is not None:
                view.setItemDelegateForColumn(position, NodeDelegate(self.tokens, view))
        return view


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
                caption = QLabel(name, self)
                caption.setStyleSheet(f"color: {self._tokens.ink_3};")
                self._layout.addRow(caption, label)
                self._values[name] = label
            label.setText(value)
