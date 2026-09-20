"""The contract every database mutation follows.

A command is built from the wanted end state. ``preview`` reads what is there
now, keeps it, and returns the difference; ``apply`` writes; ``revert`` puts
the kept state back. Nothing is written until ``apply`` is called, so a preview
is always safe to show.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

from milonga.compat import StrEnum
from milonga.core.backend.protocol import TangoBackend
from milonga.core.errors import ReadOnlyError


class RevertUnsupported(Exception):
    """The command cannot be undone through the Tango database API."""


class DiffKind(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class DiffLine:
    owner: str
    name: str
    kind: DiffKind
    before: tuple[str, ...] = ()
    after: tuple[str, ...] = ()

    @property
    def target(self) -> str:
        return f"{self.owner} · {self.name}" if self.owner else self.name

    def __str__(self) -> str:
        match self.kind:
            case DiffKind.ADDED:
                return f"+ {self.target} = {_join(self.after)}"
            case DiffKind.REMOVED:
                return f"- {self.target} = {_join(self.before)}"
            case DiffKind.CHANGED:
                return f"± {self.target}: {_join(self.before)} → {_join(self.after)}"
            case _:
                return f"  {self.target} = {_join(self.after)}"


@dataclass(frozen=True, slots=True)
class Diff:
    lines: tuple[DiffLine, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.changes)

    @property
    def changes(self) -> tuple[DiffLine, ...]:
        return tuple(line for line in self.lines if line.kind is not DiffKind.UNCHANGED)

    def merged(self, other: "Diff") -> "Diff":
        return Diff(self.lines + other.lines)

    def text(self) -> str:
        return "\n".join(str(line) for line in self.changes)


@dataclass
class Command(ABC):
    """One mutation. Subclasses hold the wanted state and capture the old one."""

    _captured: bool = field(default=False, init=False, repr=False)

    @property
    @abstractmethod
    def summary(self) -> str:
        """One line for the journal, in the vocabulary of the database."""

    @property
    def destructive(self) -> bool:
        return False

    @property
    def confirmation_name(self) -> str | None:
        """The name a destructive command asks the user to type back."""
        return None

    @property
    def revertible(self) -> bool:
        return self._captured

    @abstractmethod
    async def preview(self, backend: TangoBackend) -> Diff:
        """Read the current state, keep it for the undo, and report the change."""

    @abstractmethod
    async def apply(self, backend: TangoBackend) -> None: ...

    async def revert(self, backend: TangoBackend) -> None:
        raise RevertUnsupported(self.summary)


class CommandRunner:
    """Applies commands, refusing early when the session cannot write."""

    def __init__(self, backend: TangoBackend) -> None:
        self._backend = backend

    async def preview(self, commands: Sequence[Command]) -> Diff:
        diff = Diff()
        for command in commands:
            diff = diff.merged(await command.preview(self._backend))
        return diff

    async def run(self, commands: Sequence[Command]) -> Diff:
        self._check_writable()
        diff = await self.preview(commands)
        for command in commands:
            await command.apply(self._backend)
        return diff

    async def undo(self, command: Command) -> None:
        self._check_writable()
        await command.revert(self._backend)

    def _check_writable(self) -> None:
        if not self._backend.writable:
            raise ReadOnlyError("the session is read-only; nothing was written")


def _join(values: Sequence[str]) -> str:
    return ", ".join(values) if values else "∅"
