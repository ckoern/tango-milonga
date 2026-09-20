"""Live host state for the Astor views.

One subscription per host carries the whole server table: the Starter's
``Servers`` attribute is a line per controlled server with its state and
startup level, so a change event refreshes a host without any polling.
"""

from collections.abc import Sequence
from functools import partial

from PySide6.QtCore import QObject, Signal

from milonga.core.model import EventData, HostSnapshot, ServerSnapshot
from milonga.core.monitor import Watch
from milonga.core.services.control import (
    StarterControl,
    build_host_snapshot,
    unreachable_host,
)
from milonga.core.services.starter_protocol import parse_server_lines
from milonga.ui.context import AppContext


class LiveHosts(QObject):
    changed = Signal(str)

    def __init__(self, context: AppContext, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._watches: dict[str, Watch] = {}
        self._snapshots: dict[str, HostSnapshot] = {}
        self._uncontrolled: dict[str, tuple[ServerSnapshot, ...]] = {}

    @property
    def hosts(self) -> tuple[str, ...]:
        return tuple(self._snapshots)

    @property
    def watched(self) -> frozenset[str]:
        return frozenset(self._watches)

    def snapshot(self, host: str) -> HostSnapshot | None:
        return self._snapshots.get(host)

    def snapshots(self) -> tuple[HostSnapshot, ...]:
        return tuple(self._snapshots[host] for host in sorted(self._snapshots))

    async def watch(self, hosts: Sequence[str]) -> None:
        wanted = list(hosts)
        for host in list(self._watches):
            if host not in wanted:
                await self._watches.pop(host).aclose()
        groups = {host: self._context.groups.group_of(host) for host in wanted}
        for snapshot in await self._context.control.host_snapshots(wanted, groups=groups):
            # the Starter's events say nothing about these; they keep the state
            # probed at load until the next refresh
            self._uncontrolled[snapshot.name] = tuple(
                server for server in snapshot.servers if not server.info.is_controlled
            )
            self._publish(snapshot)
        for host in wanted:
            if host not in self._watches:
                self._watches[host] = await self._context.monitor.watch(
                    StarterControl.servers_attribute(host), partial(self._received, host)
                )

    async def release(self) -> None:
        for host in list(self._watches):
            await self._watches.pop(host).aclose()

    def _received(self, host: str, event: EventData) -> None:
        group = self._context.groups.group_of(host)
        if event.error is not None:
            self._publish(unreachable_host(host, event.error, group=group))
            return
        if event.value is None:
            return
        self._publish(
            build_host_snapshot(
                host,
                parse_server_lines(event.value.value),
                group=group,
                uncontrolled=self._uncontrolled.get(host, ()),
                updated_at=event.value.timestamp,
            )
        )

    def _publish(self, snapshot: HostSnapshot) -> None:
        self._snapshots[snapshot.name] = snapshot
        self._context.store.put_host(snapshot)
        self.changed.emit(snapshot.name)
