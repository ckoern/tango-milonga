"""Small shared widgets: state chips, section headings, error banners."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from milonga.core.enums import StateCategory
from milonga.core.errors import ErrorReport
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
