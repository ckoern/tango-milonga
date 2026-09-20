"""System overview: every controlled host at a glance.

One tile per host; the strip under the name is one tick per controlled
server, so a mixed host is visible before reading a single number.
"""

from collections.abc import Sequence

from PySide6.QtCore import QPoint
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from milonga.core.enums import HostState
from milonga.core.errors import ErrorReport
from milonga.core.model import HostSnapshot
from milonga.ui.context import AppContext, Target
from milonga.ui.live_hosts import LiveHosts
from milonga.ui.menus import SEPARATOR, MenuEntry, MenuItems, popup
from milonga.ui.panels.base import Panel
from milonga.ui.process import ProcessActions
from milonga.ui.theme import Tokens, host_state_category, run_state_category
from milonga.ui.tiles import Tile, TileGrid


def levels_text(levels: Sequence[int]) -> str:
    """``L1–3`` for a contiguous run, ``L1, 2, 5`` otherwise."""
    if not levels:
        return "not controlled"
    if len(levels) > 2 and list(levels) == list(range(levels[0], levels[-1] + 1)):
        return f"L{levels[0]}–{levels[-1]}"
    return "L" + ", ".join(str(level) for level in levels)


def host_tile(snapshot: HostSnapshot) -> Tile:
    if snapshot.error is not None:
        return Tile(
            key=snapshot.name,
            title=snapshot.name,
            subtitle=snapshot.group or "—",
            chip=snapshot.state.value,
            category=host_state_category(snapshot.state),
            footer_left="Starter does not answer",
            tooltip=snapshot.error.message,
            alert=True,
        )
    stopped = snapshot.stopped_count
    running = f"{snapshot.running_count} running"
    return Tile(
        key=snapshot.name,
        title=snapshot.name,
        subtitle=snapshot.group or "—",
        chip=snapshot.state.value,
        category=host_state_category(snapshot.state),
        marks=tuple(run_state_category(server.run_state) for server in snapshot.servers),
        footer_left=(
            "nothing to control"
            if snapshot.state is HostState.IDLE
            else (f"{running} · {stopped} stopped" if stopped else running)
        ),
        footer_right=levels_text(snapshot.levels),
    )


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
        self.process = ProcessActions(context, self.runner, self)
        self.process.failed.connect(self._failed)

        self.summary = QLabel(self)
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.refresh)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(self.summary)
        controls.addStretch(1)
        controls.addWidget(self.refresh_button)

        self.grid = TileGrid(tokens, self)
        self.grid.activated.connect(self._open_host)
        self.grid.menuRequested.connect(self._menu)

        layout = self.base_layout()
        layout.addLayout(controls)
        layout.addWidget(self.grid, 1)
        self.header.set_header("System", context.tango_host)
        self.header.chip.setVisible(False)
        self._update_summary()

    @property
    def title(self) -> str:
        return "System"

    @property
    def cards(self) -> dict[str, object]:
        return dict(self.grid.cards)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.refresh()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
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
        self.grid.set_tiles([host_tile(snapshot) for snapshot in self.live.snapshots()])
        self._update_summary()

    def _host_changed(self, host: str) -> None:
        snapshot = self.live.snapshot(host)
        if snapshot is not None:
            self.grid.update_tile(host_tile(snapshot))
            self._update_summary()

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

    # ---------------------------------------------------------------- right click

    def card_items(self, host: str) -> MenuItems:
        snapshot = self.live.snapshot(host)
        writable = self.process.enabled and snapshot is not None and snapshot.error is None
        items: list[MenuEntry | None] = [
            MenuEntry("Open host panel", lambda: self._open_host(host)),
        ]
        if snapshot is not None:
            items += [
                SEPARATOR,
                MenuEntry("Start all levels", lambda: self.process.start_all(snapshot), writable),
                MenuEntry("Stop all levels", lambda: self.process.stop_all(snapshot), writable),
            ]
        return items

    def _menu(self, host: str, point: QPoint) -> None:
        card = self.grid.card(host)
        if card is not None:
            popup(card, point, self.card_items(host))

    def _open_host(self, host: str) -> None:
        self.context.open_target(Target.host(host))

    def _failed(self, report: ErrorReport) -> None:
        self.banner.show_error(report, "system")
