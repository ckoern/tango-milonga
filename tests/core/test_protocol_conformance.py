"""Assignments here fail type checking if a backend drifts from the protocol."""

from milonga.core.backend.demo import build_demo_backend
from milonga.core.backend.fake import FakeBackend
from milonga.core.backend.protocol import TangoBackend
from milonga.core.backend.readonly import WRITE_METHODS, ReadOnlyBackend


def test_fake_backend_satisfies_the_protocol() -> None:
    backend: TangoBackend = FakeBackend()
    assert backend.writable is True


def test_read_only_wrapper_satisfies_the_protocol() -> None:
    guarded: TangoBackend = ReadOnlyBackend(build_demo_backend())
    assert guarded.writable is False


def test_every_blocked_method_exists_on_the_backend() -> None:
    backend = FakeBackend()
    missing = [name for name in WRITE_METHODS if not hasattr(backend, name)]
    assert missing == []
