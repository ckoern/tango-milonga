"""Tiles for the members of a branch."""

import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.enums import StateCategory
from milonga.core.names import DeviceName
from milonga.ui.context import AppContext, Target, TargetKind
from milonga.ui.menus import labels
from milonga.ui.navigator import Scope
from milonga.ui.panels import create_panel
from milonga.ui.panels.node_tiles import NodeTilesPanel
from milonga.ui.theme import Tokens


async def _tiles(context: AppContext, tokens: Tokens, scope: Scope, *path: str) -> NodeTilesPanel:
    panel = create_panel(context, tokens, Target.tiles(scope.name, path))
    assert isinstance(panel, NodeTilesPanel)
    await panel.idle()
    return panel


async def test_a_domain_shows_its_families(context: AppContext, tokens: Tokens) -> None:
    panel = await _tiles(context, tokens, Scope.DEVICES, "sys")
    assert set(panel.grid.cards) == {"access_control", "database", "tg_test"}
    tg_test = panel.grid.card("tg_test")
    assert tg_test is not None
    assert tg_test.chip.text() == "1/1", "one device, exported"
    assert tg_test.left.text() == "1 member"
    assert panel.title == "sys"
    assert "3 families" in panel.summary.text()


async def test_a_family_with_a_stopped_device_is_marked(
    context: AppContext, tokens: Tokens, backend: FakeBackend
) -> None:
    backend.stop_server("TangoTest/test")
    panel = await _tiles(context, tokens, Scope.DEVICES, "sys")
    card = panel.grid.card("tg_test")
    assert card is not None
    assert card.chip.text() == "0/1"


async def test_a_server_executable_shows_its_instances(
    context: AppContext, tokens: Tokens, backend: FakeBackend
) -> None:
    backend.register_server("TangoTest/spare", "id09-srv-02")
    panel = await _tiles(context, tokens, Scope.SERVERS, "TangoTest")
    assert set(panel.grid.cards) == {"test", "spare"}
    running = panel.grid.card("test")
    stopped = panel.grid.card("spare")
    assert running is not None and stopped is not None
    assert running.chip.text() == "RUNNING"
    assert stopped.chip.text() == "STOPPED"
    assert running.left.text().startswith("pid ")


async def test_a_class_shows_its_devices(context: AppContext, tokens: Tokens) -> None:
    panel = await _tiles(context, tokens, Scope.CLASSES, "IcePAPMotor")
    assert set(panel.grid.cards) == {"id09/motor/phi", "id09/motor/theta"}
    card = panel.grid.card("id09/motor/phi")
    assert card is not None
    assert card.chip.text() == "ON"
    assert card.subtitle.text() == "IcePAPMotor"
    assert card.left.text() == "IcePAP/id09"


async def test_a_group_of_hosts_shows_host_tiles(
    context: AppContext, tokens: Tokens
) -> None:
    panel = await _tiles(context, tokens, Scope.HOSTS, "Beamline")
    assert set(panel.grid.cards) == {
        "id09-det-01",
        "id09-srv-01",
        "id09-srv-02",
        "id09-vac-01",
    }
    card = panel.grid.card("id09-srv-02")
    assert card is not None
    assert card.chip.text() == "MIXED"


async def test_opening_a_member_follows_what_it_holds(
    context: AppContext, tokens: Tokens
) -> None:
    opened: list[Target] = []
    context.open_target = opened.append

    branches = await _tiles(context, tokens, Scope.DEVICES, "sys")
    branches.open_member("tg_test")
    assert opened[-1].kind is TargetKind.TILES
    assert opened[-1].path == ("DEVICES", "sys", "tg_test")

    leaves = await _tiles(context, tokens, Scope.DEVICES, "sys", "tg_test")
    assert set(leaves.grid.cards) == {"1"}
    leaves.open_member("1")
    assert opened[-1] == Target.device(DeviceName.parse("sys/tg_test/1"))


async def test_a_member_menu_offers_both_views(context: AppContext, tokens: Tokens) -> None:
    panel = await _tiles(context, tokens, Scope.SERVERS, "IcePAP")
    assert labels(panel.member_items("id09")) == ["Open panel", "Open tiles", "Refresh"]
    devices = await _tiles(context, tokens, Scope.CLASSES, "IcePAPMotor")
    assert labels(devices.member_items("id09/motor/phi")) == ["Open panel", "Refresh"]


async def test_a_branch_that_is_gone_says_so(context: AppContext, tokens: Tokens) -> None:
    panel = await _tiles(context, tokens, Scope.DEVICES, "nowhere")
    assert panel.banner.isVisibleTo(panel)
    assert panel.grid.cards == {}


async def test_large_branches_are_counted_not_inspected(
    context: AppContext, tokens: Tokens, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    from milonga.ui.panels import node_tiles

    monkeypatch.setattr(node_tiles, "MEMBER_LIMIT", 1)
    panel = await _tiles(context, tokens, Scope.DEVICES, "id09")
    card = panel.grid.card("motor")
    assert card is not None
    assert card.chip.text() == ""
    assert "members" in card.left.text()


def test_a_group_tile_summarises_its_members(tokens: Tokens) -> None:
    from milonga.ui.models.tree import NodeKind, TreeNode
    from milonga.ui.panels.node_tiles import group_tile
    from milonga.ui.tiles import Tile

    node = TreeNode(NodeKind.FAMILY, "motor", expandable=True)
    members = [
        Tile(key="a", title="a", category=StateCategory.NOMINAL),
        Tile(key="b", title="b", category=StateCategory.INACTIVE),
    ]
    mixed = group_tile(node, members, count_only=False)
    assert mixed.chip == "1/2"
    assert mixed.category is StateCategory.WARNING
    assert mixed.marks == (StateCategory.NOMINAL, StateCategory.INACTIVE)

    assert group_tile(node, members[:1], count_only=False).category is StateCategory.NOMINAL
    assert group_tile(node, members[1:], count_only=False).category is StateCategory.FAULT
    assert group_tile(node, [], count_only=True).chip == ""
