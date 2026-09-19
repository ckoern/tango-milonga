import pytest

from milonga.core.backend.demo import build_demo_backend
from milonga.core.backend.fake import FakeBackend
from milonga.core.services.control import StarterControl
from milonga.core.store import StoreDiff, SystemStore


@pytest.fixture
def backend() -> FakeBackend:
    return build_demo_backend()


@pytest.fixture
def store() -> SystemStore:
    return SystemStore()


@pytest.fixture
def control(backend: FakeBackend) -> StarterControl:
    return StarterControl(backend)


@pytest.fixture
def diffs(store: SystemStore) -> list[StoreDiff]:
    collected: list[StoreDiff] = []
    store.subscribe(collected.append)
    return collected
