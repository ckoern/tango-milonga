"""Read-only enforcement in one place.

Every mutating method of :class:`TangoBackend` is listed here and refused;
everything else is forwarded to the wrapped backend by ``__getattr__``, so a
new read method needs no change and a new write method fails loudly in tests
until it is classified.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from milonga.core.backend.protocol import TangoBackend
from milonga.core.errors import ReadOnlyError

WRITE_METHODS: frozenset[str] = frozenset(
    {
        "add_device",
        "add_logging_target",
        "add_server",
        "delete_class_properties",
        "delete_device",
        "delete_device_alias",
        "delete_device_attribute_properties",
        "delete_device_properties",
        "delete_properties",
        "delete_server",
        "execute_command",
        "put_class_attribute_properties",
        "put_class_properties",
        "put_device_alias",
        "put_device_attribute_properties",
        "put_device_properties",
        "put_properties",
        "put_server_info",
        "remove_logging_target",
        "rename_device",
        "rename_server",
        "set_attribute_specs",
        "set_logging_level",
        "set_polling",
        "stop_polling",
        "unexport_device",
        "write_attribute",
    }
)


class ReadOnlyBackend:
    """Wraps a backend so that no write leaves the process."""

    def __init__(self, backend: TangoBackend, *, allow_commands: bool = False) -> None:
        self._backend = backend
        self._blocked = WRITE_METHODS - ({"execute_command"} if allow_commands else set())

    @property
    def tango_host(self) -> str:
        return self._backend.tango_host

    @property
    def writable(self) -> bool:
        return False

    @property
    def wrapped(self) -> TangoBackend:
        return self._backend

    async def close(self) -> None:
        await self._backend.close()

    def __getattr__(self, name: str) -> Any:
        if name in self._blocked:
            return _refuse(name)
        return getattr(self._backend, name)


def _refuse(name: str) -> Callable[..., Coroutine[Any, Any, Any]]:
    async def refused(*_args: Any, **_kwargs: Any) -> Any:
        raise ReadOnlyError(f"{name} refused: the session is read-only")

    return refused
