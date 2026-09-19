"""The live attribute table of the device panel."""

from collections.abc import Sequence
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt

from milonga.core.enums import AttrDataFormat, StateCategory
from milonga.core.model import AttributeSpec
from milonga.core.names import AttributeRef, DeviceName
from milonga.ui.format import format_time, format_value
from milonga.ui.live import LiveAttributes
from milonga.ui.models.tree import CATEGORY_ROLE
from milonga.ui.theme import mono_font, quality_category

COLUMNS = ("Attribute", "Value", "Unit", "Quality", "Updated", "Source")
INTRINSIC = ("State", "Status")
_FORMAT_ORDER = {
    AttrDataFormat.SCALAR: 0,
    AttrDataFormat.SPECTRUM: 1,
    AttrDataFormat.IMAGE: 2,
}


def sort_key(spec: AttributeSpec) -> tuple[int, int, str]:
    """Scalars first, then spectra and images; State and Status lead."""
    return (
        _FORMAT_ORDER[spec.data_format],
        0 if spec.name in INTRINSIC else 1,
        spec.name.lower(),
    )


class AttributeValuesModel(QAbstractTableModel):
    def __init__(
        self, live: LiveAttributes, device: DeviceName, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._live = live
        self._device = device
        self._specs: list[AttributeSpec] = []
        live.changed.connect(self._value_changed)

    @property
    def specs(self) -> tuple[AttributeSpec, ...]:
        return tuple(self._specs)

    def set_specs(self, specs: Sequence[AttributeSpec]) -> None:
        self.beginResetModel()
        self._specs = sorted(specs, key=sort_key)
        self.endResetModel()

    def spec_at(self, index: QModelIndex) -> AttributeSpec | None:
        row = index.row()
        if not index.isValid() or not 0 <= row < len(self._specs):
            return None
        return self._specs[row]

    def ref_at(self, index: QModelIndex) -> AttributeRef | None:
        spec = self.spec_at(index)
        return AttributeRef(self._device, spec.name) if spec else None

    def refs(self) -> tuple[AttributeRef, ...]:
        return tuple(AttributeRef(self._device, spec.name) for spec in self._specs)

    def row_of(self, name: str) -> int:
        for row, spec in enumerate(self._specs):
            if spec.name == name:
                return row
        return -1

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._specs)

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

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        spec = self.spec_at(index)
        if spec is None:
            return None
        ref = AttributeRef(self._device, spec.name)
        value = self._live.latest(ref)
        error = self._live.error(ref)
        column = index.column()
        match role:
            case Qt.ItemDataRole.DisplayRole:
                return self._text(column, spec, ref, value, error)
            case Qt.ItemDataRole.ToolTipRole:
                if error is not None:
                    return error.message
                return spec.description or spec.label or None
            case Qt.ItemDataRole.FontRole:
                return mono_font() if column in (0, 1, 4) else None
            case _ if role == CATEGORY_ROLE and column == 3:
                if error is not None:
                    return StateCategory.FAULT
                return quality_category(value.quality) if value else StateCategory.UNKNOWN
            case _:
                return None

    def _text(
        self,
        column: int,
        spec: AttributeSpec,
        ref: AttributeRef,
        value: Any,
        error: Any,
    ) -> str:
        match column:
            case 0:
                return spec.name
            case 1:
                return error.message if error is not None else format_value(value, spec)
            case 2:
                return spec.unit or "—"
            case 3:
                if error is not None:
                    return "ERROR"
                return value.quality.value if value else "—"
            case 4:
                return format_time(value.timestamp) if value else "—"
            case _:
                source = self._live.source(ref)
                return source.value if source else "—"

    def _value_changed(self, ref: AttributeRef) -> None:
        row = self.row_of(ref.attribute)
        if row < 0:
            return
        self.dataChanged.emit(self.index(row, 1), self.index(row, len(COLUMNS) - 1))
