"""Device panel: what Jive's device node and its test panel show, read-only."""

from PyQt6.QtWidgets import QTabWidget, QWidget

from milonga.core.enums import StateCategory
from milonga.core.errors import ErrorReport
from milonga.core.model import AttributeSpec, CommandSpec, DeviceSnapshot, DeviceVersionInfo
from milonga.core.names import DeviceName
from milonga.ui.context import AppContext, Target
from milonga.ui.models.tables import ObjectTableModel
from milonga.ui.panels.base import InfoForm, Panel
from milonga.ui.panels.columns import attribute_columns, command_columns, property_columns
from milonga.ui.theme import Tokens, device_state_category


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
        self.info = InfoForm(tokens, self)
        self.properties = ObjectTableModel(property_columns(), self)
        self.attributes = ObjectTableModel(attribute_columns(), self)
        self.commands = ObjectTableModel(command_columns(), self)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.info, "Info")
        self.tabs.addTab(self.make_table(self.properties), "Properties")
        self.tabs.addTab(self.make_table(self.attributes), "Attributes")
        self.tabs.addTab(self.make_table(self.commands), "Commands")

        layout = self.base_layout()
        layout.addWidget(self.tabs, 1)
        self.header.set_header(str(self.device))
        self.refresh()

    def refresh(self) -> None:
        self.banner.clear()
        self.runner.run(
            self.context.inventory.device_snapshot(self.device),
            on_result=self._apply_snapshot,
            on_error=self._failed,
        )
        self.runner.run(
            self.context.backend.get_device_properties(self.device),
            on_result=self.properties.set_rows,
            on_error=self._failed,
        )

    def _apply_snapshot(self, snapshot: DeviceSnapshot) -> None:
        info = snapshot.info
        if snapshot.error is not None:
            self.banner.show_error(snapshot.error, "device")
        category = (
            device_state_category(snapshot.state.state)
            if info.exported
            else StateCategory.INACTIVE
        )
        self.header.chip.set_state(
            snapshot.state.state.value if info.exported else "NOT EXPORTED", category
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
        self.attributes.set_rows(specs)
        self.tabs.setTabText(2, f"Attributes ({len(specs)})")

    def _apply_commands(self, specs: tuple[CommandSpec, ...]) -> None:
        self.commands.set_rows(specs)
        self.tabs.setTabText(3, f"Commands ({len(specs)})")

    def _apply_version(self, version: DeviceVersionInfo) -> None:
        self.info.set_fields(
            [
                ("IDL", str(version.idl_version)),
                ("Server version", version.server_version or "—"),
                ("Tango release", version.tango_release or "—"),
            ]
        )

    def _failed(self, report: ErrorReport) -> None:
        self.banner.show_error(report, str(self.device))
        self.context.journal.report(report, str(self.device))


def _timestamp(value: object) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if hasattr(value, "strftime") else "—"
