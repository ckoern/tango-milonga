"""The editable property table.

Edits are held in the model and shown as pending until they are applied, so
nothing reaches the database without passing through a diff.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt
from PyQt6.QtGui import QColor, QFont

from milonga.core.commands import Command, DeleteProperties, PropertyTarget, PutProperties
from milonga.core.model import PropertyEntry, PropertyValues
from milonga.ui.theme import Tokens, mono_font

COLUMNS = ("Property", "Value", "Values", "State")
VALUE_COLUMN = 1


class RowState(StrEnum):
    UNCHANGED = "unchanged"
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(slots=True)
class PropertyRow:
    name: str
    values: PropertyValues
    original: PropertyValues
    state: RowState = RowState.UNCHANGED

    @property
    def single(self) -> bool:
        return len(self.values) <= 1

    @property
    def text(self) -> str:
        return self.values[0] if self.values else ""


class PropertyEditorModel(QAbstractTableModel):
    def __init__(self, tokens: Tokens, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._rows: list[PropertyRow] = []

    # ------------------------------------------------------------------ contents

    @property
    def rows(self) -> tuple[PropertyRow, ...]:
        return tuple(self._rows)

    def set_entries(self, entries: Sequence[PropertyEntry]) -> None:
        self.beginResetModel()
        self._rows = [
            PropertyRow(entry.name, tuple(entry.values), tuple(entry.values))
            for entry in sorted(entries, key=lambda entry: entry.name.lower())
        ]
        self.endResetModel()

    def row_at(self, index: QModelIndex) -> PropertyRow | None:
        row = index.row()
        if not index.isValid() or not 0 <= row < len(self._rows):
            return None
        return self._rows[row]

    def row_named(self, name: str) -> int:
        for position, row in enumerate(self._rows):
            if row.name == name:
                return position
        return -1

    # ------------------------------------------------------------------- editing

    def set_values(self, position: int, values: Sequence[str]) -> None:
        row = self._rows[position]
        row.values = tuple(values)
        if row.state is not RowState.ADDED:
            row.state = (
                RowState.UNCHANGED if row.values == row.original else RowState.MODIFIED
            )
        self._touched(position)

    def add_property(self, name: str, values: Sequence[str]) -> int:
        existing = self.row_named(name)
        if existing >= 0:
            self.set_values(existing, values)
            return existing
        position = len(self._rows)
        self.beginInsertRows(QModelIndex(), position, position)
        self._rows.append(PropertyRow(name, tuple(values), (), RowState.ADDED))
        self.endInsertRows()
        return position

    def mark_deleted(self, positions: Sequence[int]) -> None:
        for position in positions:
            row = self._rows[position]
            if row.state is RowState.ADDED:
                continue
            row.state = RowState.DELETED
            self._touched(position)

    def revert_rows(self, positions: Sequence[int]) -> None:
        for position in sorted(positions, reverse=True):
            row = self._rows[position]
            if row.state is RowState.ADDED:
                self.beginRemoveRows(QModelIndex(), position, position)
                self._rows.pop(position)
                self.endRemoveRows()
                continue
            row.values = row.original
            row.state = RowState.UNCHANGED
            self._touched(position)

    def discard(self) -> None:
        self.revert_rows(range(len(self._rows)))

    @property
    def pending(self) -> tuple[PropertyRow, ...]:
        return tuple(row for row in self._rows if row.state is not RowState.UNCHANGED)

    def commands(self, target: PropertyTarget) -> list[Command]:
        written = [
            PropertyEntry(row.name, row.values)
            for row in self._rows
            if row.state in (RowState.ADDED, RowState.MODIFIED)
        ]
        deleted = [row.name for row in self._rows if row.state is RowState.DELETED]
        commands: list[Command] = []
        if written:
            commands.append(PutProperties(target, tuple(written)))
        if deleted:
            commands.append(DeleteProperties(target, tuple(deleted)))
        return commands

    # -------------------------------------------------------------- model basics

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if orientation is Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        row = self.row_at(index)
        if row is None or index.column() != VALUE_COLUMN:
            return base
        if row.state is RowState.DELETED or not row.single:
            return base
        return base | Qt.ItemFlag.ItemIsEditable

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        row = self.row_at(index)
        if row is None:
            return None
        match role:
            case Qt.ItemDataRole.DisplayRole | Qt.ItemDataRole.EditRole:
                return self._text(row, index.column())
            case Qt.ItemDataRole.ToolTipRole:
                return "\n".join(row.values) or None
            case Qt.ItemDataRole.FontRole:
                return self._font(row, index.column())
            case Qt.ItemDataRole.ForegroundRole:
                return self._colour(row)
            case _:
                return None

    def setData(
        self, index: QModelIndex, value: Any, role: int = int(Qt.ItemDataRole.EditRole)
    ) -> bool:
        row = self.row_at(index)
        if row is None or role != Qt.ItemDataRole.EditRole or index.column() != VALUE_COLUMN:
            return False
        text = str(value)
        self.set_values(index.row(), (text,) if text else ())
        return True

    # ------------------------------------------------------------------ internals

    def _text(self, row: PropertyRow, column: int) -> str:
        match column:
            case 0:
                return row.name
            case 1:
                return " ⏎ ".join(row.values) if not row.single else row.text
            case 2:
                return str(len(row.values))
            case _:
                return "" if row.state is RowState.UNCHANGED else row.state.value

    def _font(self, row: PropertyRow, column: int) -> QFont | None:
        if column not in (0, 1):
            return None
        font = mono_font()
        if row.state is RowState.DELETED:
            font.setStrikeOut(True)
        return font

    def _colour(self, row: PropertyRow) -> QColor | None:
        match row.state:
            case RowState.ADDED:
                return QColor(self._tokens.ok)
            case RowState.MODIFIED:
                return QColor(self._tokens.accent)
            case RowState.DELETED:
                return QColor(self._tokens.bad)
            case _:
                return None

    def _touched(self, position: int) -> None:
        self.dataChanged.emit(
            self.index(position, 0), self.index(position, len(COLUMNS) - 1)
        )
