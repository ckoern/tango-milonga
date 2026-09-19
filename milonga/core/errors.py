"""Structured errors. ``DevFailed`` chains become data instead of strings."""

from dataclasses import dataclass, field
from enum import StrEnum


class ErrorSeverity(StrEnum):
    WARN = "WARN"
    ERR = "ERR"
    PANIC = "PANIC"


@dataclass(frozen=True, slots=True)
class ErrorFrame:
    """One element of a Tango error stack."""

    reason: str
    description: str = ""
    origin: str = ""
    severity: ErrorSeverity = ErrorSeverity.ERR


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """What the client was doing when the call failed."""

    operation: str = ""
    target: str = ""


class TangoError(Exception):
    """Base class for every failure the backend reports."""

    def __init__(
        self,
        message: str,
        *,
        frames: tuple[ErrorFrame, ...] = (),
        context: ErrorContext | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.frames = frames
        self.context = context or ErrorContext()

    @property
    def reason(self) -> str:
        return self.frames[0].reason if self.frames else type(self).__name__

    def __str__(self) -> str:
        target = self.context.target
        return f"{self.message} [{target}]" if target else self.message


class DeviceUnreachable(TangoError):
    """The device could not be contacted (no server, no host, no network)."""


class RequestTimeout(TangoError):
    """The call was cut off by the client timeout."""


class ObjectNotFound(TangoError):
    """The database has no such device, server, class or property."""


class PermissionDenied(TangoError):
    """Tango Access Control refused the operation."""


class ReadOnlyError(PermissionDenied):
    """The session is read-only; the write was never sent."""


class CommandFailed(TangoError):
    """The device accepted the call and answered with an error."""


class BackendUnavailable(TangoError):
    """The backend itself cannot serve requests (no database, not connected)."""


@dataclass(frozen=True, slots=True)
class ErrorReport:
    """A failure captured for display, keeping the exception out of the UI layer."""

    message: str
    reason: str
    frames: tuple[ErrorFrame, ...] = ()
    context: ErrorContext = field(default_factory=ErrorContext)

    @classmethod
    def from_exception(cls, error: TangoError) -> "ErrorReport":
        return cls(error.message, error.reason, error.frames, error.context)
