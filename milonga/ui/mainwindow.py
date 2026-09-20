"""The single window: navigator, document tabs, inspector, journal, status bar."""

from collections.abc import Sequence

from PyQt6.QtCore import QModelIndex, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QDockWidget,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from milonga.core.enums import StateCategory
from milonga.ui.context import AppContext, JournalEntry, JournalKind, Target, TargetKind
from milonga.ui.menus import MenuEntry, MenuItems, popup
from milonga.ui.models.tables import Column, ObjectTableModel
from milonga.ui.models.tree import TreeNode
from milonga.ui.navigator import Navigator, Scope, target_of
from milonga.ui.panels import Panel, create_panel
from milonga.ui.panels.base import InfoForm
from milonga.ui.search import SearchDialog
from milonga.ui.tasks import TaskRunner
from milonga.ui.theme import Theme, Tokens


def journal_columns() -> list[Column[JournalEntry]]:
    return [
        Column("Time", lambda entry: entry.at.strftime("%H:%M:%S"), mono=True),
        Column(
            "Kind",
            lambda entry: entry.kind.value,
            category=lambda entry: (
                StateCategory.FAULT if entry.kind is JournalKind.ERROR else StateCategory.NOMINAL
            ),
        ),
        Column(
            "Operation",
            lambda entry: entry.summary,
            tooltip=lambda entry: entry.detail,
            mono=True,
            stretch=3,
        ),
        Column("", lambda entry: "undo" if entry.undoable else ""),
    ]


UNDO_COLUMN = 3


NOTHING_TO_OPEN = "Select something to open"

_PANEL_NAMES: dict[TargetKind, str] = {
    TargetKind.SYSTEM: "system overview",
    TargetKind.DEVICE: "device panel",
    TargetKind.SERVER: "server panel",
    TargetKind.CLASS: "class panel",
    TargetKind.OBJECT: "free properties",
    TargetKind.HOST: "host panel",
}


def open_label(target: Target) -> str:
    """Says what opens, so the button explains itself."""
    return f"Open {_PANEL_NAMES[target.kind]}"


class Inspector(QWidget):
    """Context for the navigator selection, with the action that opens it."""

    openRequested = pyqtSignal(object)

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.form = InfoForm(tokens, self)
        self.open_button = QPushButton(NOTHING_TO_OPEN, self)
        self.open_button.setEnabled(False)
        self.open_button.setToolTip("Opens the selection in a tab — the same as double-clicking it")
        self.open_button.clicked.connect(self._open)
        self._target: Target | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self.form)
        layout.addStretch(1)
        layout.addWidget(self.open_button)

    def show_node(self, node: TreeNode | None) -> None:
        if node is None:
            self.form.set_fields([("Selection", "—"), ("Kind", "—"), ("Detail", "—")])
            self._target = None
            self.open_button.setEnabled(False)
            self.open_button.setText(NOTHING_TO_OPEN)
            return
        self._target = target_of(node)
        self.form.set_fields(
            [
                ("Selection", node.label),
                ("Kind", node.kind.value),
                ("Detail", node.detail or "—"),
                ("State", node.category.value if node.category else "—"),
            ]
        )
        self.open_button.setEnabled(self._target is not None)
        self.open_button.setText(open_label(self._target) if self._target else NOTHING_TO_OPEN)

    def _open(self) -> None:
        if self._target is not None:
            self.openRequested.emit(self._target)


def _floated(dock: QDockWidget, floating: bool) -> None:
    """A floating dock is a plain window, not a tool window.

    Qt floats docks as tool windows, which some window managers — WSLg's among
    them — leave undecorated, always on top and unfocusable, so they cannot be
    moved or docked again. A normal window is one the window manager handles.
    """
    if not floating:
        return
    dock.setWindowFlags(
        Qt.WindowType.Window
        | Qt.WindowType.WindowTitleHint
        | Qt.WindowType.WindowMinMaxButtonsHint
        | Qt.WindowType.WindowCloseButtonHint
    )
    dock.show()


def _journal_text(entry: JournalEntry) -> str:
    stamp = entry.at.strftime("%Y-%m-%d %H:%M:%S")
    return f"{stamp}  {entry.kind.value}  {entry.summary}" + (
        f"\n{entry.detail}" if entry.detail else ""
    )


def _copy(text: str) -> None:
    clipboard = QApplication.clipboard()
    if clipboard is not None:
        clipboard.setText(text)


class MainWindow(QMainWindow):
    themeToggled = pyqtSignal(object)

    def __init__(self, context: AppContext, tokens: Tokens, theme: Theme) -> None:
        super().__init__()
        self.context = context
        self.tokens = tokens
        self.theme = theme
        self.setWindowTitle(f"Milonga — {context.tango_host}")
        self.resize(1440, 900)
        context.open_target = self.open_target

        self.tabs = QTabWidget(self)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self.tabs)
        self._panels: dict[str, Panel] = {}
        self._docks: dict[str, tuple[QDockWidget, Qt.DockWidgetArea]] = {}
        self._runner = TaskRunner(self, context.journal, context="window")

        self.navigator = Navigator(context, tokens, self)
        self.navigator.targetActivated.connect(self.open_target)
        self.navigator.nodeSelected.connect(self._node_selected)
        self._dock("Navigator", self.navigator, Qt.DockWidgetArea.LeftDockWidgetArea, 300)

        self.inspector = Inspector(tokens, self)
        self.inspector.openRequested.connect(self.open_target)
        self._dock("Inspector", self.inspector, Qt.DockWidgetArea.RightDockWidgetArea, 280)

        self.journal_model = ObjectTableModel(journal_columns(), self)
        self._dock("Journal", self._journal_dock(), Qt.DockWidgetArea.BottomDockWidgetArea, 160)
        context.journal.entryAdded.connect(self.journal_model.append_row)
        self.journal_view.clicked.connect(self._journal_clicked)

        self._build_toolbar()
        self._build_status_bar()
        self.navigator.refresh()
        self.open_target(Target.system())
        context.journal.info(f"Connected to {context.tango_host}")

    # --------------------------------------------------------------------- panels

    def open_target(self, target: Target) -> None:
        panel = self._panels.get(target.uri)
        if panel is None:
            panel = create_panel(self.context, self.tokens, target)
            self._panels[target.uri] = panel
            self.tabs.addTab(panel, panel.title)
        self.tabs.setCurrentWidget(panel)
        self._update_status()

    def open_targets(self, targets: Sequence[Target]) -> None:
        for target in targets:
            self.open_target(target)

    @property
    def open_panels(self) -> tuple[Target, ...]:
        return tuple(panel.target for panel in self._panels.values())

    def current_panel(self) -> Panel | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, Panel) else None

    def refresh_current(self) -> None:
        panel = self.current_panel()
        if panel is not None:
            panel.refresh()
        else:
            self.navigator.refresh()

    async def idle(self) -> None:
        await self.navigator.idle()
        await self._runner.idle()
        for panel in list(self._panels.values()):
            await panel.idle()

    def _close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if not isinstance(widget, Panel):
            return
        self._panels.pop(widget.target.uri, None)
        self.tabs.removeTab(index)
        widget.runner.cancel_all()
        # The panel's own runner is gone, so its subscriptions are released on
        # the window's runner instead of being abandoned.
        self._runner.run(widget.aclose(), on_result=lambda _: widget.deleteLater())
        self._update_status()

    # ------------------------------------------------------------------- chrome

    def _dock(self, title: str, widget: QWidget, area: Qt.DockWidgetArea, size: int) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(f"dock-{title.lower()}")
        dock.setWidget(widget)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        dock.topLevelChanged.connect(lambda floating, d=dock: _floated(d, floating))
        self.addDockWidget(area, dock)
        if area in (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea):
            self.resizeDocks([dock], [size], Qt.Orientation.Horizontal)
        else:
            self.resizeDocks([dock], [size], Qt.Orientation.Vertical)
        self._docks[title] = (dock, area)
        return dock

    def reset_layout(self) -> None:
        """Bring every dock back where it started, in case one is lost or stuck."""
        for dock, area in self._docks.values():
            dock.setFloating(False)
            self.addDockWidget(area, dock)
            dock.show()

    def _journal_dock(self) -> QWidget:
        holder = Panel(self.context, self.tokens, Target.free_object("journal"), self)
        holder.header.setVisible(False)
        holder.banner.setVisible(False)
        self.journal_view = holder.make_table(self.journal_model)
        self.journal_view.setToolTip("Click “undo” to reverse a write")
        self.journal_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.journal_view.customContextMenuRequested.connect(self._journal_menu)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.journal_view)
        return holder

    def journal_items(self, entry: JournalEntry) -> MenuItems:
        items: list[MenuEntry | None] = []
        if entry.undoable:
            items.append(MenuEntry("Undo", lambda: self._undo(entry), not self.context.read_only))
        items.append(MenuEntry("Copy", lambda: _copy(_journal_text(entry))))
        return items

    def _journal_menu(self, point: QPoint) -> None:
        entry = self.journal_model.row_at(self.journal_view.indexAt(point))
        if entry is not None:
            popup(self.journal_view, point, self.journal_items(entry))

    def _journal_clicked(self, index: QModelIndex) -> None:
        if index.column() != UNDO_COLUMN:
            return
        entry = self.journal_model.row_at(index)
        if entry is not None:
            self._undo(entry)

    def _undo(self, entry: JournalEntry) -> None:
        if not entry.undoable or entry.command is None:
            return
        command = entry.command
        self._runner.run(
            self.context.commands.undo(command),
            on_result=lambda _: self._undone(entry),
            label=f"undo {command.summary}",
        )

    def _undone(self, entry: JournalEntry) -> None:
        self.context.journal.undone(entry)
        self.refresh_current()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setObjectName("toolbar-main")
        self.addToolBar(toolbar)
        toolbar.setMovable(False)

        host_label = QLabel(f"  {self.context.tango_host}  ")
        toolbar.addWidget(host_label)

        system = QAction("System", self)
        system.setShortcut(QKeySequence("Ctrl+1"))
        system.triggered.connect(lambda: self.open_target(Target.system()))
        toolbar.addAction(system)

        search = QAction("Search…", self)
        search.setShortcut(QKeySequence("Ctrl+K"))
        search.triggered.connect(self.open_search)
        toolbar.addAction(search)

        create = QAction("New…", self)
        create.setShortcut(QKeySequence("Ctrl+N"))
        create.setEnabled(not self.context.read_only)
        create.triggered.connect(self.navigator.create_here)
        toolbar.addAction(create)

        refresh = QAction("Refresh", self)
        refresh.setShortcut(QKeySequence("F5"))
        refresh.triggered.connect(self.refresh_current)
        toolbar.addAction(refresh)

        reload_tree = QAction("Reload tree", self)
        reload_tree.triggered.connect(self.navigator.refresh)
        toolbar.addAction(reload_tree)

        self.view_button = QToolButton(self)
        self.view_button.setText("View")
        self.view_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.view_menu = QMenu(self.view_button)
        for dock, _area in self._docks.values():
            action = dock.toggleViewAction()
            if action is not None:
                self.view_menu.addAction(action)
        self.view_menu.addSeparator()
        self.view_menu.addAction("Reset layout", self.reset_layout)
        self.view_button.setMenu(self.view_menu)
        toolbar.addWidget(self.view_button)

        toggle = QAction("Theme", self)
        toggle.triggered.connect(self._toggle_theme)
        toolbar.addAction(toggle)

        for scope in Scope:
            action = QAction(scope.value, self)
            action.triggered.connect(lambda _checked=False, s=scope: self.navigator.set_scope(s))
            self.addAction(action)

    def _build_status_bar(self) -> None:
        self.status_label = QLabel()
        bar = self.statusBar()
        if bar is not None:
            bar.addPermanentWidget(self.status_label)
        self._update_status()

    def _update_status(self) -> None:
        mode = "read-only" if self.context.read_only else "read/write"
        self.status_label.setText(
            f"{self.context.tango_host}   ·   {len(self._panels)} open   ·   {mode}   "
        )

    def open_search(self) -> None:
        dialog = SearchDialog(self.context, self.tokens, self)
        dialog.targetChosen.connect(self.open_target)
        dialog.exec()

    def _node_selected(self, node: TreeNode | None) -> None:
        self.inspector.show_node(node)

    def _toggle_theme(self) -> None:
        self.themeToggled.emit(Theme.LIGHT if self.theme is Theme.DARK else Theme.DARK)
