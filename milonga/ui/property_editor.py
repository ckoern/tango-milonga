"""Editing properties: change here, review, then write.

Edits live in the table until Apply. Apply previews every command, shows the
diff, asks again for anything destructive, and only then writes — and what it
wrote lands in the journal with an undo.
"""

from collections.abc import Sequence

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from milonga.core.commands import (
    Command,
    CopyProperties,
    PropertyGateway,
    PropertyTarget,
    RenameProperty,
)
from milonga.core.enums import PropertyScope
from milonga.core.model import PropertyEntry, PropertyHistoryEntry
from milonga.ui.context import AppContext
from milonga.ui.dialogs import CopyToDialog, HistoryDialog, NameDialog, ValuesDialog
from milonga.ui.menus import SEPARATOR, MenuEntry, MenuItems, popup
from milonga.ui.models.properties import PropertyEditorModel, PropertyRow, RowState
from milonga.ui.tasks import TaskRunner
from milonga.ui.theme import Tokens
from milonga.ui.write import WriteAction


class PropertyEditor(QWidget):
    applied = pyqtSignal()
    failed = pyqtSignal(object)

    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        target: PropertyTarget,
        runner: TaskRunner,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.context = context
        self.tokens = tokens
        self.target = target
        self._runner = runner
        self._gateway = PropertyGateway(context.backend)
        self._write = WriteAction(context, tokens, runner, self)
        self._write.done.connect(self._applied)
        self._write.nothingToDo.connect(self._nothing_to_do)
        self._write.failed.connect(self.failed.emit)

        self.model = PropertyEditorModel(tokens, self)
        self.model.dataChanged.connect(lambda *_: self._update_buttons())
        self.model.modelReset.connect(self._update_buttons)
        self.model.rowsInserted.connect(lambda *_: self._update_buttons())
        self.model.rowsRemoved.connect(lambda *_: self._update_buttons())

        self.view = QTableView(self)
        self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        self.view.setShowGrid(False)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.doubleClicked.connect(lambda _index: self._edit_values())
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        vertical = self.view.verticalHeader()
        if vertical is not None:
            vertical.setVisible(False)
            vertical.setDefaultSectionSize(24)
        horizontal = self.view.horizontalHeader()
        if horizontal is not None:
            horizontal.setHighlightSections(False)
            for column in (0, 2, 3):
                horizontal.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
            horizontal.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        self.add_button = _button("Add…", self._add_property)
        self.values_button = _button("Edit values…", self._edit_values)
        self.rename_button = _button("Rename…", self._rename)
        self.delete_button = _button("Delete", self._delete)
        self.history_button = _button("History", self._history)
        self.copy_button = _button("Copy to…", self._copy_to)
        self.discard_button = _button("Discard", self._discard)
        self.apply_button = _button("Apply", self.apply_changes)
        self.apply_button.setDefault(True)
        # the label grows to "Apply (n)", so reserve the width up front
        self.apply_button.setMinimumWidth(110)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        for button in (
            self.add_button,
            self.values_button,
            self.rename_button,
            self.delete_button,
            self.history_button,
            self.copy_button,
        ):
            button.setParent(self)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        for button in (self.discard_button, self.apply_button):
            button.setParent(self)
            toolbar.addWidget(button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addWidget(self.view, 1)
        self._update_buttons()

    # -------------------------------------------------------------------- loading

    def refresh(self) -> None:
        self._runner.run(
            self._gateway.read(self.target),
            on_result=self.model.set_entries,
            on_error=self.failed.emit,
            label=f"properties of {self.target}",
        )

    @property
    def read_only(self) -> bool:
        return self.context.read_only

    def selected_rows(self) -> list[int]:
        selection = self.view.selectionModel()
        if selection is None:
            return []
        return sorted({index.row() for index in selection.selectedRows()})

    def selected(self) -> PropertyRow | None:
        rows = self.selected_rows()
        return self.model.rows[rows[0]] if rows else None

    # ----------------------------------------------------------------- right click

    def context_items(self, row: PropertyRow | None) -> MenuItems:
        writable = not self.read_only
        if row is None:
            return [MenuEntry("Add…", self._add_property, writable)]
        items: list[MenuEntry | None] = [
            MenuEntry("Edit values…", self._edit_values, writable),
            MenuEntry("Rename…", self._rename, writable),
            MenuEntry("Delete", self._delete, writable),
            SEPARATOR,
            MenuEntry("History", self._history),
            MenuEntry("Copy to…", self._copy_to, writable),
        ]
        if row.state is not RowState.UNCHANGED:
            position = self.model.row_named(row.name)
            items += [
                SEPARATOR,
                MenuEntry("Undo this edit", lambda: self.model.revert_rows([position])),
            ]
        return items

    def _context_menu(self, point: QPoint) -> None:
        index = self.view.indexAt(point)
        row = self.model.row_at(index)
        if row is not None:
            self.view.selectRow(index.row())
        popup(self.view, point, self.context_items(row))

    # --------------------------------------------------------------------- edits

    def _add_property(self) -> None:
        dialog = NameDialog("New property", "Name", parent=self)
        if not dialog.exec() or not dialog.name():
            return
        values = ValuesDialog(dialog.name(), (), self)
        if not values.exec():
            return
        position = self.model.add_property(dialog.name(), values.values())
        self.view.selectRow(position)

    def _edit_values(self) -> None:
        row = self.selected()
        if row is None or self.read_only:
            return
        dialog = ValuesDialog(row.name, row.values, self)
        if dialog.exec():
            self.model.set_values(self.model.row_named(row.name), dialog.values())

    def _rename(self) -> None:
        row = self.selected()
        if row is None:
            return
        dialog = NameDialog("Rename property", "New name", row.name, self)
        if not dialog.exec() or not dialog.name() or dialog.name() == row.name:
            return
        self._execute([RenameProperty(self.target, row.name, dialog.name())])

    def _delete(self) -> None:
        rows = self.selected_rows()
        if rows:
            self.model.mark_deleted(rows)

    def _discard(self) -> None:
        self.model.discard()

    def _history(self) -> None:
        row = self.selected()
        if row is None:
            return
        self._runner.run(
            self._gateway.history(self.target, row.name),
            on_result=lambda entries: self._show_history(row.name, entries),
            on_error=self.failed.emit,
        )

    def _show_history(self, name: str, entries: Sequence[PropertyHistoryEntry]) -> None:
        HistoryDialog(name, entries, self).exec()

    def _copy_to(self) -> None:
        row = self.selected()
        if row is None:
            return
        self._runner.run(
            self._candidates(),
            on_result=lambda names: self._choose_destinations(row, names),
            on_error=self.failed.emit,
        )

    async def _candidates(self) -> tuple[str, ...]:
        backend = self.context.backend
        match self.target.scope:
            case PropertyScope.DEVICE:
                return tuple(str(device) for device in await backend.get_device_list())
            case PropertyScope.CLASS:
                return await backend.get_class_list()
            case _:
                return await backend.get_object_list()

    def _choose_destinations(self, row: PropertyRow, candidates: Sequence[str]) -> None:
        others = [name for name in candidates if name != self.target.owner]
        dialog = CopyToDialog(others, self)
        if not dialog.exec() or not dialog.chosen():
            return
        destinations = tuple(
            PropertyTarget(self.target.scope, name) for name in dialog.chosen()
        )
        self._execute([CopyProperties(self.target, destinations, (row.name,))])

    # ------------------------------------------------------------------- applying

    def apply_changes(self) -> None:
        commands = self.model.commands(self.target)
        if commands:
            self._execute(commands)

    def _execute(self, commands: Sequence[Command]) -> None:
        self._write.execute(commands, confirm_word=str(self.target))

    def _nothing_to_do(self) -> None:
        self.context.journal.info(f"{self.target} · nothing to write")
        self.model.discard()

    def _applied(self) -> None:
        self.applied.emit()
        self.refresh()

    # -------------------------------------------------------------------- buttons

    def _update_buttons(self) -> None:
        pending = len(self.model.pending)
        editable = not self.read_only
        self.apply_button.setText(f"Apply ({pending})" if pending else "Apply")
        self.apply_button.setEnabled(editable and pending > 0)
        self.discard_button.setEnabled(pending > 0)
        for button in (
            self.add_button,
            self.values_button,
            self.rename_button,
            self.delete_button,
            self.copy_button,
        ):
            button.setEnabled(editable)

    def pending_entries(self) -> tuple[PropertyEntry, ...]:
        return tuple(PropertyEntry(row.name, row.values) for row in self.model.pending)


def _button(text: str, slot: object) -> QPushButton:
    button = QPushButton(text)
    button.clicked.connect(slot)  # type: ignore[arg-type]
    return button
