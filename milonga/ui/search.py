"""The command palette: one box that resolves any object in the control system."""

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from milonga.core.names import DeviceName, ServerName
from milonga.ui.context import AppContext, Target
from milonga.ui.tasks import TaskRunner
from milonga.ui.theme import Tokens, mono_font

RESULT_LIMIT = 40
DEBOUNCE_MS = 180


class SearchDialog(QDialog):
    targetChosen = pyqtSignal(object)

    def __init__(self, context: AppContext, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Search")
        self.setModal(True)
        self.resize(560, 420)
        self._context = context
        self._runner = TaskRunner(self, context.journal, context="search")

        self.input = QLineEdit(self)
        self.input.setPlaceholderText("Device, alias, server or class…")
        self.results = QListWidget(self)
        self.results.setFont(mono_font())
        self.hint = QLabel("Enter opens the selected object", self)
        self.hint.setStyleSheet(f"color: {tokens.ink_3};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.input)
        layout.addWidget(self.results, 1)
        layout.addWidget(self.hint)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._search)
        self.input.textChanged.connect(lambda _text: self._timer.start())
        self.input.returnPressed.connect(self._choose_current)
        self.results.itemActivated.connect(self._choose)

    def keyPressEvent(self, a0: object) -> None:
        key = getattr(a0, "key", lambda: None)()
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.results.count():
            self.results.setFocus()
        super().keyPressEvent(a0)  # type: ignore[arg-type]

    def _search(self) -> None:
        text = self.input.text().strip()
        if not text:
            self.results.clear()
            return
        self._runner.run(self._collect(text), on_result=self._show)

    async def _collect(self, text: str) -> list[Target]:
        backend = self._context.backend
        pattern = f"*{text}*"
        found: list[Target] = []
        for device in await backend.get_device_list(pattern):
            found.append(Target.device(device))
        for alias in await backend.get_device_alias_list(pattern):
            device = await backend.get_device_from_alias(alias)
            found.append(Target.device(device))
        for server in await backend.get_server_list(pattern):
            found.append(Target.server(server))
        for name in await backend.get_class_list(pattern):
            found.append(Target.device_class(name))
        seen: set[str] = set()
        unique: list[Target] = []
        for target in found:
            if target.uri not in seen:
                seen.add(target.uri)
                unique.append(target)
        return unique[:RESULT_LIMIT]

    def _show(self, targets: list[Target]) -> None:
        self.results.clear()
        for target in targets:
            item = QListWidgetItem(f"{target.kind.value:<7} {target.name}")
            item.setData(int(Qt.ItemDataRole.UserRole), target)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _choose_current(self) -> None:
        item = self.results.currentItem()
        if item is not None:
            self._choose(item)

    def _choose(self, item: QListWidgetItem) -> None:
        target = item.data(int(Qt.ItemDataRole.UserRole))
        if isinstance(target, Target):
            self.targetChosen.emit(target)
            self.accept()


def parse_target(text: str) -> Target | None:
    """Accept a typed device or server name without touching the database."""
    try:
        return Target.device(DeviceName.parse(text))
    except ValueError:
        pass
    try:
        return Target.server(ServerName.parse(text))
    except ValueError:
        return None
