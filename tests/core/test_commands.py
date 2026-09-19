import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.backend.protocol import TangoBackend
from milonga.core.backend.readonly import ReadOnlyBackend
from milonga.core.commands import (
    CommandRunner,
    CopyProperties,
    DeleteProperties,
    DiffKind,
    PropertyGateway,
    PropertyTarget,
    PutProperties,
    RenameProperty,
    RevertUnsupported,
    SetAttributeConfig,
    config_values,
)
from milonga.core.commands.base import Command, Diff
from milonga.core.errors import ReadOnlyError, TangoError
from milonga.core.model import AlarmConfig, AttributeSpec, PropertyEntry
from milonga.core.names import DeviceName

MOTOR = PropertyTarget.device("id09/motor/phi")
THETA = PropertyTarget.device("id09/motor/theta")
MOTOR_CLASS = PropertyTarget.device_class("IcePAPMotor")
FREE = PropertyTarget.free("Milonga")
DEVICE = DeviceName.parse("sys/tg_test/1")


@pytest.fixture
def runner(backend: FakeBackend) -> CommandRunner:
    return CommandRunner(backend)


@pytest.fixture
def gateway(backend: FakeBackend) -> PropertyGateway:
    return PropertyGateway(backend)


async def _values(gateway: PropertyGateway, target: PropertyTarget, name: str) -> tuple[str, ...]:
    (entry,) = await gateway.read(target, [name])
    return tuple(entry.values)


async def test_preview_changes_nothing(
    runner: CommandRunner, gateway: PropertyGateway
) -> None:
    command = PutProperties(MOTOR, (PropertyEntry("Velocity", ("4.0",)),))
    diff = await runner.preview([command])
    assert [line.kind for line in diff.changes] == [DiffKind.CHANGED]
    assert await _values(gateway, MOTOR, "Velocity") == ("2.5",)


async def test_apply_then_undo_restores_the_old_value(
    runner: CommandRunner, gateway: PropertyGateway
) -> None:
    command = PutProperties(MOTOR, (PropertyEntry("Velocity", ("4.0",)),))
    await runner.run([command])
    assert await _values(gateway, MOTOR, "Velocity") == ("4.0",)
    assert command.revertible
    await runner.undo(command)
    assert await _values(gateway, MOTOR, "Velocity") == ("2.5",)


async def test_undoing_a_new_property_removes_it(
    runner: CommandRunner, gateway: PropertyGateway
) -> None:
    command = PutProperties(MOTOR, (PropertyEntry("Backlash", ("0.1",)),))
    diff = await runner.run([command])
    assert diff.changes[0].kind is DiffKind.ADDED
    await runner.undo(command)
    assert "Backlash" not in await gateway.names(MOTOR)


async def test_writing_the_same_value_is_reported_as_unchanged(
    runner: CommandRunner,
) -> None:
    diff = await runner.preview([PutProperties(MOTOR, (PropertyEntry("Velocity", ("2.5",)),))])
    assert diff.changes == ()
    assert not diff


async def test_deleting_a_property_is_undoable(
    runner: CommandRunner, gateway: PropertyGateway
) -> None:
    command = DeleteProperties(MOTOR, ("Velocity",))
    assert command.destructive
    diff = await runner.run([command])
    assert diff.changes[0].kind is DiffKind.REMOVED
    assert "Velocity" not in await gateway.names(MOTOR)
    await runner.undo(command)
    assert await _values(gateway, MOTOR, "Velocity") == ("2.5",)


async def test_renaming_moves_the_value(
    runner: CommandRunner, gateway: PropertyGateway
) -> None:
    command = RenameProperty(MOTOR, "Velocity", "Speed")
    await runner.run([command])
    assert await _values(gateway, MOTOR, "Speed") == ("2.5",)
    assert "Velocity" not in await gateway.names(MOTOR)
    await runner.undo(command)
    assert await _values(gateway, MOTOR, "Velocity") == ("2.5",)
    assert "Speed" not in await gateway.names(MOTOR)


async def test_copying_reports_one_line_per_destination(
    runner: CommandRunner, gateway: PropertyGateway
) -> None:
    command = CopyProperties(MOTOR, (THETA,), ("AxisNumber", "Velocity"))
    diff = await runner.run([command])
    assert len(diff.changes) == 2
    assert await _values(gateway, THETA, "AxisNumber") == ("3",)
    await runner.undo(command)
    assert await _values(gateway, THETA, "AxisNumber") == ("4",)


async def test_class_and_free_scopes_use_the_same_commands(
    runner: CommandRunner, gateway: PropertyGateway
) -> None:
    await runner.run([PutProperties(MOTOR_CLASS, (PropertyEntry("EncoderType", ("INCR",)),))])
    assert await _values(gateway, MOTOR_CLASS, "EncoderType") == ("INCR",)
    await runner.run([PutProperties(FREE, (PropertyEntry("HostGroups", ("All:*",)),))])
    assert await _values(gateway, FREE, "HostGroups") == ("All:*",)


async def test_history_is_available_for_every_scope(
    runner: CommandRunner, gateway: PropertyGateway
) -> None:
    await runner.run([PutProperties(MOTOR_CLASS, (PropertyEntry("EncoderType", ("INCR",)),))])
    history = await gateway.history(MOTOR_CLASS, "EncoderType")
    assert [entry.values for entry in history] == [("INCR",), ("ABSOLUTE",)]
    assert await gateway.history(FREE, "HostGroups")


async def test_several_commands_apply_as_one_batch(
    runner: CommandRunner, gateway: PropertyGateway
) -> None:
    commands: list[Command] = [
        PutProperties(MOTOR, (PropertyEntry("Velocity", ("9",)),)),
        DeleteProperties(MOTOR, ("AxisNumber",)),
    ]
    diff = await runner.run(commands)
    assert len(diff.changes) == 2
    assert await _values(gateway, MOTOR, "Velocity") == ("9",)
    assert "AxisNumber" not in await gateway.names(MOTOR)


async def test_a_read_only_session_refuses_before_touching_anything(
    backend: FakeBackend, gateway: PropertyGateway
) -> None:
    runner = CommandRunner(ReadOnlyBackend(backend))
    command = PutProperties(MOTOR, (PropertyEntry("Velocity", ("4.0",)),))
    with pytest.raises(ReadOnlyError):
        await runner.run([command])
    assert await _values(gateway, MOTOR, "Velocity") == ("2.5",)


async def test_a_read_only_session_can_still_preview(backend: FakeBackend) -> None:
    runner = CommandRunner(ReadOnlyBackend(backend))
    diff = await runner.preview([PutProperties(MOTOR, (PropertyEntry("Velocity", ("4",)),))])
    assert diff.changes


async def test_attribute_configuration_round_trips(
    runner: CommandRunner, backend: FakeBackend
) -> None:
    (spec,) = [
        spec
        for spec in await backend.get_attribute_specs(DEVICE)
        if spec.name == "double_scalar"
    ]
    edited = AttributeSpec(
        **{
            **{field: getattr(spec, field) for field in ("name", "data_type", "data_format")},
            "label": "Position",
            "unit": "mm",
            "alarms": AlarmConfig(max_alarm="900"),
        }
    )
    command = SetAttributeConfig(DEVICE, edited)
    diff = await runner.run([command])
    labels = {line.name for line in diff.changes}
    assert "label" in labels and "alarms.max_alarm" in labels
    after = await backend.get_attribute_specs(DEVICE)
    assert config_values(next(s for s in after if s.name == "double_scalar"))["label"] == "Position"
    await runner.undo(command)
    restored = await backend.get_attribute_specs(DEVICE)
    assert next(s for s in restored if s.name == "double_scalar").label == spec.label


async def test_an_unknown_attribute_previews_as_no_change(
    runner: CommandRunner
) -> None:
    command = SetAttributeConfig(DEVICE, AttributeSpec("not_there"))
    assert await runner.preview([command]) == Diff()


def test_a_command_without_capture_cannot_be_undone() -> None:
    command = PutProperties(MOTOR, (PropertyEntry("Velocity", ("4.0",)),))
    assert not command.revertible


async def test_reverting_an_unsupported_command_says_so(backend: FakeBackend) -> None:
    class Unsupported(Command):
        @property
        def summary(self) -> str:
            return "unsupported"

        async def preview(self, backend: TangoBackend) -> Diff:
            return Diff()

        async def apply(self, backend: TangoBackend) -> None:
            return None

    with pytest.raises(RevertUnsupported):
        await CommandRunner(backend).undo(Unsupported())


def test_diff_lines_read_as_text() -> None:
    from milonga.core.commands.base import DiffLine

    added = DiffLine("dev", "P", DiffKind.ADDED, (), ("1",))
    removed = DiffLine("dev", "P", DiffKind.REMOVED, ("1",), ())
    changed = DiffLine("dev", "P", DiffKind.CHANGED, ("1",), ("2",))
    assert str(added) == "+ dev · P = 1"
    assert str(removed) == "- dev · P = 1"
    assert str(DiffLine("dev", "P", DiffKind.ADDED)) == "+ dev · P = ∅"
    assert "1 → 2" in str(changed)
    assert Diff((added, changed)).text().count("\n") == 1


async def test_changing_what_a_starter_controls(
    runner: CommandRunner, backend: FakeBackend
) -> None:
    from milonga.core.commands import SetServerControl
    from milonga.core.names import ServerName

    server = ServerName.parse("Vacuum/id09-front")
    command = SetServerControl(server, "id09-srv-02", level=5)
    diff = await runner.run([command])
    assert {line.name for line in diff.changes} == {"startup level"}
    assert (await backend.get_server_info(server)).level == 5
    await runner.undo(command)
    assert (await backend.get_server_info(server)).level == 2


async def test_moving_a_server_updates_both_starters(
    runner: CommandRunner, backend: FakeBackend
) -> None:
    from milonga.core.commands import SetServerControl
    from milonga.core.names import ServerName

    server = ServerName.parse("TangoTest/test")
    command = SetServerControl(server, "id09-srv-01", level=2)
    backend.call_log.clear()
    await runner.run([command])
    assert (await backend.get_server_info(server)).host == "id09-srv-01"
    assert backend.call_log.count("execute_command") == 2


# ------------------------------------------------------------ creating and removing


async def test_creating_a_device_and_undoing_it(
    runner: CommandRunner, backend: FakeBackend
) -> None:
    from milonga.core.commands import CreateDevice
    from milonga.core.model import DeviceRegistration
    from milonga.core.names import ServerName

    device = DeviceName.parse("id09/motor/chi")
    command = CreateDevice(DeviceRegistration(device, "IcePAPMotor", ServerName("IcePAP", "id09")))
    diff = await runner.run([command])
    assert diff.changes[0].kind is DiffKind.ADDED
    assert device in await backend.get_device_list()
    await runner.undo(command)
    assert device not in await backend.get_device_list()


async def test_creating_a_device_that_exists_is_refused(runner: CommandRunner) -> None:
    from milonga.core.commands import CreateDevice
    from milonga.core.model import DeviceRegistration
    from milonga.core.names import ServerName

    command = CreateDevice(
        DeviceRegistration(DEVICE, "TangoTest", ServerName("TangoTest", "test"))
    )
    with pytest.raises(TangoError, match="already exists"):
        await runner.preview([command])


async def test_deleting_a_device_keeps_enough_to_put_it_back(
    runner: CommandRunner, backend: FakeBackend, gateway: PropertyGateway
) -> None:
    from milonga.core.commands import DeleteDevice

    device = DeviceName.parse("id09/motor/phi")
    command = DeleteDevice(device)
    assert command.destructive
    assert command.confirmation_name == "id09/motor/phi"
    diff = await runner.run([command])
    assert {line.name for line in diff.changes} >= {"device", "Velocity", "alias"}
    assert device not in await backend.get_device_list()

    await runner.undo(command)
    assert device in await backend.get_device_list()
    assert await _values(gateway, MOTOR, "Velocity") == ("2.5",)
    assert await backend.get_alias_from_device(device) == "phi"


async def test_renaming_a_device(runner: CommandRunner, backend: FakeBackend) -> None:
    from milonga.core.commands import RenameDevice

    old = DeviceName.parse("id09/motor/phi")
    new = DeviceName.parse("id09/motor/phi2")
    command = RenameDevice(old, new)
    await runner.run([command])
    assert new in await backend.get_device_list()
    await runner.undo(command)
    assert old in await backend.get_device_list()


async def test_setting_and_dropping_an_alias(
    runner: CommandRunner, backend: FakeBackend
) -> None:
    from milonga.core.commands import SetDeviceAlias

    device = DeviceName.parse("id09/vac/gauge-1")
    command = SetDeviceAlias(device, "gauge")
    await runner.run([command])
    assert await backend.get_alias_from_device(device) == "gauge"
    await runner.undo(command)
    assert await backend.get_alias_from_device(device) is None

    drop = SetDeviceAlias(DeviceName.parse("id09/motor/phi"), "")
    await runner.run([drop])
    assert await backend.get_alias_from_device(DeviceName.parse("id09/motor/phi")) is None
    await runner.undo(drop)
    assert await backend.get_alias_from_device(DeviceName.parse("id09/motor/phi")) == "phi"


async def test_creating_a_server_registers_its_devices_and_level(
    runner: CommandRunner, backend: FakeBackend
) -> None:
    from milonga.core.commands import CreateServer
    from milonga.core.model import DeviceRegistration
    from milonga.core.names import ServerName

    server = ServerName("Vacuum", "id09-back")
    device = DeviceName.parse("id09/vac/gauge-2")
    command = CreateServer(
        server,
        (DeviceRegistration(device, "VacuumGauge", server),),
        host="id09-srv-02",
        level=3,
    )
    await runner.run([command])
    info = await backend.get_server_info(server)
    assert info.host == "id09-srv-02" and info.level == 3 and info.controlled
    assert device in await backend.get_device_list_for_server(server)
    await runner.undo(command)
    assert server not in await backend.get_server_list()


async def test_deleting_a_server_can_be_undone(
    runner: CommandRunner, backend: FakeBackend
) -> None:
    from milonga.core.commands import DeleteServer
    from milonga.core.names import ServerName

    server = ServerName("IcePAP", "id09")
    command = DeleteServer(server)
    diff = await runner.run([command])
    assert len(diff.changes) == 4
    assert server not in await backend.get_server_list()
    await runner.undo(command)
    assert server in await backend.get_server_list()
    assert len(await backend.get_device_list_for_server(server)) == 3
    assert (await backend.get_server_info(server)).level == 2


async def test_renaming_a_server_moves_its_devices(
    runner: CommandRunner, backend: FakeBackend
) -> None:
    from milonga.core.commands import RenameServer
    from milonga.core.names import ServerName

    old = ServerName("TangoTest", "test")
    new = ServerName("TangoTest", "prod")
    command = RenameServer(old, new)
    await runner.run([command])
    assert DEVICE in await backend.get_device_list_for_server(new)
    await runner.undo(command)
    assert DEVICE in await backend.get_device_list_for_server(old)


# ------------------------------------------------------------------------ polling


async def test_polling_can_be_set_changed_and_stopped(
    runner: CommandRunner, backend: FakeBackend
) -> None:
    from milonga.core.commands import SetPolling

    device = DeviceName.parse("id09/motor/phi")
    start = SetPolling(device, "position", period_ms=200)
    diff = await runner.run([start])
    assert diff.changes[0].kind is DiffKind.ADDED
    assert (await backend.get_polling(device))[0].period_ms == 200

    faster = SetPolling(device, "position", period_ms=100)
    await runner.run([faster])
    assert (await backend.get_polling(device))[0].period_ms == 100
    await runner.undo(faster)
    assert (await backend.get_polling(device))[0].period_ms == 200

    stop = SetPolling(device, "position", period_ms=0)
    await runner.run([stop])
    assert await backend.get_polling(device) == ()
    await runner.undo(stop)
    assert (await backend.get_polling(device))[0].period_ms == 200
