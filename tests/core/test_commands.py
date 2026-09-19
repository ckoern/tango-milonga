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
from milonga.core.errors import ReadOnlyError
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
