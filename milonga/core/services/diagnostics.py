"""Per-server facts a client can actually get: PID, uptime and Tango version.

Every device server has an admin device, ``dserver/<executable>/<instance>``.
Whether it is exported is what tells a client the server runs, and its info
carries the PID and the time it registered itself.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from milonga.core.backend.protocol import TangoBackend
from milonga.core.errors import ErrorReport, TangoError
from milonga.core.model import DeviceVersionInfo
from milonga.core.names import ServerName
from milonga.core.tasks import gather_limited


@dataclass(frozen=True, slots=True)
class ServerDetail:
    name: ServerName
    running: bool = False
    pid: int | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    version: DeviceVersionInfo | None = None
    error: ErrorReport | None = None

    def uptime(self, now: datetime | None = None) -> timedelta | None:
        if not self.running or self.started_at is None:
            return None
        return (now or datetime.now()) - self.started_at


class Diagnostics:
    def __init__(self, backend: TangoBackend) -> None:
        self._backend = backend

    async def server_detail(self, server: ServerName) -> ServerDetail:
        admin = server.admin_device
        try:
            info = await self._backend.get_device_info(admin)
        except TangoError as error:
            return ServerDetail(server, error=ErrorReport.from_exception(error))
        if not info.exported:
            return ServerDetail(server, stopped_at=info.unexported_at)
        version: DeviceVersionInfo | None = None
        try:
            version = await self._backend.get_device_version(admin)
        except TangoError:
            version = None
        return ServerDetail(
            server,
            running=True,
            pid=info.pid,
            started_at=info.exported_at,
            version=version,
        )

    async def server_details(
        self, servers: list[ServerName] | tuple[ServerName, ...]
    ) -> tuple[ServerDetail, ...]:
        return await gather_limited(servers, self.server_detail)


def format_uptime(delta: timedelta | None) -> str:
    if delta is None:
        return "—"
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min"
    return f"{seconds // 86400} d {seconds % 86400 // 3600} h"
