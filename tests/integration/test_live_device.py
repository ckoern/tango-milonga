"""A running TangoTest, through the backend, the monitor and the commands.

Skipped unless ``sys/tg_test/1`` answers. Its configuration and polling are
changed and put back; the test checks the database holds what it held before.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace

import numpy as np
import pytest

from milonga.core.backend.pytango_backend import PyTangoBackend
from milonga.core.commands import CommandRunner, SetAttributeConfig, SetPolling
from milonga.core.enums import AttrDataFormat, TangoState
from milonga.core.errors import DeviceUnreachable
from milonga.core.model import EventData
from milonga.core.monitor import MonitorHub
from milonga.core.names import AttributeRef, DeviceName

pytestmark = pytest.mark.integration

DEVICE = DeviceName.parse("sys/tg_test/1")


@pytest.fixture
async def tangotest(tango_backend: PyTangoBackend) -> AsyncIterator[PyTangoBackend]:
    try:
        await tango_backend.ping(DEVICE)
    except DeviceUnreachable:
        pytest.skip("sys/tg_test/1 is not running")
    properties = await tango_backend.get_device_properties(DEVICE)
    attribute_properties = await tango_backend.get_device_attribute_properties(DEVICE)
    yield tango_backend
    assert await tango_backend.get_device_properties(DEVICE) == properties
    assert await tango_backend.get_device_attribute_properties(DEVICE) == attribute_properties


async def test_every_data_format_reads(tangotest: PyTangoBackend) -> None:
    scalar, spectrum, image, state = await tangotest.read_attributes(
        DEVICE, ["double_scalar", "double_spectrum", "double_image", "State"]
    )
    assert isinstance(scalar.value, float)
    assert spectrum.data_format is AttrDataFormat.SPECTRUM
    assert isinstance(spectrum.value, np.ndarray) and spectrum.dim_x == len(spectrum.value)
    assert image.data_format is AttrDataFormat.IMAGE
    assert image.value.shape == (image.dim_y, image.dim_x)
    assert isinstance(state.value, TangoState)


async def test_writing_and_commands(tangotest: PyTangoBackend) -> None:
    (before,) = await tangotest.read_attributes(DEVICE, ["long_scalar"])
    await tangotest.write_attribute(DEVICE, "long_scalar", 1234)
    (after,) = await tangotest.read_attributes(DEVICE, ["long_scalar"])
    assert after.write_value == 1234
    await tangotest.write_attribute(DEVICE, "long_scalar", int(before.write_value or 0))
    assert await tangotest.execute_command(DEVICE, "DevDouble", 3.5) == 3.5
    assert await tangotest.execute_command(DEVICE, "DevString", "milonga") == "milonga"
    assert list(await tangotest.execute_command(DEVICE, "DevVarLongArray", [1, 2, 3])) == [1, 2, 3]
    assert isinstance(await tangotest.execute_command(DEVICE, "State"), TangoState)


async def test_the_monitor_delivers_values(tangotest: PyTangoBackend) -> None:
    hub = MonitorHub(tangotest, fallback_period=0.2)
    seen: list[EventData] = []
    await hub.watch(AttributeRef(DEVICE, "double_scalar"), seen.append)
    await asyncio.sleep(0.8)
    await hub.aclose()
    assert len(seen) >= 2
    assert all(event.value is not None for event in seen)


async def test_attribute_configuration_is_undone_exactly(tangotest: PyTangoBackend) -> None:
    (spec,) = [s for s in await tangotest.get_attribute_specs(DEVICE) if s.name == "double_scalar"]
    runner = CommandRunner(tangotest)
    command = SetAttributeConfig(DEVICE, replace(spec, label="Integration test", unit="mm"))
    await runner.run([command])
    (changed,) = [
        s for s in await tangotest.get_attribute_specs(DEVICE) if s.name == "double_scalar"
    ]
    assert (changed.label, changed.unit) == ("Integration test", "mm")
    await runner.undo(command)
    (restored,) = [
        s for s in await tangotest.get_attribute_specs(DEVICE) if s.name == "double_scalar"
    ]
    assert (restored.label, restored.unit) == (spec.label, spec.unit)


async def test_polling_is_undone(tangotest: PyTangoBackend) -> None:
    before = await tangotest.get_polling(DEVICE)
    runner = CommandRunner(tangotest)
    command = SetPolling(DEVICE, "long_scalar", period_ms=700)
    await runner.run([command])
    polled = {entry.name: entry.period_ms for entry in await tangotest.get_polling(DEVICE)}
    assert polled["long_scalar"] == 700
    await runner.undo(command)
    assert await tangotest.get_polling(DEVICE) == before or {
        entry.name for entry in await tangotest.get_polling(DEVICE)
    } == {entry.name for entry in before}
