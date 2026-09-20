"""Document panels."""

from milonga.ui.context import AppContext, Target, TargetKind
from milonga.ui.panels.base import Panel
from milonga.ui.panels.class_panel import ClassPanel
from milonga.ui.panels.device import DevicePanel
from milonga.ui.panels.host import HostPanel
from milonga.ui.panels.node_tiles import NodeTilesPanel
from milonga.ui.panels.object_panel import ObjectPanel
from milonga.ui.panels.overview import OverviewPanel
from milonga.ui.panels.server import ServerPanel
from milonga.ui.theme import Tokens

__all__ = [
    "ClassPanel",
    "DevicePanel",
    "HostPanel",
    "NodeTilesPanel",
    "ObjectPanel",
    "OverviewPanel",
    "Panel",
    "ServerPanel",
    "create_panel",
]


def create_panel(context: AppContext, tokens: Tokens, target: Target) -> Panel:
    match target.kind:
        case TargetKind.DEVICE:
            return DevicePanel(context, tokens, target)
        case TargetKind.SERVER:
            return ServerPanel(context, tokens, target)
        case TargetKind.CLASS:
            return ClassPanel(context, tokens, target)
        case TargetKind.OBJECT:
            return ObjectPanel(context, tokens, target)
        case TargetKind.HOST:
            return HostPanel(context, tokens, target)
        case TargetKind.SYSTEM:
            return OverviewPanel(context, tokens, target)
        case TargetKind.TILES:
            return NodeTilesPanel(context, tokens, target)
