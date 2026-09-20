"""One table model, configured by column, for every read-only table."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt

from milonga.core.enums import StateCategory
from milonga.ui.models import Index
from milonga.ui.models.tree import CATEGORY_ROLE
from milonga.ui.theme import mono_font

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Column(Generic[T]):
    title: str
    value: Callable[[T], str]
    tooltip: Callable[[T], str] | None = None
    category: Callable[[T], StateCategory | None] | None = None
    mono: bool = False
    align_right: bool = False
    stretch: int = 1


class ObjectTableModel(QAbstractTableModel, Generic[T]):
    def __init__(self, columns: Sequence[Column[T]], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._columns = list(columns)
        self._rows: list[T] = []

    @property
    def columns(self) -> list[Column[T]]:
        return self._columns

    @property
    def rows(self) -> tuple[T, ...]:
        return tuple(self._rows)

    def set_rows(self, rows: Sequence[T]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def append_row(self, row: T) -> None:
        position = len(self._rows)
        self.beginInsertRows(QModelIndex(), position, position)
        self._rows.append(row)
        self.endInsertRows()

    def row_at(self, index: Index) -> T | None:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        return self._rows[index.row()]

    def rowCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: Index = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if orientation is Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._columns[section].title
        return None

    def data(self, index: Index, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        row = self.row_at(index)
        if row is None:
            return None
        column = self._columns[index.column()]
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return column.value(row)
            case Qt.ItemDataRole.ToolTipRole:
                return column.tooltip(row) if column.tooltip else None
            case Qt.ItemDataRole.FontRole:
                return mono_font() if column.mono else None
            case Qt.ItemDataRole.TextAlignmentRole:
                if column.align_right:
                    return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            case _ if role == CATEGORY_ROLE:
                return column.category(row) if column.category else None
            case _:
                return None
