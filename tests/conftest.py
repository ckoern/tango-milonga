import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from milonga.core.backend.demo import build_demo_backend
from milonga.core.backend.fake import FakeBackend
from milonga.core.services.control import StarterControl
from milonga.core.store import StoreDiff, SystemStore


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        help="also run the tests that need a live Tango database (TANGO_HOST)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: needs a live Tango database")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--integration"):
        return
    skip = pytest.mark.skip(reason="needs --integration and a live Tango database")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)



@pytest.fixture
def backend() -> FakeBackend:
    return build_demo_backend()


@pytest.fixture
def store() -> SystemStore:
    return SystemStore()


@pytest.fixture
def control(backend: FakeBackend) -> StarterControl:
    return StarterControl(backend, exit_grace=0.0)


@pytest.fixture
def diffs(store: SystemStore) -> list[StoreDiff]:
    collected: list[StoreDiff] = []
    store.subscribe(collected.append)
    return collected
