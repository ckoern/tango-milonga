"""Server panel: registration, served classes and devices."""

from PyQt6.QtWidgets import QTabWidget, QWidget

from milonga.core.enums import StateCategory
from milonga.core.errors import ErrorReport
from milonga.core.model import DeviceSnapshot, ServerInfo
from milonga.core.names import ServerName
from milonga.ui.context import AppContext, Target
from milonga.ui.models.tables import ObjectTableModel
from milonga.ui.panels.base import InfoForm, Panel
from milonga.ui.panels.columns import device_columns
from milonga.ui.theme import Tokens


class ServerPanel(Panel):
    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        target: Target,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(context, tokens, target, parent)
        self.server = ServerName.parse(target.name)
        self.info = InfoForm(tokens, self)
        self.devices = ObjectTableModel(device_columns(), self)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.info, "Info")
        self.tabs.addTab(self.make_table(self.devices), "Devices")

        layout = self.base_layout()
        layout.addWidget(self.tabs, 1)
        self.header.set_header(str(self.server))
        self.refresh()

    def refresh(self) -> None:
        self.banner.clear()
        backend = self.context.backend
        self.runner.run(
            backend.get_server_info(self.server), on_result=self._apply_info, on_error=self._failed
        )
        self.runner.run(
            self.context.inventory.device_snapshot(
                self.server.admin_device, with_state=False
            ),
            on_result=self._apply_run_state,
        )
        self.runner.run(self._load_devices(), on_result=self._apply_devices, on_error=self._failed)
        self.runner.run(
            backend.get_server_class_list(self.server),
            on_result=lambda classes: self.info.set_fields(
                [("Classes", ", ".join(classes) or "—")]
            ),
            on_error=self._failed,
        )

    async def _load_devices(self) -> tuple[DeviceSnapshot, ...]:
        devices = await self.context.backend.get_device_list_for_server(self.server)
        return await self.context.inventory.device_snapshots(devices, with_state=False)

    def _apply_info(self, info: ServerInfo) -> None:
        self.header.set_header(
            str(self.server), f"{info.host or 'unassigned'} · level {info.level}"
        )
        self.info.set_fields(
            [
                ("Host", info.host or "—"),
                ("Startup level", str(info.level) if info.level else "not controlled"),
                ("Controlled", "yes" if info.controlled else "no"),
                ("Admin device", str(self.server.admin_device)),
            ]
        )

    def _apply_run_state(self, snapshot: DeviceSnapshot) -> None:
        """A server runs exactly when its admin device is exported."""
        running = snapshot.info.exported
        self.header.chip.set_state(
            "RUNNING" if running else "STOPPED",
            StateCategory.NOMINAL if running else StateCategory.FAULT,
        )
        self.info.set_fields([("PID", str(snapshot.info.pid or "—"))])

    def _apply_devices(self, snapshots: tuple[DeviceSnapshot, ...]) -> None:
        self.devices.set_rows(snapshots)
        self.tabs.setTabText(1, f"Devices ({len(snapshots)})")

    def _failed(self, report: ErrorReport) -> None:
        self.banner.show_error(report, str(self.server))
