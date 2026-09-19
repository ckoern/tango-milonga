"""Dialogs for the write path: preview, confirm, edit, inspect."""

from collections.abc import Sequence
from dataclasses import fields, replace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from milonga.core.commands import Diff, DiffKind
from milonga.core.commands.attributes import CONFIG_FIELDS
from milonga.core.enums import DisplayLevel
from milonga.core.model import AlarmConfig, AttributeSpec, EventConfig, PropertyHistoryEntry
from milonga.ui.theme import Tokens, mono_font

_DIFF_COLOUR = {
    DiffKind.ADDED: "ok",
    DiffKind.REMOVED: "bad",
    DiffKind.CHANGED: "accent",
    DiffKind.UNCHANGED: "ink_3",
}


class DiffDialog(QDialog):
    """Shows exactly what will be written, before anything is."""

    def __init__(self, diff: Diff, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review changes")
        self.resize(640, 420)
        changes = diff.changes

        noun = "change" if len(changes) == 1 else "changes"
        heading = QLabel(f"{len(changes)} {noun} will be written to the database", self)
        self.list = QListWidget(self)
        self.list.setFont(mono_font())
        for line in changes:
            item = QListWidgetItem(str(line))
            item.setForeground(_colour(tokens, line.kind))
            self.list.addItem(item)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply,
            parent=self,
        )
        apply = buttons.button(QDialogButtonBox.StandardButton.Apply)
        if apply is not None:
            apply.setDefault(True)
            apply.setEnabled(bool(changes))
            apply.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(self.list, 1)
        layout.addWidget(buttons)


class ConfirmDialog(QDialog):
    """A destructive action asks for the object's name, not just an OK."""

    def __init__(
        self,
        title: str,
        message: str,
        *,
        confirm_word: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._confirm_word = confirm_word

        text = QLabel(message, self)
        text.setWordWrap(True)
        self.input = QLineEdit(self)
        self.input.setFont(mono_font())
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(text)
        if confirm_word is not None:
            layout.addWidget(QLabel(f"Type “{confirm_word}” to confirm", self))
            layout.addWidget(self.input)
            self.input.textChanged.connect(self._check)
            self._check("")
        layout.addWidget(self.buttons)

    def _check(self, text: str) -> None:
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if button is not None:
            button.setEnabled(text.strip() == self._confirm_word)


class ValuesDialog(QDialog):
    """Array properties are edited one value per line."""

    def __init__(
        self, name: str, values: Sequence[str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Edit {name}")
        self.resize(520, 360)
        self.editor = QPlainTextEdit("\n".join(values), self)
        self.editor.setFont(mono_font())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("One value per line", self))
        layout.addWidget(self.editor, 1)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, ...]:
        text = self.editor.toPlainText()
        return tuple(line for line in text.splitlines() if line.strip())


class NameDialog(QDialog):
    def __init__(
        self,
        title: str,
        label: str,
        initial: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.input = QLineEdit(initial, self)
        self.input.setFont(mono_font())
        self.input.selectAll()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QFormLayout(self)
        layout.addRow(label, self.input)
        layout.addRow(buttons)

    def name(self) -> str:
        return self.input.text().strip()


class HistoryDialog(QDialog):
    def __init__(
        self,
        name: str,
        entries: Sequence[PropertyHistoryEntry],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"History of {name}")
        self.resize(560, 360)
        self.list = QListWidget(self)
        self.list.setFont(mono_font())
        for entry in entries:
            stamp = entry.changed_at.strftime("%Y-%m-%d %H:%M:%S") if entry.changed_at else "—"
            values = "deleted" if entry.deleted else ", ".join(entry.values)
            self.list.addItem(f"{stamp}   {values}")
        if not entries:
            self.list.addItem("no recorded history")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close is not None:
            close.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addWidget(buttons)


class CopyToDialog(QDialog):
    """Pick the objects a property is copied to."""

    def __init__(
        self, candidates: Sequence[str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Copy properties to")
        self.resize(480, 420)
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText("Filter…")
        self.list = QListWidget(self)
        self.list.setFont(mono_font())
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        for candidate in candidates:
            self.list.addItem(candidate)
        self.filter.textChanged.connect(self._filter)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.filter)
        layout.addWidget(self.list, 1)
        layout.addWidget(buttons)

    def _filter(self, text: str) -> None:
        needle = text.lower()
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item is not None:
                item.setHidden(needle not in item.text().lower())

    def chosen(self) -> tuple[str, ...]:
        return tuple(item.text() for item in self.list.selectedItems())


class AttributeConfigDialog(QDialog):
    """Jive's attribute configuration, as a form over one attribute."""

    def __init__(
        self, spec: AttributeSpec, tokens: Tokens, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Configure {spec.name}")
        self.resize(520, 640)
        self._spec = spec
        self._fields: dict[str, QLineEdit] = {}

        layout = QFormLayout(self)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for name in CONFIG_FIELDS:
            if name == "display_level":
                continue
            self._fields[name] = self._add_row(layout, name, str(getattr(spec, name)))

        self.level = QComboBox(self)
        self.level.addItems([level.value for level in DisplayLevel])
        self.level.setCurrentText(spec.display_level.value)
        layout.addRow("display level", self.level)

        for group, prefix in ((spec.alarms, "alarms"), (spec.events, "events")):
            for item in fields(group):
                key = f"{prefix}.{item.name}"
                self._fields[key] = self._add_row(layout, key, str(getattr(group, item.name)))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _add_row(self, layout: QFormLayout, label: str, value: str) -> QLineEdit:
        editor = QLineEdit(value, self)
        editor.setFont(mono_font())
        layout.addRow(label.replace("_", " "), editor)
        return editor

    def edited(self) -> AttributeSpec:
        alarms = AlarmConfig(
            **{
                item.name: self._fields[f"alarms.{item.name}"].text()
                for item in fields(self._spec.alarms)
            }
        )
        events = EventConfig(
            **{
                item.name: self._fields[f"events.{item.name}"].text()
                for item in fields(self._spec.events)
            }
        )
        return replace(
            self._spec,
            alarms=alarms,
            events=events,
            display_level=DisplayLevel(self.level.currentText()),
            label=self._value("label"),
            unit=self._value("unit"),
            standard_unit=self._value("standard_unit"),
            display_unit=self._value("display_unit"),
            display_format=self._value("display_format"),
            description=self._value("description"),
            min_value=self._value("min_value"),
            max_value=self._value("max_value"),
        )

    def _value(self, name: str) -> str:
        return self._fields[name].text()


def _colour(tokens: Tokens, kind: DiffKind) -> QColor:
    return QColor(getattr(tokens, _DIFF_COLOUR[kind]))
