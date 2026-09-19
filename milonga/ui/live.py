"""Live attribute values for one device.

Watches are held only while the view that needs them is on screen; the hub
keeps one subscription per attribute however many views ask for it.
"""

from collections.abc import Sequence

from PyQt6.QtCore import QObject, pyqtSignal

from milonga.core.enums import DataSource
from milonga.core.errors import ErrorReport
from milonga.core.model import AttributeValue, EventData
from milonga.core.monitor import Watch
from milonga.core.names import AttributeRef
from milonga.ui.context import AppContext


class LiveAttributes(QObject):
    """Owns the watches for a set of attributes and keeps their last value."""

    changed = pyqtSignal(object)

    def __init__(self, context: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._watches: dict[AttributeRef, Watch] = {}
        self._values: dict[AttributeRef, AttributeValue] = {}
        self._errors: dict[AttributeRef, ErrorReport] = {}
        self._pending: dict[AttributeRef, EventData] = {}
        self._paused = False

    @property
    def watched(self) -> frozenset[AttributeRef]:
        return frozenset(self._watches)

    @property
    def paused(self) -> bool:
        return self._paused

    def latest(self, ref: AttributeRef) -> AttributeValue | None:
        return self._values.get(ref)

    def error(self, ref: AttributeRef) -> ErrorReport | None:
        return self._errors.get(ref)

    def source(self, ref: AttributeRef) -> DataSource | None:
        watch = self._watches.get(ref)
        return watch.source if watch is not None else None

    async def watch(self, refs: Sequence[AttributeRef]) -> None:
        """Make the watched set exactly ``refs``."""
        wanted = set(refs)
        for ref in list(self._watches):
            if ref not in wanted:
                await self._watches.pop(ref).aclose()
        for ref in refs:
            if ref not in self._watches:
                self._watches[ref] = await self._context.monitor.watch(ref, self._received)

    async def release(self) -> None:
        for ref in list(self._watches):
            await self._watches.pop(ref).aclose()

    def set_paused(self, paused: bool) -> None:
        """While paused the display freezes; arriving values are kept for the resume."""
        self._paused = paused
        if paused:
            return
        pending, self._pending = self._pending, {}
        for event in pending.values():
            self._store(event)
            self.changed.emit(event.ref)

    def _received(self, event: EventData) -> None:
        if self._paused:
            self._pending[event.ref] = event
            return
        self._store(event)
        self.changed.emit(event.ref)

    def _store(self, event: EventData) -> None:
        if event.value is not None:
            self._values[event.ref] = event.value
            self._errors.pop(event.ref, None)
        elif event.error is not None:
            self._errors[event.ref] = event.error
