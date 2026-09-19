import numpy as np

from milonga.core.enums import HostState, ServerRunState
from milonga.core.model import (
    AttributeValue,
    DeviceInfo,
    DeviceSnapshot,
    HostSnapshot,
    ServerInfo,
    ServerSnapshot,
)
from milonga.core.names import AttributeRef, DeviceName, ServerName
from milonga.core.store import StoreDiff, SystemStore

HOST = "id09-srv-02"
SERVER = ServerName("TangoTest", "test")
OTHER = ServerName("PyAlarm", "id09")
DEVICE = DeviceName.parse("sys/tg_test/1")
REF = AttributeRef(DEVICE, "double_scalar")


def _server(name: ServerName, state: ServerRunState) -> ServerSnapshot:
    return ServerSnapshot(ServerInfo(name, HOST, 1, True), run_state=state)


def _host(*servers: ServerSnapshot) -> HostSnapshot:
    return HostSnapshot(HOST, HostState.MIXED, servers=servers)


def test_put_host_reports_host_and_servers(store: SystemStore, diffs: list[StoreDiff]) -> None:
    store.put_host(_host(_server(SERVER, ServerRunState.RUNNING)))
    assert diffs == [StoreDiff(hosts=frozenset({HOST}), servers=frozenset({SERVER}))]
    assert store.server(SERVER) is not None


def test_identical_snapshot_notifies_nobody(store: SystemStore, diffs: list[StoreDiff]) -> None:
    snapshot = _host(_server(SERVER, ServerRunState.RUNNING))
    store.put_host(snapshot)
    store.put_host(_host(_server(SERVER, ServerRunState.RUNNING)))
    assert len(diffs) == 1


def test_only_changed_servers_are_named(store: SystemStore, diffs: list[StoreDiff]) -> None:
    store.put_host(
        _host(_server(SERVER, ServerRunState.RUNNING), _server(OTHER, ServerRunState.RUNNING))
    )
    store.put_host(
        _host(_server(SERVER, ServerRunState.STOPPED), _server(OTHER, ServerRunState.RUNNING))
    )
    assert diffs[-1].servers == frozenset({SERVER})


def test_batch_coalesces_notifications(store: SystemStore, diffs: list[StoreDiff]) -> None:
    with store.batch():
        store.put_host(_host(_server(SERVER, ServerRunState.RUNNING)))
        store.put_device(DeviceSnapshot(DeviceInfo(DEVICE, "TangoTest", SERVER)))
    assert len(diffs) == 1
    assert diffs[0].hosts == frozenset({HOST})
    assert diffs[0].devices == frozenset({DEVICE})


def test_values_are_stored_even_when_array_valued(
    store: SystemStore, diffs: list[StoreDiff]
) -> None:
    value = AttributeValue("double_spectrum", np.arange(4.0))
    store.put_value(REF, value)
    store.put_value(REF, value)
    assert len(diffs) == 2
    assert store.value(REF) is value


def test_diff_targets_a_device_through_its_attributes() -> None:
    assert StoreDiff(attributes=frozenset({REF})).touches_device(DEVICE)
    assert not StoreDiff().touches_device(DEVICE)


def test_remove_host_drops_its_servers(store: SystemStore, diffs: list[StoreDiff]) -> None:
    store.put_host(_host(_server(SERVER, ServerRunState.RUNNING)))
    store.remove_host(HOST)
    assert store.host(HOST) is None
    assert store.server(SERVER) is None
    assert diffs[-1].servers == frozenset({SERVER})


def test_remove_device_drops_its_values(store: SystemStore) -> None:
    store.put_device(DeviceSnapshot(DeviceInfo(DEVICE, "TangoTest", SERVER)))
    store.put_value(REF, AttributeValue("double_scalar", 1.0))
    store.remove_device(DEVICE)
    assert store.value(REF) is None


def test_put_server_updates_the_owning_host(store: SystemStore) -> None:
    store.put_host(_host(_server(SERVER, ServerRunState.RUNNING)))
    store.put_server(_server(SERVER, ServerRunState.STOPPED))
    host = store.host(HOST)
    assert host is not None
    assert host.servers[0].run_state is ServerRunState.STOPPED


def test_unsubscribe_stops_notifications(store: SystemStore) -> None:
    seen: list[StoreDiff] = []
    unsubscribe = store.subscribe(seen.append)
    store.put_device(DeviceSnapshot(DeviceInfo(DEVICE, "TangoTest", SERVER)))
    unsubscribe()
    store.remove_device(DEVICE)
    assert len(seen) == 1
