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


async def test_double_clicking_a_branch_opens_its_members_as_tiles(
    navigator: Navigator,
) -> None:
    navigator.set_scope(Scope.CLASSES)
    await navigator.idle()
    targets: list[Target] = []
    navigator.targetActivated.connect(targets.append)
    index = navigator.proxy.index(0, 0, QModelIndex())
    label = navigator.node_at(index).label
    navigator.view.doubleClicked.emit(index)
    assert targets and targets[0].kind is TargetKind.TILES
    assert targets[0].path == ("CLASSES", label)


async def test_double_clicking_a_leaf_opens_its_panel(navigator: Navigator) -> None:
    navigator.set_scope(Scope.DEVICES)
    await navigator.idle()
    domain = navigator.proxy.index(0, 0, QModelIndex())
    navigator.proxy.fetchMore(domain)
    await navigator.idle()
    family = navigator.proxy.index(0, 0, domain)
    navigator.proxy.fetchMore(family)
    await navigator.idle()
    device = navigator.proxy.index(0, 0, family)

    targets: list[Target] = []
    navigator.targetActivated.connect(targets.append)
    navigator.view.doubleClicked.emit(device)
    assert targets and targets[0].kind is TargetKind.DEVICE


async def test_a_single_click_opens_and_closes_a_branch(navigator: Navigator) -> None:
    navigator.set_scope(Scope.DEVICES)
    await navigator.idle()
    index = navigator.proxy.index(0, 0, QModelIndex())
    assert not navigator.view.isExpanded(index)
    navigator.view.clicked.emit(index)
    await navigator.idle()
    assert navigator.view.isExpanded(index)
    navigator.view.clicked.emit(index)
    assert not navigator.view.isExpanded(index)


async def test_a_single_click_on_a_leaf_opens_nothing(navigator: Navigator) -> None:
    navigator.set_scope(Scope.ALIASES)
    await navigator.idle()
    targets: list[Target] = []
    navigator.targetActivated.connect(targets.append)
    navigator.view.clicked.emit(navigator.proxy.index(0, 0, QModelIndex()))
    await navigator.idle()
    assert targets == []


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


async def test_each_scope_keeps_its_own_tree(navigator: Navigator) -> None:
    navigator.set_scope(Scope.DEVICES)
    await navigator.idle()
    devices = navigator.model
    navigator.set_scope(Scope.CLASSES)
    await navigator.idle()
    assert navigator.model is not devices
    calls = navigator.pages[Scope.DEVICES].model.rowCount()
    navigator.set_scope(Scope.DEVICES)
    await navigator.idle()
    assert navigator.model is devices
    assert devices.rowCount() == calls


async def test_a_tab_loads_the_first_time_it_is_shown(
    navigator: Navigator, backend: FakeBackend
) -> None:
    await navigator.idle()
    assert not navigator.pages[Scope.ALIASES].loaded
    navigator.tab_bar.setCurrentIndex(list(Scope).index(Scope.ALIASES))
    await navigator.idle()
    assert navigator.pages[Scope.ALIASES].loaded
    assert navigator.scope is Scope.ALIASES
    assert navigator.model.rowCount() == 2


async def test_the_filter_follows_the_tab(navigator: Navigator) -> None:
    navigator.set_scope(Scope.CLASSES)
    await navigator.idle()
    navigator.filter_box.setText("IcePAPMotor")
    navigator.set_scope(Scope.ALIASES)
    await navigator.idle()
    assert navigator.proxy.filterRegularExpression().pattern() == "IcePAPMotor"


async def test_every_domain_in_the_device_tab_expands(navigator: Navigator) -> None:
    navigator.set_scope(Scope.DEVICES)
    await navigator.idle()
    # the path a view takes when a branch opens: through the proxy
    for row in range(navigator.proxy.rowCount(QModelIndex())):
        navigator.proxy.fetchMore(navigator.proxy.index(row, 0, QModelIndex()))
    await navigator.idle()
    for row in range(navigator.proxy.rowCount(QModelIndex())):
        domain = navigator.proxy.index(row, 0, QModelIndex())
        assert navigator.proxy.rowCount(domain) > 0, navigator.node_at(domain)


def test_every_scope_is_one_click_away(navigator: Navigator) -> None:
    """Six labels in a row need more width than the navigator has, so they wrap."""
    selector = navigator.tab_bar
    assert selector._group.buttons() and len(selector._group.buttons()) == len(list(Scope))
    assert selector.sizeHint().width() <= 300
    assert selector.sizeHint().height() > 30, "two rows"


async def test_clicking_a_scope_switches_the_tree(navigator: Navigator) -> None:
    await navigator.idle()
    buttons = {button.text(): button for button in navigator.tab_bar._group.buttons()}
    buttons["Classes"].click()
    await navigator.idle()
    assert navigator.scope is Scope.CLASSES
    assert navigator.model.rowCount() > 0
