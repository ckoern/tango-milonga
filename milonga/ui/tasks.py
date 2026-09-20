"""Running coroutines from widgets without leaving tasks behind.

A runner belongs to one widget: when the widget goes away its pending calls
are cancelled, so a slow database answer can never reach a deleted panel.
"""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, TypeAlias, TypeVar

from PySide6.QtCore import QObject
from shiboken6 import isValid

from milonga.core.errors import ErrorReport, TangoError
from milonga.core.tasks import drain_tasks
from milonga.ui.context import Journal

T = TypeVar("T")
Coro: TypeAlias = Coroutine[Any, Any, T]


def _usable_loop() -> asyncio.AbstractEventLoop | None:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return None
    return None if loop.is_closed() else loop


def close_unstarted(coro: Awaitable[Any]) -> None:
    """Dispose of a coroutine that will never run, without a warning."""
    closer = getattr(coro, "close", None)
    if callable(closer):
        closer()


class TaskRunner(QObject):
    def __init__(
        self,
        owner: QObject | None = None,
        journal: Journal | None = None,
        *,
        context: str = "",
    ) -> None:
        super().__init__(owner)
        self._owner = owner
        self._journal = journal
        self._context = context
        self._tasks: set[asyncio.Task[Any]] = set()
        if owner is not None:
            owner.destroyed.connect(lambda *_: self.cancel_all())

    @property
    def pending(self) -> int:
        return len(self._tasks)

    def run(
        self,
        coro: Awaitable[T],
        *,
        on_result: Callable[[T], None] | None = None,
        on_error: Callable[[ErrorReport], None] | None = None,
        label: str = "",
    ) -> asyncio.Task[T] | None:
        """Returns ``None`` when no loop is left to run on, which happens while
        the application shuts down and its widgets are hidden."""
        loop = _usable_loop()
        if loop is None:
            close_unstarted(coro)
            return None
        task = asyncio.ensure_future(coro, loop=loop)
        self._tasks.add(task)
        task.add_done_callback(lambda finished: self._finish(finished, on_result, on_error, label))
        return task

    def cancel_all(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    async def idle(self) -> None:
        """Wait for everything in flight, including calls those calls start."""
        await drain_tasks(self._tasks)

    def _finish(
        self,
        task: asyncio.Task[T],
        on_result: Callable[[T], None] | None,
        on_error: Callable[[ErrorReport], None] | None,
        label: str,
    ) -> None:
        self._tasks.discard(task)
        if task.cancelled() or self._owner_gone():
            return
        error = task.exception()
        if error is None:
            if on_result is not None:
                on_result(task.result())
            return
        if not isinstance(error, TangoError):
            raise error
        report = ErrorReport.from_exception(error)
        if on_error is not None:
            on_error(report)
        elif self._journal is not None:
            self._journal.report(report, label or self._context)

    def _owner_gone(self) -> bool:
        return self._owner is not None and not isValid(self._owner)
