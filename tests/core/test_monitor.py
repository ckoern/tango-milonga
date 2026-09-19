import asyncio
from collections.abc import AsyncIterator

import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.enums import DataSource
from milonga.core.model import EventData
from milonga.core.monitor import MonitorHub
from milonga.core.names import AttributeRef, DeviceName
from milonga.core.store import SystemStore

DEVICE = DeviceName.parse("sys/tg_test/1")
REF = AttributeRef(DEVICE, "double_scalar")


@pytest.fixture
async def hub(backend: FakeBackend, store: SystemStore) -> AsyncIterator[MonitorHub]:
    monitor = MonitorHub(backend, store=store, fallback_period=0.01)
    yield monitor
    await monitor.aclose()


async def test_first_value_arrives_without_waiting_for_an_event(hub: MonitorHub) -> None:
    seen: list[EventData] = []
    await hub.watch(REF, seen.append)
    assert seen[0].value is not None
    assert seen[0].value.value == pytest.approx(12.8741)
    assert seen[0].value.source is DataSource.EVENTS


async def test_events_reach_every_watcher(hub: MonitorHub, backend: FakeBackend) -> None:
    first: list[EventData] = []
    second: list[EventData] = []
    await hub.watch(REF, first.append)
    await hub.watch(REF, second.append)
    backend.set_attribute_value(DEVICE, "double_scalar", 20.0)
    assert first[-1].value is not None and first[-1].value.value == 20.0
    assert second[-1].value is not None and second[-1].value.value == 20.0


async def test_one_subscription_per_attribute(hub: MonitorHub, backend: FakeBackend) -> None:
    await hub.watch(REF, lambda _event: None)
    await hub.watch(REF, lambda _event: None)
    assert backend.subscription_count == 1
    assert hub.stats().channels == 1
    assert hub.stats().watchers == 2


async def test_channel_survives_until_the_last_watcher_leaves(
    hub: MonitorHub, backend: FakeBackend
) -> None:
    first = await hub.watch(REF, lambda _event: None)
    second = await hub.watch(REF, lambda _event: None)
    await first.aclose()
    assert backend.subscription_count == 1
    await second.aclose()
    assert backend.subscription_count == 0
    assert hub.stats().channels == 0


async def test_release_from_synchronous_code(hub: MonitorHub, backend: FakeBackend) -> None:
    watch = await hub.watch(REF, lambda _event: None)
    watch.release()
    await hub.drain()
    assert backend.subscription_count == 0


async def test_falls_back_to_polling_and_says_so(hub: MonitorHub, backend: FakeBackend) -> None:
    backend.event_blocked.add(REF)
    seen: list[EventData] = []
    watch = await hub.watch(REF, seen.append)
    await asyncio.sleep(0.05)
    assert watch.source is DataSource.POLLING
    assert seen and seen[0].value is not None
    assert seen[0].value.source is DataSource.POLLING
    assert len(seen) > 1
    assert backend.subscription_count == 0


async def test_polling_stops_with_the_last_watcher(hub: MonitorHub, backend: FakeBackend) -> None:
    backend.event_blocked.add(REF)
    watch = await hub.watch(REF, lambda _event: None)
    await asyncio.sleep(0.02)
    await watch.aclose()
    before = len(backend.call_log)
    await asyncio.sleep(0.05)
    assert len(backend.call_log) == before


async def test_values_land_in_the_store(
    hub: MonitorHub, backend: FakeBackend, store: SystemStore
) -> None:
    await hub.watch(REF, lambda _event: None)
    backend.set_attribute_value(DEVICE, "double_scalar", 7.5)
    value = store.value(REF)
    assert value is not None and value.value == 7.5


async def test_unreachable_device_reports_an_error(hub: MonitorHub, backend: FakeBackend) -> None:
    backend.set_host_reachable("id09-srv-02", False)
    seen: list[EventData] = []
    await hub.watch(REF, seen.append)
    await asyncio.sleep(0.03)
    assert seen and seen[0].error is not None
    assert "does not answer" in seen[0].error.message


async def test_late_watcher_gets_the_last_value_immediately(
    hub: MonitorHub, backend: FakeBackend
) -> None:
    await hub.watch(REF, lambda _event: None)
    backend.set_attribute_value(DEVICE, "double_scalar", 33.0)
    seen: list[EventData] = []
    await hub.watch(REF, seen.append)
    assert seen[0].value is not None and seen[0].value.value == 33.0
