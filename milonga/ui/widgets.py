"""Small shared widgets: state chips, section headings, error banners."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from milonga.core.enums import StateCategory, TangoType
from milonga.core.errors import ErrorReport
from milonga.core.model import AttributeSpec, CommandSpec
from milonga.ui.format import parse_command_argument, parse_write_value
from milonga.ui.theme import Tokens, category_background, category_color, mono_font


class StateChip(QLabel):
    """A state name on its own coloured ground."""

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self.setFont(mono_font())
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state("UNKNOWN", StateCategory.UNKNOWN)

    def set_state(self, text: str, category: StateCategory) -> None:
        self.setText(text)
        self.setStyleSheet(
            f"background: {category_background(self._tokens, category).name()};"
            f"color: {category_color(self._tokens, category).name()};"
            "border-radius: 9px; padding: 2px 10px; font-weight: 600;"
        )


class SectionLabel(QLabel):
    def __init__(self, text: str, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(text.upper(), parent)
        self.setStyleSheet(
            f"color: {tokens.ink_3}; font-weight: 600; letter-spacing: 1px; font-size: 8pt;"
        )


class ErrorBanner(QFrame):
    """Errors appear in the panel that caused them, not in a modal dialog."""

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self.setVisible(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._summary = QLabel(self)
        self._summary.setWordWrap(True)
        self._detail = QLabel(self)
        self._detail.setWordWrap(True)
        self._detail.setFont(mono_font())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(2)
        layout.addWidget(self._summary)
        layout.addWidget(self._detail)
        self.setStyleSheet(
            f"QFrame {{ background: {tokens.bad_soft}; border-left: 3px solid {tokens.bad}; }}"
            f"QLabel {{ background: transparent; color: {tokens.bad}; }}"
        )

    def show_error(self, report: ErrorReport, context: str = "") -> None:
        self._summary.setText(f"{context}: {report.message}" if context else report.message)
        self._detail.setText(
            "\n".join(f"{frame.reason}  {frame.description}" for frame in report.frames)
        )
        self._detail.setVisible(bool(report.frames))
        self.setVisible(True)

    def clear(self) -> None:
        self.setVisible(False)


class HeaderBar(QWidget):
    """Panel title: a monospace object name, a chip, and dimmed context."""

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self.name = QLabel(self)
        font = mono_font()
        font.setPointSize(13)
        font.setBold(True)
        self.name.setFont(font)
        self.chip = StateChip(tokens, self)
        self.context = QLabel(self)
        self.context.setStyleSheet(f"color: {tokens.ink_3};")
        self.context.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.name)
        layout.addWidget(self.chip)
        layout.addWidget(self.context, 1)

    def set_header(self, name: str, context: str = "") -> None:
        self.name.setText(name)
        self.context.setText(context)


class WriteBar(QWidget):
    """Writing a value is always an explicit action, never a side effect."""

    writeRequested = pyqtSignal(str, object)

    def __init__(
        self, tokens: Tokens, *, read_only: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._read_only = read_only
        self._spec: AttributeSpec | None = None

        self.caption = QLabel("Set point", self)
        self.caption.setStyleSheet(f"color: {tokens.ink_3};")
        self.name = QLabel("—", self)
        self.name.setFont(mono_font())
        self.editor = QLineEdit(self)
        self.editor.setFont(mono_font())
        self.editor.returnPressed.connect(self._submit)
        self.button = QPushButton("Write", self)
        self.button.clicked.connect(self._submit)
        self.message = QLabel(self)
        self.message.setStyleSheet(f"color: {tokens.bad};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.caption)
        layout.addWidget(self.name)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.button)
        layout.addWidget(self.message, 1)
        self.set_target(None)

    def set_target(self, spec: AttributeSpec | None, value: str = "") -> None:
        self._spec = spec
        writable = spec is not None and spec.writable.writable and not self._read_only
        self.name.setText(spec.name if spec else "—")
        self.editor.setEnabled(writable)
        self.button.setEnabled(writable)
        self.editor.setText(value if writable else "")
        self.message.clear()
        if spec is None:
            self.editor.setPlaceholderText("select an attribute")
        elif self._read_only:
            self.editor.setPlaceholderText("session is read-only")
        elif not spec.writable.writable:
            self.editor.setPlaceholderText(f"{spec.name} is read-only")
        else:
            self.editor.setPlaceholderText(f"{spec.data_type.value}")

    def show_message(self, text: str, *, error: bool = True) -> None:
        colour = self._tokens.bad if error else self._tokens.ok
        self.message.setStyleSheet(f"color: {colour};")
        self.message.setText(text)

    def _submit(self) -> None:
        if self._spec is None or not self.editor.isEnabled():
            return
        try:
            value = parse_write_value(self.editor.text(), self._spec)
        except ValueError as error:
            self.show_message(str(error))
            return
        self.message.clear()
        self.writeRequested.emit(self._spec.name, value)


class CommandBar(QWidget):
    """Runs one command with a typed argument and shows what came back."""

    executeRequested = pyqtSignal(str, object)

    def __init__(
        self, tokens: Tokens, *, read_only: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._read_only = read_only
        self._spec: CommandSpec | None = None

        self.caption = QLabel("Command", self)
        self.caption.setStyleSheet(f"color: {tokens.ink_3};")
        self.name = QLabel("—", self)
        self.name.setFont(mono_font())
        self.editor = QLineEdit(self)
        self.editor.setFont(mono_font())
        self.editor.returnPressed.connect(self._submit)
        self.button = QPushButton("Execute", self)
        self.button.clicked.connect(self._submit)
        self.result = QLabel(self)
        self.result.setFont(mono_font())
        self.result.setWordWrap(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.caption)
        layout.addWidget(self.name)
        layout.addWidget(self.editor, 1)
        layout.addWidget(self.button)
        layout.addWidget(self.result, 2)
        self.set_target(None)

    def set_target(self, spec: CommandSpec | None) -> None:
        self._spec = spec
        runnable = spec is not None and not self._read_only
        takes_argument = spec is not None and spec.in_type is not TangoType.VOID
        self.name.setText(spec.name if spec else "—")
        self.button.setEnabled(runnable)
        self.editor.setEnabled(runnable and takes_argument)
        self.editor.clear()
        self.result.clear()
        if spec is None:
            self.editor.setPlaceholderText("select a command")
        elif self._read_only:
            self.editor.setPlaceholderText("session is read-only")
        elif takes_argument:
            self.editor.setPlaceholderText(spec.in_description or spec.in_type.value)
        else:
            self.editor.setPlaceholderText("takes no argument")

    def show_result(self, text: str, *, error: bool = False) -> None:
        colour = self._tokens.bad if error else self._tokens.ink_2
        self.result.setStyleSheet(f"color: {colour};")
        self.result.setText(text)

    def _submit(self) -> None:
        if self._spec is None or not self.button.isEnabled():
            return
        argin: object = None
        if self._spec.in_type is not TangoType.VOID:
            try:
                argin = parse_command_argument(self.editor.text(), self._spec.in_type)
            except ValueError as error:
                self.show_result(str(error), error=True)
                return
        self.result.clear()
        self.executeRequested.emit(self._spec.name, argin)
