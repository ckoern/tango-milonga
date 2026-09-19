import numpy as np
import pytest

from milonga.core.enums import AttrDataFormat, AttrWriteType, TangoState, TangoType
from milonga.core.model import AttributeSpec, AttributeValue
from milonga.ui.format import (
    format_age,
    format_scalar,
    format_shape,
    format_time,
    format_value,
    parse_command_argument,
    parse_write_value,
)

DOUBLE = AttributeSpec("double_scalar", TangoType.DOUBLE, display_format="%6.2f")
LONG = AttributeSpec("long_scalar", TangoType.LONG)
BOOL = AttributeSpec("flag", TangoType.BOOLEAN)
TEXT = AttributeSpec("label", TangoType.STRING)
STATE = AttributeSpec("State", TangoType.STATE)
ENUM = AttributeSpec("mode", TangoType.ENUM, enum_labels=("idle", "busy"))
SPECTRUM = AttributeSpec("wave", TangoType.DOUBLE, AttrDataFormat.SPECTRUM, max_dim_x=8)
IMAGE = AttributeSpec("frame", TangoType.DOUBLE, AttrDataFormat.IMAGE)


def test_scalars_use_the_declared_display_format() -> None:
    plain = AttributeSpec("x", TangoType.DOUBLE, display_format="")
    assert format_scalar(12.8741, DOUBLE) == "12.87"
    assert format_scalar(12.8741, plain) == "12.8741"


def test_scalars_of_every_kind() -> None:
    assert format_scalar(143, LONG) == "143"
    assert format_scalar(True, BOOL) == "true"
    assert format_scalar(TangoState.MOVING, STATE) == "MOVING"
    assert format_scalar(1, ENUM) == "busy"
    assert format_scalar(None, TEXT) == "—"


def test_arrays_show_shape_and_a_preview() -> None:
    value = AttributeValue(
        "wave", np.arange(5.0), dim_x=5, data_format=AttrDataFormat.SPECTRUM
    )
    text = format_value(value, SPECTRUM)
    assert text.startswith("[5]")
    assert "…" in text
    assert format_shape(value) == "[5]"


def test_images_report_both_dimensions() -> None:
    value = AttributeValue(
        "frame", np.zeros((4, 6)), dim_x=6, dim_y=4, data_format=AttrDataFormat.IMAGE
    )
    assert format_shape(value) == "[4 × 6]"


def test_timestamps_and_ages() -> None:
    assert format_time(0.0) == "—"
    assert len(format_time(1_700_000_000.25)) == 12
    assert format_age(0.0, 10.0) == "—"
    assert format_age(100.0, 100.4) == "just now"
    assert format_age(100.0, 130.0) == "30 s ago"
    assert format_age(100.0, 400.0) == "5 min ago"
    assert format_age(100.0, 7_400.0) == "2 h ago"


@pytest.mark.parametrize(
    ("text", "spec", "expected"),
    [
        ("12.5", DOUBLE, 12.5),
        ("143", LONG, 143),
        ("0x10", LONG, 16),
        ("true", BOOL, True),
        ("off", BOOL, False),
        ("anything", TEXT, "anything"),
        ("moving", STATE, TangoState.MOVING),
        ("busy", ENUM, 1),
    ],
)
def test_typed_parsing(text: str, spec: AttributeSpec, expected: object) -> None:
    assert parse_write_value(text, spec) == expected


def test_spectrum_parsing_accepts_commas_or_spaces() -> None:
    assert list(parse_write_value("1, 2  3", SPECTRUM)) == [1.0, 2.0, 3.0]


@pytest.mark.parametrize(
    ("text", "spec", "message"),
    [
        ("abc", DOUBLE, "not a number"),
        ("abc", LONG, "not an integer"),
        ("maybe", BOOL, "not a boolean"),
        ("DANCING", STATE, "not a Tango state"),
        ("", SPECTRUM, "at least one value"),
        ("1 2", IMAGE, "images cannot be written"),
    ],
)
def test_bad_input_explains_itself(text: str, spec: AttributeSpec, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_write_value(text, spec)


def test_command_arguments_follow_the_declared_type() -> None:
    assert parse_command_argument("2.5", TangoType.DOUBLE) == 2.5
    assert parse_command_argument("", TangoType.VOID) is None
    assert parse_command_argument("a b", TangoType.VAR_STRING_ARRAY) == ["a", "b"]
    assert parse_command_argument("1,2", TangoType.VAR_LONG_ARRAY) == [1, 2]


def test_writable_flag_is_read_from_the_spec() -> None:
    assert AttrWriteType.READ_WRITE.writable
    assert not AttrWriteType.READ.writable
