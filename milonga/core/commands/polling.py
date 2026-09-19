"""Polling configuration, as Jive's polling tab edits it."""

from dataclasses import dataclass, field

from milonga.core.backend.protocol import TangoBackend
from milonga.core.commands.base import Command, Diff, DiffKind, DiffLine
from milonga.core.enums import PollableKind
from milonga.core.model import PollingEntry
from milonga.core.names import DeviceName

STOP = 0


@dataclass
class SetPolling(Command):
    """A period of zero stops the polling of that attribute or command."""

    device: DeviceName
    name: str
    kind: PollableKind = PollableKind.ATTRIBUTE
    period_ms: int = 1000
    _before: PollingEntry | None = field(default=None, init=False, repr=False)

    @property
    def summary(self) -> str:
        if self.period_ms == STOP:
            return f"{self.device} · stop polling {self.name}"
        return f"{self.device} · poll {self.name} every {self.period_ms} ms"

    async def preview(self, backend: TangoBackend) -> Diff:
        entries = await backend.get_polling(self.device)
        self._before = next(
            (
                entry
                for entry in entries
                if entry.name == self.name and entry.kind is self.kind
            ),
            None,
        )
        self._captured = True
        before = (f"{self._before.period_ms} ms",) if self._before else ()
        after = (f"{self.period_ms} ms",) if self.period_ms else ()
        kind = DiffKind.CHANGED
        if not before:
            kind = DiffKind.ADDED
        elif not after:
            kind = DiffKind.REMOVED
        if before == after:
            kind = DiffKind.UNCHANGED
        return Diff((DiffLine(str(self.device), self.name, kind, before, after),))

    async def apply(self, backend: TangoBackend) -> None:
        if not self._captured:
            await self.preview(backend)
        await self._write(backend, self.period_ms)

    async def revert(self, backend: TangoBackend) -> None:
        await self._write(backend, self._before.period_ms if self._before else STOP)

    async def _write(self, backend: TangoBackend, period_ms: int) -> None:
        if period_ms == STOP:
            await backend.stop_polling(self.device, self.name, self.kind)
            return
        await backend.set_polling(self.device, self.name, self.kind, period_ms)
