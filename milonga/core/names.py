"""Value objects for the names Tango addresses things by."""

import re
from dataclasses import dataclass
from functools import total_ordering
from typing import Self

_SEGMENT = re.compile(r"^[A-Za-z0-9_\-.+*]+$")
_URI_PREFIX = re.compile(r"^(?:tango://)?(?P<host>[^/]+:\d+)/(?P<rest>.+)$", re.IGNORECASE)


class TangoNameError(ValueError):
    """Raised when a Tango name cannot be parsed."""


def _check_segment(value: str, what: str) -> str:
    if not _SEGMENT.match(value):
        raise TangoNameError(f"invalid {what}: {value!r}")
    return value


class _CaseInsensitive:
    """Tango compares names without regard to case but keeps the spelling.

    The database answers ``tango/admin/desktop-h2ai4s9`` from one call and
    ``tango/admin/DESKTOP-H2AI4S9`` from another; both are the same device.
    """

    __slots__ = ()

    def _key(self) -> tuple[str, ...]:
        raise NotImplementedError

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash((type(self).__name__, self._key()))

    def __lt__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self._key() < other._key()


@total_ordering
@dataclass(frozen=True, slots=True, eq=False)
class DeviceName(_CaseInsensitive):
    """A three-field device name, ``domain/family/member``."""

    domain: str
    family: str
    member: str

    def _key(self) -> tuple[str, ...]:
        return (self.domain.lower(), self.family.lower(), self.member.lower())

    def __post_init__(self) -> None:
        _check_segment(self.domain, "domain")
        _check_segment(self.family, "family")
        _check_segment(self.member, "member")

    @classmethod
    def parse(cls, text: str) -> Self:
        parts = text.strip().strip("/").split("/")
        if len(parts) != 3:
            raise TangoNameError(f"device name must have three fields: {text!r}")
        return cls(*parts)

    def __str__(self) -> str:
        return f"{self.domain}/{self.family}/{self.member}"


@total_ordering
@dataclass(frozen=True, slots=True, eq=False)
class ServerName(_CaseInsensitive):
    """A device server instance, ``exec_name/instance`` (e.g. ``TangoTest/test``)."""

    exec_name: str
    instance: str

    def _key(self) -> tuple[str, ...]:
        return (self.exec_name.lower(), self.instance.lower())

    def __post_init__(self) -> None:
        _check_segment(self.exec_name, "server executable")
        _check_segment(self.instance, "server instance")

    @classmethod
    def parse(cls, text: str) -> Self:
        parts = text.strip().strip("/").split("/")
        if len(parts) != 2:
            raise TangoNameError(f"server name must have two fields: {text!r}")
        return cls(*parts)

    @property
    def admin_device(self) -> DeviceName:
        return DeviceName("dserver", self.exec_name, self.instance)

    def __str__(self) -> str:
        return f"{self.exec_name}/{self.instance}"


@total_ordering
@dataclass(frozen=True, slots=True, eq=False)
class AttributeRef(_CaseInsensitive):
    """A device attribute, the unit of monitoring."""

    device: DeviceName
    attribute: str

    def _key(self) -> tuple[str, ...]:
        return (*self.device._key(), self.attribute.lower())

    def __post_init__(self) -> None:
        _check_segment(self.attribute, "attribute name")

    @classmethod
    def parse(cls, text: str) -> Self:
        device, _, attribute = text.strip().rpartition("/")
        return cls(DeviceName.parse(device), attribute)

    def __str__(self) -> str:
        return f"{self.device}/{self.attribute}"


@dataclass(frozen=True, slots=True)
class DeviceReference:
    """A device name plus the control system it belongs to, if one was given."""

    device: DeviceName
    tango_host: str | None = None

    def __str__(self) -> str:
        return f"tango://{self.tango_host}/{self.device}" if self.tango_host else str(self.device)


def parse_device_reference(text: str) -> DeviceReference:
    """Parse ``a/b/c``, ``tango://host:10000/a/b/c`` or ``host:10000/a/b/c``."""
    match = _URI_PREFIX.match(text.strip())
    if match is None:
        return DeviceReference(DeviceName.parse(text))
    return DeviceReference(DeviceName.parse(match["rest"]), match["host"])


def starter_device(host: str) -> DeviceName:
    """The Starter device Astor and Milonga look for on a controlled host."""
    return DeviceName("tango", "admin", short_hostname(host))


def short_hostname(host: str) -> str:
    return host.split(".", 1)[0]
