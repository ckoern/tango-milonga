from collections.abc import Iterator

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from milonga.core.backend.demo import build_demo_backend
from milonga.core.backend.fake import FakeBackend
from milonga.core.commands import CommandRunner
from milonga.core.monitor import MonitorHub
from milonga.core.services.control import StarterControl
from milonga.core.services.groups import load_host_groups
from milonga.core.services.inventory import Inventory
from milonga.core.store import SystemStore
from milonga.ui.context import AppContext, Journal
from milonga.ui.theme import LIGHT, Theme, Tokens, apply_theme


@pytest.fixture(scope="session")
def app(qapp: QApplication) -> QApplication:
    """pytest-qt owns the QApplication so it outlives every widget."""
    return qapp


@pytest.fixture(autouse=True)
def close_windows(app: QApplication) -> Iterator[None]:
    """Destroy what a test leaves behind.

    Qt owns a widget that has a parent, and shiboken keeps the Python side of
    it alive with the C++ object, so windows a test never closes stay alive
    for the whole session and every later restyle walks them. Only this
    project's own windows are deleted: the top level also holds widgets Qt
    and pyqtgraph own, such as menus and tooltips.
    """
    yield
    for widget in list(app.topLevelWidgets()):
        if isValid(widget) and type(widget).__module__.startswith("milonga."):
            widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(scope="session")
def tokens(app: QApplication) -> Tokens:
    """Applied once: setting a stylesheet re-polishes every widget alive."""
    return apply_theme(app, Theme.LIGHT)


@pytest.fixture
async def context(backend: FakeBackend) -> AppContext:
    store = SystemStore()
    return AppContext(
        backend=backend,
        store=store,
        monitor=MonitorHub(backend, store=store),
        inventory=Inventory(backend),
        control=StarterControl(backend, exit_grace=0.0),
        journal=Journal(),
        commands=CommandRunner(backend),
        groups=await load_host_groups(backend),
    )


@pytest.fixture
def demo_backend() -> FakeBackend:
    return build_demo_backend()


@pytest.fixture
def light_tokens() -> Tokens:
    return LIGHT


@pytest.fixture
async def read_only_context(backend: FakeBackend) -> AppContext:
    from milonga.core.backend.readonly import ReadOnlyBackend

    guarded = ReadOnlyBackend(backend)
    store = SystemStore()
    return AppContext(
        backend=guarded,
        store=store,
        monitor=MonitorHub(guarded, store=store),
        inventory=Inventory(guarded),
        control=StarterControl(guarded, exit_grace=0.0),
        journal=Journal(),
        commands=CommandRunner(guarded),
        groups=await load_host_groups(guarded),
    )
