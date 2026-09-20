"""Immutable snapshots of control-system objects.

Timestamps are ``datetime`` for database records and float epoch seconds for
attribute values, where one object per event would otherwise be allocated at
the event rate.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeAlias

from milonga.core.enums import (
    AttrDataFormat,
    AttrQuality,
    AttrWriteType,
    DataSource,
    DisplayLevel,
    EventType,
    HostState,
    LogLevel,
    LogTargetType,
    PollableKind,
    PropertyScope,
    ServerRunState,
    StateCategory,
    TangoState,
    TangoType,
)
from milonga.core.errors import ErrorReport
from milonga.core.names import AttributeRef, DeviceName, ServerName

PropertyValues: TypeAlias = tuple[str, ...]


# --------------------------------------------------------------------------- database


@dataclass(frozen=True, slots=True)
class PropertyEntry:
    name: str
    values: PropertyValues
    scope: PropertyScope = PropertyScope.DEVICE
    owner: str = ""
    attribute: str | None = None

    @property
    def single(self) -> str:
        return self.values[0] if self.values else ""


@dataclass(frozen=True, slots=True)
class PropertyHistoryEntry:
    name: str
    values: PropertyValues
    changed_at: datetime | None = None
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class DeviceRegistration:
    """The minimum needed to add a device to a server."""

    name: DeviceName
    class_name: str
    server: ServerName


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    name: DeviceName
    class_name: str
    server: ServerName
    host: str = ""
    exported: bool = False
    pid: int | None = None
    ior: str = ""
    alias: str | None = None
    exported_at: datetime | None = None
    unexported_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DeviceVersionInfo:
    idl_version: int = 0
    server_version: str = ""
    tango_release: str = ""
    doc_url: str = ""


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """The database record a Starter reads to decide what it controls."""

    name: ServerName
    host: str = ""
    level: int = 0
    controlled: bool = False

    @property
    def is_controlled(self) -> bool:
        return self.controlled and self.level > 0


# ------------------------------------------------------------------------- attributes


@dataclass(frozen=True, slots=True)
class EventConfig:
    change_abs: str = ""
    change_rel: str = ""
    period_ms: str = ""
    archive_abs: str = ""
    archive_rel: str = ""
    archive_period_ms: str = ""


@dataclass(frozen=True, slots=True)
class AlarmConfig:
    min_alarm: str = ""
    max_alarm: str = ""
    min_warning: str = ""
    max_warning: str = ""
    delta_t: str = ""
    delta_val: str = ""


@dataclass(frozen=True, slots=True)
class AttributeSpec:
    """Static configuration of an attribute, as stored in the device and database."""

    name: str
    data_type: TangoType = TangoType.DOUBLE
    data_format: AttrDataFormat = AttrDataFormat.SCALAR
    writable: AttrWriteType = AttrWriteType.READ
    label: str = ""
    unit: str = ""
    standard_unit: str = ""
    display_unit: str = ""
    display_format: str = "%6.2f"
    description: str = ""
    min_value: str = ""
    max_value: str = ""
    alarms: AlarmConfig = field(default_factory=AlarmConfig)
    events: EventConfig = field(default_factory=EventConfig)
    display_level: DisplayLevel = DisplayLevel.OPERATOR
    max_dim_x: int = 1
    max_dim_y: int = 0
    enum_labels: tuple[str, ...] = ()
    root_attribute: str = ""

    @property
    def title(self) -> str:
        return self.label or self.name


@dataclass(frozen=True, slots=True)
class AttributeValue:
    """One reading. ``value`` keeps the backend's native container."""

    name: str
    value: Any = None
    write_value: Any = None
    quality: AttrQuality = AttrQuality.VALID
    timestamp: float = 0.0
    dim_x: int = 1
    dim_y: int = 0
    data_format: AttrDataFormat = AttrDataFormat.SCALAR
    source: DataSource = DataSource.EVENTS
    error: ErrorReport | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.quality is not AttrQuality.INVALID


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    in_type: TangoType = TangoType.VOID
    out_type: TangoType = TangoType.VOID
    in_description: str = ""
    out_description: str = ""
    display_level: DisplayLevel = DisplayLevel.OPERATOR
    polling_period_ms: int = 0


@dataclass(frozen=True, slots=True)
class PollingEntry:
    name: str
    kind: PollableKind = PollableKind.ATTRIBUTE
    period_ms: int = 0
    polled: bool = False
    ring_depth: int = 0
    last_read_ms: float = 0.0
    delta_ms: float = 0.0
    externally_triggered: bool = False


@dataclass(frozen=True, slots=True)
class LoggingTarget:
    target_type: LogTargetType
    name: str = ""

    def __str__(self) -> str:
        if self.target_type is LogTargetType.OTHER:
            return self.name
        return f"{self.target_type}::{self.name}" if self.name else str(self.target_type)


@dataclass(frozen=True, slots=True)
class DeviceStateInfo:
    state: TangoState = TangoState.UNKNOWN
    status: str = ""
    timestamp: float = 0.0


# --------------------------------------------------------------------------- snapshots


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    info: DeviceInfo
    state: DeviceStateInfo = field(default_factory=DeviceStateInfo)
    error: ErrorReport | None = None

    @property
    def name(self) -> DeviceName:
        return self.info.name


@dataclass(frozen=True, slots=True)
class ServerSnapshot:
    info: ServerInfo
    run_state: ServerRunState = ServerRunState.UNKNOWN
    pid: int | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    classes: tuple[str, ...] = ()
    device_count: int = 0
    version: DeviceVersionInfo | None = None

    @property
    def name(self) -> ServerName:
        return self.info.name

    @property
    def running(self) -> bool:
        return self.run_state in (ServerRunState.RUNNING, ServerRunState.CHANGING)


@dataclass(frozen=True, slots=True)
class HostSnapshot:
    name: str
    state: HostState = HostState.UNREACHABLE
    starter: DeviceName | None = None
    group: str = ""
    servers: tuple[ServerSnapshot, ...] = ()
    updated_at: float = 0.0
    error: ErrorReport | None = None

    @property
    def running_count(self) -> int:
        return sum(1 for server in self.servers if server.run_state is ServerRunState.RUNNING)

    @property
    def stopped_count(self) -> int:
        return sum(1 for server in self.servers if server.run_state is ServerRunState.STOPPED)

    @property
    def levels(self) -> tuple[int, ...]:
        return tuple(sorted({server.info.level for server in self.servers if server.info.level}))


def aggregate_host_state(
    servers: Sequence[ServerSnapshot], *, starter_reachable: bool = True
) -> HostState:
    """Host state as Astor computes it: uncontrolled servers do not count."""
    if not starter_reachable:
        return HostState.UNREACHABLE
    controlled = [server for server in servers if server.info.is_controlled]
    if not controlled:
        return HostState.IDLE
    states = {server.run_state for server in controlled}
    if ServerRunState.CHANGING in states:
        return HostState.CHANGING
    if states == {ServerRunState.RUNNING}:
        return HostState.ALL_RUNNING
    if states <= {ServerRunState.STOPPED, ServerRunState.UNKNOWN}:
        return HostState.ALL_STOPPED
    return HostState.MIXED


# ------------------------------------------------------------------------------ events


@dataclass(frozen=True, slots=True)
class EventData:
    """Delivered on the event loop thread, never on a backend thread."""

    ref: AttributeRef
    event_type: EventType
    value: AttributeValue | None = None
    error: ErrorReport | None = None
    received_at: float = 0.0


@dataclass(frozen=True, slots=True)
class MonitorStats:
    channels: int = 0
    by_events: int = 0
    by_polling: int = 0
    watchers: int = 0


AttributePropertyMap: TypeAlias = Mapping[str, Mapping[str, PropertyValues]]
StateSummary: TypeAlias = Mapping[StateCategory, int]
LogLevelMap: TypeAlias = Mapping[DeviceName, LogLevel]
