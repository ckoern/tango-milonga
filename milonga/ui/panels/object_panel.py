"""Free properties: site configuration that belongs to no device."""

from PyQt6.QtWidgets import QWidget

from milonga.core.commands import PropertyTarget
from milonga.core.errors import ErrorReport
from milonga.ui.context import AppContext, Target
from milonga.ui.panels.base import Panel
from milonga.ui.property_editor import PropertyEditor
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
        self.properties = PropertyEditor(
            context, tokens, PropertyTarget.free(target.name), self.runner, self
        )
        self.properties.failed.connect(self._failed)

        layout = self.base_layout()
        layout.addWidget(self.properties, 1)
        self.header.set_header(self.object_name, "free properties")
        self.header.chip.setVisible(False)
        self.refresh()

    def refresh(self) -> None:
        self.banner.clear()
        self.properties.refresh()

    def _failed(self, report: ErrorReport) -> None:
        self.banner.show_error(report, self.object_name)
