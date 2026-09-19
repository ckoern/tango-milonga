import pytest
from PyQt6.QtCore import QModelIndex

from milonga.core.backend.fake import FakeBackend
from milonga.core.enums import StateCategory
from milonga.core.names import DeviceName, ServerName
from milonga.ui.context import AppContext, Target, TargetKind
from milonga.ui.models.tree import NodeKind, TreeNode
from milonga.ui.navigator import Navigator, Scope, ScopeLoader, target_of
from milonga.ui.theme import Tokens


@pytest.fixture
def navigator(context: AppContext, tokens: Tokens) -> Navigator:
    return Navigator(context, tokens)


async def _load(loader: ScopeLoader, scope: Scope) -> list[TreeNode]:
    loader.scope = scope
    return await loader.roots()


async def test_device_scope_is_three_levels(context: AppContext) -> None:
    loader = ScopeLoader(context)
    domains = await _load(loader, Scope.DEVICES)
    assert {node.label for node in domains} == {"id09", "sys", "tango"}

    id09 = next(node for node in domains if node.label == "id09")
    families = await loader.children(id09)
    assert "motor" in {node.label for node in families}

    motor = next(node for node in families if node.label == "motor")
    members = await loader.children(motor)
    assert {node.label for node in members} == {"phi", "theta"}
    assert members[0].payload == DeviceName.parse("id09/motor/phi")


async def test_host_scope_groups_hosts_and_shows_state(context: AppContext) -> None:
    loader = ScopeLoader(context)
    groups = await _load(loader, Scope.HOSTS)
    assert {node.label for node in groups} == {"Beamline", "Accelerator"}

    beamline = next(node for node in groups if node.label == "Beamline")
    hosts = await loader.children(beamline)
    by_name = {node.label: node for node in hosts}
    assert by_name["id09-srv-02"].category is StateCategory.WARNING
    assert by_name["id09-vac-01"].category is StateCategory.FAULT
    assert by_name["id09-vac-01"].detail == "unreachable"
    assert by_name["id09-vac-01"].expandable is False


async def test_host_children_are_its_servers(context: AppContext) -> None:
    loader = ScopeLoader(context)
    groups = await _load(loader, Scope.HOSTS)
    beamline = next(node for node in groups if node.label == "Beamline")
    hosts = await loader.children(beamline)
    host = next(node for node in hosts if node.label == "id09-srv-02")
    servers = await loader.children(host)
    stopped = next(node for node in servers if node.label == "Vacuum/id09-front")
    assert stopped.category is StateCategory.FAULT
    assert stopped.detail == "L2"


async def test_server_scope_groups_instances_by_executable(context: AppContext) -> None:
    loader = ScopeLoader(context)
    executables = await _load(loader, Scope.SERVERS)
    camera = next(node for node in executables if node.label == "Camera")
    assert camera.detail == "4"
    instances = await loader.children(camera)
    assert {node.label for node in instances} == {"det-1", "det-2", "det-3", "det-4"}
    assert "id09-det-01" in instances[0].detail


async def test_class_scope_lists_its_devices(context: AppContext) -> None:
    loader = ScopeLoader(context)
    classes = await _load(loader, Scope.CLASSES)
    motor = next(node for node in classes if node.label == "IcePAPMotor")
    devices = await loader.children(motor)
    assert {node.label for node in devices} == {"id09/motor/phi", "id09/motor/theta"}


async def test_free_property_scope_previews_values(context: AppContext) -> None:
    loader = ScopeLoader(context)
    objects = await _load(loader, Scope.PROPERTIES)
    assert [node.label for node in objects] == ["Milonga"]
    properties = await loader.children(objects[0])
    assert properties[0].label == "HostGroups"
    assert "+1" in properties[0].detail


async def test_alias_scope_is_flat(context: AppContext) -> None:
    loader = ScopeLoader(context)
    aliases = await _load(loader, Scope.ALIASES)
    assert {node.label for node in aliases} == {"phi", "theta"}
    assert all(not node.expandable for node in aliases)


def test_nodes_map_to_targets() -> None:
    device = target_of(TreeNode(NodeKind.DEVICE, "phi", DeviceName.parse("id09/motor/phi")))
    server = target_of(
        TreeNode(NodeKind.SERVER, "TangoTest/test", ServerName.parse("TangoTest/test"))
    )
    assert device is not None and device.kind is TargetKind.DEVICE
    assert server is not None and server.name == "TangoTest/test"
    assert target_of(TreeNode(NodeKind.DOMAIN, "sys", "sys")) is None


async def test_widget_loads_roots_and_switches_scope(navigator: Navigator) -> None:
    navigator.refresh()
    await navigator.idle()
    assert navigator.model.rowCount() == 3

    navigator.set_scope(Scope.CLASSES)
    await navigator.idle()
    assert navigator.model.rowCount() > 3
    assert navigator.scope is Scope.CLASSES


async def test_double_click_emits_the_target(navigator: Navigator) -> None:
    navigator.set_scope(Scope.CLASSES)
    await navigator.idle()
    targets: list[Target] = []
    navigator.targetActivated.connect(targets.append)
    index = navigator.proxy.index(0, 0, QModelIndex())
    navigator.view.doubleClicked.emit(index)
    assert targets and targets[0].kind is TargetKind.CLASS


async def test_activating_an_alias_resolves_the_device(navigator: Navigator) -> None:
    navigator.set_scope(Scope.ALIASES)
    await navigator.idle()
    targets: list[Target] = []
    navigator.targetActivated.connect(targets.append)
    index = navigator.proxy.index(0, 0, QModelIndex())
    navigator.view.doubleClicked.emit(index)
    await navigator.idle()
    assert targets and targets[0].name == "id09/motor/phi"


async def test_filter_hides_non_matching_rows(navigator: Navigator) -> None:
    navigator.set_scope(Scope.CLASSES)
    await navigator.idle()
    total = navigator.proxy.rowCount(QModelIndex())
    navigator.filter_box.setText("IcePAPMotor")
    assert navigator.proxy.rowCount(QModelIndex()) == 1
    assert total > 1


async def test_unreachable_host_is_reported_not_raised(
    context: AppContext, backend: FakeBackend
) -> None:
    backend.set_host_reachable("id09-srv-02", False)
    loader = ScopeLoader(context)
    groups = await _load(loader, Scope.HOSTS)
    beamline = next(node for node in groups if node.label == "Beamline")
    hosts = await loader.children(beamline)
    failed = next(node for node in hosts if node.label == "id09-srv-02")
    assert failed.category is StateCategory.FAULT
    assert "does not answer" in failed.tooltip
