"""Fixtures for tests against the control system named by TANGO_HOST.

Reads touch whatever is there. Writes are confined to a sandbox — a server,
devices, a class and a free-property object all named ``MilongaTest`` — that
is removed before and after every test, including after a failed one.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest

from milonga.core.errors import TangoError
from milonga.core.model import DeviceRegistration
from milonga.core.names import DeviceName, ServerName

if TYPE_CHECKING:
    from milonga.core.backend.pytango_backend import PyTangoBackend

pytestmark = pytest.mark.integration

SANDBOX_SERVER = ServerName("MilongaTest", "pytest")
SANDBOX_DEVICES = (DeviceName.parse("milonga/test/1"), DeviceName.parse("milonga/test/2"))
SANDBOX_CLASS = "MilongaTestClass"
SANDBOX_OBJECT = "MilongaTest"
SANDBOX_ALIAS = "milonga-test-alias"


@pytest.fixture
async def tango_backend() -> AsyncIterator["PyTangoBackend"]:
    pytest.importorskip("tango")
    from milonga.core.backend.pytango_backend import PyTangoBackend

    try:
        backend = PyTangoBackend()
        await asyncio.wait_for(backend.get_server_list("DataBaseds/*"), 15)
    except (TangoError, TimeoutError) as error:
        pytest.skip(f"no Tango database reachable: {error}")
    yield backend
    await backend.close()


@pytest.fixture
async def sandbox(tango_backend: "PyTangoBackend") -> AsyncIterator["PyTangoBackend"]:
    await clean_sandbox(tango_backend)
    await tango_backend.add_server(
        SANDBOX_SERVER,
        [DeviceRegistration(device, SANDBOX_CLASS, SANDBOX_SERVER) for device in SANDBOX_DEVICES],
    )
    yield tango_backend
    await clean_sandbox(tango_backend)


SANDBOX_INSTANCES = ("pytest", "second", "renamed")


async def clean_sandbox(backend: "PyTangoBackend") -> None:
    for server in await backend.get_server_list(f"{SANDBOX_SERVER.exec_name}/*"):
        await backend.delete_server(server)
    # a run interrupted between writing a record and deleting its server leaves
    # the record behind, where the server list no longer shows it
    for instance in SANDBOX_INSTANCES:
        await asyncio.to_thread(
            _delete_record, str(ServerName(SANDBOX_SERVER.exec_name, instance))
        )
    for device in await backend.get_device_list("milonga/*/*"):
        await backend.delete_device(device)
    for alias in await backend.get_device_alias_list("milonga-test*"):
        await backend.delete_device_alias(alias)
    names = await backend.get_class_property_names(SANDBOX_CLASS)
    if names:
        await backend.delete_class_properties(SANDBOX_CLASS, names)
    names = await backend.get_object_property_names(SANDBOX_OBJECT)
    if names:
        await backend.delete_properties(SANDBOX_OBJECT, names)


def _delete_record(name: str) -> None:
    import tango

    tango.Database().delete_server_info(name)
