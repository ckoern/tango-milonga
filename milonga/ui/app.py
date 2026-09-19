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
    demo: bool = True
    tango_host: str | None = None
    read_only: bool = False
    theme: Theme = Theme.DARK
    scope: Scope = Scope.DEVICES
    open: Sequence[str] = field(default_factory=tuple)


def build_backend(options: AppOptions) -> TangoBackend:
    if not options.demo:
        raise SystemExit(
            "The PyTango backend is not implemented yet. Run with --demo to use the "
            "in-memory control system."
        )
    from milonga.core.backend.demo import build_demo_backend

    backend: TangoBackend = build_demo_backend()
    if options.read_only:
        backend = ReadOnlyBackend(backend)
    return backend


async def build_context(options: AppOptions) -> AppContext:
    backend = build_backend(options)
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
        previous.close()
        previous.deleteLater()
        self._options.theme = theme
        window = self._create(theme, restore)
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
