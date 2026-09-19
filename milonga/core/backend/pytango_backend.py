"""The Tango backend. The only module in the project that imports ``tango``.

Every PyTango call is synchronous and runs in a thread pool owned here: one
for the database, and one per server host for devices, so a host that stops
answering can only exhaust its own threads. PyTango's asyncio green mode is
not used: it delegates to a single global pool and creates its own event
loop when first used outside a running one.

Event callbacks arrive on PyTango threads, and the first one arrives on the
subscribing thread before ``subscribe_event`` returns. They are converted
there and handed to the event loop with ``call_soon_threadsafe``.
"""

import asyncio
import itertools
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

import numpy as np
import tango

from milonga.core.backend.formats import (
    format_logging_target,
    log_level_from_int,
    log_level_to_int,
    parse_db_date,
    parse_logging_target,
    parse_polling_status,
    parse_server_info,
    tango_release,
)
from milonga.core.backend.protocol import EventCallback
from milonga.core.enums import (
    AttrDataFormat,
    AttrQuality,
    AttrWriteType,
    DisplayLevel,
    EventType,
    LogLevel,
    PollableKind,
    PropertyScope,
    TangoState,
    TangoType,
)
from milonga.core.errors import (
    BackendUnavailable,
    CommandFailed,
    DeviceUnreachable,
    ErrorContext,
    ErrorFrame,
    ErrorReport,
    ErrorSeverity,
    ObjectNotFound,
    PermissionDenied,
    ReadOnlyError,
    RequestTimeout,
    TangoError,
)
from milonga.core.model import (
    AlarmConfig,
    AttributePropertyMap,
    AttributeSpec,
    AttributeValue,
    CommandSpec,
    DeviceInfo,
    DeviceRegistration,
    DeviceStateInfo,
    DeviceVersionInfo,
    EventConfig,
    EventData,
    LoggingTarget,
    PollingEntry,
    PropertyEntry,
    PropertyHistoryEntry,
    ServerInfo,
)
from milonga.core.names import AttributeRef, DeviceName, ServerName

ADMIN_DOMAIN = "dserver"
ADMIN_CLASS = "DServer"
DEFAULT_TIMEOUT_MS = 3000
DATABASE_WORKERS = 4
HOST_WORKERS = 4
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN = 10.0

_UNREACHABLE = frozenset(
    {
        "API_CantConnectToDevice",
        "API_DeviceNotExported",
        "API_ServerNotRunning",
        "API_CorbaException",
        "API_CantConnectToDatabase",
        "API_ConnectionFailed",
        "API_DeviceNotReachable",
    }
)
_TIMEOUT = frozenset({"API_DeviceTimedOut", "API_CommandTimedOut", "API_AsynReplyNotArrived"})
_NOT_FOUND = frozenset(
    {
        "DB_DeviceNotDefined",
        "DB_AliasNotDefined",
        "API_DeviceNotDefined",
        "API_AttrNotFound",
        "API_CommandNotFound",
        "API_ClassNotFound",
        "DB_ClassNotFoundError",
    }
)
_DENIED = frozenset({"API_ReadOnlyMode", "API_NotAllowed", "API_AccessDenied"})

_EVENT_TYPES: dict[EventType, Any] = {
    EventType.CHANGE: tango.EventType.CHANGE_EVENT,
    EventType.PERIODIC: tango.EventType.PERIODIC_EVENT,
    EventType.ARCHIVE: tango.EventType.ARCHIVE_EVENT,
    EventType.USER: tango.EventType.USER_EVENT,
    EventType.ATTR_CONF: tango.EventType.ATTR_CONF_EVENT,
    EventType.DATA_READY: tango.EventType.DATA_READY_EVENT,
    EventType.INTERFACE: tango.EventType.INTERFACE_CHANGE_EVENT,
}


# --------------------------------------------------------------------- conversion


def convert_error(error: tango.DevFailed, operation: str, target: str) -> TangoError:
    """A ``DevFailed`` becomes the matching error class with its whole stack."""
    frames = tuple(_frame(item) for item in error.args if hasattr(item, "reason"))
    reasons = {frame.reason for frame in frames}
    message = _first_line(frames[0].description) if frames else str(error)
    context = ErrorContext(operation, target)
    kind: type[TangoError] = CommandFailed
    if reasons & _TIMEOUT:
        kind = RequestTimeout
    elif isinstance(error, tango.ConnectionFailed) or reasons & _UNREACHABLE:
        kind = DeviceUnreachable
    elif reasons & _NOT_FOUND:
        kind = ObjectNotFound
    elif reasons & _DENIED:
        kind = PermissionDenied
    elif isinstance(error, tango.CommunicationFailed):
        kind = DeviceUnreachable
    return kind(message, frames=frames, context=context)


def _frame(item: Any) -> ErrorFrame:
    severity = getattr(item.severity, "name", str(item.severity))
    return ErrorFrame(
        reason=str(item.reason),
        description=str(item.desc).strip(),
        origin=str(item.origin).strip(),
        severity=ErrorSeverity(severity)
        if severity in ErrorSeverity.__members__
        else (ErrorSeverity.ERR),
    )


def _report(errors: Sequence[Any]) -> ErrorReport:
    frames = tuple(_frame(item) for item in errors)
    first = frames[0] if frames else ErrorFrame("unknown")
    return ErrorReport(_first_line(first.description) or first.reason, first.reason, frames)


def _first_line(text: str) -> str:
    """Tango descriptions run to several lines; the rest stays in the frames."""
    return text.strip().splitlines()[0] if text.strip() else ""


def _tango_type(value: Any) -> TangoType:
    name = getattr(value, "name", None)
    if name is None:
        try:
            name = tango.CmdArgType(int(value)).name
        except (ValueError, TypeError):
            return TangoType.UNKNOWN
    try:
        return TangoType(name)
    except ValueError:
        return TangoType.UNKNOWN


def _state(value: Any) -> TangoState:
    name = getattr(value, "name", str(value))
    return TangoState.__members__.get(name, TangoState.UNKNOWN)


def _quality(value: Any) -> AttrQuality:
    name = getattr(value, "name", str(value)).removeprefix("ATTR_")
    return AttrQuality.__members__.get(name, AttrQuality.INVALID)


def _data_format(value: Any) -> AttrDataFormat:
    return AttrDataFormat.__members__.get(getattr(value, "name", str(value)), AttrDataFormat.SCALAR)


def _write_type(value: Any) -> AttrWriteType:
    return AttrWriteType.__members__.get(getattr(value, "name", str(value)), AttrWriteType.READ)


def _display_level(value: Any) -> DisplayLevel:
    return DisplayLevel.__members__.get(getattr(value, "name", str(value)), DisplayLevel.OPERATOR)


def _to_python(value: Any) -> Any:
    """Tango states become ours; numpy arrays stay numpy arrays."""
    if isinstance(value, tango.DevState):
        return _state(value)
    return value


def _to_tango(value: Any) -> Any:
    if isinstance(value, TangoState):
        return tango.DevState.names[value.value]
    if isinstance(value, np.ndarray):
        return value
    if isinstance(value, (list, tuple)):
        return [_to_tango(item) for item in value]
    return value


def attribute_value(attribute: Any) -> AttributeValue:
    if attribute.has_failed:
        return AttributeValue(
            attribute.name,
            quality=AttrQuality.INVALID,
            error=_report(attribute.get_err_stack()),
        )
    return AttributeValue(
        name=attribute.name,
        value=_to_python(attribute.value),
        write_value=_to_python(attribute.w_value),
        quality=_quality(attribute.quality),
        timestamp=attribute.time.totime(),
        dim_x=attribute.dim_x,
        dim_y=attribute.dim_y,
        data_format=_data_format(attribute.data_format),
    )


def attribute_spec(info: Any) -> AttributeSpec:
    alarms = info.alarms
    events = info.events
    return AttributeSpec(
        name=info.name,
        data_type=_tango_type(info.data_type),
        data_format=_data_format(info.data_format),
        writable=_write_type(info.writable),
        label=info.label,
        unit=info.unit,
        standard_unit=info.standard_unit,
        display_unit=info.display_unit,
        display_format=info.format,
        description=info.description,
        min_value=info.min_value,
        max_value=info.max_value,
        alarms=AlarmConfig(
            min_alarm=alarms.min_alarm,
            max_alarm=alarms.max_alarm,
            min_warning=alarms.min_warning,
            max_warning=alarms.max_warning,
            delta_t=alarms.delta_t,
            delta_val=alarms.delta_val,
        ),
        events=EventConfig(
            change_abs=events.ch_event.abs_change,
            change_rel=events.ch_event.rel_change,
            period_ms=events.per_event.period,
            archive_abs=events.arch_event.archive_abs_change,
            archive_rel=events.arch_event.archive_rel_change,
            archive_period_ms=events.arch_event.archive_period,
        ),
        display_level=_display_level(info.disp_level),
        max_dim_x=info.max_dim_x,
        max_dim_y=info.max_dim_y,
        enum_labels=tuple(info.enum_labels),
        root_attribute=info.root_attr_name,
    )


def _apply_spec(info: Any, spec: AttributeSpec) -> Any:
    """Copy the editable settings onto a configuration read from the device."""
    info.label = spec.label
    info.unit = spec.unit
    info.standard_unit = spec.standard_unit
    info.display_unit = spec.display_unit
    info.format = spec.display_format
    info.description = spec.description
    info.min_value = spec.min_value
    info.max_value = spec.max_value
    info.alarms.min_alarm = spec.alarms.min_alarm
    info.alarms.max_alarm = spec.alarms.max_alarm
    info.alarms.min_warning = spec.alarms.min_warning
    info.alarms.max_warning = spec.alarms.max_warning
    info.alarms.delta_t = spec.alarms.delta_t
    info.alarms.delta_val = spec.alarms.delta_val
    info.events.ch_event.abs_change = spec.events.change_abs
    info.events.ch_event.rel_change = spec.events.change_rel
    info.events.per_event.period = spec.events.period_ms
    info.events.arch_event.archive_abs_change = spec.events.archive_abs
    info.events.arch_event.archive_rel_change = spec.events.archive_rel
    info.events.arch_event.archive_period = spec.events.archive_period_ms
    return info


def command_spec(info: Any) -> CommandSpec:
    return CommandSpec(
        name=info.cmd_name,
        in_type=_tango_type(info.in_type),
        out_type=_tango_type(info.out_type),
        in_description=info.in_type_desc,
        out_description=info.out_type_desc,
        display_level=_display_level(info.disp_level),
    )


def _event_data(ref: AttributeRef, event_type: EventType, event: Any) -> EventData:
    received = time.time()
    if event.err:
        return EventData(ref, event_type, error=_report(event.errors), received_at=received)
    value = event.attr_value
    if value is None:
        return EventData(ref, event_type, received_at=received)
    return EventData(ref, event_type, attribute_value(value), received_at=received)


def _browsable(name: str) -> bool:
    return not name.lower().startswith(f"{ADMIN_DOMAIN}/")


def _strings(values: Any) -> tuple[str, ...]:
    return tuple(str(value) for value in values)


def _dev_info(registration: DeviceRegistration) -> Any:
    info = tango.DbDevInfo()
    info.name = str(registration.name)
    info._class = registration.class_name
    info.server = str(registration.server)
    return info


def _history(entries: Sequence[Any]) -> tuple[PropertyHistoryEntry, ...]:
    """Newest first, as the rest of the application expects."""
    parsed = [
        PropertyHistoryEntry(
            entry.get_name(),
            _strings(entry.get_value().value_string),
            parse_db_date(entry.get_date()),
            entry.is_deleted(),
        )
        for entry in entries
    ]
    parsed.sort(key=lambda item: item.changed_at.timestamp() if item.changed_at else 0.0)
    return tuple(reversed(parsed))


# ------------------------------------------------------------------ host breaker


@dataclass(slots=True)
class _Breaker:
    """Stops sending calls to a host after repeated timeouts, for a while.

    Only timeouts count: a refused connection to a stopped server is fast and
    says nothing about the other servers on that host.
    """

    failures: int = 0
    open_until: float = 0.0

    def check(self, host: str) -> None:
        remaining = self.open_until - time.monotonic()
        if remaining > 0:
            raise DeviceUnreachable(
                f"host {host} stopped answering; next attempt in {remaining:.0f} s",
                context=ErrorContext("call", host),
            )

    def record(self, error: TangoError | None) -> None:
        if isinstance(error, RequestTimeout):
            self.failures += 1
            if self.failures >= BREAKER_THRESHOLD:
                self.open_until = time.monotonic() + BREAKER_COOLDOWN
                self.failures = 0
            return
        if error is None:
            self.failures = 0


# ----------------------------------------------------------------------- backend


class PyTangoBackend:
    """A connection to one Tango control system through PyTango."""

    def __init__(
        self,
        tango_host: str | None = None,
        *,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        writable: bool = True,
    ) -> None:
        self._explicit_host = tango_host
        configured = tango_host or tango.ApiUtil.get_env_var("TANGO_HOST") or ""
        if not configured:
            raise BackendUnavailable(
                "no control system configured: set TANGO_HOST or pass --tango-host"
            )
        self._tango_host = configured
        self._timeout_ms = timeout_ms
        self._writable = writable
        self._database: Any = None
        self._database_lock = asyncio.Lock()
        self._db_pool = ThreadPoolExecutor(DATABASE_WORKERS, thread_name_prefix="milonga-db")
        self._host_pools: dict[str, ThreadPoolExecutor] = {}
        self._breakers: dict[str, _Breaker] = {}
        self._hosts: dict[DeviceName, str] = {}
        self._proxies: dict[DeviceName, Any] = {}
        self._proxy_locks: dict[DeviceName, asyncio.Lock] = {}
        self._subscriptions: dict[int, tuple[DeviceName, int]] = {}
        self._active: set[int] = set()
        self._ids = itertools.count(1)

    @property
    def tango_host(self) -> str:
        return self._tango_host

    @property
    def writable(self) -> bool:
        return self._writable

    async def close(self) -> None:
        for subscription in list(self._subscriptions):
            await self.unsubscribe_event(subscription)
        self._db_pool.shutdown(wait=False, cancel_futures=True)
        for pool in self._host_pools.values():
            pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ plumbing

    async def _db[T](self, operation: str, call: Callable[[Any], T], target: str = "") -> T:
        database = await self._connect()
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._db_pool, call, database)
        except tango.DevFailed as error:
            raise convert_error(error, operation, target or "database") from None

    async def _connect(self) -> Any:
        if self._database is not None:
            return self._database
        async with self._database_lock:
            if self._database is None:
                loop = asyncio.get_running_loop()
                try:
                    self._database = await loop.run_in_executor(self._db_pool, self._open_database)
                except tango.DevFailed as error:
                    raise convert_error(error, "connect", self._tango_host) from None
        return self._database

    def _open_database(self) -> Any:
        if self._explicit_host is None:
            return tango.Database()
        host, _, port = self._explicit_host.partition(":")
        return tango.Database(host, int(port or 10000))

    def _full_name(self, device: DeviceName) -> str:
        if self._explicit_host is None:
            return str(device)
        return f"tango://{self._explicit_host}/{device}"

    async def _host_of(self, device: DeviceName) -> str:
        host = self._hosts.get(device)
        if host is not None:
            return host
        try:
            info = await self._db("get_device_info", lambda db: db.get_device_info(str(device)))
            host = str(info.host or "")
        except TangoError:
            host = ""
        self._hosts[device] = host
        return host

    def _pool(self, host: str) -> ThreadPoolExecutor:
        pool = self._host_pools.get(host)
        if pool is None:
            pool = ThreadPoolExecutor(HOST_WORKERS, thread_name_prefix=f"milonga-{host or 'any'}")
            self._host_pools[host] = pool
        return pool

    async def _proxy(self, device: DeviceName, host: str) -> Any:
        proxy = self._proxies.get(device)
        if proxy is not None:
            return proxy
        lock = self._proxy_locks.setdefault(device, asyncio.Lock())
        async with lock:
            proxy = self._proxies.get(device)
            if proxy is None:
                loop = asyncio.get_running_loop()
                try:
                    proxy = await loop.run_in_executor(self._pool(host), self._make_proxy, device)
                except tango.DevFailed as error:
                    raise convert_error(error, "connect", str(device)) from None
                self._proxies[device] = proxy
        return proxy

    def _make_proxy(self, device: DeviceName) -> Any:
        proxy = tango.DeviceProxy(self._full_name(device))
        proxy.set_timeout_millis(self._timeout_ms)
        return proxy

    async def _device[T](self, device: DeviceName, operation: str, call: Callable[[Any], T]) -> T:
        host = await self._host_of(device)
        breaker = self._breakers.setdefault(host, _Breaker())
        breaker.check(host or "unknown")
        proxy = await self._proxy(device, host)
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(self._pool(host), call, proxy)
        except tango.DevFailed as error:
            converted = convert_error(error, operation, str(device))
            breaker.record(converted)
            raise converted from None
        breaker.record(None)
        return result

    def _require_writable(self, operation: str) -> None:
        if not self._writable:
            raise ReadOnlyError(f"{operation} refused: backend is read-only")

    # ---------------------------------------------------------- hosts and servers

    async def get_host_list(self, pattern: str = "*") -> tuple[str, ...]:
        hosts = await self._db("get_host_list", lambda db: db.get_host_list(pattern))
        return tuple(sorted(_strings(hosts)))

    async def get_host_server_list(self, host: str) -> tuple[ServerName, ...]:
        servers = await self._db(
            "get_host_server_list", lambda db: db.get_host_server_list(host), host
        )
        return tuple(sorted(ServerName.parse(name) for name in servers))

    async def get_server_list(self, pattern: str = "*") -> tuple[ServerName, ...]:
        servers = await self._db("get_server_list", lambda db: db.get_server_list(pattern))
        return tuple(sorted(ServerName.parse(name) for name in servers))

    async def get_server_info(self, server: ServerName) -> ServerInfo:
        fields = await self._db(
            "get_server_info",
            lambda db: db.command_inout("DbGetServerInfo", str(server)),
            str(server),
        )
        return parse_server_info(_strings(fields))

    async def put_server_info(self, info: ServerInfo) -> None:
        self._require_writable("put_server_info")
        fields = [str(info.name), info.host, "1" if info.controlled else "0", str(info.level)]
        await self._db(
            "put_server_info",
            lambda db: db.command_inout("DbPutServerInfo", fields),
            str(info.name),
        )

    async def get_server_class_list(self, server: ServerName) -> tuple[str, ...]:
        classes = await self._db(
            "get_server_class_list",
            lambda db: db.get_server_class_list(str(server)),
            str(server),
        )
        return tuple(sorted(name for name in _strings(classes) if name != ADMIN_CLASS))

    async def add_server(self, server: ServerName, devices: Sequence[DeviceRegistration]) -> None:
        self._require_writable("add_server")
        infos = [_dev_info(device) for device in devices]
        await self._db("add_server", lambda db: db.add_server(str(server), infos), str(server))

    async def delete_server(self, server: ServerName) -> None:
        """The database keeps a server's record after its devices are gone;
        a later server of the same name would inherit its startup level."""
        self._require_writable("delete_server")
        await self._db("delete_server", lambda db: db.delete_server(str(server)), str(server))
        await self._db(
            "delete_server_info", lambda db: db.delete_server_info(str(server)), str(server)
        )

    async def rename_server(self, old: ServerName, new: ServerName) -> None:
        self._require_writable("rename_server")
        await self._db("rename_server", lambda db: db.rename_server(str(old), str(new)), str(old))

    # ------------------------------------------------------------------- devices

    async def get_device_domain_list(self, pattern: str = "*") -> tuple[str, ...]:
        domains = await self._db("get_device_domain", lambda db: db.get_device_domain(pattern))
        return tuple(sorted(name for name in _strings(domains) if name.lower() != ADMIN_DOMAIN))

    async def get_device_family_list(self, domain: str) -> tuple[str, ...]:
        families = await self._db(
            "get_device_family", lambda db: db.get_device_family(f"{domain}/*"), domain
        )
        return tuple(sorted(_strings(families)))

    async def get_device_member_list(self, domain: str, family: str) -> tuple[str, ...]:
        members = await self._db(
            "get_device_member",
            lambda db: db.get_device_member(f"{domain}/{family}/*"),
            f"{domain}/{family}",
        )
        return tuple(sorted(_strings(members)))

    async def get_device_list(self, pattern: str = "*/*/*") -> tuple[DeviceName, ...]:
        names = await self._db(
            "get_device_list", lambda db: db.command_inout("DbGetDeviceWideList", pattern)
        )
        return tuple(sorted(DeviceName.parse(name) for name in _strings(names) if _browsable(name)))

    async def get_device_list_for_class(self, class_name: str) -> tuple[DeviceName, ...]:
        names = await self._db(
            "get_device_name", lambda db: db.get_device_name("*", class_name), class_name
        )
        return tuple(sorted(DeviceName.parse(name) for name in _strings(names) if _browsable(name)))

    async def get_device_list_for_server(self, server: ServerName) -> tuple[DeviceName, ...]:
        flat = _strings(
            await self._db(
                "get_device_class_list",
                lambda db: db.get_device_class_list(str(server)),
                str(server),
            )
        )
        names = flat[0::2]
        return tuple(sorted(DeviceName.parse(name) for name in names if _browsable(name)))

    async def get_device_info(self, device: DeviceName) -> DeviceInfo:
        info = await self._db(
            "get_device_info", lambda db: db.get_device_info(str(device)), str(device)
        )
        alias = await self.get_alias_from_device(device)
        host = str(info.host or "")
        self._hosts[device] = host
        return DeviceInfo(
            name=device,
            class_name=str(info.class_name),
            server=ServerName.parse(str(info.ds_full_name)),
            host=host,
            exported=bool(info.exported),
            pid=int(info.pid) or None,
            ior=str(info.ior or ""),
            alias=alias,
            exported_at=parse_db_date(str(info.started_date or "")),
            unexported_at=parse_db_date(str(info.stopped_date or "")),
        )

    async def add_device(self, registration: DeviceRegistration) -> None:
        self._require_writable("add_device")
        info = _dev_info(registration)
        await self._db("add_device", lambda db: db.add_device(info), str(registration.name))

    async def delete_device(self, device: DeviceName) -> None:
        self._require_writable("delete_device")
        await self._db("delete_device", lambda db: db.delete_device(str(device)), str(device))
        self._forget(device)

    async def rename_device(self, old: DeviceName, new: DeviceName) -> None:
        """The database has no rename: register the new name, move, then delete.

        Not atomic. A failure part-way leaves both names registered, which is
        recoverable; deleting first would not be.
        """
        self._require_writable("rename_device")
        info = await self.get_device_info(old)
        properties = await self.get_device_properties(old)
        attribute_properties = await self.get_device_attribute_properties(old)
        await self.add_device(DeviceRegistration(new, info.class_name, info.server))
        if properties:
            await self.put_device_properties(new, properties)
        if attribute_properties:
            await self.put_device_attribute_properties(new, attribute_properties)
        if info.alias:
            await self.delete_device_alias(info.alias)
            await self.put_device_alias(new, info.alias)
        await self.delete_device(old)

    async def unexport_device(self, device: DeviceName) -> None:
        self._require_writable("unexport_device")
        await self._db("unexport_device", lambda db: db.unexport_device(str(device)), str(device))

    async def get_class_list(self, pattern: str = "*") -> tuple[str, ...]:
        classes = await self._db("get_class_list", lambda db: db.get_class_list(pattern))
        return tuple(sorted(name for name in _strings(classes) if name != ADMIN_CLASS))

    def _forget(self, device: DeviceName) -> None:
        self._proxies.pop(device, None)
        self._hosts.pop(device, None)

    # ------------------------------------------------------------------- aliases

    async def get_device_alias_list(self, pattern: str = "*") -> tuple[str, ...]:
        aliases = await self._db(
            "get_device_alias_list", lambda db: db.get_device_alias_list(pattern)
        )
        return tuple(sorted(_strings(aliases)))

    async def get_alias_from_device(self, device: DeviceName) -> str | None:
        try:
            alias = await self._db(
                "get_alias_from_device",
                lambda db: db.get_alias_from_device(str(device)),
                str(device),
            )
        except ObjectNotFound:
            return None
        return str(alias) or None

    async def get_device_from_alias(self, alias: str) -> DeviceName:
        name = await self._db(
            "get_device_from_alias", lambda db: db.get_device_from_alias(alias), alias
        )
        return DeviceName.parse(str(name))

    async def put_device_alias(self, device: DeviceName, alias: str) -> None:
        self._require_writable("put_device_alias")
        await self._db(
            "put_device_alias", lambda db: db.put_device_alias(str(device), alias), str(device)
        )

    async def delete_device_alias(self, alias: str) -> None:
        self._require_writable("delete_device_alias")
        await self._db("delete_device_alias", lambda db: db.delete_device_alias(alias), alias)

    # ---------------------------------------------------------------- properties

    async def get_device_property_names(self, device: DeviceName) -> tuple[str, ...]:
        names = await self._db(
            "get_device_property_list",
            lambda db: db.get_device_property_list(str(device), "*"),
            str(device),
        )
        return tuple(sorted(_strings(names)))

    async def get_device_properties(
        self, device: DeviceName, names: Sequence[str] | None = None
    ) -> tuple[PropertyEntry, ...]:
        wanted = (
            list(names) if names is not None else list(await self.get_device_property_names(device))
        )
        if not wanted:
            return ()
        values = await self._db(
            "get_device_property",
            lambda db: db.get_device_property(str(device), wanted),
            str(device),
        )
        return _entries(values, wanted, PropertyScope.DEVICE, str(device))

    async def put_device_properties(
        self, device: DeviceName, entries: Sequence[PropertyEntry]
    ) -> None:
        self._require_writable("put_device_property")
        payload = {entry.name: list(entry.values) for entry in entries}
        await self._db(
            "put_device_property",
            lambda db: db.put_device_property(str(device), payload),
            str(device),
        )

    async def delete_device_properties(self, device: DeviceName, names: Sequence[str]) -> None:
        self._require_writable("delete_device_property")
        wanted = list(names)
        await self._db(
            "delete_device_property",
            lambda db: db.delete_device_property(str(device), wanted),
            str(device),
        )

    async def get_device_property_history(
        self, device: DeviceName, name: str
    ) -> tuple[PropertyHistoryEntry, ...]:
        entries = await self._db(
            "get_device_property_history",
            lambda db: db.get_device_property_history(str(device), name),
            str(device),
        )
        return _history(entries)

    async def get_class_property_names(self, class_name: str) -> tuple[str, ...]:
        names = await self._db(
            "get_class_property_list",
            lambda db: db.get_class_property_list(class_name),
            class_name,
        )
        return tuple(sorted(_strings(names)))

    async def get_class_properties(
        self, class_name: str, names: Sequence[str] | None = None
    ) -> tuple[PropertyEntry, ...]:
        wanted = (
            list(names)
            if names is not None
            else list(await self.get_class_property_names(class_name))
        )
        if not wanted:
            return ()
        values = await self._db(
            "get_class_property",
            lambda db: db.get_class_property(class_name, wanted),
            class_name,
        )
        return _entries(values, wanted, PropertyScope.CLASS, class_name)

    async def put_class_properties(self, class_name: str, entries: Sequence[PropertyEntry]) -> None:
        self._require_writable("put_class_property")
        payload = {entry.name: list(entry.values) for entry in entries}
        await self._db(
            "put_class_property",
            lambda db: db.put_class_property(class_name, payload),
            class_name,
        )

    async def delete_class_properties(self, class_name: str, names: Sequence[str]) -> None:
        self._require_writable("delete_class_property")
        wanted = list(names)
        await self._db(
            "delete_class_property",
            lambda db: db.delete_class_property(class_name, wanted),
            class_name,
        )

    async def get_class_property_history(
        self, class_name: str, name: str
    ) -> tuple[PropertyHistoryEntry, ...]:
        entries = await self._db(
            "get_class_property_history",
            lambda db: db.get_class_property_history(class_name, name),
            class_name,
        )
        return _history(entries)

    async def get_device_attribute_properties(self, device: DeviceName) -> AttributePropertyMap:
        attributes = _strings(
            await self._db(
                "get_device_attribute_list",
                lambda db: db.command_inout("DbGetDeviceAttributeList", [str(device), "*"]),
                str(device),
            )
        )
        if not attributes:
            return {}
        values = await self._db(
            "get_device_attribute_property",
            lambda db: db.get_device_attribute_property(str(device), list(attributes)),
            str(device),
        )
        return _attribute_map(values)

    async def put_device_attribute_properties(
        self, device: DeviceName, properties: AttributePropertyMap
    ) -> None:
        self._require_writable("put_device_attribute_property")
        payload = {
            attribute: {name: list(values) for name, values in items.items()}
            for attribute, items in properties.items()
        }
        await self._db(
            "put_device_attribute_property",
            lambda db: db.put_device_attribute_property(str(device), payload),
            str(device),
        )

    async def delete_device_attribute_properties(
        self, device: DeviceName, attribute: str, names: Sequence[str]
    ) -> None:
        self._require_writable("delete_device_attribute_property")
        payload = {attribute: list(names)}
        await self._db(
            "delete_device_attribute_property",
            lambda db: db.delete_device_attribute_property(str(device), payload),
            str(device),
        )

    async def get_class_attribute_properties(self, class_name: str) -> AttributePropertyMap:
        attributes = _strings(
            await self._db(
                "get_class_attribute_list",
                lambda db: db.command_inout("DbGetClassAttributeList", [class_name, "*"]),
                class_name,
            )
        )
        if not attributes:
            return {}
        values = await self._db(
            "get_class_attribute_property",
            lambda db: db.get_class_attribute_property(class_name, list(attributes)),
            class_name,
        )
        return _attribute_map(values)

    async def put_class_attribute_properties(
        self, class_name: str, properties: AttributePropertyMap
    ) -> None:
        self._require_writable("put_class_attribute_property")
        payload = {
            attribute: {name: list(values) for name, values in items.items()}
            for attribute, items in properties.items()
        }
        await self._db(
            "put_class_attribute_property",
            lambda db: db.put_class_attribute_property(class_name, payload),
            class_name,
        )

    async def get_object_list(self, pattern: str = "*") -> tuple[str, ...]:
        objects = await self._db("get_object_list", lambda db: db.get_object_list(pattern))
        return tuple(sorted(_strings(objects)))

    async def get_object_property_names(self, obj: str) -> tuple[str, ...]:
        names = await self._db(
            "get_object_property_list",
            lambda db: db.get_object_property_list(obj, "*"),
            obj,
        )
        return tuple(sorted(_strings(names)))

    async def get_properties(
        self, obj: str, names: Sequence[str] | None = None
    ) -> tuple[PropertyEntry, ...]:
        wanted = (
            list(names) if names is not None else list(await self.get_object_property_names(obj))
        )
        if not wanted:
            return ()
        values = await self._db("get_property", lambda db: db.get_property(obj, wanted), obj)
        return _entries(values, wanted, PropertyScope.FREE, obj)

    async def put_properties(self, obj: str, entries: Sequence[PropertyEntry]) -> None:
        self._require_writable("put_property")
        payload = {entry.name: list(entry.values) for entry in entries}
        await self._db("put_property", lambda db: db.put_property(obj, payload), obj)

    async def delete_properties(self, obj: str, names: Sequence[str]) -> None:
        self._require_writable("delete_property")
        wanted = list(names)
        await self._db("delete_property", lambda db: db.delete_property(obj, wanted), obj)

    async def get_property_history(self, obj: str, name: str) -> tuple[PropertyHistoryEntry, ...]:
        entries = await self._db(
            "get_property_history", lambda db: db.get_property_history(obj, name), obj
        )
        return _history(entries)

    async def find_devices_by_property(
        self, name: str, value: str | None = None
    ) -> tuple[DeviceName, ...]:
        """One database call per device: fine for a beamline, slow for a facility."""
        found: list[DeviceName] = []
        for device in await self.get_device_list():
            (entry,) = await self.get_device_properties(device, [name])
            if entry.values and (value is None or any(value in item for item in entry.values)):
                found.append(device)
        return tuple(found)

    # ------------------------------------------------------------ live devices

    async def ping(self, device: DeviceName) -> float:
        return float(await self._device(device, "ping", lambda proxy: proxy.ping()))

    async def get_device_state(self, device: DeviceName) -> DeviceStateInfo:
        def read(proxy: Any) -> DeviceStateInfo:
            return DeviceStateInfo(_state(proxy.state()), str(proxy.status()), time.time())

        return await self._device(device, "state", read)

    async def get_device_version(self, device: DeviceName) -> DeviceVersionInfo:
        def read(proxy: Any) -> DeviceVersionInfo:
            info = proxy.info()
            try:
                release = tango_release(int(proxy.get_tango_lib_version()))
            except tango.DevFailed:
                release = ""
            return DeviceVersionInfo(
                idl_version=int(proxy.get_idl_version()),
                server_version=str(info.server_version),
                tango_release=release,
                doc_url=str(info.doc_url).removeprefix("Doc URL = ").strip(),
            )

        return await self._device(device, "info", read)

    async def get_attribute_specs(self, device: DeviceName) -> tuple[AttributeSpec, ...]:
        infos = await self._device(
            device, "get_attribute_config", lambda proxy: proxy.attribute_list_query_ex()
        )
        return tuple(attribute_spec(info) for info in infos)

    async def set_attribute_specs(self, device: DeviceName, specs: Sequence[AttributeSpec]) -> None:
        self._require_writable("set_attribute_config")

        def write(proxy: Any) -> None:
            infos = [
                _apply_spec(proxy.get_attribute_config_ex(spec.name)[0], spec) for spec in specs
            ]
            proxy.set_attribute_config(infos)

        await self._device(device, "set_attribute_config", write)

    async def read_attributes(
        self, device: DeviceName, names: Sequence[str]
    ) -> tuple[AttributeValue, ...]:
        wanted = list(names)
        values = await self._device(
            device, "read_attributes", lambda proxy: proxy.read_attributes(wanted)
        )
        return tuple(attribute_value(value) for value in values)

    async def write_attribute(self, device: DeviceName, name: str, value: Any) -> None:
        self._require_writable("write_attribute")
        converted = _to_tango(value)
        await self._device(
            device, "write_attribute", lambda proxy: proxy.write_attribute(name, converted)
        )

    async def get_command_specs(self, device: DeviceName) -> tuple[CommandSpec, ...]:
        infos = await self._device(
            device, "command_list_query", lambda proxy: proxy.command_list_query()
        )
        return tuple(command_spec(info) for info in infos)

    async def execute_command(self, device: DeviceName, command: str, argin: Any = None) -> Any:
        self._require_writable("command_inout")
        converted = _to_tango(argin)

        def run(proxy: Any) -> Any:
            if argin is None:
                return proxy.command_inout(command)
            return proxy.command_inout(command, converted)

        return _to_python(await self._device(device, f"command {command}", run))

    async def get_polling(self, device: DeviceName) -> tuple[PollingEntry, ...]:
        status = await self._device(device, "polling_status", lambda proxy: proxy.polling_status())
        return parse_polling_status(_strings(status))

    async def set_polling(
        self, device: DeviceName, name: str, kind: PollableKind, period_ms: int
    ) -> None:
        self._require_writable("set_polling")

        def poll(proxy: Any) -> None:
            if kind is PollableKind.ATTRIBUTE:
                proxy.poll_attribute(name, period_ms)
            else:
                proxy.poll_command(name, period_ms)

        await self._device(device, "set_polling", poll)

    async def stop_polling(self, device: DeviceName, name: str, kind: PollableKind) -> None:
        self._require_writable("stop_polling")

        def stop(proxy: Any) -> None:
            if kind is PollableKind.ATTRIBUTE:
                proxy.stop_poll_attribute(name)
            else:
                proxy.stop_poll_command(name)

        await self._device(device, "stop_polling", stop)

    async def get_logging_targets(self, device: DeviceName) -> tuple[LoggingTarget, ...]:
        targets = await self._device(
            device, "get_logging_target", lambda proxy: proxy.get_logging_target()
        )
        return tuple(parse_logging_target(str(target)) for target in targets)

    async def add_logging_target(self, device: DeviceName, target: LoggingTarget) -> None:
        self._require_writable("add_logging_target")
        text = format_logging_target(target)
        await self._device(
            device, "add_logging_target", lambda proxy: proxy.add_logging_target(text)
        )

    async def remove_logging_target(self, device: DeviceName, target: LoggingTarget) -> None:
        self._require_writable("remove_logging_target")
        text = format_logging_target(target)
        await self._device(
            device, "remove_logging_target", lambda proxy: proxy.remove_logging_target(text)
        )

    async def get_logging_level(self, device: DeviceName) -> LogLevel:
        level = await self._device(
            device, "get_logging_level", lambda proxy: proxy.get_logging_level()
        )
        return log_level_from_int(int(level))

    async def set_logging_level(self, device: DeviceName, level: LogLevel) -> None:
        self._require_writable("set_logging_level")
        value = log_level_to_int(level)
        await self._device(
            device, "set_logging_level", lambda proxy: proxy.set_logging_level(value)
        )

    # ------------------------------------------------------------------- events

    async def subscribe_event(
        self, ref: AttributeRef, event_type: EventType, callback: EventCallback
    ) -> int:
        loop = asyncio.get_running_loop()
        local_id = next(self._ids)
        self._active.add(local_id)

        def received(event: Any) -> None:
            data = _event_data(ref, event_type, event)
            # the loop is closed once the application has shut down
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(self._dispatch, local_id, callback, data)

        tango_type = _EVENT_TYPES[event_type]
        try:
            tango_id = await self._device(
                ref.device,
                "subscribe_event",
                lambda proxy: proxy.subscribe_event(ref.attribute, tango_type, received),
            )
        except TangoError:
            self._active.discard(local_id)
            raise
        self._subscriptions[local_id] = (ref.device, int(tango_id))
        return local_id

    def _dispatch(self, local_id: int, callback: EventCallback, data: EventData) -> None:
        if local_id in self._active:
            callback(data)

    async def unsubscribe_event(self, subscription: int) -> None:
        self._active.discard(subscription)
        entry = self._subscriptions.pop(subscription, None)
        if entry is None:
            return
        device, tango_id = entry
        with suppress(TangoError):
            await self._device(
                device, "unsubscribe_event", lambda proxy: proxy.unsubscribe_event(tango_id)
            )

    @property
    def subscription_count(self) -> int:
        return len(self._subscriptions)


def _entries(
    values: Mapping[str, Any], names: Sequence[str], scope: PropertyScope, owner: str
) -> tuple[PropertyEntry, ...]:
    return tuple(
        PropertyEntry(name, _strings(values.get(name, ())), scope, owner) for name in names
    )


def _attribute_map(values: Mapping[str, Any]) -> AttributePropertyMap:
    return {
        str(attribute): {str(name): _strings(items) for name, items in properties.items()}
        for attribute, properties in values.items()
        if properties
    }
