"""What a Starter controls, as database mutations.

Starting and stopping a server acts on a live process and is not a command:
it has no database state to capture and no meaningful undo. Changing *what*
a Starter controls does, and goes through the same preview and journal.
"""

from dataclasses import dataclass, field

from milonga.core.backend.protocol import TangoBackend
from milonga.core.commands.base import Command, Diff, DiffKind, DiffLine
from milonga.core.model import ServerInfo
from milonga.core.names import ServerName


def _lines(owner: str, before: ServerInfo, after: ServerInfo) -> tuple[DiffLine, ...]:
    fields = (
        ("host", before.host, after.host),
        ("startup level", str(before.level), str(after.level)),
        ("controlled", _yes_no(before.controlled), _yes_no(after.controlled)),
    )
    return tuple(
        DiffLine(
            owner,
            name,
            DiffKind.UNCHANGED if old == new else DiffKind.CHANGED,
            (old,),
            (new,),
        )
        for name, old, new in fields
    )


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


@dataclass
class SetServerControl(Command):
    """Change a server's host, startup level or controlled flag."""

    server: ServerName
    host: str
    level: int
    controlled: bool = True
    _before: ServerInfo | None = field(default=None, init=False, repr=False)

    @property
    def summary(self) -> str:
        where = f"on {self.host}" if self.host else "unassigned"
        state = f"level {self.level}" if self.controlled and self.level else "not controlled"
        return f"{self.server} · {where}, {state}"

    @property
    def wanted(self) -> ServerInfo:
        return ServerInfo(self.server, self.host, self.level, self.controlled)

    async def preview(self, backend: TangoBackend) -> Diff:
        self._before = await backend.get_server_info(self.server)
        self._captured = True
        return Diff(_lines(str(self.server), self._before, self.wanted))

    async def apply(self, backend: TangoBackend) -> None:
        if not self._captured:
            await self.preview(backend)
        await backend.put_server_info(self.wanted)
        await self._notify(backend, self.wanted.host)

    async def revert(self, backend: TangoBackend) -> None:
        if self._before is None:
            return
        await backend.put_server_info(self._before)
        await self._notify(backend, self._before.host)

    async def _notify(self, backend: TangoBackend, host: str) -> None:
        """Both Starters have to re-read the database when a server moves."""
        from milonga.core.services.control import StarterControl

        control = StarterControl(backend)
        hosts = {host, self._before.host if self._before else ""} - {""}
        for name in hosts:
            await control.update_servers_info(name)
