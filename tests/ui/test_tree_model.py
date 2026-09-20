import pytest
from PyQt6.QtCore import QModelIndex
from PyQt6.QtWidgets import QApplication

from milonga.core.errors import ObjectNotFound
from milonga.ui.models.tree import LazyTreeModel, NodeKind, TreeNode
from milonga.ui.tasks import TaskRunner


@pytest.fixture
def runner(app: QApplication) -> TaskRunner:
    return TaskRunner()


async def test_children_load_only_when_a_branch_opens(runner: TaskRunner) -> None:
    calls: list[str] = []

    async def loader(node: TreeNode) -> list[TreeNode]:
        calls.append(node.label)
        return [TreeNode(NodeKind.DEVICE, f"{node.label}/child")]

    model = LazyTreeModel(loader, runner)
    model.set_roots([TreeNode(NodeKind.DOMAIN, "sys", expandable=True)])
    assert calls == []
    assert model.rowCount() == 1

    root = model.index(0, 0, QModelIndex())
    assert model.canFetchMore(root)
    model.fetchMore(root)
    await model.idle()
    assert calls == ["sys"]
    assert model.rowCount(root) == 1
    assert not model.canFetchMore(root)


async def test_a_branch_is_fetched_once(runner: TaskRunner) -> None:
    calls: list[str] = []

    async def loader(node: TreeNode) -> list[TreeNode]:
        calls.append(node.label)
        return []

    model = LazyTreeModel(loader, runner)
    model.set_roots([TreeNode(NodeKind.DOMAIN, "sys", expandable=True)])
    root = model.index(0, 0, QModelIndex())
    model.fetchMore(root)
    model.fetchMore(root)
    await model.idle()
    assert calls == ["sys"]


async def test_an_empty_branch_stops_offering_to_expand(runner: TaskRunner) -> None:
    async def loader(_node: TreeNode) -> list[TreeNode]:
        return []

    model = LazyTreeModel(loader, runner)
    model.set_roots([TreeNode(NodeKind.SERVER, "Empty/1", expandable=True)])
    root = model.index(0, 0, QModelIndex())
    model.fetchMore(root)
    await model.idle()
    assert model.rowCount(root) == 0
    assert not model.hasChildren(root)


async def test_a_failed_branch_shows_the_error_in_place(runner: TaskRunner) -> None:
    async def loader(_node: TreeNode) -> list[TreeNode]:
        raise ObjectNotFound("no such domain")

    model = LazyTreeModel(loader, runner)
    reports: list[object] = []
    model.loadFailed.connect(reports.append)
    model.set_roots([TreeNode(NodeKind.DOMAIN, "gone", expandable=True)])
    root = model.index(0, 0, QModelIndex())
    model.fetchMore(root)
    await model.idle()
    assert model.rowCount(root) == 1
    message = model.node(model.index(0, 0, root))
    assert message is not None and message.kind is NodeKind.MESSAGE
    assert "no such domain" in message.label
    assert len(reports) == 1


async def test_reload_refetches_a_branch(runner: TaskRunner) -> None:
    counter = {"n": 0}

    async def loader(_node: TreeNode) -> list[TreeNode]:
        counter["n"] += 1
        return [TreeNode(NodeKind.DEVICE, f"device-{counter['n']}")]

    model = LazyTreeModel(loader, runner)
    model.set_roots([TreeNode(NodeKind.DOMAIN, "sys", expandable=True)])
    root = model.index(0, 0, QModelIndex())
    model.fetchMore(root)
    await model.idle()
    model.reload(root)
    await model.idle()
    child = model.node(model.index(0, 0, root))
    assert child is not None and child.label == "device-2"


async def test_parent_and_index_round_trip(runner: TaskRunner) -> None:
    async def loader(node: TreeNode) -> list[TreeNode]:
        return [TreeNode(NodeKind.DEVICE, "child")]

    model = LazyTreeModel(loader, runner)
    model.set_roots([TreeNode(NodeKind.DOMAIN, "sys", expandable=True)])
    root = model.index(0, 0, QModelIndex())
    model.fetchMore(root)
    await model.idle()
    child = model.index(0, 0, root)
    assert model.parent(child) == root
    assert model.parent(root) == QModelIndex()


async def test_every_top_level_node_expands_under_its_own_index(runner: TaskRunner) -> None:
    async def loader(node: TreeNode) -> list[TreeNode]:
        return [TreeNode(NodeKind.DEVICE, f"{node.label}/child")]

    model = LazyTreeModel(loader, runner)
    model.set_roots(
        [TreeNode(NodeKind.DOMAIN, name, expandable=True) for name in ("id09", "sys", "tango")]
    )
    for row in range(3):
        model.fetchMore(model.index(row, 0, QModelIndex()))
    await model.idle()
    for row, name in enumerate(("id09", "sys", "tango")):
        parent = model.index(row, 0, QModelIndex())
        assert model.rowCount(parent) == 1
        child = model.node(model.index(0, 0, parent))
        assert child is not None and child.label == f"{name}/child"
        assert model.parent(model.index(0, 0, parent)) == parent


async def test_nodes_with_equal_contents_stay_distinct(runner: TaskRunner) -> None:
    async def loader(node: TreeNode) -> list[TreeNode]:
        return [TreeNode(NodeKind.DEVICE, "same"), TreeNode(NodeKind.DEVICE, "same")]

    model = LazyTreeModel(loader, runner)
    model.set_roots([TreeNode(NodeKind.DOMAIN, "sys", expandable=True)])
    root = model.index(0, 0, QModelIndex())
    model.fetchMore(root)
    await model.idle()
    second = model.index(1, 0, root)
    assert model.parent(second) == root
    assert second.row() == 1
