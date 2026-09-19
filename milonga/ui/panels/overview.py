"""System overview: every controlled host at a glance.

Each card is one host; the strip under the name is one tick per controlled
server, so a mixed host is visible before reading a single number.
"""

from collections.abc import Sequence

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QHideEvent, QMouseEvent, QPainter, QShowEvent
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from milonga.core.enums import HostState, ServerRunState
from milonga.core.errors import ErrorReport
from milonga.core.model import HostSnapshot
from milonga.ui.context import AppContext, Target
from milonga.ui.live_hosts import LiveHosts
from milonga.ui.panels.base import Panel
from milonga.ui.theme import (
    Tokens,
    category_color,
    host_state_category,
    mono_font,
    run_state_category,
)
from milonga.ui.widgets import StateChip

CARD_WIDTH = 260
TICK_WIDTH = 8
TICK_GAP = 2
TICK_HEIGHT = 15


def levels_text(levels: Sequence[int]) -> str:
    """``L1–3`` for a contiguous run, ``L1, 2, 5`` otherwise."""
    if not levels:
        return "not controlled"
    if len(levels) > 2 and list(levels) == list(range(levels[0], levels[-1] + 1)):
        return f"L{levels[0]}–{levels[-1]}"
    return "L" + ", ".join(str(level) for level in levels)


class ServerStrip(QWidget):
    """One tick per server, coloured by run state."""

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._states: tuple[ServerRunState, ...] = ()
        self.setMinimumHeight(TICK_HEIGHT)

    def set_states(self, states: Sequence[ServerRunState]) -> None:
        self._states = tuple(states)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(len(self._states) * (TICK_WIDTH + TICK_GAP), TICK_HEIGHT)

    def paintEvent(self, a0: object) -> None:
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        x = 0
        for state in self._states:
            category = run_state_category(state)
            painter.setBrush(category_color(self._tokens, category))
            painter.drawRoundedRect(x, 0, TICK_WIDTH, TICK_HEIGHT, 2, 2)
            x += TICK_WIDTH + TICK_GAP
            if x > self.width():
                break
        painter.end()


class HostCard(QFrame):
    activated = pyqtSignal(str)

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._host = ""
        self.setObjectName("hostCard")
        self._apply_border(tokens.line)
        self.name = QLabel(self)
        font = mono_font()
        font.setBold(True)
        self.name.setFont(font)
        self.chip = StateChip(tokens, self)
        self.group = QLabel(self)
        self.group.setStyleSheet(f"color: {tokens.ink_3};")
        self.strip = ServerStrip(tokens, self)
        self.counts = QLabel(self)
        self.counts.setStyleSheet(f"color: {tokens.ink_3};")
        self.levels = QLabel(self)
        self.levels.setStyleSheet(f"color: {tokens.ink_3};")

        # real host names run long; the name gets its own line, the chip the next
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.group)
        head.addStretch(1)
        head.addWidget(self.chip)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.counts)
        footer.addStretch(1)
        footer.addWidget(self.levels)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 10, 11, 10)
        layout.setSpacing(7)
        layout.addWidget(self.name)
        layout.addLayout(head)
        layout.addWidget(self.strip)
        layout.addLayout(footer)

    def _apply_border(self, colour: str) -> None:
        # labels inherit the window ground otherwise, banding the card
        self.setStyleSheet(
            f"#hostCard {{ background: {self._tokens.panel}; border: 1px solid {colour};"
            " border-radius: 7px; }"
            "#hostCard QLabel { background: transparent; }"
        )

    @property
    def host(self) -> str:
        return self._host

    def set_snapshot(self, snapshot: HostSnapshot) -> None:
        self._host = snapshot.name
        self.name.setText(snapshot.name)
        self.name.setToolTip(snapshot.name)
        self.chip.set_state(snapshot.state.value, host_state_category(snapshot.state))
        self.group.setText(snapshot.group or "—")
        self.strip.set_states([server.run_state for server in snapshot.servers])
        if snapshot.error is not None:
            self.counts.setText("Starter does not answer")
            self.counts.setToolTip(snapshot.error.message)
            self.levels.setText("")
        else:
            stopped = snapshot.stopped_count
            text = f"{snapshot.running_count} running"
            self.counts.setText(f"{text} · {stopped} stopped" if stopped else text)
            self.counts.setToolTip("")
            self.levels.setText(levels_text(snapshot.levels))
            if snapshot.state is HostState.IDLE:
                self.counts.setText("nothing to control")
        self._apply_border(
            self._tokens.bad
            if snapshot.state is HostState.UNREACHABLE
            else self._tokens.line
        )

    def mouseDoubleClickEvent(self, a0: QMouseEvent | None) -> None:
        super().mouseDoubleClickEvent(a0)
        if self._host:
            self.activated.emit(self._host)


class OverviewPanel(Panel):
    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        target: Target,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(context, tokens, target, parent)
        self.live = LiveHosts(context, self)
        self.live.changed.connect(self._host_changed)
        self._cards: dict[str, HostCard] = {}
        self._columns = 0

        self.summary = QLabel(self)
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.refresh)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(self.summary)
        controls.addStretch(1)
        controls.addWidget(self.refresh_button)

        self.canvas = QWidget(self)
        self.grid = QGridLayout(self.canvas)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidget(self.canvas)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        layout = self.base_layout()
        layout.addLayout(controls)
        layout.addWidget(self.scroll_area, 1)
        self.header.set_header("System", context.tango_host)
        self.header.chip.setVisible(False)
        self._update_summary()

    @property
    def title(self) -> str:
        return "System"

    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        self.refresh()

    def hideEvent(self, a0: QHideEvent | None) -> None:
        super().hideEvent(a0)
        self.runner.run(self.live.release(), label="release host watches")

    async def aclose(self) -> None:
        await self.live.release()

    def refresh(self) -> None:
        self.banner.clear()
        self.runner.run(
            self._load(), on_result=lambda _: self._rebuild(), on_error=self._failed
        )

    async def _load(self) -> None:
        await self.live.watch(await self.context.control.controlled_hosts())

    # ------------------------------------------------------------------- display

    def _rebuild(self) -> None:
        for snapshot in self.live.snapshots():
            self._card(snapshot.name).set_snapshot(snapshot)
        self._reflow(force=True)
        self._update_summary()

    def _card(self, host: str) -> HostCard:
        card = self._cards.get(host)
        if card is None:
            card = HostCard(self.tokens, self.canvas)
            card.activated.connect(self._open_host)
            card.setFixedWidth(CARD_WIDTH)
            self._cards[host] = card
        return card

    def _host_changed(self, host: str) -> None:
        snapshot = self.live.snapshot(host)
        if snapshot is None:
            return
        known = host in self._cards
        self._card(host).set_snapshot(snapshot)
        if not known:
            self._reflow(force=True)
        self._update_summary()

    def _reflow(self, *, force: bool = False) -> None:
        viewport = self.scroll_area.viewport()
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
        for position, host in enumerate(sorted(self._cards)):
            self.grid.addWidget(self._cards[host], position // columns, position % columns)
            self._cards[host].show()

    def resizeEvent(self, a0: object) -> None:
        super().resizeEvent(a0)  # type: ignore[arg-type]
        self._reflow()

    def _update_summary(self) -> None:
        snapshots = self.live.snapshots()
        running = sum(snapshot.running_count for snapshot in snapshots)
        stopped = sum(snapshot.stopped_count for snapshot in snapshots)
        unreachable = sum(
            1 for snapshot in snapshots if snapshot.state is HostState.UNREACHABLE
        )
        parts = [
            f"{len(snapshots)} host" + ("" if len(snapshots) == 1 else "s"),
            f"{running} servers running",
            f"{stopped} stopped",
        ]
        if unreachable:
            parts.append(f"{unreachable} Starter unreachable")
        self.summary.setText("   ·   ".join(parts))

    def _open_host(self, host: str) -> None:
        self.context.open_target(Target.host(host))

    def _failed(self, report: ErrorReport) -> None:
        self.banner.show_error(report, "system")
