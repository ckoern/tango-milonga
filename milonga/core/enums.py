"""Enumerations mirroring the Tango type system."""

from enum import StrEnum


class StateCategory(StrEnum):
    """Coarse grouping of device states, used for aggregation and colouring."""

    NOMINAL = "NOMINAL"
    BUSY = "BUSY"
    WARNING = "WARNING"
    FAULT = "FAULT"
    INACTIVE = "INACTIVE"
    UNKNOWN = "UNKNOWN"


class TangoState(StrEnum):
    """Member names match ``tango.DevState`` so conversion is by name."""

    ON = "ON"
    OFF = "OFF"
    CLOSE = "CLOSE"
    OPEN = "OPEN"
    INSERT = "INSERT"
    EXTRACT = "EXTRACT"
    MOVING = "MOVING"
    STANDBY = "STANDBY"
    FAULT = "FAULT"
    INIT = "INIT"
    RUNNING = "RUNNING"
    ALARM = "ALARM"
    DISABLE = "DISABLE"
    UNKNOWN = "UNKNOWN"

    @property
    def category(self) -> StateCategory:
        return _STATE_CATEGORY[self]

    @property
    def code(self) -> int:
        """Numeric value Tango transports ``DevState`` as."""
        return _DEV_STATE_ORDER.index(self)

    @classmethod
    def from_code(cls, code: int) -> "TangoState":
        if 0 <= code < len(_DEV_STATE_ORDER):
            return _DEV_STATE_ORDER[code]
        return cls.UNKNOWN


_DEV_STATE_ORDER: tuple[TangoState, ...] = (
    TangoState.ON,
    TangoState.OFF,
    TangoState.CLOSE,
    TangoState.OPEN,
    TangoState.INSERT,
    TangoState.EXTRACT,
    TangoState.MOVING,
    TangoState.STANDBY,
    TangoState.FAULT,
    TangoState.INIT,
    TangoState.RUNNING,
    TangoState.ALARM,
    TangoState.DISABLE,
    TangoState.UNKNOWN,
)

_STATE_CATEGORY: dict[TangoState, StateCategory] = {
    TangoState.ON: StateCategory.NOMINAL,
    TangoState.OPEN: StateCategory.NOMINAL,
    TangoState.EXTRACT: StateCategory.NOMINAL,
    TangoState.RUNNING: StateCategory.NOMINAL,
    TangoState.MOVING: StateCategory.BUSY,
    TangoState.INIT: StateCategory.BUSY,
    TangoState.ALARM: StateCategory.WARNING,
    TangoState.FAULT: StateCategory.FAULT,
    TangoState.OFF: StateCategory.INACTIVE,
    TangoState.CLOSE: StateCategory.INACTIVE,
    TangoState.INSERT: StateCategory.INACTIVE,
    TangoState.STANDBY: StateCategory.INACTIVE,
    TangoState.DISABLE: StateCategory.INACTIVE,
    TangoState.UNKNOWN: StateCategory.UNKNOWN,
}


class AttrQuality(StrEnum):
    VALID = "VALID"
    INVALID = "INVALID"
    ALARM = "ALARM"
    CHANGING = "CHANGING"
    WARNING = "WARNING"


class AttrDataFormat(StrEnum):
    SCALAR = "SCALAR"
    SPECTRUM = "SPECTRUM"
    IMAGE = "IMAGE"


class AttrWriteType(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    READ_WRITE = "READ_WRITE"
    READ_WITH_WRITE = "READ_WITH_WRITE"

    @property
    def writable(self) -> bool:
        return self is not AttrWriteType.READ


class TangoType(StrEnum):
    """Tango data types, limited to the ones an attribute or command can carry."""

    VOID = "DevVoid"
    BOOLEAN = "DevBoolean"
    SHORT = "DevShort"
    LONG = "DevLong"
    LONG64 = "DevLong64"
    USHORT = "DevUShort"
    ULONG = "DevULong"
    ULONG64 = "DevULong64"
    FLOAT = "DevFloat"
    DOUBLE = "DevDouble"
    STRING = "DevString"
    STATE = "DevState"
    ENUM = "DevEnum"
    ENCODED = "DevEncoded"
    UCHAR = "DevUChar"
    VAR_SHORT_ARRAY = "DevVarShortArray"
    VAR_LONG_ARRAY = "DevVarLongArray"
    VAR_DOUBLE_ARRAY = "DevVarDoubleArray"
    VAR_STRING_ARRAY = "DevVarStringArray"
    VAR_LONG_STRING_ARRAY = "DevVarLongStringArray"
    VAR_DOUBLE_STRING_ARRAY = "DevVarDoubleStringArray"

    @property
    def numeric(self) -> bool:
        return self in _NUMERIC_TYPES


_NUMERIC_TYPES: frozenset[TangoType] = frozenset(
    {
        TangoType.SHORT,
        TangoType.LONG,
        TangoType.LONG64,
        TangoType.USHORT,
        TangoType.ULONG,
        TangoType.ULONG64,
        TangoType.FLOAT,
        TangoType.DOUBLE,
        TangoType.UCHAR,
    }
)


class DisplayLevel(StrEnum):
    OPERATOR = "OPERATOR"
    EXPERT = "EXPERT"


class EventType(StrEnum):
    CHANGE = "change"
    PERIODIC = "periodic"
    ARCHIVE = "archive"
    USER = "user"
    ATTR_CONF = "attr_conf"
    DATA_READY = "data_ready"
    INTERFACE = "interface"


class DataSource(StrEnum):
    """How a monitored value reaches the client. Shown in the UI."""

    EVENTS = "events"
    POLLING = "polling"


class PropertyScope(StrEnum):
    DEVICE = "device"
    CLASS = "class"
    DEVICE_ATTRIBUTE = "device_attribute"
    CLASS_ATTRIBUTE = "class_attribute"
    FREE = "free"


class PollableKind(StrEnum):
    ATTRIBUTE = "attribute"
    COMMAND = "command"


class ServerRunState(StrEnum):
    RUNNING = "RUNNING"
    STARTING = "STARTING"
    NOT_RESPONDING = "NOT_RESPONDING"
    STOPPED = "STOPPED"
    UNKNOWN = "UNKNOWN"


class HostState(StrEnum):
    ALL_RUNNING = "ALL_RUNNING"
    STARTING = "STARTING"
    MIXED = "MIXED"
    ALL_STOPPED = "ALL_STOPPED"
    UNREACHABLE = "UNREACHABLE"


class LogLevel(StrEnum):
    OFF = "OFF"
    FATAL = "FATAL"
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    DEBUG = "DEBUG"


class LogTargetType(StrEnum):
    CONSOLE = "console"
    FILE = "file"
    DEVICE = "device"


NOT_CONTROLLED_LEVEL: int = 0
"""Startup level of a server the Starter knows but never starts by itself."""
