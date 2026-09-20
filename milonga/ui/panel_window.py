"""A panel in a window of its own, for looking at two devices at once."""

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import QMainWindow, QToolBar, QWidget

from milonga.ui.panels.base import Panel

DEFAULT_SIZE = (960, 760)
CASCADE = 40


class PanelWindow(QMainWindow):
    """Holds one panel. Closing it releases the panel; moving it back does not.

    A top level window rather than a child one: a window manager treats it
    like any other window, which a tool window does not get.
    """

    closed = Signal(object)
    reattachRequested = Signal(object)

    def __init__(self, panel: Panel) -> None:
        super().__init__(None)
        self._panel: Panel | None = panel
        self.setWindowTitle(f"{panel.title} — Milonga")
        self.setCentralWidget(panel)
        # leaving a tab hides the panel, and a central widget is not shown for you
        panel.show()
        self.resize(*DEFAULT_SIZE)

        toolbar = QToolBar("Panel", self)
        toolbar.setObjectName("toolbar-panel")
        toolbar.setMovable(False)
        self.reattach_action = QAction("Move back into tabs", self)
        self.reattach_action.triggered.connect(self._reattach)
        toolbar.addAction(self.reattach_action)
        self.addToolBar(toolbar)

    @property
    def panel(self) -> Panel | None:
        return self._panel

    def take_panel(self) -> Panel | None:
        """Hand the panel back without releasing it."""
        panel = self._panel
        self._panel = None
        self.takeCentralWidget()
        return panel

    def place_beside(self, other: QWidget, offset: int = CASCADE) -> None:
        self.move(other.x() + offset, other.y() + offset)

    def _reattach(self) -> None:
        if self._panel is not None:
            self.reattachRequested.emit(self._panel)

    def closeEvent(self, event: QCloseEvent) -> None:
        panel = self._panel
        self._panel = None
        if panel is not None:
            self.closed.emit(panel)
        super().closeEvent(event)
