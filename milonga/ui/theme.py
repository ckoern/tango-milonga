"""Design tokens and the stylesheet built from them."""

from dataclasses import dataclass
from enum import StrEnum

from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PyQt6.QtWidgets import QApplication

from milonga.core.enums import (
    AttrQuality,
    HostState,
    ServerRunState,
    StateCategory,
    TangoState,
)


class Theme(StrEnum):
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True, slots=True)
class Tokens:
    ink: str
    ink_2: str
    ink_3: str
    ground: str
    panel: str
    panel_2: str
    panel_3: str
    line: str
    line_2: str
    accent: str
    accent_soft: str
    accent_ink: str
    ok: str
    ok_soft: str
    busy: str
    busy_soft: str
    warn: str
    warn_soft: str
    bad: str
    bad_soft: str
    idle: str
    idle_soft: str


LIGHT = Tokens(
    ink="#15131d",
    ink_2="#403b51",
    ink_3="#6f6a80",
    ground="#f4f3f8",
    panel="#ffffff",
    panel_2="#edebf3",
    panel_3="#e4e1ed",
    line="#dcd8e8",
    line_2="#c5bfd6",
    accent="#6141d2",
    accent_soft="#ebe6fb",
    accent_ink="#ffffff",
    ok="#158054",
    ok_soft="#dff0e7",
    busy="#0c7f9e",
    busy_soft="#daeef4",
    warn="#a76c07",
    warn_soft="#f8eed6",
    bad="#c33d30",
    bad_soft="#fae4e1",
    idle="#857f96",
    idle_soft="#e9e7ef",
)

DARK = Tokens(
    ink="#e9e7f2",
    ink_2="#b5b0c6",
    ink_3="#847e97",
    ground="#0d0c12",
    panel="#17161f",
    panel_2="#1f1d2a",
    panel_3="#272434",
    line="#2b2839",
    line_2="#3c3850",
    accent="#a78fff",
    accent_soft="#282040",
    accent_ink="#14101f",
    ok="#46c186",
    ok_soft="#14301f",
    busy="#3cb6d6",
    busy_soft="#0d2a33",
    warn="#d6a02f",
    warn_soft="#332608",
    bad="#ef6a5b",
    bad_soft="#371814",
    idle="#8d879f",
    idle_soft="#242232",
)

TOKENS: dict[Theme, Tokens] = {Theme.LIGHT: LIGHT, Theme.DARK: DARK}

_CATEGORY_TOKEN: dict[StateCategory, str] = {
    StateCategory.NOMINAL: "ok",
    StateCategory.BUSY: "busy",
    StateCategory.WARNING: "warn",
    StateCategory.FAULT: "bad",
    StateCategory.INACTIVE: "idle",
    StateCategory.UNKNOWN: "idle",
}

_RUN_STATE_CATEGORY: dict[ServerRunState, StateCategory] = {
    ServerRunState.RUNNING: StateCategory.NOMINAL,
    ServerRunState.STARTING: StateCategory.BUSY,
    ServerRunState.NOT_RESPONDING: StateCategory.FAULT,
    ServerRunState.STOPPED: StateCategory.FAULT,
    ServerRunState.UNKNOWN: StateCategory.UNKNOWN,
}

_HOST_STATE_CATEGORY: dict[HostState, StateCategory] = {
    HostState.ALL_RUNNING: StateCategory.NOMINAL,
    HostState.STARTING: StateCategory.BUSY,
    HostState.MIXED: StateCategory.WARNING,
    HostState.ALL_STOPPED: StateCategory.INACTIVE,
    HostState.UNREACHABLE: StateCategory.FAULT,
}

_QUALITY_CATEGORY: dict[AttrQuality, StateCategory] = {
    AttrQuality.VALID: StateCategory.NOMINAL,
    AttrQuality.CHANGING: StateCategory.BUSY,
    AttrQuality.WARNING: StateCategory.WARNING,
    AttrQuality.ALARM: StateCategory.FAULT,
    AttrQuality.INVALID: StateCategory.UNKNOWN,
}


def category_color(tokens: Tokens, category: StateCategory) -> QColor:
    return QColor(getattr(tokens, _CATEGORY_TOKEN[category]))


def category_background(tokens: Tokens, category: StateCategory) -> QColor:
    return QColor(getattr(tokens, f"{_CATEGORY_TOKEN[category]}_soft"))


def run_state_category(state: ServerRunState) -> StateCategory:
    return _RUN_STATE_CATEGORY[state]


def host_state_category(state: HostState) -> StateCategory:
    return _HOST_STATE_CATEGORY[state]


def device_state_category(state: TangoState) -> StateCategory:
    return state.category


def quality_category(quality: AttrQuality) -> StateCategory:
    return _QUALITY_CATEGORY[quality]


def ui_font_family() -> str:
    return _first_available(("IBM Plex Sans", "Inter", "Noto Sans", "DejaVu Sans"))


def mono_font_family() -> str:
    return _first_available(("IBM Plex Mono", "JetBrains Mono", "DejaVu Sans Mono", "Monospace"))


def mono_font(point_size: int = 0) -> QFont:
    font = QFont(mono_font_family())
    font.setStyleHint(QFont.StyleHint.Monospace)
    if point_size:
        font.setPointSize(point_size)
    return font


def _first_available(candidates: tuple[str, ...]) -> str:
    families = set(QFontDatabase.families())
    for candidate in candidates:
        if candidate in families:
            return candidate
    return candidates[-1]


def stylesheet(tokens: Tokens) -> str:
    return f"""
QWidget {{
    background: {tokens.ground};
    color: {tokens.ink};
    font-family: "{ui_font_family()}";
    font-size: 10pt;
}}
QMainWindow::separator {{ background: {tokens.line}; width: 1px; height: 1px; }}
QToolBar {{
    background: {tokens.panel};
    border-bottom: 1px solid {tokens.line};
    padding: 4px 6px;
    spacing: 6px;
}}
QToolBar QToolButton {{ padding: 4px 8px; border-radius: 5px; color: {tokens.ink_2}; }}
QToolBar QToolButton:hover {{ background: {tokens.panel_2}; color: {tokens.ink}; }}
QStatusBar {{
    background: {tokens.panel_2};
    border-top: 1px solid {tokens.line};
    color: {tokens.ink_3};
}}
QStatusBar::item {{ border: none; }}
QDockWidget {{ color: {tokens.ink_3}; titlebar-close-icon: none; }}
QDockWidget::title {{
    background: {tokens.panel_2};
    border-bottom: 1px solid {tokens.line};
    padding: 5px 8px;
    text-transform: uppercase;
}}
QTreeView, QTableView, QListView {{
    background: {tokens.panel};
    alternate-background-color: {tokens.panel_2};
    border: 1px solid {tokens.line};
    selection-background-color: {tokens.accent_soft};
    selection-color: {tokens.accent};
    outline: none;
}}
QTreeView::item, QTableView::item, QListView::item {{ padding: 3px 2px; }}
QTreeView::item:hover, QTableView::item:hover {{ background: {tokens.panel_2}; }}
QHeaderView::section {{
    background: {tokens.panel_2};
    color: {tokens.ink_3};
    border: none;
    border-bottom: 1px solid {tokens.line};
    border-right: 1px solid {tokens.line};
    padding: 5px 8px;
    font-weight: 600;
}}
QTabWidget::pane {{ border: none; border-top: 1px solid {tokens.line}; }}
QTabBar::tab {{
    background: {tokens.panel_2};
    color: {tokens.ink_3};
    border-right: 1px solid {tokens.line};
    padding: 6px 14px;
}}
QTabBar::tab:selected {{
    background: {tokens.ground};
    color: {tokens.ink};
    border-top: 2px solid {tokens.accent};
    font-weight: 600;
}}
QTabBar::close-button {{ subcontrol-position: right; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {tokens.panel};
    border: 1px solid {tokens.line};
    border-radius: 5px;
    padding: 4px 7px;
    selection-background-color: {tokens.accent_soft};
    selection-color: {tokens.accent};
}}
QLineEdit:focus, QComboBox:focus {{ border-color: {tokens.accent}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QPushButton {{
    background: {tokens.panel};
    border: 1px solid {tokens.line};
    border-radius: 5px;
    padding: 4px 12px;
    color: {tokens.ink_2};
}}
QPushButton:hover {{ background: {tokens.panel_2}; color: {tokens.ink}; }}
QPushButton:default {{
    background: {tokens.accent};
    border-color: {tokens.accent};
    color: {tokens.accent_ink};
    font-weight: 600;
}}
QScrollBar:vertical, QScrollBar:horizontal {{ background: transparent; width: 10px; height: 10px; }}
QScrollBar::handle {{ background: {tokens.line_2}; border-radius: 5px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: {tokens.line}; }}
QToolTip {{
    background: {tokens.panel_3};
    color: {tokens.ink};
    border: 1px solid {tokens.line_2};
    padding: 4px 6px;
}}
"""


def apply_theme(app: QApplication, theme: Theme) -> Tokens:
    tokens = TOKENS[theme]
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(tokens.ground))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(tokens.ink))
    palette.setColor(QPalette.ColorRole.Base, QColor(tokens.panel))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(tokens.panel_2))
    palette.setColor(QPalette.ColorRole.Text, QColor(tokens.ink))
    palette.setColor(QPalette.ColorRole.Button, QColor(tokens.panel))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(tokens.ink_2))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(tokens.accent_soft))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(tokens.accent))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(tokens.panel_3))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(tokens.ink))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(tokens.ink_3))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(tokens))
    return tokens
