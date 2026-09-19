"""Database browsing, shaped for lazy trees."""

from collections.abc import Sequence

from milonga.core.backend.protocol import TangoBackend
from milonga.core.errors import ErrorReport, TangoError
from milonga.core.model import (
    DeviceInfo,
    DeviceSnapshot,
    DeviceStateInfo,
    ServerInfo,
    ServerSnapshot,
)
from milonga.core.names import DeviceName, ServerName
from milonga.core.tasks import gather_limited


class Inventory:
    def __init__(self, backend: TangoBackend) -> None:
        self._backend = backend

    async def domains(self, pattern: str = "*") -> tuple[str, ...]:
        return await self._backend.get_device_domain_list(pattern)

    async def families(self, domain: str) -> tuple[str, ...]:
        return await self._backend.get_device_family_list(domain)

    async def members(self, domain: str, family: str) -> tuple[str, ...]:
        return await self._backend.get_device_member_list(domain, family)

    async def device_snapshot(
        self, device: DeviceName, *, with_state: bool = True
    ) -> DeviceSnapshot:
        try:
            info = await self._backend.get_device_info(device)
        except TangoError as error:
            return DeviceSnapshot(
                DeviceInfo(device, "", ServerName("unknown", "unknown")),
                error=ErrorReport.from_exception(error),
            )
        if not with_state or not info.exported:
            return DeviceSnapshot(info)
        try:
            state = await self._backend.get_device_state(device)
        except TangoError as error:
            return DeviceSnapshot(info, error=ErrorReport.from_exception(error))
        return DeviceSnapshot(info, state)

    async def device_snapshots(
        self, devices: Sequence[DeviceName], *, with_state: bool = True
    ) -> tuple[DeviceSnapshot, ...]:
        async def load(device: DeviceName) -> DeviceSnapshot:
            return await self.device_snapshot(device, with_state=with_state)

        return await gather_limited(devices, load)

    async def server_snapshot(self, server: ServerName) -> ServerSnapshot:
        try:
            info = await self._backend.get_server_info(server)
        except TangoError:
            info = ServerInfo(server)
        devices = await self._backend.get_device_list_for_server(server)
        classes = await self._backend.get_server_class_list(server)
        return ServerSnapshot(info, classes=classes, device_count=len(devices))

    async def devices_of_class(self, class_name: str) -> tuple[DeviceName, ...]:
        return await self._backend.get_device_list_for_class(class_name)

    async def devices_of_server(self, server: ServerName) -> tuple[DeviceName, ...]:
        return await self._backend.get_device_list_for_server(server)

    async def resolve(self, text: str) -> DeviceName | None:
        """Accept a device name or an alias."""
        try:
            device = DeviceName.parse(text)
        except ValueError:
            try:
                return await self._backend.get_device_from_alias(text)
            except TangoError:
                return None
        return device if device in await self._backend.get_device_list() else None

    @staticmethod
    def unknown_state() -> DeviceStateInfo:
        return DeviceStateInfo()
