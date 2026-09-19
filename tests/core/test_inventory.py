from milonga.core.backend.fake import FakeBackend
from milonga.core.enums import TangoState
from milonga.core.names import DeviceName, ServerName
from milonga.core.services.inventory import Inventory

DEVICE = DeviceName.parse("sys/tg_test/1")
MOTOR = DeviceName.parse("id09/motor/phi")
SERVER = ServerName.parse("IcePAP/id09")


async def test_snapshot_of_a_running_device(backend: FakeBackend) -> None:
    snapshot = await Inventory(backend).device_snapshot(DEVICE)
    assert snapshot.info.exported
    assert snapshot.state.state is TangoState.ON
    assert snapshot.info.class_name == "TangoTest"
    assert snapshot.error is None


async def test_snapshot_of_a_stopped_device_has_no_state(backend: FakeBackend) -> None:
    backend.stop_server("TangoTest/test")
    snapshot = await Inventory(backend).device_snapshot(DEVICE)
    assert snapshot.info.exported is False
    assert snapshot.state.state is TangoState.UNKNOWN


async def test_snapshot_of_a_missing_device_carries_the_error(backend: FakeBackend) -> None:
    snapshot = await Inventory(backend).device_snapshot(DeviceName.parse("no/such/device"))
    assert snapshot.error is not None
    assert "unknown device" in snapshot.error.message


async def test_server_snapshot_counts_devices_and_classes(backend: FakeBackend) -> None:
    snapshot = await Inventory(backend).server_snapshot(SERVER)
    assert snapshot.device_count == 3
    assert set(snapshot.classes) == {"IcePAPController", "IcePAPMotor"}
    assert snapshot.info.level == 2


async def test_tree_listing_is_lazy_per_level(backend: FakeBackend) -> None:
    inventory = Inventory(backend)
    assert "id09" in await inventory.domains()
    assert "motor" in await inventory.families("id09")
    assert set(await inventory.members("id09", "motor")) == {"phi", "theta"}


async def test_many_snapshots_load_concurrently(backend: FakeBackend) -> None:
    devices = await backend.get_device_list()
    snapshots = await Inventory(backend).device_snapshots(devices)
    assert len(snapshots) == len(devices)


async def test_resolve_accepts_names_and_aliases(backend: FakeBackend) -> None:
    inventory = Inventory(backend)
    assert await inventory.resolve("id09/motor/phi") == MOTOR
    assert await inventory.resolve("phi") == MOTOR
    assert await inventory.resolve("nothing") is None
