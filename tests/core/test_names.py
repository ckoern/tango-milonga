import pytest

from milonga.core.names import (
    AttributeRef,
    DeviceName,
    ServerName,
    TangoNameError,
    parse_device_reference,
    short_hostname,
    starter_device,
)


def test_device_name_round_trip() -> None:
    assert str(DeviceName.parse("sys/tg_test/1")) == "sys/tg_test/1"
    assert DeviceName.parse("/sys/tg_test/1/") == DeviceName("sys", "tg_test", "1")


@pytest.mark.parametrize("text", ["sys/tg_test", "a/b/c/d", "", "sys//1"])
def test_device_name_rejects_malformed(text: str) -> None:
    with pytest.raises(TangoNameError):
        DeviceName.parse(text)


def test_server_admin_device() -> None:
    assert str(ServerName.parse("TangoTest/test").admin_device) == "dserver/TangoTest/test"


def test_attribute_ref_parses_four_fields() -> None:
    ref = AttributeRef.parse("sys/tg_test/1/double_scalar")
    assert ref.device == DeviceName("sys", "tg_test", "1")
    assert ref.attribute == "double_scalar"
    assert str(ref) == "sys/tg_test/1/double_scalar"


def test_device_reference_keeps_control_system() -> None:
    reference = parse_device_reference("tango://tango-cs:10000/sys/tg_test/1")
    assert reference.tango_host == "tango-cs:10000"
    assert str(reference.device) == "sys/tg_test/1"
    assert parse_device_reference("sys/tg_test/1").tango_host is None


def test_starter_device_uses_short_hostname() -> None:
    assert str(starter_device("id09-srv-02.esrf.fr")) == "tango/admin/id09-srv-02"
    assert short_hostname("host") == "host"


def test_names_are_orderable() -> None:
    names = [DeviceName.parse("b/b/1"), DeviceName.parse("a/z/9")]
    assert sorted(names)[0].domain == "a"
