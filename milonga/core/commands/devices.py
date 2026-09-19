"""Creating, renaming and removing devices."""

from contextlib import suppress
from dataclasses import dataclass, field

from milonga.core.backend.protocol import TangoBackend
from milonga.core.commands.base import Command, Diff, DiffKind, DiffLine
from milonga.core.commands.properties import PropertyGateway, PropertyTarget
from milonga.core.errors import ObjectNotFound, TangoError
from milonga.core.model import DeviceRegistration, PropertyEntry
from milonga.core.names import DeviceName


async def _exists(backend: TangoBackend, device: DeviceName) -> bool:
    try:
        await backend.get_device_info(device)
    except TangoError:
        return False
    return True


@dataclass
class CreateDevice(Command):
    """Add a device to a server that already exists."""

    registration: DeviceRegistration

    @property
    def summary(self) -> str:
        return (
            f"add {self.registration.name} "
            f"({self.registration.class_name}) to {self.registration.server}"
        )

    async def preview(self, backend: TangoBackend) -> Diff:
        self._captured = True
        name = str(self.registration.name)
        if await _exists(backend, self.registration.name):
            raise TangoError(f"device {name} already exists")
        return Diff(
            (
                DiffLine(
                    name,
                    "device",
                    DiffKind.ADDED,
                    (),
                    (f"{self.registration.class_name} in {self.registration.server}",),
                ),
            )
        )

    async def apply(self, backend: TangoBackend) -> None:
        await backend.add_device(self.registration)

    async def revert(self, backend: TangoBackend) -> None:
        await backend.delete_device(self.registration.name)


@dataclass
class DeleteDevice(Command):
    """Remove a device and everything the database holds about it."""

    device: DeviceName
    _registration: DeviceRegistration | None = field(default=None, init=False, repr=False)
    _properties: tuple[PropertyEntry, ...] = field(default=(), init=False, repr=False)
    _alias: str | None = field(default=None, init=False, repr=False)

    @property
    def summary(self) -> str:
        return f"delete device {self.device}"

    @property
    def destructive(self) -> bool:
        return True

    @property
    def confirmation_name(self) -> str | None:
        return str(self.device)

    async def preview(self, backend: TangoBackend) -> Diff:
        info = await backend.get_device_info(self.device)
        self._registration = DeviceRegistration(self.device, info.class_name, info.server)
        self._properties = await PropertyGateway(backend).read(
            PropertyTarget.device(self.device)
        )
        self._alias = info.alias
        self._captured = True
        name = str(self.device)
        lines = [
            DiffLine(name, "device", DiffKind.REMOVED, (f"{info.class_name} in {info.server}",), ())
        ]
        lines.extend(
            DiffLine(name, entry.name, DiffKind.REMOVED, tuple(entry.values), ())
            for entry in self._properties
        )
        if info.alias:
            lines.append(DiffLine(name, "alias", DiffKind.REMOVED, (info.alias,), ()))
        return Diff(tuple(lines))

    async def apply(self, backend: TangoBackend) -> None:
        if not self._captured:
            await self.preview(backend)
        await backend.delete_device(self.device)

    async def revert(self, backend: TangoBackend) -> None:
        if self._registration is None:
            return
        await backend.add_device(self._registration)
        await PropertyGateway(backend).write(
            PropertyTarget.device(self.device), self._properties
        )
        if self._alias:
            await backend.put_device_alias(self.device, self._alias)


@dataclass
class RenameDevice(Command):
    old: DeviceName
    new: DeviceName

    @property
    def summary(self) -> str:
        return f"rename device {self.old} to {self.new}"

    async def preview(self, backend: TangoBackend) -> Diff:
        await backend.get_device_info(self.old)
        if await _exists(backend, self.new):
            raise TangoError(f"device {self.new} already exists")
        self._captured = True
        return Diff(
            (DiffLine(str(self.old), "name", DiffKind.CHANGED, (str(self.old),), (str(self.new),)),)
        )

    async def apply(self, backend: TangoBackend) -> None:
        await backend.rename_device(self.old, self.new)

    async def revert(self, backend: TangoBackend) -> None:
        await backend.rename_device(self.new, self.old)


@dataclass
class SetDeviceAlias(Command):
    """An empty alias removes the one that is there."""

    device: DeviceName
    alias: str
    _before: str | None = field(default=None, init=False, repr=False)

    @property
    def summary(self) -> str:
        if self.alias:
            return f"alias {self.device} = {self.alias}"
        return f"drop alias of {self.device}"

    async def preview(self, backend: TangoBackend) -> Diff:
        self._before = await backend.get_alias_from_device(self.device)
        self._captured = True
        before = (self._before,) if self._before else ()
        after = (self.alias,) if self.alias else ()
        kind = DiffKind.CHANGED
        if not before:
            kind = DiffKind.ADDED
        elif not after:
            kind = DiffKind.REMOVED
        return Diff((DiffLine(str(self.device), "alias", kind, before, after),))

    async def apply(self, backend: TangoBackend) -> None:
        if not self._captured:
            await self.preview(backend)
        await self._write(backend, self.alias, self._before)

    async def revert(self, backend: TangoBackend) -> None:
        await self._write(backend, self._before or "", self.alias)

    async def _write(self, backend: TangoBackend, alias: str, previous: str | None) -> None:
        if previous:
            with suppress(ObjectNotFound):
                await backend.delete_device_alias(previous)
        if alias:
            await backend.put_device_alias(self.device, alias)
