"""Free properties: site configuration that belongs to no device."""

from PyQt6.QtWidgets import QWidget

from milonga.core.errors import ErrorReport
from milonga.core.model import PropertyEntry
from milonga.ui.context import AppContext, Target
from milonga.ui.models.tables import ObjectTableModel
from milonga.ui.panels.base import Panel
from milonga.ui.panels.columns import property_columns
from milonga.ui.theme import Tokens


class ObjectPanel(Panel):
    def __init__(
        self,
        context: AppContext,
        tokens: Tokens,
        target: Target,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(context, tokens, target, parent)
        self.object_name = target.name
        self.properties = ObjectTableModel(property_columns(), self)

        layout = self.base_layout()
        layout.addWidget(self.make_table(self.properties), 1)
        self.header.set_header(self.object_name, "free properties")
        self.header.chip.setVisible(False)
        self.refresh()

    def refresh(self) -> None:
        self.banner.clear()
        self.runner.run(
            self.context.backend.get_properties(self.object_name),
            on_result=self._apply,
            on_error=self._failed,
        )

    def _apply(self, entries: tuple[PropertyEntry, ...]) -> None:
        self.properties.set_rows(entries)
        self.header.set_header(self.object_name, f"{len(entries)} free properties")

    def _failed(self, report: ErrorReport) -> None:
        self.banner.show_error(report, self.object_name)
