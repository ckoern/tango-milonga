"""Host panel: the servers a Starter controls, grouped by startup level.

Read-only in this milestone; start and stop arrive with the process-control
milestone.
"""

from PyQt6.QtWidgets import QWidget

from milonga.core.model import HostSnapshot
from milonga.ui.context import AppContext, Target
from milonga.ui.models.tables import ObjectTableModel
from milonga.ui.panels.base import InfoForm, Panel
from milonga.ui.panels.columns import server_columns
from milonga.ui.theme import Tokens, host_state_category


class HostPanel(Panel):
    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        target: Target,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(context, tokens, target, parent)
        self.host = target.name
        self.info = InfoForm(tokens, self)
        self.servers = ObjectTableModel(server_columns(), self)

        layout = self.base_layout()
        layout.addWidget(self.info)
        layout.addWidget(self.make_table(self.servers), 1)
        self.header.set_header(self.host, "controlled host")
        self.refresh()

    def refresh(self) -> None:
        self.banner.clear()
        self.runner.run(
            self.context.control.host_snapshot(
                self.host, group=self.context.groups.group_of(self.host)
            ),
            on_result=self._apply,
        )

    def _apply(self, snapshot: HostSnapshot) -> None:
        self.header.chip.set_state(
            snapshot.state.value, host_state_category(snapshot.state)
        )
        self.header.set_header(self.host, snapshot.group or "controlled host")
        if snapshot.error is not None:
            self.banner.show_error(snapshot.error, self.host)
        self.servers.set_rows(snapshot.servers)
        self.info.set_fields(
            [
                ("Starter", str(snapshot.starter) if snapshot.starter else "—"),
                ("Group", snapshot.group or "—"),
                ("Servers", str(len(snapshot.servers))),
                ("Running", str(snapshot.running_count)),
                ("Stopped", str(snapshot.stopped_count)),
                ("Startup levels", ", ".join(str(level) for level in snapshot.levels) or "—"),
            ]
        )
