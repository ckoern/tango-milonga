"""The single source of truth for what the UI shows.

Updates are applied as whole snapshots and announced as a diff naming only
what changed, so views can repaint rows instead of resetting models.
"""

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace

from milonga.core.model import (
    AttributeValue,
    DeviceSnapshot,
    HostSnapshot,
    ServerSnapshot,
)
from milonga.core.names import AttributeRef, DeviceName, ServerName

type StoreListener = Callable[["StoreDiff"], None]
type Unsubscribe = Callable[[], None]


@dataclass(frozen=True, slots=True)
class StoreDiff:
    hosts: frozenset[str] = frozenset()
    servers: frozenset[ServerName] = frozenset()
    devices: frozenset[DeviceName] = frozenset()
    attributes: frozenset[AttributeRef] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.hosts or self.servers or self.devices or self.attributes)

    def merged(self, other: "StoreDiff") -> "StoreDiff":
        return StoreDiff(
            self.hosts | other.hosts,
            self.servers | other.servers,
            self.devices | other.devices,
            self.attributes | other.attributes,
        )

    def touches_device(self, device: DeviceName) -> bool:
        return device in self.devices or any(ref.device == device for ref in self.attributes)


class SystemStore:
    """Holds snapshots and notifies listeners with the diff they caused."""

    def __init__(self) -> None:
        self._hosts: dict[str, HostSnapshot] = {}
        self._servers: dict[ServerName, ServerSnapshot] = {}
        self._devices: dict[DeviceName, DeviceSnapshot] = {}
        self._values: dict[AttributeRef, AttributeValue] = {}
        self._listeners: list[StoreListener] = []
        self._depth = 0
        self._pending = StoreDiff()

    # -------------------------------------------------------------------- reading

    @property
    def hosts(self) -> Mapping[str, HostSnapshot]:
        return self._hosts

    @property
    def servers(self) -> Mapping[ServerName, ServerSnapshot]:
        return self._servers

    @property
    def devices(self) -> Mapping[DeviceName, DeviceSnapshot]:
        return self._devices

    @property
    def values(self) -> Mapping[AttributeRef, AttributeValue]:
        return self._values

    def host(self, name: str) -> HostSnapshot | None:
        return self._hosts.get(name)

    def server(self, name: ServerName) -> ServerSnapshot | None:
        return self._servers.get(name)

    def device(self, name: DeviceName) -> DeviceSnapshot | None:
        return self._devices.get(name)

    def value(self, ref: AttributeRef) -> AttributeValue | None:
        return self._values.get(ref)

    def servers_of(self, host: str) -> tuple[ServerSnapshot, ...]:
        snapshot = self._hosts.get(host)
        return snapshot.servers if snapshot else ()

    # -------------------------------------------------------------------- writing

    def put_host(self, snapshot: HostSnapshot) -> None:
        previous = self._hosts.get(snapshot.name)
        if previous == snapshot:
            return
        self._hosts[snapshot.name] = snapshot
        changed_servers = _changed_servers(previous, snapshot)
        for server in snapshot.servers:
            self._servers[server.name] = server
        self._emit(StoreDiff(hosts=frozenset({snapshot.name}), servers=changed_servers))

    def put_hosts(self, snapshots: Iterable[HostSnapshot]) -> None:
        with self.batch():
            for snapshot in snapshots:
                self.put_host(snapshot)

    def put_server(self, snapshot: ServerSnapshot) -> None:
        if self._servers.get(snapshot.name) == snapshot:
            return
        self._servers[snapshot.name] = snapshot
        host = self._hosts.get(snapshot.info.host)
        if host is not None:
            servers = tuple(
                snapshot if existing.name == snapshot.name else existing
                for existing in host.servers
            )
            self._hosts[host.name] = replace(host, servers=servers)
            self._emit(StoreDiff(hosts=frozenset({host.name}), servers=frozenset({snapshot.name})))
            return
        self._emit(StoreDiff(servers=frozenset({snapshot.name})))

    def put_device(self, snapshot: DeviceSnapshot) -> None:
        if self._devices.get(snapshot.name) == snapshot:
            return
        self._devices[snapshot.name] = snapshot
        self._emit(StoreDiff(devices=frozenset({snapshot.name})))

    def put_devices(self, snapshots: Iterable[DeviceSnapshot]) -> None:
        with self.batch():
            for snapshot in snapshots:
                self.put_device(snapshot)

    def put_value(self, ref: AttributeRef, value: AttributeValue) -> None:
        # Values are not compared with the previous reading: they may hold numpy
        # arrays, whose equality is elementwise rather than a bool.
        self._values[ref] = value
        self._emit(StoreDiff(attributes=frozenset({ref})))

    def remove_host(self, name: str) -> None:
        snapshot = self._hosts.pop(name, None)
        if snapshot is None:
            return
        for server in snapshot.servers:
            self._servers.pop(server.name, None)
        self._emit(
            StoreDiff(
                hosts=frozenset({name}),
                servers=frozenset(server.name for server in snapshot.servers),
            )
        )

    def remove_device(self, name: DeviceName) -> None:
        if self._devices.pop(name, None) is None:
            return
        for ref in [ref for ref in self._values if ref.device == name]:
            del self._values[ref]
        self._emit(StoreDiff(devices=frozenset({name})))

    def clear(self) -> None:
        with self.batch():
            for name in list(self._hosts):
                self.remove_host(name)
            for device in list(self._devices):
                self.remove_device(device)

    # ------------------------------------------------------------------ listeners

    def subscribe(self, listener: StoreListener) -> Unsubscribe:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Coalesce every change made inside the block into one notification."""
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0 and self._pending:
                diff, self._pending = self._pending, StoreDiff()
                self._notify(diff)

    def _emit(self, diff: StoreDiff) -> None:
        if self._depth:
            self._pending = self._pending.merged(diff)
            return
        self._notify(diff)

    def _notify(self, diff: StoreDiff) -> None:
        for listener in list(self._listeners):
            listener(diff)


def _changed_servers(previous: HostSnapshot | None, current: HostSnapshot) -> frozenset[ServerName]:
    if previous is None:
        return frozenset(server.name for server in current.servers)
    before = {server.name: server for server in previous.servers}
    after = {server.name: server for server in current.servers}
    names = set(before) | set(after)
    return frozenset(name for name in names if before.get(name) != after.get(name))
