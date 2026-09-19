"""Attribute configuration: the alarm, unit and event settings Jive edits."""

from dataclasses import dataclass, field, fields

from milonga.core.backend.protocol import TangoBackend
from milonga.core.commands.base import Command, Diff, DiffKind, DiffLine
from milonga.core.model import AttributeSpec
from milonga.core.names import DeviceName

CONFIG_FIELDS: tuple[str, ...] = (
    "label",
    "unit",
    "standard_unit",
    "display_unit",
    "display_format",
    "description",
    "min_value",
    "max_value",
    "display_level",
)


def config_values(spec: AttributeSpec) -> dict[str, str]:
    """The editable settings of one attribute, as flat text."""
    values = {name: str(getattr(spec, name)) for name in CONFIG_FIELDS}
    for group, prefix in ((spec.alarms, "alarms"), (spec.events, "events")):
        for item in fields(group):
            values[f"{prefix}.{item.name}"] = str(getattr(group, item.name))
    return values


@dataclass
class SetAttributeConfig(Command):
    """Writes one attribute's configuration to the running device."""

    device: DeviceName
    spec: AttributeSpec
    _before: AttributeSpec | None = field(default=None, init=False, repr=False)

    @property
    def summary(self) -> str:
        return f"{self.device} · configure {self.spec.name}"

    async def preview(self, backend: TangoBackend) -> Diff:
        specs = await backend.get_attribute_specs(self.device)
        self._before = next(
            (spec for spec in specs if spec.name == self.spec.name), None
        )
        self._captured = self._before is not None
        if self._before is None:
            return Diff()
        before = config_values(self._before)
        after = config_values(self.spec)
        owner = f"{self.device}/{self.spec.name}"
        return Diff(
            tuple(
                DiffLine(
                    owner,
                    name,
                    DiffKind.UNCHANGED if before[name] == after[name] else DiffKind.CHANGED,
                    (before[name],),
                    (after[name],),
                )
                for name in after
            )
        )

    async def apply(self, backend: TangoBackend) -> None:
        if not self._captured:
            await self.preview(backend)
        await backend.set_attribute_specs(self.device, [self.spec])

    async def revert(self, backend: TangoBackend) -> None:
        if self._before is None:
            return
        await backend.set_attribute_specs(self.device, [self._before])
