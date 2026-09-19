"""Class panel: the defaults every device of a class inherits, and its instances."""

from PyQt6.QtWidgets import QTabWidget, QWidget

from milonga.core.errors import ErrorReport
from milonga.core.model import DeviceSnapshot, PropertyEntry
from milonga.ui.context import AppContext, Target
from milonga.ui.models.tables import ObjectTableModel
from milonga.ui.panels.base import Panel
from milonga.ui.panels.columns import device_columns, property_columns
from milonga.ui.theme import Tokens


class ClassPanel(Panel):
    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        target: Target,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(context, tokens, target, parent)
        self.class_name = target.name
        self.properties = ObjectTableModel(property_columns(), self)
        self.devices = ObjectTableModel(device_columns(), self)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.make_table(self.properties), "Class properties")
        self.tabs.addTab(self.make_table(self.devices), "Devices")

        layout = self.base_layout()
        layout.addWidget(self.tabs, 1)
        self.header.set_header(self.class_name, "device class")
        self.header.chip.setVisible(False)
        self.refresh()

    def refresh(self) -> None:
        self.banner.clear()
        self.runner.run(
            self.context.backend.get_class_properties(self.class_name),
            on_result=self._apply_properties,
            on_error=self._failed,
        )
        self.runner.run(self._load_devices(), on_result=self._apply_devices, on_error=self._failed)

    async def _load_devices(self) -> tuple[DeviceSnapshot, ...]:
        devices = await self.context.inventory.devices_of_class(self.class_name)
        return await self.context.inventory.device_snapshots(devices, with_state=False)

    def _apply_properties(self, entries: tuple[PropertyEntry, ...]) -> None:
        self.properties.set_rows(entries)
        self.tabs.setTabText(0, f"Class properties ({len(entries)})")

    def _apply_devices(self, snapshots: tuple[DeviceSnapshot, ...]) -> None:
        self.devices.set_rows(snapshots)
        self.tabs.setTabText(1, f"Devices ({len(snapshots)})")

    def _failed(self, report: ErrorReport) -> None:
        self.banner.show_error(report, self.class_name)
