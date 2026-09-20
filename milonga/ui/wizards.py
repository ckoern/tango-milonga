"""Dialogs that create things: servers, devices, controlled hosts."""

from collections.abc import Sequence

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from milonga.core.enums import PollableKind
from milonga.core.model import DeviceRegistration
from milonga.core.names import DeviceName, ServerName, TangoNameError, starter_device
from milonga.ui.theme import Tokens, mono_font, set_role

STARTER_CLASS = "Starter"


class _ValidatedDialog(QDialog):
    """Refuses to close while a field is wrong, and says which."""

    def __init__(self, title: str, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self._tokens = tokens
        self.message = QLabel(self)
        set_role(self.message, "role", "error")
        self.message.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        self.buttons.accepted.connect(self._try_accept)
        self.buttons.rejected.connect(self.reject)

    def problem(self) -> str:
        return ""

    def _try_accept(self) -> None:
        problem = self.problem()
        self.message.setText(problem)
        if not problem:
            self.accept()


class NewServerDialog(_ValidatedDialog):
    def __init__(
        self,
        hosts: Sequence[str],
        tokens: Tokens,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("New server", tokens, parent)
        self.resize(560, 420)
        self.executable = QLineEdit(self)
        self.executable.setPlaceholderText("TangoTest")
        self.instance = QLineEdit(self)
        self.instance.setPlaceholderText("test")
        self.host = QComboBox(self)
        self.host.setEditable(True)
        self.host.addItem("")
        self.host.addItems(list(hosts))
        self.level = QSpinBox(self)
        self.level.setRange(0, 20)
        self.level.setValue(1)
        self.level.setSpecialValueText("not controlled")
        self.start_now = QCheckBox("Start it on this host now", self)
        self.start_now.setChecked(True)
        self.start_now.setToolTip(
            "A Starter controls the servers that have run on its host; "
            "starting it here puts it under that Starter"
        )
        self.host.currentTextChanged.connect(self._host_changed)
        self._host_changed(self.host.currentText())

        self.devices = QTableWidget(0, 2, self)
        self.devices.setHorizontalHeaderLabels(["Device", "Class"])
        self.devices.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.devices.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        vertical = self.devices.verticalHeader()
        if vertical is not None:
            vertical.setVisible(False)
        self.add_row()

        add = QPushButton("Add device", self)
        add.clicked.connect(self.add_row)
        remove = QPushButton("Remove device", self)
        remove.clicked.connect(self._remove_row)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch(1)

        form = QFormLayout()
        form.addRow("Executable", self.executable)
        form.addRow("Instance", self.instance)
        form.addRow("Host", self.host)
        form.addRow("Startup level", self.level)
        form.addRow("", self.start_now)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("Devices served by this server", self))
        layout.addWidget(self.devices, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.message)
        layout.addWidget(self.buttons)

    def _host_changed(self, text: str) -> None:
        self.start_now.setEnabled(bool(text.strip()))

    def start_after(self) -> bool:
        return self.start_now.isEnabled() and self.start_now.isChecked()

    def add_row(self) -> None:
        row = self.devices.rowCount()
        self.devices.insertRow(row)
        for column in range(2):
            item = QTableWidgetItem("")
            item.setFont(mono_font())
            self.devices.setItem(row, column, item)

    def _remove_row(self) -> None:
        row = self.devices.currentRow()
        if row >= 0 and self.devices.rowCount() > 1:
            self.devices.removeRow(row)

    def _cell(self, row: int, column: int) -> str:
        item = self.devices.item(row, column)
        return item.text().strip() if item is not None else ""

    def server(self) -> ServerName:
        return ServerName(self.executable.text().strip(), self.instance.text().strip())

    def host_name(self) -> str:
        return self.host.currentText().strip()

    def startup_level(self) -> int:
        return self.level.value() if self.host_name() else 0

    def registrations(self) -> tuple[DeviceRegistration, ...]:
        server = self.server()
        rows = []
        for row in range(self.devices.rowCount()):
            name, class_name = self._cell(row, 0), self._cell(row, 1)
            if name or class_name:
                rows.append(DeviceRegistration(DeviceName.parse(name), class_name, server))
        return tuple(rows)

    def problem(self) -> str:
        try:
            self.server()
        except TangoNameError as error:
            return str(error)
        filled = [
            row
            for row in range(self.devices.rowCount())
            if self._cell(row, 0) or self._cell(row, 1)
        ]
        if not filled:
            return "a server needs at least one device"
        for row in filled:
            if not self._cell(row, 1):
                return f"row {row + 1}: the device needs a class"
            try:
                DeviceName.parse(self._cell(row, 0))
            except TangoNameError as error:
                return f"row {row + 1}: {error}"
        return ""


class AddDeviceDialog(_ValidatedDialog):
    def __init__(
        self,
        server: ServerName,
        classes: Sequence[str],
        tokens: Tokens,
        parent: QWidget | None = None,
        *,
        running: bool = False,
    ) -> None:
        super().__init__(f"Add a device to {server}", tokens, parent)
        self._server = server
        self._running = running
        self.reload = QCheckBox(f"Reload {server} so it creates the device", self)
        self.reload.setChecked(running)
        self.reload.setVisible(running)
        self.reload.setToolTip(
            "A running server creates only the devices it read when it started"
        )
        self.name = QLineEdit(self)
        self.name.setFont(mono_font())
        self.name.setPlaceholderText("domain/family/member")
        self.class_name = QComboBox(self)
        self.class_name.setEditable(True)
        self.class_name.addItems(list(classes))

        form = QFormLayout()
        form.addRow("Device", self.name)
        form.addRow("Class", self.class_name)
        form.addRow("", self.reload)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.message)
        layout.addWidget(self.buttons)

    def reload_after(self) -> bool:
        return self._running and self.reload.isChecked()

    def registration(self) -> DeviceRegistration:
        return DeviceRegistration(
            DeviceName.parse(self.name.text().strip()),
            self.class_name.currentText().strip(),
            self._server,
        )

    def problem(self) -> str:
        if not self.class_name.currentText().strip():
            return "the device needs a class"
        try:
            DeviceName.parse(self.name.text().strip())
        except TangoNameError as error:
            return str(error)
        return ""


class AddHostDialog(_ValidatedDialog):
    """A controlled host is a Starter server with its admin device."""

    def __init__(self, tokens: Tokens, parent: QWidget | None = None) -> None:
        super().__init__("Add a controlled host", tokens, parent)
        self.host = QLineEdit(self)
        self.host.setFont(mono_font())
        self.host.setPlaceholderText("hostname")
        self.preview = QLabel(self)
        self.preview.setFont(mono_font())
        set_role(self.preview, "role", "muted")
        self.host.textChanged.connect(self._update_preview)

        form = QFormLayout()
        form.addRow("Host", self.host)
        form.addRow("Starter device", self.preview)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.message)
        layout.addWidget(self.buttons)
        self._update_preview("")

    def _update_preview(self, text: str) -> None:
        name = text.strip()
        self.preview.setText(str(starter_device(name)) if name else "—")

    def host_name(self) -> str:
        return self.host.text().strip()

    def server(self) -> ServerName:
        return ServerName(STARTER_CLASS, self.host_name())

    def registration(self) -> DeviceRegistration:
        return DeviceRegistration(
            starter_device(self.host_name()), STARTER_CLASS, self.server()
        )

    def problem(self) -> str:
        if not self.host_name():
            return "give a host name"
        try:
            self.server()
        except TangoNameError as error:
            return str(error)
        return ""


class PollingDialog(_ValidatedDialog):
    def __init__(
        self,
        candidates: Sequence[tuple[str, PollableKind]],
        tokens: Tokens,
        *,
        selected: str = "",
        period_ms: int = 1000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Polling", tokens, parent)
        self._candidates = list(candidates)
        self.target = QComboBox(self)
        for name, kind in self._candidates:
            self.target.addItem(f"{name}  ({kind.value})", (name, kind))
        if selected:
            index = next(
                (
                    position
                    for position, (name, _kind) in enumerate(self._candidates)
                    if name == selected
                ),
                -1,
            )
            if index >= 0:
                self.target.setCurrentIndex(index)
        self.period = QSpinBox(self)
        self.period.setRange(0, 3_600_000)
        self.period.setSingleStep(100)
        self.period.setSuffix(" ms")
        self.period.setValue(period_ms)
        self.period.setSpecialValueText("stop polling")

        form = QFormLayout()
        form.addRow("Attribute or command", self.target)
        form.addRow("Period", self.period)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.message)
        layout.addWidget(self.buttons)

    def chosen(self) -> tuple[str, PollableKind]:
        data = self.target.currentData()
        return data if data else ("", PollableKind.ATTRIBUTE)

    def period_ms(self) -> int:
        return self.period.value()

    def problem(self) -> str:
        return "" if self.chosen()[0] else "nothing to poll"
