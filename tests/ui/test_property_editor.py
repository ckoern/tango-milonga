import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.commands import PropertyGateway, PropertyTarget
from milonga.core.model import PropertyEntry
from milonga.ui.context import AppContext
from milonga.ui.dialogs import ConfirmDialog, CopyToDialog, DiffDialog, HistoryDialog, NameDialog
from milonga.ui.models.properties import PropertyEditorModel, RowState
from milonga.ui.property_editor import PropertyEditor
from milonga.ui.tasks import TaskRunner
from milonga.ui.theme import Tokens

MOTOR = PropertyTarget.device("id09/motor/phi")


@pytest.fixture
def accept_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DiffDialog, "exec", lambda self: 1)
    monkeypatch.setattr(ConfirmDialog, "exec", lambda self: 1)


@pytest.fixture
def refuse_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DiffDialog, "exec", lambda self: 0)
    monkeypatch.setattr(ConfirmDialog, "exec", lambda self: 0)


@pytest.fixture
async def editor(context: AppContext, tokens: Tokens) -> PropertyEditor:
    widget = PropertyEditor(context, tokens, MOTOR, TaskRunner(None, context.journal))
    widget.refresh()
    await widget._runner.idle()
    return widget


def _row(editor: PropertyEditor, name: str) -> int:
    position = editor.model.row_named(name)
    assert position >= 0, f"no property {name}"
    return position


async def _values(context: AppContext, name: str) -> tuple[str, ...]:
    (entry,) = await PropertyGateway(context.backend).read(MOTOR, [name])
    return tuple(entry.values)


# --------------------------------------------------------------------------- model


def test_editing_a_value_marks_the_row(tokens: Tokens) -> None:
    model = PropertyEditorModel(tokens)
    model.set_entries([PropertyEntry("Velocity", ("2.5",))])
    model.set_values(0, ("4.0",))
    assert model.rows[0].state is RowState.MODIFIED
    assert len(model.pending) == 1


def test_setting_the_original_value_back_clears_the_mark(tokens: Tokens) -> None:
    model = PropertyEditorModel(tokens)
    model.set_entries([PropertyEntry("Velocity", ("2.5",))])
    model.set_values(0, ("4.0",))
    model.set_values(0, ("2.5",))
    assert model.rows[0].state is RowState.UNCHANGED
    assert model.pending == ()


def test_adding_and_discarding(tokens: Tokens) -> None:
    model = PropertyEditorModel(tokens)
    model.set_entries([PropertyEntry("Velocity", ("2.5",))])
    model.add_property("Backlash", ("0.1",))
    assert model.rows[1].state is RowState.ADDED
    model.discard()
    assert model.rowCount() == 1
    assert model.pending == ()


def test_deleting_marks_but_keeps_the_row(tokens: Tokens) -> None:
    model = PropertyEditorModel(tokens)
    model.set_entries([PropertyEntry("Velocity", ("2.5",))])
    model.mark_deleted([0])
    assert model.rows[0].state is RowState.DELETED
    assert model.rowCount() == 1


def test_commands_cover_both_writes_and_deletions(tokens: Tokens) -> None:
    model = PropertyEditorModel(tokens)
    model.set_entries([PropertyEntry("A", ("1",)), PropertyEntry("B", ("2",))])
    model.set_values(0, ("9",))
    model.mark_deleted([1])
    commands = model.commands(MOTOR)
    assert [type(command).__name__ for command in commands] == [
        "PutProperties",
        "DeleteProperties",
    ]


def test_multi_value_rows_are_not_edited_inline(tokens: Tokens) -> None:
    from PySide6.QtCore import Qt

    model = PropertyEditorModel(tokens)
    model.set_entries([PropertyEntry("polled_attr", ("a", "1"))])
    flags = model.flags(model.index(0, 1))
    assert not flags & Qt.ItemFlag.ItemIsEditable


# -------------------------------------------------------------------------- editor


async def test_the_editor_loads_the_properties(editor: PropertyEditor) -> None:
    assert editor.model.rowCount() == 4
    assert not editor.apply_button.isEnabled()


async def test_applying_writes_and_journals_with_an_undo(
    editor: PropertyEditor, context: AppContext, accept_dialogs: None
) -> None:
    editor.model.set_values(_row(editor, "Velocity"), ("4.0",))
    assert editor.apply_button.isEnabled()
    editor.apply_changes()
    await editor._runner.idle()
    assert await _values(context, "Velocity") == ("4.0",)
    entry = context.journal.entries[-1]
    assert entry.undoable
    assert "Velocity" in entry.summary


async def test_cancelling_the_preview_writes_nothing(
    editor: PropertyEditor, context: AppContext, refuse_dialogs: None
) -> None:
    editor.model.set_values(_row(editor, "Velocity"), ("4.0",))
    editor.apply_changes()
    await editor._runner.idle()
    assert await _values(context, "Velocity") == ("2.5",)
    assert editor.model.pending


async def test_applying_an_unchanged_value_writes_nothing(
    editor: PropertyEditor, context: AppContext, backend: FakeBackend, accept_dialogs: None
) -> None:
    editor.model.set_values(_row(editor, "Velocity"), ("2.5",))
    editor.model.rows[_row(editor, "Velocity")].state = RowState.MODIFIED
    editor.apply_changes()
    await editor._runner.idle()
    assert "nothing to write" in context.journal.entries[-1].summary


async def test_deleting_asks_for_confirmation_first(
    editor: PropertyEditor, context: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def refuse(self: ConfirmDialog) -> int:
        asked.append("asked")
        return 0

    monkeypatch.setattr(ConfirmDialog, "exec", refuse)
    monkeypatch.setattr(DiffDialog, "exec", lambda self: 1)
    editor.model.mark_deleted([_row(editor, "Velocity")])
    editor.apply_changes()
    await editor._runner.idle()
    assert asked == ["asked"]
    assert await _values(context, "Velocity") == ("2.5",)


async def test_confirmed_deletion_removes_the_property(
    editor: PropertyEditor, context: AppContext, accept_dialogs: None
) -> None:
    editor.model.mark_deleted([_row(editor, "Velocity")])
    editor.apply_changes()
    await editor._runner.idle()
    assert await _values(context, "Velocity") == ()


async def test_renaming_goes_through_the_same_flow(
    editor: PropertyEditor,
    context: AppContext,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(NameDialog, "exec", lambda self: 1)
    monkeypatch.setattr(NameDialog, "name", lambda self: "Speed")
    editor.view.selectRow(_row(editor, "Velocity"))
    editor._rename()
    await editor._runner.idle()
    assert await _values(context, "Speed") == ("2.5",)
    assert await _values(context, "Velocity") == ()


async def test_copying_to_another_device(
    editor: PropertyEditor,
    context: AppContext,
    accept_dialogs: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(CopyToDialog, "exec", lambda self: 1)
    monkeypatch.setattr(CopyToDialog, "chosen", lambda self: ("id09/motor/theta",))
    editor.view.selectRow(_row(editor, "Velocity"))
    editor._copy_to()
    await editor._runner.idle()
    gateway = PropertyGateway(context.backend)
    (entry,) = await gateway.read(PropertyTarget.device("id09/motor/theta"), ["Velocity"])
    assert tuple(entry.values) == ("2.5",)


async def test_history_is_shown_for_the_selected_property(
    editor: PropertyEditor, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown: list[str] = []

    def record(self: HistoryDialog) -> int:
        shown.append("shown")
        return 1

    monkeypatch.setattr(HistoryDialog, "exec", record)
    editor.view.selectRow(_row(editor, "Velocity"))
    editor._history()
    await editor._runner.idle()
    assert shown == ["shown"]


async def test_a_read_only_session_cannot_edit(
    read_only_context: AppContext, tokens: Tokens, accept_dialogs: None
) -> None:
    widget = PropertyEditor(
        read_only_context, tokens, MOTOR, TaskRunner(None, read_only_context.journal)
    )
    widget.refresh()
    await widget._runner.idle()
    assert not widget.add_button.isEnabled()
    widget.model.set_values(widget.model.row_named("Velocity"), ("4.0",))
    assert not widget.apply_button.isEnabled()
    widget.apply_changes()
    await widget._runner.idle()
    gateway = PropertyGateway(read_only_context.backend)
    (entry,) = await gateway.read(MOTOR, ["Velocity"])
    assert tuple(entry.values) == ("2.5",)
