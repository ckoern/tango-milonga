import numpy as np
import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.backend.readonly import ReadOnlyBackend
from milonga.core.enums import AttrDataFormat, PollableKind, ServerRunState
from milonga.core.errors import DeviceUnreachable, ObjectNotFound, ReadOnlyError
from milonga.core.model import DeviceRegistration, PropertyEntry, ServerInfo
from milonga.core.names import DeviceName, ServerName, starter_device

TEST_DEVICE = DeviceName.parse("sys/tg_test/1")
MOTOR = DeviceName.parse("id09/motor/phi")
VACUUM = ServerName.parse("Vacuum/id09-front")
HOST = "id09-srv-02"


async def test_demo_system_is_populated(backend: FakeBackend) -> None:
    assert len(await backend.get_host_list()) == 6
    assert TEST_DEVICE in await backend.get_device_list()
    assert "TangoTest" in await backend.get_class_list()


async def test_reads_scalar_spectrum_and_image(backend: FakeBackend) -> None:
    values = await backend.read_attributes(
        TEST_DEVICE, ["double_scalar", "double_spectrum", "double_image"]
    )
    scalar, spectrum, image = values
    assert scalar.value == pytest.approx(12.8741)
    assert spectrum.data_format is AttrDataFormat.SPECTRUM
    assert spectrum.dim_x == 1024
    assert image.dim_x == 256 and image.dim_y == 256
    assert isinstance(image.value, np.ndarray)


async def test_write_attribute_updates_read_value(backend: FakeBackend) -> None:
    await backend.write_attribute(TEST_DEVICE, "double_scalar", 42.0)
    (value,) = await backend.read_attributes(TEST_DEVICE, ["double_scalar"])
    assert value.value == 42.0
    assert value.write_value == 42.0


async def test_write_to_read_only_attribute_fails(backend: FakeBackend) -> None:
    with pytest.raises(Exception, match="not writable"):
        await backend.write_attribute(TEST_DEVICE, "throughput", 1.0)


async def test_an_unknown_attribute_fails_alone_in_a_batch(backend: FakeBackend) -> None:
    good, bad = await backend.read_attributes(TEST_DEVICE, ["double_scalar", "nope"])
    assert good.ok
    assert bad.error is not None and bad.error.reason == "API_AttrNotFound"


async def test_unreachable_host_hides_its_devices(backend: FakeBackend) -> None:
    backend.set_host_reachable(HOST, False)
    with pytest.raises(DeviceUnreachable):
        await backend.get_device_state(TEST_DEVICE)


async def test_stopped_server_unexports_devices(backend: FakeBackend) -> None:
    backend.stop_server("TangoTest/test")
    info = await backend.get_device_info(TEST_DEVICE)
    assert info.exported is False
    with pytest.raises(DeviceUnreachable):
        await backend.ping(TEST_DEVICE)


async def test_properties_round_trip_with_history(backend: FakeBackend) -> None:
    await backend.put_device_properties(MOTOR, [PropertyEntry("Velocity", ("4.0",))])
    (entry,) = await backend.get_device_properties(MOTOR, ["Velocity"])
    assert entry.values == ("4.0",)
    history = await backend.get_device_property_history(MOTOR, "Velocity")
    assert [item.values for item in history] == [("4.0",), ("2.5",)]


async def test_deleting_a_property_is_recorded(backend: FakeBackend) -> None:
    await backend.delete_device_properties(MOTOR, ["Velocity"])
    assert "Velocity" not in await backend.get_device_property_names(MOTOR)
    assert (await backend.get_device_property_history(MOTOR, "Velocity"))[0].deleted


async def test_missing_property_reads_as_empty(backend: FakeBackend) -> None:
    (entry,) = await backend.get_device_properties(MOTOR, ["Absent"])
    assert entry.values == ()


async def test_class_and_free_properties(backend: FakeBackend) -> None:
    (encoder,) = await backend.get_class_properties("IcePAPMotor", ["EncoderType"])
    assert encoder.values == ("ABSOLUTE",)
    assert "Milonga" in await backend.get_object_list()
    (groups,) = await backend.get_properties("Milonga", ["HostGroups"])
    assert groups.values[0].startswith("Beamline:")


async def test_find_devices_by_property(backend: FakeBackend) -> None:
    found = await backend.find_devices_by_property("AxisNumber")
    assert MOTOR in found
    assert await backend.find_devices_by_property("AxisNumber", "3") == (MOTOR,)


async def test_alias_lookup(backend: FakeBackend) -> None:
    assert await backend.get_alias_from_device(MOTOR) == "phi"
    assert await backend.get_device_from_alias("phi") == MOTOR
    with pytest.raises(ObjectNotFound):
        await backend.get_device_from_alias("missing")


async def test_add_and_delete_server(backend: FakeBackend) -> None:
    server = ServerName("NewServer", "1")
    device = DeviceName.parse("test/new/1")
    await backend.add_server(server, [DeviceRegistration(device, "NewClass", server)])
    assert device in await backend.get_device_list_for_server(server)
    await backend.delete_server(server)
    assert device not in await backend.get_device_list()


async def test_rename_server_moves_devices(backend: FakeBackend) -> None:
    new = ServerName("TangoTest", "renamed")
    await backend.rename_server(ServerName("TangoTest", "test"), new)
    assert TEST_DEVICE in await backend.get_device_list_for_server(new)


async def test_rename_device_keeps_properties(backend: FakeBackend) -> None:
    new = DeviceName.parse("id09/motor/phi2")
    await backend.rename_device(MOTOR, new)
    assert (await backend.get_device_properties(new, ["AxisNumber"]))[0].values == ("3",)


async def test_put_server_info_changes_level(backend: FakeBackend) -> None:
    await backend.put_server_info(ServerInfo(VACUUM, HOST, level=5, controlled=True))
    assert (await backend.get_server_info(VACUUM)).level == 5


async def test_polling_configuration(backend: FakeBackend) -> None:
    await backend.set_polling(MOTOR, "position", PollableKind.ATTRIBUTE, 200)
    (entry,) = await backend.get_polling(MOTOR)
    assert entry.period_ms == 200 and entry.polled
    await backend.stop_polling(MOTOR, "position", PollableKind.ATTRIBUTE)
    assert await backend.get_polling(MOTOR) == ()


async def test_command_handlers(backend: FakeBackend) -> None:
    assert await backend.execute_command(TEST_DEVICE, "DevDouble", 2.5) == 2.5
    assert await backend.execute_command(TEST_DEVICE, "DevString", "abc") == "ABC"
    with pytest.raises(ObjectNotFound):
        await backend.execute_command(TEST_DEVICE, "Missing")


async def test_starter_commands_drive_server_state(backend: FakeBackend) -> None:
    starter = starter_device(HOST)
    await backend.execute_command(starter, "DevStart", "Vacuum/id09-front")
    assert backend.servers[VACUUM].run_state is ServerRunState.RUNNING
    await backend.execute_command(starter, "HardKillServer", "Vacuum/id09-front")
    assert backend.servers[VACUUM].run_state is ServerRunState.STOPPED
    assert "killed" in await backend.execute_command(starter, "DevReadLog", "Vacuum/id09-front")


async def test_starter_level_commands(backend: FakeBackend) -> None:
    starter = starter_device(HOST)
    await backend.execute_command(starter, "DevStopAll", 2)
    stopped = await backend.execute_command(starter, "DevGetStopServers", True)
    assert "IcePAP/id09" in stopped
    await backend.execute_command(starter, "DevStartAll", 2)
    assert "IcePAP/id09" in await backend.execute_command(starter, "DevGetRunningServers", True)


async def test_read_only_backend_refuses_writes(backend: FakeBackend) -> None:
    guarded = ReadOnlyBackend(backend)
    assert guarded.writable is False
    assert len(await guarded.get_host_list()) == 6
    with pytest.raises(ReadOnlyError):
        await guarded.put_device_properties(MOTOR, [PropertyEntry("Velocity", ("9",))])
    assert (await backend.get_device_properties(MOTOR, ["Velocity"]))[0].values == ("2.5",)


async def test_read_only_backend_can_allow_commands(backend: FakeBackend) -> None:
    guarded = ReadOnlyBackend(backend, allow_commands=True)
    assert await guarded.execute_command(TEST_DEVICE, "DevDouble", 1.0) == 1.0
