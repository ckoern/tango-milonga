"""What every panel is handed: the services, the journal, and a way to navigate."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from milonga.compat import StrEnum
from milonga.core.backend.protocol import TangoBackend
from milonga.core.commands import Command, CommandRunner
from milonga.core.errors import ErrorReport
from milonga.core.monitor import MonitorHub
from milonga.core.names import DeviceName, ServerName
from milonga.core.services.control import StarterControl
from milonga.core.services.groups import HostGroups
from milonga.core.services.inventory import Inventory
from milonga.core.store import SystemStore

PATH_SEPARATOR = "\t"


class TargetKind(StrEnum):
    SYSTEM = "system"
    TILES = "tiles"
    DEVICE = "device"
    SERVER = "server"
    CLASS = "class"
    OBJECT = "object"
    HOST = "host"


@dataclass(frozen=True, slots=True)
class Target:
    """What a panel shows, and the key panels are deduplicated by."""

    kind: TargetKind
    name: str

    @classmethod
    def device(cls, device: DeviceName) -> "Target":
        return cls(TargetKind.DEVICE, str(device))

    @classmethod
    def server(cls, server: ServerName) -> "Target":
        return cls(TargetKind.SERVER, str(server))

    @classmethod
    def device_class(cls, class_name: str) -> "Target":
        return cls(TargetKind.CLASS, class_name)

    @classmethod
    def free_object(cls, obj: str) -> "Target":
        return cls(TargetKind.OBJECT, obj)

    @classmethod
    def host(cls, host: str) -> "Target":
        return cls(TargetKind.HOST, host)

    @classmethod
    def system(cls) -> "Target":
        return cls(TargetKind.SYSTEM, "system")

    @classmethod
    def tiles(cls, scope: str, path: Sequence[str]) -> "Target":
        """The children of one branch of a tree, as tiles.

        Labels can hold slashes — a device is ``domain/family/member`` — so the
        path is joined with a character a Tango name cannot contain.
        """
        return cls(TargetKind.TILES, PATH_SEPARATOR.join([scope, *path]))

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(self.name.split(PATH_SEPARATOR))

    @property
    def uri(self) -> str:
        return f"milonga://{self.kind}/{self.name}"


class JournalKind(StrEnum):
    INFO = "info"
    WRITE = "write"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class JournalEntry:
    at: datetime
    kind: JournalKind
    summary: str
    detail: str = ""
    command: Command | None = None

    @property
    def undoable(self) -> bool:
        return self.command is not None and self.command.revertible


class Journal(QObject):
    """Everything the session did, and everything that failed."""

    entryAdded = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._entries: list[JournalEntry] = []

    @property
    def entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries)

    def info(self, summary: str, detail: str = "") -> None:
        self._add(JournalKind.INFO, summary, detail)

    def undone(self, entry: JournalEntry) -> None:
        self._add(JournalKind.WRITE, f"undo · {entry.summary}", "")

    def write(self, summary: str, detail: str = "", command: Command | None = None) -> None:
        self._add(JournalKind.WRITE, summary, detail, command)

    def error(self, summary: str, detail: str = "") -> None:
        self._add(JournalKind.ERROR, summary, detail)

    def report(self, report: ErrorReport, context: str = "") -> None:
        summary = f"{context}: {report.message}" if context else report.message
        detail = (
            "\n".join(f"{frame.reason}: {frame.description}" for frame in report.frames)
            or report.reason
        )
        self._add(JournalKind.ERROR, summary, detail)

    def _add(
        self,
        kind: JournalKind,
        summary: str,
        detail: str,
        command: Command | None = None,
    ) -> None:
        entry = JournalEntry(datetime.now(), kind, summary, detail, command)
        self._entries.append(entry)
        self.entryAdded.emit(entry)


def _ignore(_target: Target) -> None:
    return None


@dataclass
class AppContext:
    """Services shared by the whole window."""

    backend: TangoBackend
    store: SystemStore
    monitor: MonitorHub
    inventory: Inventory
    control: StarterControl
    journal: Journal
    commands: CommandRunner
    groups: HostGroups = field(default_factory=HostGroups)
    open_target: Callable[[Target], None] = _ignore

    @property
    def tango_host(self) -> str:
        return self.backend.tango_host

    @property
    def read_only(self) -> bool:
        return not self.backend.writable
