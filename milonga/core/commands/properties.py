"""Property editing for the three scopes Jive exposes."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from milonga.core.backend.protocol import TangoBackend
from milonga.core.commands.base import Command, Diff, DiffKind, DiffLine
from milonga.core.enums import PropertyScope
from milonga.core.model import PropertyEntry, PropertyHistoryEntry, PropertyValues
from milonga.core.names import DeviceName


@dataclass(frozen=True, slots=True)
class PropertyTarget:
    """Whose properties are being edited."""

    scope: PropertyScope
    owner: str

    @classmethod
    def device(cls, device: DeviceName | str) -> "PropertyTarget":
        return cls(PropertyScope.DEVICE, str(device))

    @classmethod
    def device_class(cls, class_name: str) -> "PropertyTarget":
        return cls(PropertyScope.CLASS, class_name)

    @classmethod
    def free(cls, obj: str) -> "PropertyTarget":
        return cls(PropertyScope.FREE, obj)

    def __str__(self) -> str:
        return self.owner


class PropertyGateway:
    """One property API over device, class and free scopes."""

    def __init__(self, backend: TangoBackend) -> None:
        self._backend = backend

    async def names(self, target: PropertyTarget) -> tuple[str, ...]:
        match target.scope:
            case PropertyScope.DEVICE:
                return await self._backend.get_device_property_names(_device(target))
            case PropertyScope.CLASS:
                return await self._backend.get_class_property_names(target.owner)
            case _:
                return await self._backend.get_object_property_names(target.owner)

    async def read(
        self, target: PropertyTarget, names: Sequence[str] | None = None
    ) -> tuple[PropertyEntry, ...]:
        match target.scope:
            case PropertyScope.DEVICE:
                return await self._backend.get_device_properties(_device(target), names)
            case PropertyScope.CLASS:
                return await self._backend.get_class_properties(target.owner, names)
            case _:
                return await self._backend.get_properties(target.owner, names)

    async def write(self, target: PropertyTarget, entries: Sequence[PropertyEntry]) -> None:
        if not entries:
            return
        match target.scope:
            case PropertyScope.DEVICE:
                await self._backend.put_device_properties(_device(target), entries)
            case PropertyScope.CLASS:
                await self._backend.put_class_properties(target.owner, entries)
            case _:
                await self._backend.put_properties(target.owner, entries)

    async def delete(self, target: PropertyTarget, names: Sequence[str]) -> None:
        if not names:
            return
        match target.scope:
            case PropertyScope.DEVICE:
                await self._backend.delete_device_properties(_device(target), names)
            case PropertyScope.CLASS:
                await self._backend.delete_class_properties(target.owner, names)
            case _:
                await self._backend.delete_properties(target.owner, names)

    async def history(
        self, target: PropertyTarget, name: str
    ) -> tuple[PropertyHistoryEntry, ...]:
        match target.scope:
            case PropertyScope.DEVICE:
                return await self._backend.get_device_property_history(_device(target), name)
            case PropertyScope.CLASS:
                return await self._backend.get_class_property_history(target.owner, name)
            case _:
                return await self._backend.get_property_history(target.owner, name)

    async def restore(
        self, target: PropertyTarget, entries: Sequence[PropertyEntry]
    ) -> None:
        """Put back a captured state: properties that had no value are deleted."""
        await self.write(target, [entry for entry in entries if entry.values])
        await self.delete(target, [entry.name for entry in entries if not entry.values])


def _device(target: PropertyTarget) -> DeviceName:
    return DeviceName.parse(target.owner)


def _diff_line(owner: str, name: str, before: PropertyValues, after: PropertyValues) -> DiffLine:
    if before == after:
        kind = DiffKind.UNCHANGED
    elif not before:
        kind = DiffKind.ADDED
    elif not after:
        kind = DiffKind.REMOVED
    else:
        kind = DiffKind.CHANGED
    return DiffLine(owner, name, kind, before, after)


@dataclass
class PutProperties(Command):
    """Create or change properties, one target at a time."""

    target: PropertyTarget
    entries: tuple[PropertyEntry, ...]
    _before: tuple[PropertyEntry, ...] = field(default=(), init=False, repr=False)

    @property
    def summary(self) -> str:
        if len(self.entries) == 1:
            entry = self.entries[0]
            return f"{self.target} · {entry.name} = {', '.join(entry.values)}"
        return f"{self.target} · {len(self.entries)} properties"

    async def preview(self, backend: TangoBackend) -> Diff:
        gateway = PropertyGateway(backend)
        names = [entry.name for entry in self.entries]
        self._before = await gateway.read(self.target, names)
        self._captured = True
        current = {entry.name: entry.values for entry in self._before}
        return Diff(
            tuple(
                _diff_line(
                    self.target.owner,
                    entry.name,
                    current.get(entry.name, ()),
                    tuple(entry.values),
                )
                for entry in self.entries
            )
        )

    async def apply(self, backend: TangoBackend) -> None:
        if not self._captured:
            await self.preview(backend)
        await PropertyGateway(backend).write(self.target, self.entries)

    async def revert(self, backend: TangoBackend) -> None:
        await PropertyGateway(backend).restore(self.target, self._before)


@dataclass
class DeleteProperties(Command):
    target: PropertyTarget
    names: tuple[str, ...]
    _before: tuple[PropertyEntry, ...] = field(default=(), init=False, repr=False)

    @property
    def summary(self) -> str:
        return f"{self.target} · delete {', '.join(self.names)}"

    @property
    def destructive(self) -> bool:
        return True

    async def preview(self, backend: TangoBackend) -> Diff:
        gateway = PropertyGateway(backend)
        self._before = await gateway.read(self.target, self.names)
        self._captured = True
        return Diff(
            tuple(
                _diff_line(self.target.owner, entry.name, tuple(entry.values), ())
                for entry in self._before
            )
        )

    async def apply(self, backend: TangoBackend) -> None:
        if not self._captured:
            await self.preview(backend)
        await PropertyGateway(backend).delete(self.target, self.names)

    async def revert(self, backend: TangoBackend) -> None:
        await PropertyGateway(backend).restore(self.target, self._before)


@dataclass
class RenameProperty(Command):
    target: PropertyTarget
    old_name: str
    new_name: str
    _values: PropertyValues = field(default=(), init=False, repr=False)

    @property
    def summary(self) -> str:
        return f"{self.target} · rename {self.old_name} to {self.new_name}"

    async def preview(self, backend: TangoBackend) -> Diff:
        gateway = PropertyGateway(backend)
        (entry,) = await gateway.read(self.target, [self.old_name])
        self._values = tuple(entry.values)
        self._captured = True
        owner = self.target.owner
        return Diff(
            (
                DiffLine(owner, self.old_name, DiffKind.REMOVED, self._values, ()),
                DiffLine(owner, self.new_name, DiffKind.ADDED, (), self._values),
            )
        )

    async def apply(self, backend: TangoBackend) -> None:
        if not self._captured:
            await self.preview(backend)
        gateway = PropertyGateway(backend)
        await gateway.write(self.target, [PropertyEntry(self.new_name, self._values)])
        await gateway.delete(self.target, [self.old_name])

    async def revert(self, backend: TangoBackend) -> None:
        gateway = PropertyGateway(backend)
        await gateway.write(self.target, [PropertyEntry(self.old_name, self._values)])
        await gateway.delete(self.target, [self.new_name])


@dataclass
class CopyProperties(Command):
    """Jive's copy between objects, previewed per destination."""

    source: PropertyTarget
    destinations: tuple[PropertyTarget, ...]
    names: tuple[str, ...]
    _before: dict[PropertyTarget, tuple[PropertyEntry, ...]] = field(
        default_factory=dict, init=False, repr=False
    )
    _values: tuple[PropertyEntry, ...] = field(default=(), init=False, repr=False)

    @property
    def summary(self) -> str:
        return (
            f"copy {', '.join(self.names)} from {self.source} "
            f"to {len(self.destinations)} object(s)"
        )

    async def preview(self, backend: TangoBackend) -> Diff:
        gateway = PropertyGateway(backend)
        self._values = tuple(
            entry for entry in await gateway.read(self.source, self.names) if entry.values
        )
        lines: list[DiffLine] = []
        for destination in self.destinations:
            before = await gateway.read(destination, [entry.name for entry in self._values])
            self._before[destination] = before
            current = {entry.name: tuple(entry.values) for entry in before}
            lines.extend(
                _diff_line(
                    destination.owner,
                    entry.name,
                    current.get(entry.name, ()),
                    tuple(entry.values),
                )
                for entry in self._values
            )
        self._captured = True
        return Diff(tuple(lines))

    async def apply(self, backend: TangoBackend) -> None:
        if not self._captured:
            await self.preview(backend)
        gateway = PropertyGateway(backend)
        for destination in self.destinations:
            await gateway.write(destination, self._values)

    async def revert(self, backend: TangoBackend) -> None:
        gateway = PropertyGateway(backend)
        for destination, before in self._before.items():
            await gateway.restore(destination, before)
