"""Context menus built from plain entries, so what they offer can be tested."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QAbstractScrollArea, QMenu, QWidget


@dataclass(frozen=True, slots=True)
class MenuEntry:
    label: str
    action: Callable[[], None]
    enabled: bool = True


SEPARATOR = None

type MenuItems = Sequence[MenuEntry | None]


def build_menu(parent: QWidget, items: MenuItems) -> QMenu:
    menu = QMenu(parent)
    for item in items:
        if item is None:
            menu.addSeparator()
            continue
        action = menu.addAction(item.label, item.action)
        if action is not None:
            action.setEnabled(item.enabled)
    return menu


def popup(widget: QWidget, point: QPoint, items: MenuItems) -> None:
    """Show a menu at a point given in the widget's (or its viewport's) coordinates."""
    entries = [item for item in items if item is not None]
    if not entries:
        return
    anchor: QWidget = widget
    if isinstance(widget, QAbstractScrollArea):
        viewport = widget.viewport()
        if viewport is not None:
            anchor = viewport
    build_menu(widget, items).exec(anchor.mapToGlobal(point))


def labels(items: MenuItems) -> list[str]:
    return [item.label for item in items if item is not None]


def entry(items: MenuItems, label: str) -> MenuEntry:
    for item in items:
        if item is not None and item.label == label:
            return item
    raise KeyError(label)
