"""Application bootstrap: one asyncio loop, driven by the Qt event loop.

``qasync`` runs asyncio on top of Qt, so widgets and coroutines share a
single thread: no locking around application state, and cancelling a panel's
pending calls is ``task.cancel()``.
"""

import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

import qasync
from PyQt6.QtWidgets import QApplication

from milonga.core.backend.protocol import TangoBackend
from milonga.core.backend.readonly import ReadOnlyBackend
from milonga.core.commands import CommandRunner
from milonga.core.errors import TangoError
from milonga.core.monitor import MonitorHub
from milonga.core.services.control import StarterControl
from milonga.core.services.groups import load_host_groups
from milonga.core.services.inventory import Inventory
from milonga.core.store import SystemStore
from milonga.ui.context import AppContext, Journal, Target
from milonga.ui.mainwindow import MainWindow
from milonga.ui.navigator import Scope
from milonga.ui.search import parse_target
from milonga.ui.theme import Theme, apply_theme


@dataclass
class AppOptions:
    demo: bool = False
    tango_host: str | None = None
    read_only: bool = False
    theme: Theme = Theme.DARK
    scope: Scope = Scope.DEVICES
    open: Sequence[str] = field(default_factory=tuple)


CONNECT_TIMEOUT = 15.0


def build_backend(options: AppOptions) -> TangoBackend:
    backend: TangoBackend
    if options.demo:
        from milonga.core.backend.demo import build_demo_backend

        backend = build_demo_backend()
    else:
        backend = _tango_backend(options.tango_host)
    if options.read_only:
        backend = ReadOnlyBackend(backend)
    return backend


def _tango_backend(tango_host: str | None) -> TangoBackend:
    try:
        from milonga.core.backend.pytango_backend import PyTangoBackend
    except ImportError:
        raise SystemExit(
            "PyTango is not installed. Install it with `pip install 'milonga[tango]'`, "
            "or run with --demo."
        ) from None
    try:
        return PyTangoBackend(tango_host)
    except TangoError as error:
        raise SystemExit(f"{error}. Run with --demo to try the in-memory system.") from None


async def check_connection(backend: TangoBackend) -> None:
    """Fail at startup with a sentence, not later with a tree full of errors."""
    try:
        await asyncio.wait_for(backend.get_server_list("DataBaseds/*"), CONNECT_TIMEOUT)
    except (TangoError, TimeoutError) as error:
        raise SystemExit(
            f"Cannot reach the Tango database at {backend.tango_host}: {error or 'timed out'}"
        ) from None


async def build_context(options: AppOptions) -> AppContext:
    backend = build_backend(options)
    await check_connection(backend)
    store = SystemStore()
    context = AppContext(
        backend=backend,
        store=store,
        monitor=MonitorHub(backend, store=store),
        inventory=Inventory(backend),
        control=StarterControl(backend),
        journal=Journal(),
        commands=CommandRunner(backend),
        groups=await load_host_groups(backend),
    )
    return context


class Shell:
    """Owns the window, so switching theme can rebuild it with the open tabs."""

    def __init__(self, app: QApplication, context: AppContext, options: AppOptions) -> None:
        self._app = app
        self._context = context
        self._options = options
        self._window: MainWindow | None = None

    @property
    def window(self) -> MainWindow | None:
        return self._window

    def start(self) -> MainWindow:
        window = self._create(self._options.theme)
        window.navigator.set_scope(self._options.scope)
        targets = [target for text in self._options.open if (target := parse_target(text))]
        window.open_targets(targets)
        return window

    def _create(self, theme: Theme, restore: Sequence[Target] = ()) -> MainWindow:
        tokens = apply_theme(self._app, theme)
        window = MainWindow(self._context, tokens, theme)
        window.themeToggled.connect(self._switch_theme)
        window.open_targets(restore)
        window.show()
        self._window = window
        return window

    def _switch_theme(self, theme: Theme) -> None:
        previous = self._window
        if previous is None:
            return
        scope = previous.navigator.scope
        restore = previous.open_panels
        self._options.theme = theme
        window = self._create(theme, restore)
        previous.close()
        previous.deleteLater()
        window.navigator.set_scope(scope)


async def shutdown(context: AppContext) -> None:
    await context.monitor.aclose()
    await context.backend.close()


def run(options: AppOptions) -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Milonga")
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    with loop:
        context = loop.run_until_complete(build_context(options))
        shell = Shell(app, context, options)
        shell.start()
        app.lastWindowClosed.connect(loop.stop)
        loop.run_forever()
        loop.run_until_complete(shutdown(context))
    return 0
