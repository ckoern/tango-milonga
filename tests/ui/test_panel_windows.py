"""Panels moved into windows of their own."""

import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.names import DeviceName
from milonga.ui.context import AppContext, Target
from milonga.ui.mainwindow import MainWindow
from milonga.ui.menus import entry, labels
from milonga.ui.panel_window import PanelWindow
from milonga.ui.panels.device import DevicePanel
from milonga.ui.theme import Theme, Tokens

FIRST = Target.device(DeviceName.parse("sys/tg_test/1"))
SECOND = Target.device(DeviceName.parse("id09/motor/phi"))


@pytest.fixture
async def window(context: AppContext, tokens: Tokens) -> MainWindow:
    main = MainWindow(context, tokens, Theme.LIGHT)
    await main.idle()
    return main


async def _detach(window: MainWindow, target: Target) -> tuple[DevicePanel, PanelWindow]:
    window.open_target(target)
    await window.idle()
    panel = window._panels[target.uri]
    assert isinstance(panel, DevicePanel)
    detached = window.detach_panel(panel)
    detached.show()
    await window.idle()
    return panel, detached


async def test_a_tab_moves_into_its_own_window(window: MainWindow) -> None:
    others = window.tabs.count()
    panel, detached = await _detach(window, FIRST)
    assert window.tabs.count() == others, "the panel left the tabs"
    assert window.tabs.indexOf(panel) == -1
    assert detached.panel is panel
    assert panel.parent() is not window
    assert detached.windowTitle().startswith("sys/tg_test/1")
    assert panel.isVisible(), "the panel is shown in its window, not just parented to it"
    assert detached.centralWidget() is panel
    assert window.detached_panels == (FIRST,)
    detached.close()
    await window.idle()


async def test_two_devices_can_be_watched_at_once(
    window: MainWindow, backend: FakeBackend
) -> None:
    first, first_window = await _detach(window, FIRST)
    second, second_window = await _detach(window, SECOND)
    for panel in (first, second):
        panel.set_live(True)
    await window.idle()

    assert first.live.watched and second.live.watched
    backend.set_attribute_value(first.device, "double_scalar", 55.5)
    row = first.values.row_of("double_scalar")
    assert first.values.data(first.values.index(row, 1)) == "55.50"
    assert len(window._windows) == 2
    for detached in (first_window, second_window):
        detached.close()
    await window.idle()


async def test_opening_it_again_finds_the_window_it_is_in(window: MainWindow) -> None:
    panel, detached = await _detach(window, FIRST)
    tabs = window.tabs.count()
    window.open_target(FIRST)
    await window.idle()
    assert window.tabs.count() == tabs
    assert window._panels[FIRST.uri] is panel
    assert len(window._panels) == tabs + 1
    detached.close()
    await window.idle()


async def test_moving_a_panel_back_into_the_tabs(window: MainWindow) -> None:
    panel, detached = await _detach(window, FIRST)
    detached.reattach_action.trigger()
    await window.idle()

    assert window.tabs.indexOf(panel) >= 0
    assert window.tabs.currentWidget() is panel
    assert not panel.isHidden()
    assert window._windows == {}
    assert window._panels[FIRST.uri] is panel
    assert panel.live is not None, "the panel is still alive, not released"


async def test_closing_the_window_releases_the_panel(
    window: MainWindow, backend: FakeBackend
) -> None:
    panel, detached = await _detach(window, FIRST)
    panel.set_live(True)
    await window.idle()
    assert backend.subscription_count > 0

    detached.close()
    await window.idle()
    assert FIRST.uri not in window._panels
    assert window._windows == {}
    assert backend.subscription_count == 0


async def test_closing_the_main_window_closes_the_others(window: MainWindow) -> None:
    _panel, detached = await _detach(window, FIRST)
    assert detached.isVisible()
    window.close()
    await window.idle()
    assert not detached.isVisible()
    assert window._windows == {}


async def test_the_tab_menu_offers_a_window(window: MainWindow) -> None:
    window.open_target(SECOND)
    await window.idle()
    index = window.tabs.indexOf(window._panels[SECOND.uri])
    items = window.tab_items(index)
    assert labels(items) == ["Open in new window", "Close"]
    entry(items, "Open in new window").action()
    await window.idle()
    assert window.detached_panels == (SECOND,)
    assert "1 in windows" in window.status_label.text()
    window._windows[SECOND.uri].close()
    await window.idle()
