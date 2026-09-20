"""Process control through the Starter device.

Host state is computed from the ``Servers`` attribute rather than read from
``HostState``, because the aggregation rule (uncontrolled servers do not
count) has to match what the tree shows.
"""

import asyncio
import time
from collections.abc import Sequence
from typing import Any

from milonga.core.backend.protocol import TangoBackend
from milonga.core.enums import NOT_CONTROLLED_LEVEL, ServerRunState
from milonga.core.errors import CommandFailed, ErrorReport, TangoError
from milonga.core.model import (
    HostSnapshot,
    ServerInfo,
    ServerSnapshot,
    aggregate_host_state,
)
from milonga.core.names import AttributeRef, DeviceName, ServerName, starter_device
from milonga.core.services.starter_protocol import ServerLine, parse_server_lines
from milonga.core.tasks import gather_limited, gather_settled

SERVERS_ATTRIBUTE = "Servers"
STARTER_CLASS = "Starter"


def build_host_snapshot(
    host: str,
    lines: Sequence[ServerLine],
    *,
    group: str = "",
    uncontrolled: Sequence[ServerSnapshot] = (),
    updated_at: float = 0.0,
) -> HostSnapshot:
    """Turn parsed ``Servers`` lines into a snapshot.

    The Starter reports only the servers it controls; the others that ran on
    the host are passed in as ``uncontrolled``, already probed.
    """
    servers = [
        ServerSnapshot(
            ServerInfo(line.name, host, line.level, line.controlled), run_state=line.run_state
        )
        for line in lines
    ]
    reported = {snapshot.name for snapshot in servers}
    servers.extend(server for server in uncontrolled if server.name not in reported)
    servers.sort(key=lambda snapshot: (snapshot.info.level, str(snapshot.name)))
    return HostSnapshot(
        name=host,
        state=aggregate_host_state(servers),
        starter=starter_device(host),
        group=group,
        servers=tuple(servers),
        updated_at=updated_at,
    )


def unreachable_host(
    host: str, error: TangoError | ErrorReport, *, group: str = ""
) -> HostSnapshot:
    report = error if isinstance(error, ErrorReport) else ErrorReport.from_exception(error)
    return HostSnapshot(
        name=host, starter=starter_device(host), group=group, error=report
    )


STOP_TIMEOUT = 20.0
START_TIMEOUT = 20.0
POLL_INTERVAL = 0.25
EXIT_GRACE = 5.0
"""For a server its Starter does not list: how long after unregistering a start
is accepted. The Starter drops a start while it still believes the server
alive, and it notices a stop about 4.6 s late (measured on TangoTest). Listed
servers need no guess: the start waits for the Starter to report them gone."""


class StarterControl:
    """Client of the Starter devices of a control system."""

    def __init__(
        self,
        backend: TangoBackend,
        *,
        stop_timeout: float = STOP_TIMEOUT,
        start_timeout: float = START_TIMEOUT,
        exit_grace: float = EXIT_GRACE,
    ) -> None:
        self._backend = backend
        self._stop_timeout = stop_timeout
        self._start_timeout = start_timeout
        self._exit_grace = exit_grace
        self._stopped: dict[ServerName, float] = {}

    async def controlled_hosts(self) -> tuple[str, ...]:
        """Hosts with a Starter, which are the ones process control applies to.

        The database's own host list also names every machine a server ever
        ran on, including containers and placeholders nothing controls.
        """
        starters = await self._backend.get_device_list_for_class(STARTER_CLASS)
        hosts = {
            device.member
            for device in starters
            if device.domain.lower() == "tango" and device.family.lower() == "admin"
        }
        return tuple(sorted(hosts, key=str.lower))

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
        lines = parse_server_lines(values[0].value if values else ())
        reported = {line.name for line in lines}
        known = await self._backend.get_host_server_list(host)
        unreported = [
            name for name in known if name.exec_name != STARTER_CLASS and name not in reported
        ]
        uncontrolled = await gather_limited(
            unreported, lambda name: self.probe_server(host, name)
        )
        return build_host_snapshot(
            host,
            lines,
            group=group,
            uncontrolled=uncontrolled,
            updated_at=values[0].timestamp if values else 0.0,
        )

    async def probe_server(self, host: str, server: ServerName) -> ServerSnapshot:
        """The run state of a server the Starter does not report.

        Its admin device is exported while it runs; an export left behind by
        a crash is told apart by the ping failing.
        """
        admin = server.admin_device
        info = ServerInfo(server, host, NOT_CONTROLLED_LEVEL, controlled=False)
        try:
            device = await self._backend.get_device_info(admin)
        except TangoError:
            return ServerSnapshot(info, ServerRunState.UNKNOWN)
        if not device.exported:
            return ServerSnapshot(info, ServerRunState.STOPPED, stopped_at=device.unexported_at)
        try:
            await self._backend.ping(admin)
        except TangoError:
            return ServerSnapshot(info, ServerRunState.NOT_RESPONDING, pid=device.pid)
        return ServerSnapshot(
            info, ServerRunState.RUNNING, pid=device.pid, started_at=device.exported_at
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
        stopped = self._stopped.pop(server, None)
        if stopped is not None and time.monotonic() - stopped < self._stop_timeout:
            await self._let_previous_process_exit(host, server)
        await self._command(host, "DevStart", str(server))

    async def _let_previous_process_exit(self, host: str, server: ServerName) -> None:
        """A start the Starter receives while it still believes the server alive
        comes to nothing, so wait until it has noticed the stop."""
        if await self._reported_state(host, server) is not None:
            await self.wait_for_report(host, server, ServerRunState.STOPPED)
            return
        await self.wait_until(server, running=False, timeout=self._stop_timeout)
        await asyncio.sleep(self._exit_grace)

    async def reported_line(self, host: str, server: ServerName) -> ServerLine | None:
        """The Starter's own view, for the servers that ran on its host."""
        values = await self._backend.read_attributes(starter_device(host), [SERVERS_ATTRIBUTE])
        for line in parse_server_lines(values[0].value if values else ()):
            if line.name == server:
                return line
        return None

    async def _reported_state(self, host: str, server: ServerName) -> ServerRunState | None:
        line = await self.reported_line(host, server)
        return line.run_state if line is not None else None

    async def wait_for_report(
        self,
        host: str,
        server: ServerName,
        state: ServerRunState,
        *,
        timeout: float | None = None,
    ) -> None:
        """The Starter's view trails the database by seconds in both directions."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (timeout if timeout is not None else self._stop_timeout)
        while await self._reported_state(host, server) is not state:
            if loop.time() >= deadline:
                raise CommandFailed(f"the Starter still reports {server} as not {state.value}")
            await asyncio.sleep(POLL_INTERVAL)

    async def stop_server(self, host: str, server: ServerName) -> None:
        """The Starter stops only what it controls; any other server is asked
        to stop through its admin device, which is what the Starter does too."""
        if await self._controls(host, server):
            await self._command(host, "DevStop", str(server))
        else:
            await self._backend.execute_command(server.admin_device, "Kill")
        self._stopped[server] = time.monotonic()

    async def _controls(self, host: str, server: ServerName) -> bool:
        line = await self.reported_line(host, server)
        return line is not None and line.controlled

    async def restart_server(self, host: str, server: ServerName) -> None:
        await self.stop_server(host, server)
        if not await self.wait_until(server, running=False, timeout=self._stop_timeout):
            raise CommandFailed(f"{server} did not stop within {self._stop_timeout:.0f} s")
        await self.start_and_confirm(host, server)

    async def start_and_confirm(self, host: str, server: ServerName) -> None:
        """Start a server once and wait until it has registered.

        Never started twice: a slow start cannot be told apart from a failed
        one, and a second start of a slow server would be a second process.
        """
        await self.start_server(host, server)
        if not await self.wait_until(server, running=True, timeout=self._start_timeout):
            raise CommandFailed(
                f"{server} did not register within {self._start_timeout:.0f} s; "
                "its Starter log may say why"
            )

    async def reload_server(self, server: ServerName) -> None:
        """A running server creates only the devices it read at startup;
        ``RestartServer`` makes it read the database again, in the same process."""
        await self._backend.execute_command(server.admin_device, "RestartServer")

    async def is_running(self, server: ServerName) -> bool:
        try:
            info = await self._backend.get_device_info(server.admin_device)
        except TangoError:
            return False
        return info.exported

    async def wait_until(self, server: ServerName, *, running: bool, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if await self.is_running(server) is running:
                return True
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(POLL_INTERVAL)

    async def hard_kill_server(self, host: str, server: ServerName) -> None:
        await self._command(host, "HardKillServer", str(server))

    async def start_level(self, host: str, level: int) -> None:
        await self._command(host, "DevStartAll", level)

    async def stop_level(self, host: str, level: int) -> None:
        await self._command(host, "DevStopAll", level)

    async def start_all(self, snapshot: HostSnapshot) -> None:
        """Levels come up in order, the way the Starter boots a host."""
        for level in snapshot.levels:
            await self.start_level(snapshot.name, level)

    async def stop_all(self, snapshot: HostSnapshot) -> None:
        for level in reversed(snapshot.levels):
            await self.stop_level(snapshot.name, level)

    async def running_servers(self, host: str) -> tuple[ServerName, ...]:
        result = await self._command(host, "DevGetRunningServers", True)
        return tuple(ServerName.parse(str(name)) for name in result or ())

    async def stopped_servers(self, host: str) -> tuple[ServerName, ...]:
        result = await self._command(host, "DevGetStopServers", True)
        return tuple(ServerName.parse(str(name)) for name in result or ())

    async def read_log(self, host: str, server: ServerName) -> str:
        """Empty when the Starter has no log yet; it reports that as an error."""
        try:
            return str(await self._command(host, "DevReadLog", str(server)) or "")
        except CommandFailed as error:
            if any(frame.reason.startswith("Cannot open") for frame in error.frames):
                return ""
            raise

    async def reset_statistics(self, host: str) -> None:
        await self._command(host, "ResetStatistics")

    async def update_servers_info(self, host: str) -> None:
        await self._command(host, "UpdateServersInfo")

    # ------------------------------------------------------------------ database

    async def set_server_control(
        self, server: ServerName, host: str, *, level: int, controlled: bool = True
    ) -> None:
        """Change a server's startup level, then make the Starter re-read it.

        This does not move a server between hosts: a Starter controls the
        servers that last ran on its host, whatever the server record says.
        """
        await self._backend.put_server_info(ServerInfo(server, host, level, controlled))
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
