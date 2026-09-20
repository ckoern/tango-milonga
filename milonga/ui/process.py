"""Acting on server processes: the confirmations and the journal, in one place.

Used by the host panel, the navigator's host tree and the overview cards, so
a stop asks the same question wherever it is started from.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import TypeAlias

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from milonga.core.model import HostSnapshot
from milonga.core.names import ServerName
from milonga.ui.context import AppContext
from milonga.ui.dialogs import ConfirmDialog
from milonga.ui.tasks import TaskRunner

Operation: TypeAlias = Callable[[str, ServerName], Awaitable[None]]


class ProcessActions(QObject):
    done = Signal()
    failed = Signal(object)

    def __init__(self, context: AppContext, runner: TaskRunner, widget: QWidget) -> None:
        super().__init__(widget)
        self._context = context
        self._runner = runner
        self._widget = widget

    @property
    def enabled(self) -> bool:
        return not self._context.read_only

    def start(self, host: str, servers: Sequence[ServerName]) -> None:
        """Starting needs no confirmation: nothing is lost if it was a mistake."""
        self._run("start", host, servers, self._context.control.start_server)

    def stop(self, host: str, servers: Sequence[ServerName]) -> None:
        if self._confirm("stop", host, servers, typed=False):
            self._run("stop", host, servers, self._context.control.stop_server)

    def restart(self, host: str, servers: Sequence[ServerName]) -> None:
        if self._confirm("restart", host, servers, typed=False):
            self._run("restart", host, servers, self._context.control.restart_server)

    def hard_kill(self, host: str, servers: Sequence[ServerName]) -> None:
        if self._confirm("hard kill", host, servers, typed=True):
            self._run("hard kill", host, servers, self._context.control.hard_kill_server)

    def start_level(self, host: str, level: int) -> None:
        if self.enabled:
            self._level("start", host, level, self._context.control.start_level)

    def stop_level(self, host: str, level: int) -> None:
        if self.enabled and self._confirm(f"stop level {level}", host, (), typed=False):
            self._level("stop", host, level, self._context.control.stop_level)

    def start_all(self, snapshot: HostSnapshot) -> None:
        if not self.enabled:
            return
        self._whole_host("start all levels", snapshot, self._context.control.start_all)

    def stop_all(self, snapshot: HostSnapshot) -> None:
        names = [server.name for server in snapshot.servers]
        if self.enabled and self._confirm("stop all levels", snapshot.name, names, typed=True):
            self._whole_host("stop all levels", snapshot, self._context.control.stop_all)

    # ------------------------------------------------------------------ internals

    def _run(
        self, action: str, host: str, servers: Sequence[ServerName], operation: Operation
    ) -> None:
        if not servers or not self.enabled:
            return

        async def run() -> None:
            for server in servers:
                await operation(host, server)
                self._context.journal.write(f"{action} {server} on {host}")

        self._runner.run(run(), on_result=lambda _: self.done.emit(), on_error=self.failed.emit)

    def _level(
        self, action: str, host: str, level: int, operation: Callable[[str, int], Awaitable[None]]
    ) -> None:
        async def run() -> None:
            await operation(host, level)
            self._context.journal.write(f"{action} level {level} on {host}")

        self._runner.run(run(), on_result=lambda _: self.done.emit(), on_error=self.failed.emit)

    def _whole_host(
        self,
        action: str,
        snapshot: HostSnapshot,
        operation: Callable[[HostSnapshot], Awaitable[None]],
    ) -> None:
        async def run() -> None:
            await operation(snapshot)
            self._context.journal.write(f"{action} on {snapshot.name}")

        self._runner.run(run(), on_result=lambda _: self.done.emit(), on_error=self.failed.emit)

    def _confirm(
        self, action: str, host: str, servers: Sequence[ServerName], *, typed: bool
    ) -> bool:
        names = "\n".join(str(server) for server in servers)
        detail = f":\n\n{names}" if names else "."
        dialog = ConfirmDialog(
            f"Confirm {action}",
            f"This acts on running processes on {host}{detail}",
            confirm_word=host if typed else None,
            parent=self._widget,
        )
        return bool(dialog.exec())
