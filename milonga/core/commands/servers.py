"""What a Starter controls, as database mutations.

Starting and stopping a server acts on a live process and is not a command:
it has no database state to capture and no meaningful undo. Changing *what*
a Starter controls does, and goes through the same preview and journal.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from milonga.core.backend.protocol import TangoBackend
from milonga.core.commands.base import Command, Diff, DiffKind, DiffLine
from milonga.core.errors import TangoError
from milonga.core.model import DeviceRegistration, ServerInfo
from milonga.core.names import ServerName

if TYPE_CHECKING:
    from milonga.core.services.control import StarterControl


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
    """Change a server's startup level or controlled flag.

    The host written here is the server record's. It does not move the server
    to another Starter: a Starter controls the servers that last ran on its
    host, so moving means starting the server there.
    """

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
        hosts = {host, self._before.host if self._before else ""} - {""}
        for name in hosts:
            await _control(backend).update_servers_info(name)


@dataclass
class CreateServer(Command):
    """Register a server with its first devices, and what controls it."""

    server: ServerName
    devices: tuple[DeviceRegistration, ...]
    host: str = ""
    level: int = 0

    @property
    def summary(self) -> str:
        return f"create server {self.server} with {len(self.devices)} device(s)"

    async def preview(self, backend: TangoBackend) -> Diff:
        if self.server in await backend.get_server_list():
            raise TangoError(f"server {self.server} already exists")
        self._captured = True
        owner = str(self.server)
        lines = [
            DiffLine(owner, "server", DiffKind.ADDED, (), (f"on {self.host or 'no host'}",))
        ]
        lines.extend(
            DiffLine(owner, str(device.name), DiffKind.ADDED, (), (device.class_name,))
            for device in self.devices
        )
        if self.host:
            lines.append(
                DiffLine(owner, "startup level", DiffKind.ADDED, (), (str(self.level),))
            )
        return Diff(tuple(lines))

    async def apply(self, backend: TangoBackend) -> None:
        await backend.add_server(self.server, self.devices)
        if self.host:
            await backend.put_server_info(
                ServerInfo(self.server, self.host, self.level, self.level > 0)
            )
            await _control(backend).update_servers_info(self.host)

    async def revert(self, backend: TangoBackend) -> None:
        await backend.delete_server(self.server)
        if self.host:
            await _control(backend).update_servers_info(self.host)


@dataclass
class DeleteServer(Command):
    """Remove a server and every device it served."""

    server: ServerName
    _info: ServerInfo | None = field(default=None, init=False, repr=False)
    _devices: tuple[DeviceRegistration, ...] = field(default=(), init=False, repr=False)

    @property
    def summary(self) -> str:
        return f"delete server {self.server}"

    @property
    def destructive(self) -> bool:
        return True

    @property
    def confirmation_name(self) -> str | None:
        return str(self.server)

    async def preview(self, backend: TangoBackend) -> Diff:
        self._info = await backend.get_server_info(self.server)
        devices = await backend.get_device_list_for_server(self.server)
        registrations = []
        for device in devices:
            info = await backend.get_device_info(device)
            registrations.append(DeviceRegistration(device, info.class_name, self.server))
        self._devices = tuple(registrations)
        self._captured = True
        owner = str(self.server)
        lines = [DiffLine(owner, "server", DiffKind.REMOVED, (self._info.host or "no host",), ())]
        lines.extend(
            DiffLine(owner, str(device.name), DiffKind.REMOVED, (device.class_name,), ())
            for device in self._devices
        )
        return Diff(tuple(lines))

    async def apply(self, backend: TangoBackend) -> None:
        if not self._captured:
            await self.preview(backend)
        await backend.delete_server(self.server)
        if self._info and self._info.host:
            await _control(backend).update_servers_info(self._info.host)

    async def revert(self, backend: TangoBackend) -> None:
        if self._info is None:
            return
        await backend.add_server(self.server, self._devices)
        await backend.put_server_info(self._info)
        if self._info.host:
            await _control(backend).update_servers_info(self._info.host)


@dataclass
class RenameServer(Command):
    old: ServerName
    new: ServerName

    @property
    def summary(self) -> str:
        return f"rename server {self.old} to {self.new}"

    async def preview(self, backend: TangoBackend) -> Diff:
        known = await backend.get_server_list()
        if self.old not in known:
            raise TangoError(f"no server {self.old}")
        if self.new in known:
            raise TangoError(f"server {self.new} already exists")
        self._captured = True
        return Diff(
            (DiffLine(str(self.old), "name", DiffKind.CHANGED, (str(self.old),), (str(self.new),)),)
        )

    async def apply(self, backend: TangoBackend) -> None:
        await backend.rename_server(self.old, self.new)

    async def revert(self, backend: TangoBackend) -> None:
        await backend.rename_server(self.new, self.old)


def _control(backend: TangoBackend) -> "StarterControl":
    # imported here because the control service builds on these commands
    from milonga.core.services.control import StarterControl

    return StarterControl(backend)
