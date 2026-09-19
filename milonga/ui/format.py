"""Turning Tango values into text, and typed text back into values."""

from datetime import datetime
from typing import Any

import numpy as np

from milonga.core.enums import AttrDataFormat, TangoState, TangoType
from milonga.core.model import AttributeSpec, AttributeValue

ARRAY_PREVIEW = 3
BOOL_TRUE = frozenset({"1", "true", "yes", "on"})
BOOL_FALSE = frozenset({"0", "false", "no", "off"})


def format_scalar(value: Any, spec: AttributeSpec) -> str:
    if value is None:
        return "—"
    if isinstance(value, TangoState):
        return value.value
    if isinstance(value, bool):
        return "true" if value else "false"
    if spec.data_type is TangoType.ENUM and spec.enum_labels:
        index = int(value)
        if 0 <= index < len(spec.enum_labels):
            return spec.enum_labels[index]
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        return _format_float(float(value), spec)
    return str(value)


def _format_float(value: float, spec: AttributeSpec) -> str:
    template = spec.display_format
    if template.startswith("%") and template[-1] in "efgEFG":
        try:
            return (template % value).strip()
        except (TypeError, ValueError):
            pass
    return f"{value:.6g}"


def format_shape(value: AttributeValue) -> str:
    if value.data_format is AttrDataFormat.SPECTRUM:
        return f"[{value.dim_x}]"
    if value.data_format is AttrDataFormat.IMAGE:
        return f"[{value.dim_y} × {value.dim_x}]"
    return ""


def format_array(value: Any, spec: AttributeSpec) -> str:
    """A shape and the first values, enough to see the array is alive."""
    array = np.asarray(value)
    if array.size == 0:
        return "empty"
    flat = array.reshape(-1)
    head = ", ".join(format_scalar(item, spec) for item in flat[:ARRAY_PREVIEW])
    return f"{head}, …" if flat.size > ARRAY_PREVIEW else head


def format_value(value: AttributeValue | None, spec: AttributeSpec) -> str:
    if value is None:
        return "—"
    if value.error is not None:
        return "error"
    if spec.data_format is AttrDataFormat.SCALAR:
        return format_scalar(value.value, spec)
    if value.value is None:
        return "—"
    return f"{format_shape(value)}  {format_array(value.value, spec)}"


def format_time(timestamp: float) -> str:
    if not timestamp:
        return "—"
    return datetime.fromtimestamp(timestamp).strftime("%H:%M:%S.%f")[:-3]


def format_age(timestamp: float, now: float) -> str:
    if not timestamp:
        return "—"
    seconds = max(now - timestamp, 0.0)
    if seconds < 1:
        return "just now"
    if seconds < 60:
        return f"{seconds:.0f} s ago"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min ago"
    return f"{seconds / 3600:.0f} h ago"


def parse_write_value(text: str, spec: AttributeSpec) -> Any:
    """Convert what the user typed. Raises ``ValueError`` with a usable message."""
    if spec.data_format is AttrDataFormat.IMAGE:
        raise ValueError("images cannot be written from this panel")
    if spec.data_format is AttrDataFormat.SPECTRUM:
        items = [item for item in text.replace(",", " ").split() if item]
        if not items:
            raise ValueError("give at least one value")
        return np.array([_parse_scalar(item, spec) for item in items])
    return _parse_scalar(text.strip(), spec)


def _parse_scalar(text: str, spec: AttributeSpec) -> Any:
    data_type = spec.data_type
    if data_type is TangoType.BOOLEAN:
        lowered = text.lower()
        if lowered in BOOL_TRUE:
            return True
        if lowered in BOOL_FALSE:
            return False
        raise ValueError(f"{text!r} is not a boolean; use true or false")
    if data_type is TangoType.STATE:
        try:
            return TangoState[text.upper()]
        except KeyError:
            raise ValueError(f"{text!r} is not a Tango state") from None
    if data_type is TangoType.ENUM:
        if text in spec.enum_labels:
            return spec.enum_labels.index(text)
        return _parse_int(text, spec)
    if data_type is TangoType.STRING:
        return text
    if data_type.numeric:
        if data_type in (TangoType.FLOAT, TangoType.DOUBLE):
            return _parse_float(text)
        return _parse_int(text, spec)
    return text


def _parse_float(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{text!r} is not a number") from None


def _parse_int(text: str, spec: AttributeSpec) -> int:
    try:
        return int(text, 0) if text.lower().startswith("0x") else int(text)
    except ValueError:
        raise ValueError(f"{text!r} is not an integer ({spec.data_type.value})") from None


def parse_command_argument(text: str, data_type: TangoType) -> Any:
    """Command arguments use the same rules as scalars, plus array types."""
    spec = AttributeSpec("argin", data_type)
    if data_type.name.startswith("VAR_"):
        items = [item for item in text.replace(",", " ").split() if item]
        element = _ARRAY_ELEMENT.get(data_type, TangoType.STRING)
        return [_parse_scalar(item, AttributeSpec("argin", element)) for item in items]
    if data_type is TangoType.VOID:
        return None
    return _parse_scalar(text.strip(), spec)


_ARRAY_ELEMENT: dict[TangoType, TangoType] = {
    TangoType.VAR_SHORT_ARRAY: TangoType.SHORT,
    TangoType.VAR_LONG_ARRAY: TangoType.LONG,
    TangoType.VAR_DOUBLE_ARRAY: TangoType.DOUBLE,
    TangoType.VAR_STRING_ARRAY: TangoType.STRING,
}
