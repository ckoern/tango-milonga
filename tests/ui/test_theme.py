"""Switching theme happens in place."""

from collections.abc import Iterator

import pytest
from PyQt6.QtWidgets import QApplication

from milonga.core.enums import StateCategory
from milonga.core.names import DeviceName
from milonga.ui.app import AppOptions, Shell
from milonga.ui.context import AppContext, Target
from milonga.ui.panels.device import DevicePanel
from milonga.ui.theme import ACTIVE, DARK, LIGHT, Theme, apply_theme
from milonga.ui.widgets import StateChip


@pytest.fixture
def restore_light(app: QApplication) -> Iterator[None]:
    yield
    apply_theme(app, Theme.LIGHT)


async def test_the_window_survives_a_theme_change(
    app: QApplication, context: AppContext, restore_light: None
) -> None:
    shell = Shell(app, context, AppOptions(theme=Theme.LIGHT))
    window = shell.start()
    window.setGeometry(120, 80, 1300, 800)
    window.open_target(Target.device(DeviceName.parse("id09/motor/phi")))
    await window.idle()
    panel = window.current_panel()
    assert isinstance(panel, DevicePanel)
    editor = panel.properties
    editor.model.add_property("Backlash", ("0.1",))
    geometry = window.geometry()

    shell.switch_theme(Theme.DARK)

    assert shell.window is window
    assert window.geometry() == geometry
    assert window.current_panel() is panel
    assert [row.name for row in editor.model.pending] == ["Backlash"]
    assert window.theme is Theme.DARK
    window.close()


def test_the_shared_palette_changes_in_place(app: QApplication, restore_light: None) -> None:
    before = ACTIVE
    apply_theme(app, Theme.DARK)
    assert ACTIVE is before
    assert ACTIVE.panel == DARK.panel
    assert LIGHT.panel != DARK.panel, "the templates are never mutated"
    apply_theme(app, Theme.LIGHT)
    assert ACTIVE.panel == LIGHT.panel


def test_colours_come_from_the_stylesheet(app: QApplication, restore_light: None) -> None:
    chip = StateChip(ACTIVE)
    chip.set_state("ON", StateCategory.NOMINAL)
    assert chip.property("chip") == "ok"
    assert chip.styleSheet() == ""
    apply_theme(app, Theme.DARK)
    assert DARK.ok_soft in app.styleSheet()
    assert chip.property("chip") == "ok"


def test_plots_follow_the_theme(app: QApplication, restore_light: None) -> None:
    from milonga.ui.plots import SpectrumView

    view = SpectrumView(ACTIVE)
    apply_theme(app, Theme.DARK)
    assert view.plot.backgroundBrush().color().name() == DARK.panel



def test_a_closed_plot_does_not_break_the_next_theme_change(
    app: QApplication, restore_light: None
) -> None:
    from milonga.ui.plots import ImageView, SpectrumView

    for view in (ImageView(ACTIVE), SpectrumView(ACTIVE)):
        view.deleteLater()
    QApplication.sendPostedEvents(None, 0)
    from PyQt6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    apply_theme(app, Theme.DARK)
