import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.commands import PropertyGateway, PropertyTarget, PutProperties
from milonga.core.model import AlarmConfig, AttributeSpec, PropertyEntry
from milonga.core.names import DeviceName
from milonga.ui.context import AppContext, Target
from milonga.ui.dialogs import AttributeConfigDialog, ConfirmDialog, DiffDialog
from milonga.ui.mainwindow import UNDO_COLUMN, MainWindow
from milonga.ui.panels.device import DevicePanel
from milonga.ui.theme import Theme, Tokens

DEVICE = DeviceName.parse("sys/tg_test/1")
MOTOR = PropertyTarget.device("id09/motor/phi")


@pytest.fixture
def accept_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DiffDialog, "exec", lambda self: 1)
    monkeypatch.setattr(ConfirmDialog, "exec", lambda self: 1)


@pytest.fixture
async def device_panel(context: AppContext, tokens: Tokens) -> DevicePanel:
    panel = DevicePanel(context, tokens, Target.device(DEVICE))
    await panel.idle()
    return panel


def _select_spec(panel: DevicePanel, name: str) -> AttributeSpec:
    for row in range(panel.specs.rowCount()):
        index = panel.specs.index(row, 0)
        if panel.specs.data(index) == name:
            panel.config_view.setCurrentIndex(index)
            spec = panel.specs.row_at(index)
            assert spec is not None
            return spec
    raise AssertionError(f"no attribute {name}")


async def test_attribute_configuration_is_written_and_journalled(
    device_panel: DevicePanel,
    context: AppContext,
    backend: FakeBackend,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _select_spec(device_panel, "double_scalar")
    edited = AttributeSpec(
        spec.name,
        spec.data_type,
        spec.data_format,
        spec.writable,
        label="Sample position",
        unit="mm",
        alarms=AlarmConfig(max_alarm="900"),
    )
    monkeypatch.setattr(AttributeConfigDialog, "exec", lambda self: 1)
    monkeypatch.setattr(AttributeConfigDialog, "edited", lambda self: edited)

    device_panel._edit_config()
    await device_panel.idle()

    live = await backend.get_attribute_specs(DEVICE)
    written = next(item for item in live if item.name == "double_scalar")
    assert written.label == "Sample position"
    assert written.alarms.max_alarm == "900"
    entry = context.journal.entries[-1]
    assert "configure double_scalar" in entry.summary
    assert entry.undoable


async def test_cancelling_the_configuration_dialog_writes_nothing(
    device_panel: DevicePanel,
    backend: FakeBackend,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _select_spec(device_panel, "double_scalar")
    monkeypatch.setattr(AttributeConfigDialog, "exec", lambda self: 0)
    device_panel._edit_config()
    await device_panel.idle()
    live = await backend.get_attribute_specs(DEVICE)
    assert next(item for item in live if item.name == "double_scalar").label == before.label


async def test_a_read_only_session_cannot_configure(
    read_only_context: AppContext, tokens: Tokens
) -> None:
    panel = DevicePanel(read_only_context, tokens, Target.device(DEVICE))
    await panel.idle()
    assert not panel.config_button.isEnabled()
    await panel.aclose()


async def test_the_journal_undoes_a_write(
    context: AppContext, tokens: Tokens, backend: FakeBackend
) -> None:
    window = MainWindow(context, tokens, Theme.LIGHT)
    await window.idle()
    command = PutProperties(MOTOR, (PropertyEntry("Velocity", ("4.0",)),))
    await context.commands.run([command])
    context.journal.write(command.summary, "", command)

    row = window.journal_model.rowCount() - 1
    assert window.journal_model.data(window.journal_model.index(row, UNDO_COLUMN)) == "undo"
    window.journal_view.clicked.emit(window.journal_model.index(row, UNDO_COLUMN))
    await window.idle()

    (entry,) = await PropertyGateway(backend).read(MOTOR, ["Velocity"])
    assert tuple(entry.values) == ("2.5",)
    assert context.journal.entries[-1].summary.startswith("undo ·")


async def test_entries_without_a_command_offer_no_undo(
    context: AppContext, tokens: Tokens
) -> None:
    window = MainWindow(context, tokens, Theme.LIGHT)
    await window.idle()
    context.journal.info("connected")
    row = window.journal_model.rowCount() - 1
    assert window.journal_model.data(window.journal_model.index(row, UNDO_COLUMN)) == ""
    window.journal_view.clicked.emit(window.journal_model.index(row, UNDO_COLUMN))
    await window.idle()


async def test_the_device_panel_edits_its_properties(
    context: AppContext, tokens: Tokens, accept_dialogs: None
) -> None:
    panel = DevicePanel(context, tokens, Target.device(DeviceName.parse("id09/motor/phi")))
    await panel.idle()
    editor = panel.properties
    editor.model.set_values(editor.model.row_named("Velocity"), ("6.0",))
    editor.apply_changes()
    await panel.idle()
    (entry,) = await PropertyGateway(context.backend).read(MOTOR, ["Velocity"])
    assert tuple(entry.values) == ("6.0",)
    await panel.aclose()


async def test_the_configuration_dialog_round_trips_every_field(
    tokens: Tokens, backend: FakeBackend
) -> None:
    specs = await backend.get_attribute_specs(DEVICE)
    spec = next(item for item in specs if item.name == "double_scalar")
    dialog = AttributeConfigDialog(spec, tokens)
    assert dialog.edited() == spec

    dialog._fields["label"].setText("Sample position")
    dialog._fields["alarms.max_alarm"].setText("900")
    dialog._fields["events.change_abs"].setText("0.5")
    edited = dialog.edited()
    assert edited.label == "Sample position"
    assert edited.alarms.max_alarm == "900"
    assert edited.events.change_abs == "0.5"
    assert edited.data_type is spec.data_type
    assert edited.max_dim_x == spec.max_dim_x
