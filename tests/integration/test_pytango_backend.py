"""The PyTango backend against the control system named by TANGO_HOST.

Run with ``pytest --integration``. Nothing outside the MilongaTest sandbox is
modified.
"""

import asyncio
import threading
from datetime import datetime

import pytest

from milonga.core.backend.pytango_backend import PyTangoBackend
from milonga.core.commands import (
    CommandRunner,
    CreateServer,
    DeleteDevice,
    DeleteServer,
    PropertyGateway,
    PropertyTarget,
    PutProperties,
    RenameDevice,
    SetDeviceAlias,
)
from milonga.core.enums import DataSource, EventType, HostState, PropertyScope, TangoState
from milonga.core.errors import DeviceUnreachable, ObjectNotFound, ReadOnlyError
from milonga.core.model import DeviceRegistration, EventData, PropertyEntry, ServerInfo
from milonga.core.monitor import MonitorHub
from milonga.core.names import AttributeRef, DeviceName, ServerName
from milonga.core.services.control import StarterControl
from tests.integration.conftest import (
    SANDBOX_ALIAS,
    SANDBOX_CLASS,
    SANDBOX_DEVICES,
    SANDBOX_OBJECT,
    SANDBOX_SERVER,
)

pytestmark = pytest.mark.integration

FIRST, SECOND = SANDBOX_DEVICES


async def _database_device(backend: PyTangoBackend) -> DeviceName:
    devices = await backend.get_device_list_for_class("DataBase")
    assert devices, "every control system has a DataBase device"
    return devices[0]


async def _starter(backend: PyTangoBackend) -> tuple[DeviceName, str]:
    for device in await backend.get_device_list_for_class("Starter"):
        info = await backend.get_device_info(device)
        if info.exported:
            try:
                await backend.ping(device)
            except DeviceUnreachable:
                continue
            return device, info.host
    pytest.skip("no running Starter in this control system")


# ----------------------------------------------------------------------- reading


async def test_listings_hide_admin_devices(tango_backend: PyTangoBackend) -> None:
    servers = await tango_backend.get_server_list()
    assert any(server.exec_name == "DataBaseds" for server in servers)
    assert "dserver" not in await tango_backend.get_device_domain_list()
    devices = await tango_backend.get_device_list()
    assert devices
    assert all(device.domain.lower() != "dserver" for device in devices)
    assert "DServer" not in await tango_backend.get_class_list()


async def test_device_info_of_the_database_device(tango_backend: PyTangoBackend) -> None:
    device = await _database_device(tango_backend)
    info = await tango_backend.get_device_info(device)
    assert info.class_name == "DataBase"
    assert info.exported
    assert isinstance(info.exported_at, datetime)
    assert info.server in await tango_backend.get_server_list()


async def test_the_live_interface_of_a_running_device(tango_backend: PyTangoBackend) -> None:
    device, _host = await _starter(tango_backend)
    assert await tango_backend.ping(device) > 0
    state = await tango_backend.get_device_state(device)
    assert state.state is not TangoState.UNKNOWN
    specs = {spec.name for spec in await tango_backend.get_attribute_specs(device)}
    assert {"State", "Status", "Servers"} <= specs
    commands = {spec.name for spec in await tango_backend.get_command_specs(device)}
    assert {"DevStart", "DevStop", "UpdateServersInfo"} <= commands
    version = await tango_backend.get_device_version(device)
    assert version.idl_version >= 5
    assert version.tango_release
    values = await tango_backend.read_attributes(device, ["State", "Status"])
    assert isinstance(values[0].value, TangoState)
    assert values[1].value


async def test_names_match_whatever_their_case(tango_backend: PyTangoBackend) -> None:
    device, _host = await _starter(tango_backend)
    shouting = DeviceName(device.domain.upper(), device.family.upper(), device.member.upper())
    assert shouting == device
    assert (await tango_backend.get_device_info(shouting)).name == device


async def test_polling_status_is_parsed(tango_backend: PyTangoBackend) -> None:
    device, _host = await _starter(tango_backend)
    entries = await tango_backend.get_polling(device)
    if not entries:
        pytest.skip("the Starter polls nothing")
    assert all(entry.period_ms > 0 for entry in entries)


async def test_errors_are_classified(tango_backend: PyTangoBackend) -> None:
    with pytest.raises(ObjectNotFound):
        await tango_backend.get_device_info(DeviceName.parse("no/such/device"))
    device, _host = await _starter(tango_backend)
    good, bad = await tango_backend.read_attributes(device, ["State", "NoSuchAttribute"])
    assert good.ok
    assert bad.error is not None and bad.error.reason == "API_AttrNotFound"
    with pytest.raises(DeviceUnreachable):
        await tango_backend.ping(await _unexported(tango_backend))


async def _unexported(backend: PyTangoBackend) -> DeviceName:
    for device in await backend.get_device_list():
        if not (await backend.get_device_info(device)).exported:
            return device
    pytest.skip("every device is exported")


async def test_events_arrive_on_the_event_loop(tango_backend: PyTangoBackend) -> None:
    device, _host = await _starter(tango_backend)
    loop_thread = threading.get_ident()
    seen: list[tuple[int, EventData]] = []
    subscription = await tango_backend.subscribe_event(
        AttributeRef(device, "State"),
        EventType.CHANGE,
        lambda event: seen.append((threading.get_ident(), event)),
    )
    await asyncio.sleep(0.5)
    await tango_backend.unsubscribe_event(subscription)
    assert seen, "a subscription delivers the current value first"
    assert all(thread == loop_thread for thread, _event in seen)
    assert seen[0][1].value is not None


async def test_the_monitor_polls_what_has_no_event_criteria(
    tango_backend: PyTangoBackend,
) -> None:
    device, _host = await _starter(tango_backend)
    hub = MonitorHub(tango_backend, fallback_period=0.2)
    seen: list[EventData] = []
    watch = await hub.watch(AttributeRef(device, "HostState"), seen.append)
    await asyncio.sleep(0.5)
    source = watch.source
    await hub.aclose()
    assert seen and seen[0].value is not None
    assert source in (DataSource.EVENTS, DataSource.POLLING)


async def test_the_starter_snapshot(tango_backend: PyTangoBackend) -> None:
    _device, host = await _starter(tango_backend)
    snapshot = await StarterControl(tango_backend).host_snapshot(host)
    assert snapshot.error is None
    assert snapshot.state is not HostState.UNREACHABLE


# ----------------------------------------------------------------------- writing


async def test_the_sandbox_is_registered(sandbox: PyTangoBackend) -> None:
    assert set(await sandbox.get_device_list_for_server(SANDBOX_SERVER)) == set(SANDBOX_DEVICES)
    assert SANDBOX_CLASS in await sandbox.get_class_list()
    assert not (await sandbox.get_device_info(FIRST)).exported


async def test_a_server_record_round_trips(sandbox: PyTangoBackend) -> None:
    before = await sandbox.get_server_info(SANDBOX_SERVER)
    assert not before.controlled
    await sandbox.put_server_info(ServerInfo(SANDBOX_SERVER, "", level=3, controlled=True))
    after = await sandbox.get_server_info(SANDBOX_SERVER)
    assert after.level == 3 and after.controlled


async def test_device_properties_with_history_and_undo(sandbox: PyTangoBackend) -> None:
    runner = CommandRunner(sandbox)
    gateway = PropertyGateway(sandbox)
    target = PropertyTarget.device(FIRST)
    await runner.run([PutProperties(target, (PropertyEntry("Speed", ("1.0",)),))])
    second = PutProperties(target, (PropertyEntry("Speed", ("2.0", "3.0")),))
    await runner.run([second])
    (entry,) = await gateway.read(target, ["Speed"])
    assert entry.values == ("2.0", "3.0")
    history = await gateway.history(target, "Speed")
    assert history[0].values == ("2.0", "3.0")
    await runner.undo(second)
    (entry,) = await gateway.read(target, ["Speed"])
    assert entry.values == ("1.0",)


async def test_class_and_free_properties(sandbox: PyTangoBackend) -> None:
    gateway = PropertyGateway(sandbox)
    for target in (PropertyTarget.device_class(SANDBOX_CLASS), PropertyTarget.free(SANDBOX_OBJECT)):
        await gateway.write(target, [PropertyEntry("Note", ("written by the tests",))])
        (entry,) = await gateway.read(target, ["Note"])
        assert entry.values == ("written by the tests",)
        assert entry.scope in (PropertyScope.CLASS, PropertyScope.FREE)
        assert await gateway.history(target, "Note")
        await gateway.delete(target, ["Note"])
        assert "Note" not in await gateway.names(target)


async def test_attribute_properties_round_trip(sandbox: PyTangoBackend) -> None:
    await sandbox.put_device_attribute_properties(FIRST, {"position": {"unit": ("mm",)}})
    properties = await sandbox.get_device_attribute_properties(FIRST)
    assert properties["position"]["unit"] == ("mm",)
    await sandbox.delete_device_attribute_properties(FIRST, "position", ["unit"])
    assert "position" not in await sandbox.get_device_attribute_properties(FIRST)


async def test_aliases_with_undo(sandbox: PyTangoBackend) -> None:
    runner = CommandRunner(sandbox)
    command = SetDeviceAlias(FIRST, SANDBOX_ALIAS)
    await runner.run([command])
    assert await sandbox.get_alias_from_device(FIRST) == SANDBOX_ALIAS
    assert await sandbox.get_device_from_alias(SANDBOX_ALIAS) == FIRST
    await runner.undo(command)
    assert await sandbox.get_alias_from_device(FIRST) is None


async def test_renaming_a_device_moves_its_properties_and_alias(sandbox: PyTangoBackend) -> None:
    renamed = DeviceName.parse("milonga/test/9")
    await sandbox.put_device_properties(FIRST, [PropertyEntry("Speed", ("5",))])
    await sandbox.put_device_alias(FIRST, SANDBOX_ALIAS)
    runner = CommandRunner(sandbox)
    command = RenameDevice(FIRST, renamed)
    await runner.run([command])
    devices = await sandbox.get_device_list_for_server(SANDBOX_SERVER)
    assert renamed in devices and FIRST not in devices
    (entry,) = await sandbox.get_device_properties(renamed, ["Speed"])
    assert entry.values == ("5",)
    assert await sandbox.get_alias_from_device(renamed) == SANDBOX_ALIAS
    await runner.undo(command)
    assert FIRST in await sandbox.get_device_list_for_server(SANDBOX_SERVER)


async def test_deleting_a_device_can_be_undone(sandbox: PyTangoBackend) -> None:
    await sandbox.put_device_properties(SECOND, [PropertyEntry("Speed", ("7",))])
    runner = CommandRunner(sandbox)
    command = DeleteDevice(SECOND)
    await runner.run([command])
    assert SECOND not in await sandbox.get_device_list_for_server(SANDBOX_SERVER)
    await runner.undo(command)
    (entry,) = await sandbox.get_device_properties(SECOND, ["Speed"])
    assert entry.values == ("7",)


async def test_creating_and_deleting_a_server(sandbox: PyTangoBackend) -> None:
    server = ServerName(SANDBOX_SERVER.exec_name, "second")
    device = DeviceName.parse("milonga/test/3")
    runner = CommandRunner(sandbox)
    create = CreateServer(server, (DeviceRegistration(device, SANDBOX_CLASS, server),))
    await runner.run([create])
    assert device in await sandbox.get_device_list_for_server(server)
    delete = DeleteServer(server)
    await runner.run([delete])
    assert server not in await sandbox.get_server_list()
    await runner.undo(delete)
    assert device in await sandbox.get_device_list_for_server(server)


async def test_renaming_a_server(sandbox: PyTangoBackend) -> None:
    renamed = ServerName(SANDBOX_SERVER.exec_name, "renamed")
    await sandbox.rename_server(SANDBOX_SERVER, renamed)
    assert set(await sandbox.get_device_list_for_server(renamed)) == set(SANDBOX_DEVICES)
    await sandbox.rename_server(renamed, SANDBOX_SERVER)


async def test_a_read_only_backend_writes_nothing(sandbox: PyTangoBackend) -> None:
    guarded = PyTangoBackend(writable=False)
    with pytest.raises(ReadOnlyError):
        await guarded.put_device_properties(FIRST, [PropertyEntry("Speed", ("9",))])
    await guarded.close()
    assert "Speed" not in await sandbox.get_device_property_names(FIRST)
