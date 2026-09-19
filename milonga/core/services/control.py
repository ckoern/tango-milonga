"""Process control through the Starter device.

Host state is computed from the ``Servers`` attribute rather than read from
``HostState``, because the aggregation rule (uncontrolled servers do not
count) has to match what the tree shows.
"""

from collections.abc import Sequence
from typing import Any

from milonga.core.backend.protocol import TangoBackend
from milonga.core.enums import NOT_CONTROLLED_LEVEL, ServerRunState
from milonga.core.errors import ErrorReport, TangoError
from milonga.core.model import (
    HostSnapshot,
    ServerInfo,
    ServerSnapshot,
    aggregate_host_state,
)
from milonga.core.names import AttributeRef, DeviceName, ServerName, starter_device
from milonga.core.services.starter_protocol import ServerLine, parse_server_lines
from milonga.core.tasks import gather_settled

SERVERS_ATTRIBUTE = "Servers"


def build_host_snapshot(
    host: str,
    lines: Sequence[ServerLine],
    *,
    group: str = "",
    known_servers: Sequence[ServerName] = (),
    updated_at: float = 0.0,
) -> HostSnapshot:
    """Turn parsed ``Servers`` lines into a snapshot, keeping DB-only servers."""
    servers = [
        ServerSnapshot(
            ServerInfo(line.name, host, line.level, line.controlled), run_state=line.run_state
        )
        for line in lines
    ]
    reported = {snapshot.name for snapshot in servers}
    servers.extend(
        ServerSnapshot(ServerInfo(name, host, NOT_CONTROLLED_LEVEL, controlled=False))
        for name in known_servers
        if name not in reported
    )
    servers.sort(key=lambda snapshot: (snapshot.info.level, str(snapshot.name)))
    return HostSnapshot(
        name=host,
        state=aggregate_host_state(servers),
        starter=starter_device(host),
        group=group,
        servers=tuple(servers),
        updated_at=updated_at,
    )


def unreachable_host(host: str, error: TangoError, *, group: str = "") -> HostSnapshot:
    return HostSnapshot(
        name=host,
        starter=starter_device(host),
        group=group,
        error=ErrorReport.from_exception(error),
    )


class StarterControl:
    """Client of the Starter devices of a control system."""

    def __init__(self, backend: TangoBackend) -> None:
        self._backend = backend

    @staticmethod
    def servers_attribute(host: str) -> AttributeRef:
        """The one attribute to subscribe to for a host's complete state."""
        return AttributeRef(starter_device(host), SERVERS_ATTRIBUTE)

    async def host_snapshot(self, host: str, *, group: str = "") -> HostSnapshot:
        starter = starter_device(host)
        try:
            values = await self._backend.read_attributes(starter, [SERVERS_ATTRIBUTE])
        except TangoError as error:
            return unreachable_host(host, error, group=group)
        known = await self._backend.get_host_server_list(host)
        lines = parse_server_lines(values[0].value if values else ())
        return build_host_snapshot(
            host,
            lines,
            group=group,
            known_servers=[name for name in known if name.exec_name != "Starter"],
            updated_at=values[0].timestamp if values else 0.0,
        )

    async def host_snapshots(
        self, hosts: Sequence[str], *, groups: dict[str, str] | None = None
    ) -> tuple[HostSnapshot, ...]:
        groups = groups or {}

        async def load(host: str) -> HostSnapshot:
            return await self.host_snapshot(host, group=groups.get(host, ""))

        results = await gather_settled(hosts, load)
        snapshots: list[HostSnapshot] = []
        for host, result in results:
            if isinstance(result, HostSnapshot):
                snapshots.append(result)
            elif isinstance(result, TangoError):
                snapshots.append(unreachable_host(host, result, group=groups.get(host, "")))
            elif isinstance(result, BaseException):
                raise result
        return tuple(snapshots)

    # ------------------------------------------------------------------- commands

    async def start_server(self, host: str, server: ServerName) -> None:
        await self._command(host, "DevStart", str(server))

    async def stop_server(self, host: str, server: ServerName) -> None:
        await self._command(host, "DevStop", str(server))

    async def restart_server(self, host: str, server: ServerName) -> None:
        await self.stop_server(host, server)
        await self.start_server(host, server)

    async def hard_kill_server(self, host: str, server: ServerName) -> None:
        await self._command(host, "HardKillServer", str(server))

    async def start_level(self, host: str, level: int) -> None:
        await self._command(host, "DevStartAll", level)

    async def stop_level(self, host: str, level: int) -> None:
        await self._command(host, "DevStopAll", level)

    async def running_servers(self, host: str) -> tuple[ServerName, ...]:
        result = await self._command(host, "DevGetRunningServers", True)
        return tuple(ServerName.parse(str(name)) for name in result or ())

    async def stopped_servers(self, host: str) -> tuple[ServerName, ...]:
        result = await self._command(host, "DevGetStopServers", True)
        return tuple(ServerName.parse(str(name)) for name in result or ())

    async def read_log(self, host: str, server: ServerName) -> str:
        return str(await self._command(host, "DevReadLog", str(server)) or "")

    async def reset_statistics(self, host: str) -> None:
        await self._command(host, "ResetStatistics")

    async def update_servers_info(self, host: str) -> None:
        await self._command(host, "UpdateServersInfo")

    # ------------------------------------------------------------------ database

    async def set_server_control(
        self, server: ServerName, host: str, *, level: int, controlled: bool = True
    ) -> None:
        """Change what a Starter controls, then make it re-read the database."""
        await self._backend.put_server_info(ServerInfo(server, host, level, controlled))
        await self.update_servers_info(host)

    async def move_server(self, server: ServerName, *, from_host: str, to_host: str) -> None:
        info = await self._backend.get_server_info(server)
        await self._backend.put_server_info(
            ServerInfo(server, to_host, info.level, info.controlled)
        )
        for host in (from_host, to_host):
            await self.update_servers_info(host)

    async def stopped_server_names(self, snapshot: HostSnapshot) -> tuple[ServerName, ...]:
        return tuple(
            server.name
            for server in snapshot.servers
            if server.run_state is ServerRunState.STOPPED and server.info.is_controlled
        )

    async def _command(self, host: str, command: str, argin: Any = None) -> Any:
        return await self._backend.execute_command(self._starter(host), command, argin)

    @staticmethod
    def _starter(host: str) -> DeviceName:
        return starter_device(host)
