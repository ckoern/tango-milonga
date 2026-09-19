import asyncio

import numpy as np
import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.enums import AttrQuality, DataSource, StateCategory, TangoState
from milonga.core.names import AttributeRef, DeviceName
from milonga.ui.context import AppContext, Target
from milonga.ui.models.tree import CATEGORY_ROLE
from milonga.ui.panels.device import DevicePanel
from milonga.ui.plots import ImageView, SpectrumView
from milonga.ui.theme import Tokens

DEVICE = DeviceName.parse("sys/tg_test/1")
DOUBLE = AttributeRef(DEVICE, "double_scalar")
SPECTRUM = AttributeRef(DEVICE, "double_spectrum")
IMAGE = AttributeRef(DEVICE, "double_image")


@pytest.fixture
async def panel(context: AppContext, tokens: Tokens) -> DevicePanel:
    widget = DevicePanel(context, tokens, Target.device(DEVICE))
    await widget.idle()
    return widget


async def _live(widget: DevicePanel) -> None:
    widget.set_live(True)
    await widget.idle()


def _cell(widget: DevicePanel, name: str, column: int) -> str:
    row = widget.values.row_of(name)
    assert row >= 0, f"no row for {name}"
    return str(widget.values.data(widget.values.index(row, column)))


async def test_values_appear_when_the_panel_goes_live(panel: DevicePanel) -> None:
    assert panel.live.watched == frozenset()
    await _live(panel)
    assert _cell(panel, "double_scalar", 1) == "12.87"
    assert _cell(panel, "double_scalar", 2) == "mm"
    assert _cell(panel, "double_scalar", 3) == "VALID"


async def test_only_scalars_are_watched_by_default(panel: DevicePanel) -> None:
    await _live(panel)
    watched = {ref.attribute for ref in panel.live.watched}
    assert "double_scalar" in watched
    assert "double_spectrum" not in watched
    assert "double_image" not in watched
    assert len(watched) == 7


async def test_events_update_the_table(panel: DevicePanel, backend: FakeBackend) -> None:
    await _live(panel)
    changed: list[object] = []
    panel.values.dataChanged.connect(lambda *args: changed.append(args))
    backend.set_attribute_value(DEVICE, "double_scalar", 42.5)
    assert _cell(panel, "double_scalar", 1) == "42.50"
    assert changed


async def test_hiding_the_panel_releases_every_subscription(
    panel: DevicePanel, backend: FakeBackend
) -> None:
    await _live(panel)
    assert backend.subscription_count > 0
    panel.set_live(False)
    await panel.idle()
    assert backend.subscription_count == 0
    assert panel.live.watched == frozenset()


async def test_selecting_a_spectrum_watches_and_plots_it(panel: DevicePanel) -> None:
    await _live(panel)
    _select(panel, "double_spectrum")
    await panel.idle()
    assert SPECTRUM in panel.live.watched
    assert isinstance(panel.detail_stack.currentWidget(), SpectrumView)
    assert panel.spectrum is not None
    assert "1024 points" in panel.spectrum.detail.text()


async def test_selecting_an_image_shows_the_image_view(panel: DevicePanel) -> None:
    await _live(panel)
    _select(panel, "double_image")
    await panel.idle()
    assert IMAGE in panel.live.watched
    assert isinstance(panel.detail_stack.currentWidget(), ImageView)
    assert panel.image is not None
    assert "256 × 256" in panel.image.detail.text()


async def test_moving_off_an_array_drops_its_subscription(panel: DevicePanel) -> None:
    await _live(panel)
    _select(panel, "double_spectrum")
    await panel.idle()
    _select(panel, "double_scalar")
    await panel.idle()
    assert SPECTRUM not in panel.live.watched
    assert DOUBLE in panel.live.watched


async def test_a_large_spectrum_is_decimated_and_says_so(
    panel: DevicePanel, backend: FakeBackend
) -> None:
    backend.set_attribute_value(DEVICE, "double_spectrum", np.arange(20_000.0))
    await _live(panel)
    _select(panel, "double_spectrum")
    await panel.idle()
    assert panel.spectrum is not None
    assert "shown 1:5" in panel.spectrum.detail.text()


async def test_pause_freezes_the_display(panel: DevicePanel, backend: FakeBackend) -> None:
    await _live(panel)
    panel.pause.setChecked(True)
    backend.set_attribute_value(DEVICE, "double_scalar", 99.0)
    assert _cell(panel, "double_scalar", 1) == "12.87"
    panel.pause.setChecked(False)
    assert _cell(panel, "double_scalar", 1) == "99.00"


async def test_the_header_follows_the_live_state(
    panel: DevicePanel, backend: FakeBackend
) -> None:
    await _live(panel)
    assert panel.header.chip.text() == "ON"
    backend.devices[DEVICE].state = TangoState.ALARM
    backend.set_attribute_value(DEVICE, "State", TangoState.ALARM)
    assert panel.header.chip.text() == "ALARM"


async def test_a_quality_change_is_shown(panel: DevicePanel, backend: FakeBackend) -> None:
    await _live(panel)
    backend.set_attribute_value(DEVICE, "double_scalar", 1.0, quality=AttrQuality.ALARM)
    row = panel.values.row_of("double_scalar")
    index = panel.values.index(row, 3)
    assert panel.values.data(index) == "ALARM"
    assert panel.values.data(index, CATEGORY_ROLE) is StateCategory.FAULT


async def test_an_unexported_device_reports_the_error_in_the_row(
    panel: DevicePanel, backend: FakeBackend
) -> None:
    await _live(panel)
    backend.stop_server("TangoTest/test")
    row = panel.values.row_of("double_scalar")
    assert panel.values.data(panel.values.index(row, 3)) == "ERROR"
    assert "stopped" in _cell(panel, "double_scalar", 1)


async def test_polling_fallback_is_named_in_the_source_column(
    context: AppContext, tokens: Tokens, backend: FakeBackend
) -> None:
    backend.event_blocked.add(DOUBLE)
    widget = DevicePanel(context, tokens, Target.device(DEVICE))
    await widget.idle()
    await _live(widget)
    await asyncio.sleep(0.05)
    assert widget.live.source(DOUBLE) is DataSource.POLLING
    assert _cell(widget, "double_scalar", 5) == "polling"
    assert "1 polled" in widget.summary.text()
    await widget.aclose()


async def test_writing_an_attribute_sends_it_and_journals_it(
    panel: DevicePanel, context: AppContext, backend: FakeBackend
) -> None:
    await _live(panel)
    _select(panel, "double_scalar")
    panel.write_bar.editor.setText("7.5")
    panel.write_bar.button.click()
    await panel.idle()
    assert backend.devices[DEVICE].attributes["double_scalar"].value == 7.5
    assert panel.write_bar.message.text() == "written"
    assert "write_attribute" in context.journal.entries[-1].summary


async def test_the_write_field_starts_from_the_current_value(panel: DevicePanel) -> None:
    await _live(panel)
    _select(panel, "double_scalar")
    assert panel.write_bar.editor.text() == "12.87"


async def test_a_read_only_attribute_cannot_be_written(panel: DevicePanel) -> None:
    await _live(panel)
    _select(panel, "throughput")
    assert not panel.write_bar.editor.isEnabled()
    assert "read-only" in panel.write_bar.editor.placeholderText()


async def test_bad_input_never_reaches_the_control_system(
    panel: DevicePanel, backend: FakeBackend
) -> None:
    await _live(panel)
    _select(panel, "double_scalar")
    calls = len(backend.call_log)
    panel.write_bar.editor.setText("not a number")
    panel.write_bar.button.click()
    await panel.idle()
    assert "not a number" in panel.write_bar.message.text()
    assert len(backend.call_log) == calls


async def test_a_read_only_session_disables_writing(
    read_only_context: AppContext, tokens: Tokens
) -> None:
    widget = DevicePanel(read_only_context, tokens, Target.device(DEVICE))
    await widget.idle()
    await _live(widget)
    _select(widget, "double_scalar")
    assert not widget.write_bar.button.isEnabled()
    assert not widget.command_bar.button.isEnabled()
    await widget.aclose()


async def test_commands_run_and_report_their_result(
    panel: DevicePanel, context: AppContext
) -> None:
    _select_command(panel, "DevString")
    panel.command_bar.editor.setText("hello")
    panel.command_bar.button.click()
    await panel.idle()
    assert panel.command_bar.result.text() == "HELLO"
    assert "command" in context.journal.entries[-1].summary


async def test_a_command_without_an_argument_takes_none(panel: DevicePanel) -> None:
    _select_command(panel, "DevVoid")
    assert not panel.command_bar.editor.isEnabled()
    panel.command_bar.button.click()
    await panel.idle()
    assert panel.command_bar.result.text() == "done"


async def test_a_failing_command_shows_why(panel: DevicePanel, backend: FakeBackend) -> None:
    backend.stop_server("TangoTest/test")
    _select_command(panel, "DevVoid")
    panel.command_bar.button.click()
    await panel.idle()
    assert "not exported" in panel.command_bar.result.text()


def _select(widget: DevicePanel, attribute: str) -> None:
    row = widget.values.row_of(attribute)
    assert row >= 0, f"no row for {attribute}"
    widget.value_view.setCurrentIndex(widget.values.index(row, 0))


def _select_command(widget: DevicePanel, command: str) -> None:
    for row in range(widget.commands.rowCount()):
        index = widget.commands.index(row, 0)
        if widget.commands.data(index) == command:
            widget.command_view.setCurrentIndex(index)
            return
    raise AssertionError(f"no command {command}")
