import pytest
from PyQt6.QtWidgets import QApplication

from milonga.core.backend.demo import build_demo_backend
from milonga.core.backend.fake import FakeBackend
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


@pytest.fixture
def tokens(app: QApplication) -> Tokens:
    return apply_theme(app, Theme.LIGHT)


@pytest.fixture
async def context(backend: FakeBackend) -> AppContext:
    store = SystemStore()
    return AppContext(
        backend=backend,
        store=store,
        monitor=MonitorHub(backend, store=store),
        inventory=Inventory(backend),
        control=StarterControl(backend),
        journal=Journal(),
        groups=await load_host_groups(backend),
    )


@pytest.fixture
def demo_backend() -> FakeBackend:
    return build_demo_backend()


@pytest.fixture
def light_tokens() -> Tokens:
    return LIGHT
