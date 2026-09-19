"""An in-memory control system implementing :class:`TangoBackend`.

It exists so that every layer above the backend can be developed and tested
without a running Tango database, and so the application has a demo mode.
The Starter device is simulated faithfully enough that the process-control
code paths are exercised for real.
"""

import asyncio
import fnmatch
import itertools
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from milonga.core.enums import (
    AttrDataFormat,
    AttrQuality,
    EventType,
    LogLevel,
    PollableKind,
    PropertyScope,
    ServerRunState,
    TangoState,
    TangoType,
)
from milonga.core.errors import (
    CommandFailed,
    DeviceUnreachable,
    ErrorReport,
    ObjectNotFound,
    ReadOnlyError,
    TangoError,
)
from milonga.core.model import (
    AttributePropertyMap,
    AttributeSpec,
    AttributeValue,
    CommandSpec,
    DeviceInfo,
    DeviceRegistration,
    DeviceStateInfo,
    DeviceVersionInfo,
    EventData,
    LoggingTarget,
    PollingEntry,
    PropertyEntry,
    PropertyHistoryEntry,
    PropertyValues,
    ServerInfo,
)
from milonga.core.names import AttributeRef, DeviceName, ServerName, starter_device
from milonga.core.services.starter_protocol import ServerLine, format_server_line

type CommandHandler = Callable[["FakeBackend", Any], Any]

STARTER_CLASS = "Starter"
ADMIN_CLASS = "DServer"
ADMIN_DOMAIN = "dserver"


@dataclass(slots=True)
class FakeAttribute:
    spec: AttributeSpec
    value: Any = None
    write_value: Any = None
    quality: AttrQuality = AttrQuality.VALID
    timestamp: float = 0.0


@dataclass(slots=True)
class FakeDevice:
    name: DeviceName
    class_name: str
    server: ServerName
    attributes: dict[str, FakeAttribute] = field(default_factory=dict)
    commands: dict[str, CommandSpec] = field(default_factory=dict)
    handlers: dict[str, CommandHandler] = field(default_factory=dict)
    state: TangoState = TangoState.UNKNOWN
    status: str = "Not connected"
    alias: str | None = None
    exported: bool = False
    pid: int | None = None
    ior: str = ""
    exported_at: datetime | None = None
    unexported_at: datetime | None = None
    polling: dict[tuple[PollableKind, str], PollingEntry] = field(default_factory=dict)
    log_targets: list[LoggingTarget] = field(default_factory=list)
    log_level: LogLevel = LogLevel.WARNING
    version: DeviceVersionInfo = field(default_factory=lambda: DeviceVersionInfo(5, "1.0", "9.3.5"))


@dataclass(slots=True)
class FakeServer:
    name: ServerName
    host: str = ""
    level: int = 0
    controlled: bool = False
    run_state: ServerRunState = ServerRunState.STOPPED
    pid: int | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    log: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FakeHost:
    name: str
    group: str = ""
    reachable: bool = True


@dataclass(slots=True)
class _Subscription:
    ref: AttributeRef
    event_type: EventType
    callback: Callable[[EventData], None]


class FakeBackend:
    """A complete, mutable control system held in dictionaries."""

    def __init__(
        self,
        *,
        tango_host: str = "fake-cs:10000",
        latency: float = 0.0,
        clock: Callable[[], float] = time.time,
        writable: bool = True,
    ) -> None:
        self.hosts: dict[str, FakeHost] = {}
        self.servers: dict[ServerName, FakeServer] = {}
        self.devices: dict[DeviceName, FakeDevice] = {}
        self.classes: set[str] = set()
        self.latency = latency
        self.event_blocked: set[AttributeRef] = set()
        self.call_log: list[str] = []
        self._tango_host = tango_host
        self._writable = writable
        self._clock = clock
        self._properties: dict[tuple[PropertyScope, str], dict[str, PropertyValues]] = {}
        self._attribute_properties: dict[
            tuple[PropertyScope, str], dict[str, dict[str, PropertyValues]]
        ] = {}
        self._history: dict[tuple[PropertyScope, str, str], list[PropertyHistoryEntry]] = {}
        self._subscriptions: dict[int, _Subscription] = {}
        self._subscription_ids = itertools.count(1)
        self._pids = itertools.count(1000)

    # ----------------------------------------------------------------- construction

    def register_host(self, name: str, *, group: str = "", reachable: bool = True) -> FakeHost:
        host = FakeHost(name, group, reachable)
        self.hosts[name] = host
        return host

    def register_server(
        self,
        name: ServerName | str,
        host: str,
        *,
        level: int = 0,
        controlled: bool = False,
        running: bool = False,
    ) -> FakeServer:
        server_name = name if isinstance(name, ServerName) else ServerName.parse(name)
        server = FakeServer(
            server_name,
            host,
            level,
            controlled,
            ServerRunState.RUNNING if running else ServerRunState.STOPPED,
        )
        if running:
            server.pid = next(self._pids)
            server.started_at = datetime.now()
        self.servers[server_name] = server
        admin = FakeDevice(server_name.admin_device, ADMIN_CLASS, server_name)
        self.devices[admin.name] = admin
        if running:
            self._export(admin)
        return server

    def register_device(
        self,
        name: DeviceName | str,
        class_name: str,
        server: ServerName | str,
        *,
        alias: str | None = None,
    ) -> FakeDevice:
        device_name = name if isinstance(name, DeviceName) else DeviceName.parse(name)
        server_name = server if isinstance(server, ServerName) else ServerName.parse(server)
        device = FakeDevice(device_name, class_name, server_name, alias=alias)
        device.attributes["State"] = FakeAttribute(
            AttributeSpec("State", TangoType.STATE, AttrDataFormat.SCALAR, label="State")
        )
        device.attributes["Status"] = FakeAttribute(
            AttributeSpec("Status", TangoType.STRING, AttrDataFormat.SCALAR, label="Status")
        )
        self.devices[device_name] = device
        self.classes.add(class_name)
        owner = self.servers.get(server_name)
        if owner is not None and owner.run_state is ServerRunState.RUNNING:
            self._export(device)
        return device

    def register_attribute(
        self, device: DeviceName | str, spec: AttributeSpec, value: Any = None
    ) -> FakeAttribute:
        target = self._device(self._as_device_name(device))
        attribute = FakeAttribute(spec, value, timestamp=self._clock())
        target.attributes[spec.name] = attribute
        return attribute

    def register_command(
        self,
        device: DeviceName | str,
        spec: CommandSpec,
        handler: CommandHandler | None = None,
    ) -> None:
        target = self._device(self._as_device_name(device))
        target.commands[spec.name] = spec
        if handler is not None:
            target.handlers[spec.name] = handler

    def install_starter(self, host: str) -> FakeDevice:
        """Register the Starter server and device that Milonga looks for on a host."""
        server = ServerName(STARTER_CLASS, host)
        self.register_server(server, host, running=True)
        device = self.register_device(starter_device(host), STARTER_CLASS, server)
        device.state = TangoState.ON
        device.status = "Starter is ON"
        for name, data_format, data_type in (
            ("Servers", AttrDataFormat.SPECTRUM, TangoType.STRING),
            ("RunningServers", AttrDataFormat.SPECTRUM, TangoType.STRING),
            ("StoppedServers", AttrDataFormat.SPECTRUM, TangoType.STRING),
            ("HostState", AttrDataFormat.SCALAR, TangoType.SHORT),
            ("NotifdState", AttrDataFormat.SCALAR, TangoType.STATE),
        ):
            device.attributes[name] = FakeAttribute(
                AttributeSpec(name, data_type, data_format, max_dim_x=1024)
            )
        return device

    # --------------------------------------------------------------- process control

    def start_server(self, name: ServerName | str, *, starting: bool = False) -> None:
        server = self._server(self._as_server_name(name))
        server.run_state = ServerRunState.STARTING if starting else ServerRunState.RUNNING
        server.pid = next(self._pids)
        server.started_at = datetime.now()
        server.stopped_at = None
        server.log.append(
            f"{datetime.now():%H:%M:%S}  started {server.name} (level {server.level})"
        )
        if not starting:
            for device in self._devices_of(server.name):
                self._export(device)
        self._publish_host(server.host)

    def stop_server(self, name: ServerName | str, *, hard: bool = False) -> None:
        server = self._server(self._as_server_name(name))
        server.run_state = ServerRunState.STOPPED
        server.pid = None
        server.stopped_at = datetime.now()
        server.log.append(
            f"{datetime.now():%H:%M:%S}  {'killed' if hard else 'stopped'} {server.name}"
        )
        for device in self._devices_of(server.name):
            device.exported = False
            device.pid = None
            device.unexported_at = datetime.now()
            device.state = TangoState.UNKNOWN
            device.status = "Server is stopped"
            self._push_device_error(device.name, f"server {server.name} is stopped")
        self._publish_host(server.host)

    def set_host_reachable(self, host: str, reachable: bool) -> None:
        self._host(host).reachable = reachable
        self._publish_host(host)

    # ---------------------------------------------------------------------- events

    def push_event(
        self,
        ref: AttributeRef,
        value: AttributeValue | None = None,
        *,
        event_type: EventType = EventType.CHANGE,
        error: ErrorReport | None = None,
    ) -> None:
        event = EventData(ref, event_type, value, error, self._clock())
        for subscription in list(self._subscriptions.values()):
            if subscription.ref == ref and subscription.event_type is event_type:
                subscription.callback(event)

    def _push_device_error(self, device: DeviceName, message: str) -> None:
        report = ErrorReport(message, "API_DeviceNotExported")
        for subscription in list(self._subscriptions.values()):
            if subscription.ref.device == device:
                event = EventData(
                    subscription.ref, subscription.event_type, None, report, self._clock()
                )
                subscription.callback(event)

    def set_attribute_value(
        self,
        device: DeviceName | str,
        name: str,
        value: Any,
        *,
        quality: AttrQuality = AttrQuality.VALID,
    ) -> None:
        device_name = self._as_device_name(device)
        attribute = self._attribute(self._device(device_name), name)
        attribute.value = value
        attribute.quality = quality
        attribute.timestamp = self._clock()
        self.push_event(AttributeRef(device_name, name), self._value_of(attribute))

    async def simulate(self, interval: float = 0.5, *, jitter: float = 0.01) -> None:
        """Vary numeric scalars and push change events until cancelled."""
        import random

        while True:
            await asyncio.sleep(interval)
            for device in list(self.devices.values()):
                if not device.exported:
                    continue
                for attribute in list(device.attributes.values()):
                    spec = attribute.spec
                    if spec.data_format is not AttrDataFormat.SCALAR or not spec.data_type.numeric:
                        continue
                    if not isinstance(attribute.value, (int, float)):
                        continue
                    delta = attribute.value * jitter
                    new_value = attribute.value + random.uniform(-delta, delta)
                    self.set_attribute_value(
                        device.name, spec.name, type(attribute.value)(new_value)
                    )

    # ------------------------------------------------------------------- properties

    def set_properties(
        self, scope: PropertyScope, owner: str, properties: dict[str, Iterable[str]]
    ) -> None:
        store = self._properties.setdefault((scope, owner), {})
        for name, values in properties.items():
            store[name] = tuple(values)
            self._record_history(scope, owner, name, store[name], deleted=False)

    # --------------------------------------------------------------------- backend

    @property
    def tango_host(self) -> str:
        return self._tango_host

    @property
    def writable(self) -> bool:
        return self._writable

    async def close(self) -> None:
        self._subscriptions.clear()

    # -- hosts and servers

    async def get_host_list(self, pattern: str = "*") -> tuple[str, ...]:
        await self._io("get_host_list")
        return tuple(sorted(name for name in self.hosts if _matches(name, pattern)))

    async def get_host_server_list(self, host: str) -> tuple[ServerName, ...]:
        await self._io("get_host_server_list")
        return tuple(sorted(name for name, srv in self.servers.items() if srv.host == host))

    async def get_server_list(self, pattern: str = "*") -> tuple[ServerName, ...]:
        await self._io("get_server_list")
        return tuple(sorted(name for name in self.servers if _matches(str(name), pattern)))

    async def get_server_info(self, server: ServerName) -> ServerInfo:
        await self._io("get_server_info")
        entry = self._server(server)
        return ServerInfo(entry.name, entry.host, entry.level, entry.controlled)

    async def put_server_info(self, info: ServerInfo) -> None:
        await self._write("put_server_info")
        entry = self.servers.get(info.name) or self.register_server(info.name, info.host)
        entry.host = info.host
        entry.level = info.level
        entry.controlled = info.controlled
        self._publish_host(entry.host)

    async def get_server_class_list(self, server: ServerName) -> tuple[str, ...]:
        await self._io("get_server_class_list")
        self._server(server)
        return tuple(
            sorted({device.class_name for device in self._devices_of(server, visible_only=True)})
        )

    async def add_server(self, server: ServerName, devices: Sequence[DeviceRegistration]) -> None:
        await self._write("add_server")
        if server in self.servers:
            raise TangoError(f"server {server} already exists")
        self.servers[server] = FakeServer(server)
        for registration in devices:
            self.devices[registration.name] = FakeDevice(
                registration.name, registration.class_name, server
            )
            self.classes.add(registration.class_name)

    async def delete_server(self, server: ServerName) -> None:
        await self._write("delete_server")
        entry = self._server(server)
        for device in self._devices_of(server):
            del self.devices[device.name]
        del self.servers[server]
        self._publish_host(entry.host)

    async def rename_server(self, old: ServerName, new: ServerName) -> None:
        await self._write("rename_server")
        entry = self._server(old)
        if new in self.servers:
            raise TangoError(f"server {new} already exists")
        del self.servers[old]
        entry.name = new
        self.servers[new] = entry
        for device in list(self.devices.values()):
            if device.server == old:
                device.server = new
        self._publish_host(entry.host)

    # -- devices

    async def get_device_domain_list(self, pattern: str = "*") -> tuple[str, ...]:
        await self._io("get_device_domain_list")
        return tuple(
            sorted(
                {
                    name.domain
                    for name in self.devices
                    if _browsable(name) and _matches(name.domain, pattern)
                }
            )
        )

    async def get_device_family_list(self, domain: str) -> tuple[str, ...]:
        await self._io("get_device_family_list")
        return tuple(
            sorted(
                {
                    name.family
                    for name in self.devices
                    if _browsable(name) and _matches(name.domain, domain)
                }
            )
        )

    async def get_device_member_list(self, domain: str, family: str) -> tuple[str, ...]:
        await self._io("get_device_member_list")
        return tuple(
            sorted(
                name.member
                for name in self.devices
                if _browsable(name)
                and _matches(name.domain, domain)
                and _matches(name.family, family)
            )
        )

    async def get_device_list(self, pattern: str = "*/*/*") -> tuple[DeviceName, ...]:
        await self._io("get_device_list")
        return tuple(
            sorted(
                name
                for name in self.devices
                if _browsable(name) and _matches(str(name), pattern)
            )
        )

    async def get_device_list_for_class(self, class_name: str) -> tuple[DeviceName, ...]:
        await self._io("get_device_list_for_class")
        return tuple(
            sorted(
                name
                for name, dev in self.devices.items()
                if dev.class_name == class_name and _browsable(name)
            )
        )

    async def get_device_list_for_server(self, server: ServerName) -> tuple[DeviceName, ...]:
        await self._io("get_device_list_for_server")
        return tuple(sorted(device.name for device in self._devices_of(server, visible_only=True)))

    async def get_device_info(self, device: DeviceName) -> DeviceInfo:
        await self._io("get_device_info")
        entry = self._device(device)
        server = self.servers.get(entry.server)
        return DeviceInfo(
            entry.name,
            entry.class_name,
            entry.server,
            server.host if server else "",
            entry.exported,
            entry.pid,
            entry.ior,
            entry.alias,
            entry.exported_at,
            entry.unexported_at,
        )

    async def add_device(self, registration: DeviceRegistration) -> None:
        await self._write("add_device")
        if registration.name in self.devices:
            raise TangoError(f"device {registration.name} already exists")
        if registration.server not in self.servers:
            self.servers[registration.server] = FakeServer(registration.server)
        device = FakeDevice(registration.name, registration.class_name, registration.server)
        self.devices[registration.name] = device
        self.classes.add(registration.class_name)

    async def delete_device(self, device: DeviceName) -> None:
        await self._write("delete_device")
        entry = self._device(device)
        if entry.alias:
            entry.alias = None
        del self.devices[device]
        self._properties.pop((PropertyScope.DEVICE, str(device)), None)

    async def rename_device(self, old: DeviceName, new: DeviceName) -> None:
        await self._write("rename_device")
        entry = self._device(old)
        if new in self.devices:
            raise TangoError(f"device {new} already exists")
        del self.devices[old]
        entry.name = new
        self.devices[new] = entry
        properties = self._properties.pop((PropertyScope.DEVICE, str(old)), None)
        if properties is not None:
            self._properties[(PropertyScope.DEVICE, str(new))] = properties

    async def unexport_device(self, device: DeviceName) -> None:
        await self._write("unexport_device")
        entry = self._device(device)
        entry.exported = False
        entry.unexported_at = datetime.now()

    async def get_class_list(self, pattern: str = "*") -> tuple[str, ...]:
        await self._io("get_class_list")
        return tuple(sorted(name for name in self.classes if _matches(name, pattern)))

    # -- aliases

    async def get_device_alias_list(self, pattern: str = "*") -> tuple[str, ...]:
        await self._io("get_device_alias_list")
        return tuple(
            sorted(
                device.alias
                for device in self.devices.values()
                if device.alias and _matches(device.alias, pattern)
            )
        )

    async def get_alias_from_device(self, device: DeviceName) -> str | None:
        await self._io("get_alias_from_device")
        return self._device(device).alias

    async def get_device_from_alias(self, alias: str) -> DeviceName:
        await self._io("get_device_from_alias")
        for device in self.devices.values():
            if device.alias == alias:
                return device.name
        raise ObjectNotFound(f"no device with alias {alias!r}")

    async def put_device_alias(self, device: DeviceName, alias: str) -> None:
        await self._write("put_device_alias")
        self._device(device).alias = alias

    async def delete_device_alias(self, alias: str) -> None:
        await self._write("delete_device_alias")
        for device in self.devices.values():
            if device.alias == alias:
                device.alias = None
                return
        raise ObjectNotFound(f"no device with alias {alias!r}")

    # -- properties

    async def get_device_property_names(self, device: DeviceName) -> tuple[str, ...]:
        await self._io("get_device_property_names")
        return self._property_names(PropertyScope.DEVICE, str(device))

    async def get_device_properties(
        self, device: DeviceName, names: Sequence[str] | None = None
    ) -> tuple[PropertyEntry, ...]:
        await self._io("get_device_properties")
        self._device(device)
        return self._get_properties(PropertyScope.DEVICE, str(device), names)

    async def put_device_properties(
        self, device: DeviceName, entries: Sequence[PropertyEntry]
    ) -> None:
        await self._write("put_device_properties")
        self._put_properties(PropertyScope.DEVICE, str(device), entries)

    async def delete_device_properties(self, device: DeviceName, names: Sequence[str]) -> None:
        await self._write("delete_device_properties")
        self._delete_properties(PropertyScope.DEVICE, str(device), names)

    async def get_device_property_history(
        self, device: DeviceName, name: str
    ) -> tuple[PropertyHistoryEntry, ...]:
        await self._io("get_device_property_history")
        return self._read_history(PropertyScope.DEVICE, str(device), name)

    async def get_class_property_names(self, class_name: str) -> tuple[str, ...]:
        await self._io("get_class_property_names")
        return self._property_names(PropertyScope.CLASS, class_name)

    async def get_class_properties(
        self, class_name: str, names: Sequence[str] | None = None
    ) -> tuple[PropertyEntry, ...]:
        await self._io("get_class_properties")
        return self._get_properties(PropertyScope.CLASS, class_name, names)

    async def put_class_properties(self, class_name: str, entries: Sequence[PropertyEntry]) -> None:
        await self._write("put_class_properties")
        self._put_properties(PropertyScope.CLASS, class_name, entries)

    async def delete_class_properties(self, class_name: str, names: Sequence[str]) -> None:
        await self._write("delete_class_properties")
        self._delete_properties(PropertyScope.CLASS, class_name, names)

    async def get_class_property_history(
        self, class_name: str, name: str
    ) -> tuple[PropertyHistoryEntry, ...]:
        await self._io("get_class_property_history")
        return self._read_history(PropertyScope.CLASS, class_name, name)

    async def get_property_history(
        self, obj: str, name: str
    ) -> tuple[PropertyHistoryEntry, ...]:
        await self._io("get_property_history")
        return self._read_history(PropertyScope.FREE, obj, name)

    async def get_device_attribute_properties(self, device: DeviceName) -> AttributePropertyMap:
        await self._io("get_device_attribute_properties")
        return self._attribute_properties.get((PropertyScope.DEVICE, str(device)), {})

    async def put_device_attribute_properties(
        self, device: DeviceName, properties: AttributePropertyMap
    ) -> None:
        await self._write("put_device_attribute_properties")
        store = self._attribute_properties.setdefault((PropertyScope.DEVICE, str(device)), {})
        for attribute, values in properties.items():
            store.setdefault(attribute, {}).update({k: tuple(v) for k, v in values.items()})

    async def delete_device_attribute_properties(
        self, device: DeviceName, attribute: str, names: Sequence[str]
    ) -> None:
        await self._write("delete_device_attribute_properties")
        store = self._attribute_properties.get((PropertyScope.DEVICE, str(device)), {})
        for name in names:
            store.get(attribute, {}).pop(name, None)

    async def get_class_attribute_properties(self, class_name: str) -> AttributePropertyMap:
        await self._io("get_class_attribute_properties")
        return self._attribute_properties.get((PropertyScope.CLASS, class_name), {})

    async def put_class_attribute_properties(
        self, class_name: str, properties: AttributePropertyMap
    ) -> None:
        await self._write("put_class_attribute_properties")
        store = self._attribute_properties.setdefault((PropertyScope.CLASS, class_name), {})
        for attribute, values in properties.items():
            store.setdefault(attribute, {}).update({k: tuple(v) for k, v in values.items()})

    async def get_object_list(self, pattern: str = "*") -> tuple[str, ...]:
        await self._io("get_object_list")
        return tuple(
            sorted(
                owner
                for scope, owner in self._properties
                if scope is PropertyScope.FREE and _matches(owner, pattern)
            )
        )

    async def get_object_property_names(self, obj: str) -> tuple[str, ...]:
        await self._io("get_object_property_names")
        return self._property_names(PropertyScope.FREE, obj)

    async def get_properties(
        self, obj: str, names: Sequence[str] | None = None
    ) -> tuple[PropertyEntry, ...]:
        await self._io("get_properties")
        return self._get_properties(PropertyScope.FREE, obj, names)

    async def put_properties(self, obj: str, entries: Sequence[PropertyEntry]) -> None:
        await self._write("put_properties")
        self._put_properties(PropertyScope.FREE, obj, entries)

    async def delete_properties(self, obj: str, names: Sequence[str]) -> None:
        await self._write("delete_properties")
        self._delete_properties(PropertyScope.FREE, obj, names)

    async def find_devices_by_property(
        self, name: str, value: str | None = None
    ) -> tuple[DeviceName, ...]:
        await self._io("find_devices_by_property")
        found: list[DeviceName] = []
        for (scope, owner), properties in self._properties.items():
            if scope is not PropertyScope.DEVICE or name not in properties:
                continue
            if value is None or any(value in item for item in properties[name]):
                found.append(DeviceName.parse(owner))
        return tuple(sorted(found))

    # -- live device access

    async def ping(self, device: DeviceName) -> float:
        await self._io("ping")
        self._live(device)
        return 250.0

    async def get_device_state(self, device: DeviceName) -> DeviceStateInfo:
        await self._io("get_device_state")
        entry = self._live(device)
        return DeviceStateInfo(entry.state, entry.status, self._clock())

    async def get_device_version(self, device: DeviceName) -> DeviceVersionInfo:
        await self._io("get_device_version")
        return self._live(device).version

    async def get_attribute_specs(self, device: DeviceName) -> tuple[AttributeSpec, ...]:
        await self._io("get_attribute_specs")
        entry = self._live(device)
        return tuple(attribute.spec for attribute in entry.attributes.values())

    async def set_attribute_specs(self, device: DeviceName, specs: Sequence[AttributeSpec]) -> None:
        await self._write("set_attribute_specs")
        entry = self._live(device)
        for spec in specs:
            attribute = self._attribute(entry, spec.name)
            attribute.spec = spec

    async def read_attributes(
        self, device: DeviceName, names: Sequence[str]
    ) -> tuple[AttributeValue, ...]:
        await self._io("read_attributes")
        entry = self._live(device)
        if entry.class_name == STARTER_CLASS:
            self._refresh_starter(entry)
        self._refresh_intrinsic(entry)
        return tuple(self._value_of(self._attribute(entry, name)) for name in names)

    async def write_attribute(self, device: DeviceName, name: str, value: Any) -> None:
        await self._write("write_attribute")
        entry = self._live(device)
        attribute = self._attribute(entry, name)
        if not attribute.spec.writable.writable:
            raise CommandFailed(f"attribute {name} is not writable")
        attribute.write_value = value
        attribute.value = value
        attribute.timestamp = self._clock()
        self.push_event(AttributeRef(device, name), self._value_of(attribute))

    async def get_command_specs(self, device: DeviceName) -> tuple[CommandSpec, ...]:
        await self._io("get_command_specs")
        return tuple(self._live(device).commands.values())

    async def execute_command(self, device: DeviceName, command: str, argin: Any = None) -> Any:
        await self._io("execute_command")
        entry = self._live(device)
        if entry.class_name == STARTER_CLASS:
            return self._starter_command(entry, command, argin)
        handler = entry.handlers.get(command)
        if handler is not None:
            return handler(self, argin)
        if command == "State":
            return entry.state
        if command == "Status":
            return entry.status
        if command not in entry.commands:
            raise ObjectNotFound(f"device {device} has no command {command!r}")
        return None

    async def get_polling(self, device: DeviceName) -> tuple[PollingEntry, ...]:
        await self._io("get_polling")
        return tuple(self._live(device).polling.values())

    async def set_polling(
        self, device: DeviceName, name: str, kind: PollableKind, period_ms: int
    ) -> None:
        await self._write("set_polling")
        entry = self._live(device)
        entry.polling[(kind, name)] = PollingEntry(name, kind, period_ms, polled=True)

    async def stop_polling(self, device: DeviceName, name: str, kind: PollableKind) -> None:
        await self._write("stop_polling")
        self._live(device).polling.pop((kind, name), None)

    async def get_logging_targets(self, device: DeviceName) -> tuple[LoggingTarget, ...]:
        await self._io("get_logging_targets")
        return tuple(self._live(device).log_targets)

    async def add_logging_target(self, device: DeviceName, target: LoggingTarget) -> None:
        await self._write("add_logging_target")
        entry = self._live(device)
        if target not in entry.log_targets:
            entry.log_targets.append(target)

    async def remove_logging_target(self, device: DeviceName, target: LoggingTarget) -> None:
        await self._write("remove_logging_target")
        entry = self._live(device)
        if target in entry.log_targets:
            entry.log_targets.remove(target)

    async def get_logging_level(self, device: DeviceName) -> LogLevel:
        await self._io("get_logging_level")
        return self._live(device).log_level

    async def set_logging_level(self, device: DeviceName, level: LogLevel) -> None:
        await self._write("set_logging_level")
        self._live(device).log_level = level

    # -- events

    async def subscribe_event(
        self, ref: AttributeRef, event_type: EventType, callback: Callable[[EventData], None]
    ) -> int:
        await self._io("subscribe_event")
        self._live(ref.device)
        if ref in self.event_blocked:
            raise TangoError(f"no event channel for {ref}")
        subscription = next(self._subscription_ids)
        self._subscriptions[subscription] = _Subscription(ref, event_type, callback)
        return subscription

    async def unsubscribe_event(self, subscription: int) -> None:
        await self._io("unsubscribe_event")
        self._subscriptions.pop(subscription, None)

    @property
    def subscription_count(self) -> int:
        return len(self._subscriptions)

    # ------------------------------------------------------------------- internals

    async def _io(self, operation: str) -> None:
        self.call_log.append(operation)
        if self.latency:
            await asyncio.sleep(self.latency)

    async def _write(self, operation: str) -> None:
        if not self._writable:
            raise ReadOnlyError(f"{operation} refused: backend is read-only")
        await self._io(operation)

    @staticmethod
    def _as_device_name(device: DeviceName | str) -> DeviceName:
        return device if isinstance(device, DeviceName) else DeviceName.parse(device)

    @staticmethod
    def _as_server_name(server: ServerName | str) -> ServerName:
        return server if isinstance(server, ServerName) else ServerName.parse(server)

    def _host(self, name: str) -> FakeHost:
        host = self.hosts.get(name)
        if host is None:
            raise ObjectNotFound(f"unknown host {name!r}")
        return host

    def _server(self, name: ServerName) -> FakeServer:
        server = self.servers.get(name)
        if server is None:
            raise ObjectNotFound(f"unknown server {name}")
        return server

    def _device(self, name: DeviceName) -> FakeDevice:
        device = self.devices.get(name)
        if device is None:
            raise ObjectNotFound(f"unknown device {name}")
        return device

    def _live(self, name: DeviceName) -> FakeDevice:
        device = self._device(name)
        server = self.servers.get(device.server)
        host = self.hosts.get(server.host) if server else None
        if host is not None and not host.reachable:
            raise DeviceUnreachable(f"host {host.name} does not answer")
        if not device.exported:
            raise DeviceUnreachable(f"device {name} is not exported")
        return device

    @staticmethod
    def _attribute(device: FakeDevice, name: str) -> FakeAttribute:
        attribute = device.attributes.get(name)
        if attribute is None:
            raise ObjectNotFound(f"device {device.name} has no attribute {name!r}")
        return attribute

    def _devices_of(self, server: ServerName, *, visible_only: bool = False) -> list[FakeDevice]:
        return [
            device
            for device in self.devices.values()
            if device.server == server and (not visible_only or _browsable(device.name))
        ]

    def _export(self, device: FakeDevice) -> None:
        server = self.servers.get(device.server)
        device.exported = True
        device.pid = server.pid if server else None
        device.exported_at = datetime.now()
        device.ior = f"IOR:fake:{device.name}"
        if device.state is TangoState.UNKNOWN:
            device.state = TangoState.ON
            device.status = "Device is ON"

    def _refresh_intrinsic(self, device: FakeDevice) -> None:
        """``State`` and ``Status`` always mirror the device, as in a real server."""
        now = self._clock()
        for name, value in (("State", device.state), ("Status", device.status)):
            attribute = device.attributes.get(name)
            if attribute is not None:
                attribute.value = value
                attribute.timestamp = now

    def _value_of(self, attribute: FakeAttribute) -> AttributeValue:
        spec = attribute.spec
        value = attribute.value
        dim_x, dim_y = 1, 0
        if spec.data_format is AttrDataFormat.SPECTRUM and value is not None:
            dim_x = len(value)
        elif spec.data_format is AttrDataFormat.IMAGE and value is not None:
            dim_y, dim_x = value.shape[0], value.shape[1]
        return AttributeValue(
            spec.name,
            value,
            attribute.write_value,
            attribute.quality,
            attribute.timestamp or self._clock(),
            dim_x,
            dim_y,
            spec.data_format,
        )

    # -- starter simulation

    def _starter_host(self, device: FakeDevice) -> str:
        server = self.servers.get(device.server)
        return server.host if server else device.name.member

    def _controlled_servers(self, host: str) -> list[FakeServer]:
        return [
            server
            for server in self.servers.values()
            if server.host == host and server.name.exec_name != STARTER_CLASS
        ]

    def _server_lines(self, host: str) -> tuple[str, ...]:
        return tuple(
            format_server_line(
                ServerLine(server.name, server.run_state, server.controlled, server.level)
            )
            for server in sorted(self._controlled_servers(host), key=lambda s: str(s.name))
        )

    def _host_state_code(self, host: str) -> int:
        """Starter publishes the aggregated host state as a ``DevState`` code."""
        servers = [server for server in self._controlled_servers(host) if server.controlled]
        if not servers:
            return TangoState.OFF.code
        states = {server.run_state for server in servers}
        if ServerRunState.STARTING in states:
            return TangoState.MOVING.code
        if states == {ServerRunState.RUNNING}:
            return TangoState.ON.code
        if states <= {ServerRunState.STOPPED, ServerRunState.UNKNOWN}:
            return TangoState.OFF.code
        return TangoState.ALARM.code

    def _refresh_starter(self, device: FakeDevice) -> None:
        host = self._starter_host(device)
        running = tuple(
            str(server.name)
            for server in self._controlled_servers(host)
            if server.run_state is ServerRunState.RUNNING
        )
        stopped = tuple(
            str(server.name)
            for server in self._controlled_servers(host)
            if server.run_state is ServerRunState.STOPPED
        )
        now = self._clock()
        for name, value in (
            ("Servers", self._server_lines(host)),
            ("RunningServers", running),
            ("StoppedServers", stopped),
            ("HostState", self._host_state_code(host)),
            ("NotifdState", TangoState.ON),
        ):
            attribute = device.attributes.get(name)
            if attribute is not None:
                attribute.value = value
                attribute.timestamp = now

    def _publish_host(self, host: str) -> None:
        if not host:
            return
        name = starter_device(host)
        device = self.devices.get(name)
        if device is None or not device.exported:
            return
        self._refresh_starter(device)
        for attribute_name in ("Servers", "HostState"):
            attribute = device.attributes.get(attribute_name)
            if attribute is not None:
                self.push_event(AttributeRef(name, attribute_name), self._value_of(attribute))

    def _starter_command(self, device: FakeDevice, command: str, argin: Any) -> Any:
        host = self._starter_host(device)
        match command:
            case "DevStart":
                self.start_server(str(argin))
            case "DevStop":
                self.stop_server(str(argin))
            case "HardKillServer":
                self.stop_server(str(argin), hard=True)
            case "DevStartAll":
                for server in self._controlled_servers(host):
                    if server.controlled and server.level == int(argin):
                        self.start_server(server.name)
            case "DevStopAll":
                for server in self._controlled_servers(host):
                    if server.controlled and server.level == int(argin):
                        self.stop_server(server.name)
            case "DevGetRunningServers":
                return tuple(
                    str(server.name)
                    for server in self._controlled_servers(host)
                    if server.run_state is ServerRunState.RUNNING
                )
            case "DevGetStopServers":
                return tuple(
                    str(server.name)
                    for server in self._controlled_servers(host)
                    if server.run_state is not ServerRunState.RUNNING
                )
            case "DevReadLog":
                return "\n".join(self._server(ServerName.parse(str(argin))).log)
            case "UpdateServersInfo" | "ResetStatistics":
                return None
            case "NotifyDaemonState":
                return TangoState.ON
            case _:
                raise ObjectNotFound(f"Starter has no command {command!r}")
        return None

    # -- property store

    def _property_names(self, scope: PropertyScope, owner: str) -> tuple[str, ...]:
        return tuple(sorted(self._properties.get((scope, owner), {})))

    def _get_properties(
        self, scope: PropertyScope, owner: str, names: Sequence[str] | None
    ) -> tuple[PropertyEntry, ...]:
        store = self._properties.get((scope, owner), {})
        wanted = list(names) if names is not None else sorted(store)
        return tuple(PropertyEntry(name, store.get(name, ()), scope, owner) for name in wanted)

    def _put_properties(
        self, scope: PropertyScope, owner: str, entries: Sequence[PropertyEntry]
    ) -> None:
        store = self._properties.setdefault((scope, owner), {})
        for entry in entries:
            store[entry.name] = tuple(entry.values)
            self._record_history(scope, owner, entry.name, tuple(entry.values), deleted=False)

    def _delete_properties(self, scope: PropertyScope, owner: str, names: Sequence[str]) -> None:
        store = self._properties.get((scope, owner), {})
        for name in names:
            if store.pop(name, None) is not None:
                self._record_history(scope, owner, name, (), deleted=True)

    def _read_history(
        self, scope: PropertyScope, owner: str, name: str
    ) -> tuple[PropertyHistoryEntry, ...]:
        return tuple(reversed(self._history.get((scope, owner, name), [])))

    def _record_history(
        self,
        scope: PropertyScope,
        owner: str,
        name: str,
        values: PropertyValues,
        *,
        deleted: bool,
    ) -> None:
        entries = self._history.setdefault((scope, owner, name), [])
        entries.append(PropertyHistoryEntry(name, values, datetime.now(), deleted))


def _matches(name: str, pattern: str) -> bool:
    """The Tango database matches names case-insensitively; so does this."""
    return fnmatch.fnmatchcase(name.lower(), pattern.lower())


def _browsable(name: DeviceName) -> bool:
    """Admin devices live in the database but are not objects users browse."""
    return name.domain != ADMIN_DOMAIN
