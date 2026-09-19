"""The one path every database write takes: preview, confirm, apply, journal."""

from collections.abc import Sequence

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QWidget

from milonga.core.commands import Command, Diff
from milonga.ui.context import AppContext
from milonga.ui.dialogs import ConfirmDialog, DiffDialog
from milonga.ui.tasks import TaskRunner
from milonga.ui.theme import Tokens


class WriteAction(QObject):
    """Runs commands through the confirmation flow and records what was written."""

    done = pyqtSignal()
    nothingToDo = pyqtSignal()
    failed = pyqtSignal(object)

    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        runner: TaskRunner,
        widget: QWidget,
    ) -> None:
        super().__init__(widget)
        self._context = context
        self._tokens = tokens
        self._runner = runner
        self._widget = widget

    def execute(self, commands: Sequence[Command], *, confirm_word: str = "") -> None:
        if self._context.read_only or not commands:
            return
        self._runner.run(
            self._context.commands.preview(commands),
            on_result=lambda diff: self._confirm(commands, diff, confirm_word),
            on_error=self.failed.emit,
        )

    def _confirm(
        self, commands: Sequence[Command], diff: Diff, confirm_word: str
    ) -> None:
        if not diff:
            self.nothingToDo.emit()
            return
        destructive = [command for command in commands if command.destructive]
        if destructive and not self._ask(destructive, confirm_word):
            return
        if not DiffDialog(diff, self._tokens, self._widget).exec():
            return
        self._runner.run(
            self._context.commands.run(commands),
            on_result=lambda applied: self._applied(commands, applied),
            on_error=self.failed.emit,
        )

    def _ask(self, commands: Sequence[Command], confirm_word: str) -> bool:
        summary = "\n".join(command.summary for command in commands)
        word = confirm_word or next(
            (command.confirmation_name for command in commands if command.confirmation_name),
            "",
        )
        dialog = ConfirmDialog(
            "Confirm",
            f"This removes data from the database:\n\n{summary}",
            confirm_word=word or None,
            parent=self._widget,
        )
        return bool(dialog.exec())

    def _applied(self, commands: Sequence[Command], diff: Diff) -> None:
        for command in commands:
            self._context.journal.write(command.summary, diff.text(), command)
        self.done.emit()
