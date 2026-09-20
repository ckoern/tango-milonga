"""The children of one branch of a tree, as tiles.

Double-clicking a branch asks what is in it and how each member is: the
families of a domain, the instances of a server, the devices of a class.
"""

from collections.abc import Sequence

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from milonga.core.enums import StateCategory
from milonga.core.errors import ErrorReport, ObjectNotFound
from milonga.core.model import DeviceSnapshot, HostSnapshot
from milonga.core.services.diagnostics import Diagnostics, ServerDetail, format_uptime
from milonga.core.tasks import gather_limited
from milonga.ui.context import AppContext, Target
from milonga.ui.menus import SEPARATOR, MenuEntry, MenuItems, popup
from milonga.ui.models.tree import NodeKind, TreeNode
from milonga.ui.navigator import Scope, ScopeLoader, target_of
from milonga.ui.panels.base import Panel
from milonga.ui.panels.overview import host_tile
from milonga.ui.theme import Tokens, device_state_category
from milonga.ui.tiles import Tile, TileGrid


def counted(amount: int, word: str) -> str:
    """``1 family`` / ``3 families``, for the node kinds shown on the tiles."""
    if amount == 1:
        return f"{amount} {word}"
    plural = f"{word[:-1]}ies" if word.endswith("y") else f"{word}s"
    return f"{amount} {plural}"


MEMBER_LIMIT = 200
"""Above this many members, groups are counted rather than looked at: asking
every device of a large domain how it is would be a flood of calls."""


def device_tile(snapshot: DeviceSnapshot, label: str) -> Tile:
    exported = snapshot.info.exported
    return Tile(
        key=label,
        title=label,
        subtitle=snapshot.info.class_name or "—",
        chip=snapshot.state.state.value if exported else "NOT EXPORTED",
        category=(
            device_state_category(snapshot.state.state)
            if exported
            else StateCategory.INACTIVE
        ),
        footer_left=str(snapshot.info.server),
        footer_right=f"pid {snapshot.info.pid}" if snapshot.info.pid else "",
        tooltip=str(snapshot.name),
        alert=snapshot.error is not None,
    )


def server_tile(detail: ServerDetail, node: TreeNode) -> Tile:
    return Tile(
        key=node.label,
        title=node.label,
        subtitle=node.detail or "—",
        chip="RUNNING" if detail.running else "STOPPED",
        category=StateCategory.NOMINAL if detail.running else StateCategory.FAULT,
        footer_left=f"pid {detail.pid}" if detail.pid else "not running",
        footer_right=format_uptime(detail.uptime()) if detail.running else "",
        tooltip=str(detail.name),
    )


def group_tile(node: TreeNode, members: Sequence[Tile], count_only: bool) -> Tile:
    """A branch: how many members it has, and how they are."""
    if count_only or not members:
        return Tile(
            key=node.label,
            title=node.label,
            subtitle=node.kind.value,
            footer_left=(
                counted(int(node.detail), "member")
                if count_only and (node.detail or "").isdigit()
                else ("many members" if count_only else "empty")
            ),
            tooltip=node.tooltip or node.label,
        )
    good = sum(1 for tile in members if tile.category is StateCategory.NOMINAL)
    category = StateCategory.NOMINAL
    if good == 0:
        category = StateCategory.FAULT
    elif good < len(members):
        category = StateCategory.WARNING
    return Tile(
        key=node.label,
        title=node.label,
        subtitle=node.kind.value,
        chip=f"{good}/{len(members)}",
        category=category,
        marks=tuple(tile.category or StateCategory.UNKNOWN for tile in members),
        footer_left=counted(len(members), "member"),
        footer_right=f"{good} up" if good else "none up",
        tooltip=node.tooltip or node.label,
    )


def plain_tile(node: TreeNode) -> Tile:
    return Tile(
        key=node.label,
        title=node.label,
        subtitle=node.kind.value,
        footer_left=node.detail,
        tooltip=node.tooltip or node.label,
    )


class NodeTilesPanel(Panel):
    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        target: Target,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(context, tokens, target, parent)
        scope_name, *path = target.path
        self.scope = Scope[scope_name]
        self.path = tuple(path)
        self.loader = ScopeLoader(context, self.scope)
        self._children: dict[str, TreeNode] = {}

        self.summary = QLabel(self)
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.refresh)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(self.summary)
        controls.addStretch(1)
        controls.addWidget(self.refresh_button)

        self.grid = TileGrid(tokens, self)
        self.grid.activated.connect(self.open_member)
        self.grid.menuRequested.connect(self._menu)

        layout = self.base_layout()
        layout.addLayout(controls)
        layout.addWidget(self.grid, 1)
        self.header.set_header(self.title, " ▸ ".join((self.scope.value, *self.path[:-1])))
        self.header.chip.setVisible(False)
        self.refresh()

    @property
    def title(self) -> str:
        return self.path[-1] if self.path else self.scope.value

    def refresh(self) -> None:
        self.banner.clear()
        self.runner.run(self._load(), on_result=self._show, on_error=self._failed)

    # -------------------------------------------------------------------- loading

    async def _load(self) -> tuple[list[Tile], dict[str, TreeNode]]:
        node = await self._walk()
        children = list(await self.loader.children(node)) if node is not None else []
        tiles = await self._describe(children)
        return tiles, {child.label: child for child in children}

    async def _walk(self) -> TreeNode | None:
        """Find the branch again from the scope's roots, by the labels on the way."""
        nodes = list(await self.loader.roots())
        node: TreeNode | None = None
        for label in self.path:
            node = next((item for item in nodes if item.label == label), None)
            if node is None:
                raise ObjectNotFound(
                    f"{label} is no longer in the {self.scope.value.lower()} tree"
                )
            nodes = list(await self.loader.children(node))
        return node

    async def _describe(self, children: Sequence[TreeNode]) -> list[Tile]:
        if not children:
            return []
        kinds = {child.kind for child in children}
        if kinds == {NodeKind.DEVICE}:
            return await self._device_tiles(children)
        if kinds == {NodeKind.SERVER}:
            return await self._server_tiles(children)
        if kinds == {NodeKind.HOST}:
            return [
                host_tile(child.payload)
                for child in children
                if isinstance(child.payload, HostSnapshot)
            ]
        return await self._group_tiles(children)

    async def _device_tiles(self, children: Sequence[TreeNode]) -> list[Tile]:
        snapshots = await self.context.inventory.device_snapshots(
            [child.payload for child in children]
        )
        return [
            device_tile(snapshot, child.label)
            for child, snapshot in zip(children, snapshots, strict=True)
        ]

    async def _server_tiles(self, children: Sequence[TreeNode]) -> list[Tile]:
        details = await Diagnostics(self.context.backend).server_details(
            [child.payload for child in children]
        )
        return [
            server_tile(detail, child)
            for child, detail in zip(children, details, strict=True)
        ]

    async def _group_tiles(self, children: Sequence[TreeNode]) -> list[Tile]:
        async def members(node: TreeNode) -> tuple[TreeNode, list[TreeNode]]:
            if not node.expandable:
                return node, []
            return node, list(await self.loader.children(node))

        loaded = await gather_limited(children, members)
        total = sum(len(items) for _node, items in loaded)
        count_only = total > MEMBER_LIMIT
        tiles: list[Tile] = []
        for node, items in loaded:
            if not node.expandable:
                tiles.append(plain_tile(node))
                continue
            node.detail = node.detail or str(len(items))
            described = [] if count_only else await self._describe(items)
            tiles.append(group_tile(node, described, count_only))
        return tiles

    def _show(self, loaded: tuple[list[Tile], dict[str, TreeNode]]) -> None:
        tiles, children = loaded
        self._children = children
        self.grid.set_tiles(tiles)
        kinds = sorted({child.kind.value for child in children.values()})
        self.summary.setText(
            ", ".join(
                counted(sum(1 for child in children.values() if child.kind.value == kind), kind)
                for kind in kinds
            )
            or "nothing"
        )

    # -------------------------------------------------------------------- opening

    def open_member(self, key: str) -> None:
        """A branch opens as tiles of its own; a leaf opens its panel."""
        node = self._children.get(key)
        if node is None:
            return
        target = self.tiles_target(node) if node.expandable else target_of(node)
        if target is not None:
            self.context.open_target(target)

    def tiles_target(self, node: TreeNode) -> Target:
        return Target.tiles(self.scope.name, (*self.path, node.label))

    def member_items(self, key: str) -> MenuItems:
        node = self._children.get(key)
        if node is None:
            return []
        items: list[MenuEntry | None] = []
        target = target_of(node)
        if target is not None:
            items.append(
                MenuEntry("Open panel", lambda: self.context.open_target(target))
            )
        if node.expandable:
            items.append(
                MenuEntry(
                    "Open tiles",
                    lambda: self.context.open_target(self.tiles_target(node)),
                )
            )
        if items:
            items.append(SEPARATOR)
        items.append(MenuEntry("Refresh", self.refresh))
        return items

    def _menu(self, key: str, point: QPoint) -> None:
        card = self.grid.card(key)
        if card is not None:
            popup(card, point, self.member_items(key))

    def _failed(self, report: ErrorReport) -> None:
        self.banner.show_error(report, self.title)
