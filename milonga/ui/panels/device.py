"""Device panel: the generic device GUI, live.

Values arrive through the monitor hub while the panel is on screen and stop
when it is hidden, so a device tab nobody is looking at costs nothing. Scalars
are watched together; a spectrum or an image is watched only while it is the
selected attribute.
"""

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QModelIndex, QPoint, Qt
from PyQt6.QtGui import QHideEvent, QShowEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from milonga.core.commands import PropertyTarget, SetAttributeConfig, SetPolling
from milonga.core.enums import (
    AttrDataFormat,
    DataSource,
    PollableKind,
    StateCategory,
    TangoState,
    TangoType,
)
from milonga.core.errors import ErrorReport
from milonga.core.model import (
    AttributeSpec,
    CommandSpec,
    DeviceSnapshot,
    DeviceVersionInfo,
    PollingEntry,
)
from milonga.core.names import AttributeRef, DeviceName
from milonga.ui.context import AppContext, Target
from milonga.ui.dialogs import AttributeConfigDialog
from milonga.ui.format import format_scalar
from milonga.ui.live import LiveAttributes
from milonga.ui.menus import SEPARATOR, MenuEntry, MenuItems, popup
from milonga.ui.models.tables import ObjectTableModel
from milonga.ui.models.values import AttributeValuesModel
from milonga.ui.panels.base import InfoForm, Panel
from milonga.ui.panels.columns import attribute_columns, command_columns, polling_columns
from milonga.ui.plots import ImageView, SpectrumView
from milonga.ui.property_editor import PropertyEditor
from milonga.ui.theme import Tokens, device_state_category, set_role
from milonga.ui.widgets import CommandBar, WriteBar
from milonga.ui.wizards import PollingDialog
from milonga.ui.write import WriteAction

STATE_ATTRIBUTE = "State"


class DevicePanel(Panel):
    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        target: Target,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(context, tokens, target, parent)
        self.device = DeviceName.parse(target.name)
        self.live = LiveAttributes(context, self)
        self.live.changed.connect(self._value_changed)

        self.values = AttributeValuesModel(self.live, self.device, self)
        self.specs = ObjectTableModel(attribute_columns(), self)
        self.commands = ObjectTableModel(command_columns(), self)
        self.properties = PropertyEditor(
            context, tokens, PropertyTarget.device(self.device), self.runner, self
        )
        self.properties.failed.connect(self._failed)
        self.write = WriteAction(context, tokens, self.runner, self)
        self.write.done.connect(self._config_written)
        self.write.failed.connect(self._failed)
        self.info = InfoForm(tokens, self)

        self._selected_array: AttributeRef | None = None
        self._live_enabled = False
        self._exported = False
        # Plot scenes are expensive; a device panel that never shows an array
        # never builds one.
        self.spectrum: SpectrumView | None = None
        self.image: ImageView | None = None

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._attributes_tab(), "Attributes")
        self.tabs.addTab(self._commands_tab(), "Commands")
        self.tabs.addTab(self.properties, "Properties")
        self.tabs.addTab(self._config_tab(), "Config")
        self.tabs.addTab(self._polling_tab(), "Polling")
        self.tabs.addTab(self.info, "Info")

        layout = self.base_layout()
        layout.addWidget(self.tabs, 1)
        self.header.set_header(str(self.device))
        self.refresh()

    # --------------------------------------------------------------------- layout

    def _attributes_tab(self) -> QWidget:
        page = QWidget(self)
        self.pause = QCheckBox("Pause", page)
        self.pause.toggled.connect(self.live.set_paused)
        self.summary = QLabel(page)
        set_role(self.summary, "role", "muted")

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(self.pause)
        controls.addStretch(1)
        controls.addWidget(self.summary)

        self.value_view = self.make_value_view()
        self.write_bar = WriteBar(self.tokens, read_only=self.context.read_only, parent=page)
        self.write_bar.writeRequested.connect(self._write_attribute)

        self.blank = QLabel("Select a spectrum or image attribute to plot it", page)
        self.blank.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_role(self.blank, "role", "muted")
        self.detail_stack = QStackedWidget(page)
        self.detail_stack.addWidget(self.blank)
        self.detail_stack.setMinimumHeight(240)

        splitter = QSplitter(Qt.Orientation.Vertical, page)
        top = QWidget(splitter)
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)
        top_layout.addLayout(controls)
        top_layout.addWidget(self.value_view, 1)
        top_layout.addWidget(self.write_bar)
        splitter.addWidget(top)
        splitter.addWidget(self.detail_stack)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([460, 320])
        self.splitter = splitter

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(splitter)
        return page

    def make_value_view(self) -> QAbstractItemView:
        view = self.make_table(self.values, chips=[3], stretch=[1])
        _menu_on(
            view,
            lambda point: self._table_menu(view, self.values.spec_at, self.attribute_items, point),
        )
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        selection = view.selectionModel()
        if selection is not None:
            selection.currentRowChanged.connect(self._selection_changed)
        return view

    def _config_tab(self) -> QWidget:
        page = QWidget(self)
        self.config_view = self.make_table(self.specs)
        _menu_on(
            self.config_view,
            lambda point: self._table_menu(
                self.config_view, self.specs.row_at, self.attribute_items, point
            ),
        )
        self.config_button = QPushButton("Edit…", page)
        self.config_button.setEnabled(not self.context.read_only)
        self.config_button.clicked.connect(self._edit_config)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addWidget(self.config_button)
        toolbar.addStretch(1)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addWidget(self.config_view, 1)
        return page

    def _edit_config(self, spec: AttributeSpec | None = None) -> None:
        if spec is None:
            selection = self.config_view.selectionModel()
            if selection is None:
                return
            spec = self.specs.row_at(selection.currentIndex())
        if spec is None or self.context.read_only:
            return
        dialog = AttributeConfigDialog(spec, self.tokens, self)
        if not dialog.exec():
            return
        self.write.execute([SetAttributeConfig(self.device, dialog.edited())])

    def _config_written(self) -> None:
        self.runner.run(
            self.context.backend.get_attribute_specs(self.device),
            on_result=self._apply_attributes,
            on_error=self._failed,
        )
        self._load_polling()

    def _polling_tab(self) -> QWidget:
        page = QWidget(self)
        self.polling = ObjectTableModel(polling_columns(), self)
        self.polling_view = self.make_table(self.polling)
        _menu_on(
            self.polling_view,
            lambda point: self._table_menu(
                self.polling_view, self.polling.row_at, self.polling_items, point
            ),
        )
        writable = not self.context.read_only
        self.poll_button = QPushButton("Poll…", page)
        self.poll_button.setEnabled(writable)
        self.poll_button.clicked.connect(self._edit_polling)
        self.stop_poll_button = QPushButton("Stop polling", page)
        self.stop_poll_button.setEnabled(writable)
        self.stop_poll_button.clicked.connect(self._stop_polling)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.addWidget(self.poll_button)
        toolbar.addWidget(self.stop_poll_button)
        toolbar.addStretch(1)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addLayout(toolbar)
        layout.addWidget(self.polling_view, 1)
        return page

    def _pollable(self) -> list[tuple[str, PollableKind]]:
        items = [(spec.name, PollableKind.ATTRIBUTE) for spec in self.values.specs]
        for row in range(self.commands.rowCount()):
            command = self.commands.row_at(self.commands.index(row, 0))
            if command is not None:
                items.append((command.name, PollableKind.COMMAND))
        return items

    def _selected_polling(self) -> PollingEntry | None:
        selection = self.polling_view.selectionModel()
        if selection is None:
            return None
        return self.polling.row_at(selection.currentIndex())

    def _edit_polling(self, name: str = "") -> None:
        if self.context.read_only:
            return
        current = self._selected_polling()
        if name:
            current = next((entry for entry in self.polling.rows if entry.name == name), None)
        dialog = PollingDialog(
            self._pollable(),
            self.tokens,
            selected=name or (current.name if current else ""),
            period_ms=current.period_ms if current else 1000,
            parent=self,
        )
        if not dialog.exec():
            return
        name, kind = dialog.chosen()
        self.write.execute([SetPolling(self.device, name, kind, dialog.period_ms())])

    def _stop_polling(self, current: PollingEntry | None = None) -> None:
        current = current or self._selected_polling()
        if current is None or self.context.read_only:
            return
        self.write.execute([SetPolling(self.device, current.name, current.kind, 0)])

    def _load_polling(self) -> None:
        self.runner.run(
            self.context.backend.get_polling(self.device),
            on_result=self.polling.set_rows,
            on_error=self._failed,
        )

    # ----------------------------------------------------------------- right click

    def attribute_items(self, spec: AttributeSpec) -> MenuItems:
        writable = not self.context.read_only
        items: list[MenuEntry | None] = []
        if spec.data_format is AttrDataFormat.SCALAR and spec.writable.writable:
            items.append(MenuEntry("Write…", self._focus_write, writable))
        items += [
            MenuEntry("Configure…", lambda: self._edit_config(spec), writable),
            MenuEntry("Poll…", lambda: self._edit_polling(spec.name), writable),
            SEPARATOR,
            MenuEntry("Copy name", lambda: _copy(f"{self.device}/{spec.name}")),
        ]
        return items

    def command_items(self, spec: CommandSpec) -> MenuItems:
        writable = not self.context.read_only
        takes_argument = spec.in_type is not TangoType.VOID
        return [
            MenuEntry(
                "Execute…" if takes_argument else "Execute",
                lambda: self._execute_from_menu(spec),
                writable,
            ),
            MenuEntry("Poll…", lambda: self._edit_polling(spec.name), writable),
            SEPARATOR,
            MenuEntry("Copy name", lambda: _copy(spec.name)),
        ]

    def polling_items(self, entry: PollingEntry) -> MenuItems:
        writable = not self.context.read_only
        return [
            MenuEntry("Change period…", lambda: self._edit_polling(entry.name), writable),
            MenuEntry("Stop polling", lambda: self._stop_polling(entry), writable),
        ]

    def _focus_write(self) -> None:
        self.write_bar.editor.setFocus()
        self.write_bar.editor.selectAll()

    def _execute_from_menu(self, spec: CommandSpec) -> None:
        """A command without an argument runs; one with an argument asks for it."""
        self._select_command(spec)
        if spec.in_type is TangoType.VOID:
            self._execute_command(spec.name, None)
        else:
            self.command_bar.editor.setFocus()

    def _select_command(self, spec: CommandSpec) -> None:
        for row in range(self.commands.rowCount()):
            index = self.commands.index(row, 0)
            if self.commands.row_at(index) == spec:
                self.command_view.setCurrentIndex(index)
                return

    def _table_menu[T](
        self,
        view: QAbstractItemView,
        row_at: Callable[[QModelIndex], T | None],
        items: Callable[[T], MenuItems],
        point: QPoint,
    ) -> None:
        index = view.indexAt(point)
        row = row_at(index)
        if row is None:
            return
        view.setCurrentIndex(index)
        popup(view, point, items(row))

    def _commands_tab(self) -> QWidget:
        page = QWidget(self)
        self.command_view = self.make_table(self.commands)
        _menu_on(
            self.command_view,
            lambda point: self._table_menu(
                self.command_view, self.commands.row_at, self.command_items, point
            ),
        )
        selection = self.command_view.selectionModel()
        if selection is not None:
            selection.currentRowChanged.connect(self._command_selected)
        self.command_bar = CommandBar(
            self.tokens, read_only=self.context.read_only, parent=page
        )
        self.command_bar.executeRequested.connect(self._execute_command)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.command_view, 1)
        layout.addWidget(self.command_bar)
        return page

    # ------------------------------------------------------------------ lifecycle

    def showEvent(self, a0: QShowEvent | None) -> None:
        super().showEvent(a0)
        self.set_live(True)

    def hideEvent(self, a0: QHideEvent | None) -> None:
        super().hideEvent(a0)
        self.set_live(False)

    def set_live(self, enabled: bool) -> None:
        """Subscriptions follow the panel's visibility."""
        self._live_enabled = enabled
        if enabled:
            self._apply_watches()
        else:
            self.runner.run(self.live.release(), label="release watches")

    async def aclose(self) -> None:
        await self.live.release()

    def refresh(self) -> None:
        self.banner.clear()
        self.runner.run(
            self.context.inventory.device_snapshot(self.device),
            on_result=self._apply_snapshot,
            on_error=self._failed,
        )
        self.properties.refresh()

    def _apply_watches(self) -> None:
        if not self._live_enabled or not self._exported:
            return
        refs = [
            AttributeRef(self.device, spec.name)
            for spec in self.values.specs
            if spec.data_format is AttrDataFormat.SCALAR
        ]
        if self._selected_array is not None:
            refs.append(self._selected_array)
        self.runner.run(self.live.watch(refs), on_result=lambda _: self._update_summary())

    # -------------------------------------------------------------------- loading

    def _apply_snapshot(self, snapshot: DeviceSnapshot) -> None:
        info = snapshot.info
        self._exported = info.exported
        if snapshot.error is not None:
            self.banner.show_error(snapshot.error, "device")
        self._set_state_chip(
            snapshot.state.state if info.exported else None,
            "NOT EXPORTED" if not info.exported else None,
        )
        self.header.set_header(str(self.device), f"{info.class_name} · {info.server}")
        self.info.set_fields(
            [
                ("Class", info.class_name),
                ("Server", str(info.server)),
                ("Host", info.host or "—"),
                ("Exported", "yes" if info.exported else "no"),
                ("PID", str(info.pid or "—")),
                ("Alias", info.alias or "—"),
                ("Status", snapshot.state.status or "—"),
                ("Exported at", _timestamp(info.exported_at)),
                ("Unexported at", _timestamp(info.unexported_at)),
                ("IOR", info.ior or "—"),
            ]
        )
        if info.exported:
            self._load_device_interface()
            self._load_polling()
        else:
            self.values.set_specs([])
            self.specs.set_rows([])
            self.commands.set_rows([])
            self.polling.set_rows([])
            self._update_summary()

    def _load_device_interface(self) -> None:
        backend = self.context.backend
        self.runner.run(
            backend.get_attribute_specs(self.device),
            on_result=self._apply_attributes,
            on_error=self._failed,
        )
        self.runner.run(
            backend.get_command_specs(self.device),
            on_result=self._apply_commands,
            on_error=self._failed,
        )
        self.runner.run(
            backend.get_device_version(self.device),
            on_result=self._apply_version,
            on_error=self._failed,
        )

    def _apply_attributes(self, specs: tuple[AttributeSpec, ...]) -> None:
        self.values.set_specs(specs)
        self.specs.set_rows(sorted(specs, key=lambda spec: spec.name.lower()))
        self.tabs.setTabText(0, f"Attributes ({len(specs)})")
        self._apply_watches()

    def _apply_commands(self, specs: tuple[CommandSpec, ...]) -> None:
        self.commands.set_rows(specs)
        self.tabs.setTabText(1, f"Commands ({len(specs)})")

    def _apply_version(self, version: DeviceVersionInfo) -> None:
        self.info.set_fields(
            [
                ("IDL", str(version.idl_version)),
                ("Server version", version.server_version or "—"),
                ("Tango release", version.tango_release or "—"),
            ]
        )

    # ------------------------------------------------------------------ reactions

    def _selection_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        spec = self.values.spec_at(current)
        if spec is None:
            self.write_bar.set_target(None)
            return
        ref = AttributeRef(self.device, spec.name)
        if spec.data_format is AttrDataFormat.SCALAR:
            self._selected_array = None
            self.detail_stack.setCurrentWidget(self.blank)
            self.write_bar.set_target(spec, self._write_default(ref, spec))
        else:
            self._selected_array = ref
            self.write_bar.set_target(None)
            view = self._plot_for(spec)
            view.set_attribute(spec)
            self.detail_stack.setCurrentWidget(view)
        self._apply_watches()
        self._show_detail(ref)

    def _plot_for(self, spec: AttributeSpec) -> SpectrumView | ImageView:
        if spec.data_format is AttrDataFormat.SPECTRUM:
            if self.spectrum is None:
                self.spectrum = SpectrumView(self.tokens, self)
                self.detail_stack.addWidget(self.spectrum)
            return self.spectrum
        if self.image is None:
            self.image = ImageView(self.tokens, self)
            self.detail_stack.addWidget(self.image)
        return self.image

    def _write_default(self, ref: AttributeRef, spec: AttributeSpec) -> str:
        value = self.live.latest(ref)
        if value is None:
            return ""
        current = value.write_value if value.write_value is not None else value.value
        return format_scalar(current, spec)

    def _value_changed(self, ref: AttributeRef) -> None:
        if ref.attribute == STATE_ATTRIBUTE:
            value = self.live.latest(ref)
            state = value.value if value is not None else None
            self._set_state_chip(state if isinstance(state, TangoState) else None)
        if ref == self._selected_array:
            self._show_detail(ref)
        self._update_summary()

    def _show_detail(self, ref: AttributeRef) -> None:
        if ref != self._selected_array:
            return
        value = self.live.latest(ref)
        if value is None or value.value is None:
            return
        widget = self.detail_stack.currentWidget()
        if isinstance(widget, (SpectrumView, ImageView)):
            widget.set_value(value)

    def _set_state_chip(self, state: TangoState | None, override: str | None = None) -> None:
        if override is not None:
            self.header.chip.set_state(override, StateCategory.INACTIVE)
            return
        if state is None:
            self.header.chip.set_state("UNKNOWN", StateCategory.UNKNOWN)
            return
        self.header.chip.set_state(state.value, device_state_category(state))

    def _update_summary(self) -> None:
        watched = self.live.watched
        polled = sum(
            1 for ref in watched if self.live.source(ref) is DataSource.POLLING
        )
        parts = [f"{len(self.values.specs)} attributes"]
        if watched:
            parts.append(f"{len(watched) - polled} by events")
            if polled:
                parts.append(f"{polled} polled")
        else:
            parts.append("not watching")
        if self.live.paused:
            parts.append("paused")
        self.summary.setText("  ·  ".join(parts))

    # --------------------------------------------------------------------- writes

    def _write_attribute(self, name: str, value: object) -> None:
        self.runner.run(
            self.context.backend.write_attribute(self.device, name, value),
            on_result=lambda _: self._written(name, value),
            on_error=self._write_failed,
        )

    def _written(self, name: str, value: object) -> None:
        self.write_bar.show_message("written", error=False)
        self.context.journal.write(f"write_attribute {self.device}/{name} = {value}")

    def _write_failed(self, report: ErrorReport) -> None:
        self.write_bar.show_message(report.message)
        self.context.journal.report(report, str(self.device))

    def _command_selected(self, current: QModelIndex, _previous: QModelIndex) -> None:
        self.command_bar.set_target(self.commands.row_at(current))

    def _execute_command(self, name: str, argin: object) -> None:
        self.runner.run(
            self.context.backend.execute_command(self.device, name, argin),
            on_result=lambda result: self._command_done(name, argin, result),
            on_error=self._command_failed,
        )

    def _command_done(self, name: str, argin: object, result: Any) -> None:
        text = _format_result(result)
        self.command_bar.show_result(text)
        detail = f"({argin})" if argin is not None else "()"
        self.context.journal.write(f"command {self.device}/{name}{detail} → {text}")

    def _command_failed(self, report: ErrorReport) -> None:
        self.command_bar.show_result(report.message, error=True)
        self.context.journal.report(report, str(self.device))

    def _failed(self, report: ErrorReport) -> None:
        self.banner.show_error(report, str(self.device))
        self.context.journal.report(report, str(self.device))


def _menu_on(view: QAbstractItemView, handler: Callable[[QPoint], None]) -> None:
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    view.customContextMenuRequested.connect(handler)


def _copy(text: str) -> None:
    clipboard = QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)


def _format_result(result: Any) -> str:
    if result is None:
        return "done"
    if isinstance(result, (list, tuple)):
        items = ", ".join(str(item) for item in result[:6])
        return f"[{items}{', …' if len(result) > 6 else ''}]"
    return str(result)


def _timestamp(value: object) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else "—"
