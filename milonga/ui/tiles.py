"""Tiles: a grid of cards, one per object, that says how each one is.

The system overview is one of these; so is the view behind double-clicking a
branch of a tree.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QContextMenuEvent, QMouseEvent, QPainter
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from milonga.core.enums import StateCategory
from milonga.ui.theme import Tokens, category_color, mono_font, set_role
from milonga.ui.widgets import StateChip

CARD_WIDTH = 260
TICK_WIDTH = 8
TICK_GAP = 2
TICK_HEIGHT = 15
MAX_MARKS = 60


@dataclass(frozen=True, slots=True)
class Tile:
    """What one card shows. ``key`` identifies it across refreshes."""

    key: str
    title: str
    subtitle: str = ""
    chip: str = ""
    category: StateCategory | None = None
    marks: tuple[StateCategory, ...] = field(default_factory=tuple)
    footer_left: str = ""
    footer_right: str = ""
    tooltip: str = ""
    alert: bool = False


class MarkStrip(QWidget):
    """One tick per member, coloured by its state."""

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._marks: tuple[StateCategory, ...] = ()
        self.setMinimumHeight(TICK_HEIGHT)

    def set_marks(self, marks: Sequence[StateCategory]) -> None:
        self._marks = tuple(marks[:MAX_MARKS])
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(len(self._marks) * (TICK_WIDTH + TICK_GAP), TICK_HEIGHT)

    def paintEvent(self, a0: object) -> None:
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        x = 0
        for mark in self._marks:
            painter.setBrush(category_color(self._tokens, mark))
            painter.drawRoundedRect(x, 0, TICK_WIDTH, TICK_HEIGHT, 2, 2)
            x += TICK_WIDTH + TICK_GAP
            if x > self.width():
                break
        painter.end()


class TileCard(QFrame):
    activated = pyqtSignal(str)
    menuRequested = pyqtSignal(str, QPoint)

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = ""
        self.setObjectName("hostCard")
        self.title = QLabel(self)
        font = mono_font()
        font.setBold(True)
        self.title.setFont(font)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.chip = StateChip(tokens, self)
        self.subtitle = QLabel(self)
        set_role(self.subtitle, "role", "muted")
        self.strip = MarkStrip(tokens, self)
        self.left = QLabel(self)
        set_role(self.left, "role", "muted")
        self.right = QLabel(self)
        set_role(self.right, "role", "muted")

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.subtitle)
        head.addStretch(1)
        head.addWidget(self.chip)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.left)
        footer.addStretch(1)
        footer.addWidget(self.right)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 10, 11, 10)
        layout.setSpacing(7)
        layout.addWidget(self.title)
        layout.addLayout(head)
        layout.addWidget(self.strip)
        layout.addLayout(footer)

    @property
    def key(self) -> str:
        return self._key

    def set_tile(self, tile: Tile) -> None:
        self._key = tile.key
        self.title.setText(tile.title)
        self.title.setToolTip(tile.tooltip or tile.title)
        self.subtitle.setText(tile.subtitle or "—")
        self.chip.setVisible(bool(tile.chip))
        if tile.chip:
            self.chip.set_state(tile.chip, tile.category or StateCategory.UNKNOWN)
        else:
            self.chip.setText("")
        self.strip.set_marks(tile.marks)
        self.strip.setVisible(bool(tile.marks))
        self.left.setText(tile.footer_left)
        self.left.setToolTip(tile.tooltip)
        self.right.setText(tile.footer_right)
        set_role(self, "alert", "true" if tile.alert else "false")

    def contextMenuEvent(self, a0: QContextMenuEvent | None) -> None:
        if a0 is not None and self._key:
            self.menuRequested.emit(self._key, a0.pos())

    def mouseDoubleClickEvent(self, a0: QMouseEvent | None) -> None:
        super().mouseDoubleClickEvent(a0)
        if self._key:
            self.activated.emit(self._key)


class TileGrid(QScrollArea):
    """Cards in a grid that reflows to the width it is given."""

    activated = pyqtSignal(str)
    menuRequested = pyqtSignal(str, QPoint)

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._cards: dict[str, TileCard] = {}
        self._columns = 0
        self.canvas = QWidget(self)
        self.grid = QGridLayout(self.canvas)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.canvas)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

    @property
    def cards(self) -> dict[str, TileCard]:
        return self._cards

    def card(self, key: str) -> TileCard | None:
        return self._cards.get(key)

    def set_tiles(self, tiles: Sequence[Tile]) -> None:
        wanted = [tile.key for tile in tiles]
        for key in list(self._cards):
            if key not in wanted:
                card = self._cards.pop(key)
                card.setParent(None)
                card.deleteLater()
        for tile in tiles:
            self._card(tile.key).set_tile(tile)
        self._reflow(order=wanted, force=True)

    def update_tile(self, tile: Tile) -> None:
        known = tile.key in self._cards
        self._card(tile.key).set_tile(tile)
        if not known:
            self._reflow(force=True)

    def _card(self, key: str) -> TileCard:
        card = self._cards.get(key)
        if card is None:
            card = TileCard(self._tokens, self.canvas)
            card.setFixedWidth(CARD_WIDTH)
            card.activated.connect(self.activated)
            card.menuRequested.connect(self.menuRequested)
            self._cards[key] = card
        return card

    def _reflow(self, *, order: Sequence[str] | None = None, force: bool = False) -> None:
        viewport = self.viewport()
        width = viewport.width() if viewport is not None else CARD_WIDTH
        columns = max(1, (width or CARD_WIDTH) // (CARD_WIDTH + 10))
        if columns == self._columns and not force:
            return
        self._columns = columns
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(self.canvas)
        for position, key in enumerate(order or sorted(self._cards)):
            card = self._cards[key]
            self.grid.addWidget(card, position // columns, position % columns)
            card.show()

    def resizeEvent(self, a0: object) -> None:
        super().resizeEvent(a0)  # type: ignore[arg-type]
        self._reflow()
