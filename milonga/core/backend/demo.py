"""A populated :class:`FakeBackend`, used by the tests and by demo mode."""

import numpy as np

from milonga.core.backend.fake import FakeBackend
from milonga.core.enums import (
    AttrDataFormat,
    AttrWriteType,
    DisplayLevel,
    PropertyScope,
    TangoType,
)
from milonga.core.model import AlarmConfig, AttributeSpec, CommandSpec, EventConfig
from milonga.core.names import DeviceName, ServerName

BEAMLINE = "Beamline"
ACCELERATOR = "Accelerator"


def build_demo_backend(**kwargs: object) -> FakeBackend:
    backend = FakeBackend(tango_host="tango-cs.id09:10000", **kwargs)  # type: ignore[arg-type]
    _build_beamline(backend)
    _build_accelerator(backend)
    return backend


def _build_beamline(backend: FakeBackend) -> None:
    for host in ("id09-srv-01", "id09-srv-02", "id09-det-01", "id09-vac-01"):
        backend.register_host(host, group=BEAMLINE)
        backend.install_starter(host)

    for name, level in (("Databaseds/2", 1), ("TangoAccessControl/1", 1), ("PyAlarm/id09", 3)):
        backend.register_server(name, "id09-srv-02", level=level, controlled=True, running=True)
    backend.register_server("IcePAP/id09", "id09-srv-02", level=2, controlled=True, running=True)
    backend.register_server("Vacuum/id09-front", "id09-srv-02", level=2, controlled=True)
    backend.register_server(
        "LimaCCDs/pilatus", "id09-srv-02", level=2, controlled=True, running=True
    )
    backend.register_server("TangoTest/test", "id09-srv-02", level=3, controlled=True, running=True)

    backend.register_device("sys/database/2", "DataBase", "Databaseds/2")
    backend.register_device("sys/access_control/1", "TangoAccessControl", "TangoAccessControl/1")
    backend.register_device("id09/alarm/main", "PyAlarm", "PyAlarm/id09")
    backend.register_device("id09/icepap/ctrl", "IcePAPController", "IcePAP/id09")
    backend.register_device("id09/motor/phi", "IcePAPMotor", "IcePAP/id09", alias="phi")
    backend.register_device("id09/motor/theta", "IcePAPMotor", "IcePAP/id09", alias="theta")
    backend.register_device("id09/vac/gauge-1", "VacuumGauge", "Vacuum/id09-front")
    backend.register_device("id09/det/pilatus", "LimaCCDs", "LimaCCDs/pilatus")
    backend.register_device("sys/tg_test/1", "TangoTest", "TangoTest/test")

    _build_tango_test(backend, DeviceName.parse("sys/tg_test/1"))
    _build_motor(backend, DeviceName.parse("id09/motor/phi"), position=41.233)
    _build_motor(backend, DeviceName.parse("id09/motor/theta"), position=-12.5)

    backend.set_properties(
        PropertyScope.DEVICE,
        "id09/motor/phi",
        {
            "AxisNumber": ["3"],
            "Velocity": ["2.5"],
            "polled_attr": ["position", "200", "state", "1000"],
            "__SubDevices": ["id09/icepap/ctrl"],
        },
    )
    backend.set_properties(
        PropertyScope.DEVICE, "id09/motor/theta", {"AxisNumber": ["4"], "Velocity": ["1.0"]}
    )
    backend.set_properties(
        PropertyScope.CLASS,
        "IcePAPMotor",
        {"EncoderType": ["ABSOLUTE"], "Velocity": ["1.0"], "Description": ["IcePAP axis"]},
    )
    backend.set_properties(
        PropertyScope.FREE,
        "Milonga",
        {"HostGroups": [f"{BEAMLINE}:id09-*", f"{ACCELERATOR}:sr-*"]},
    )

    backend.set_host_reachable("id09-vac-01", False)

    for index, name in enumerate(("Undulator/id09", "Frontend/id09"), start=1):
        backend.register_server(name, "id09-vac-01", level=index, controlled=True)
    for index in range(1, 5):
        backend.register_server(
            f"Camera/det-{index}", "id09-det-01", level=1, controlled=True, running=True
        )
    for index in range(1, 8):
        backend.register_server(
            f"Beamline/srv-{index}",
            "id09-srv-01",
            level=1 + index % 3,
            controlled=True,
            running=True,
        )


def _build_accelerator(backend: FakeBackend) -> None:
    for host in ("sr-ctl-01", "sr-ctl-02"):
        backend.register_host(host, group=ACCELERATOR)
        backend.install_starter(host)
    for index in range(1, 12):
        backend.register_server(
            f"Machine/sr-{index}", "sr-ctl-01", level=1 + index % 5, controlled=True, running=True
        )
    for index in range(1, 9):
        backend.register_server(
            f"Booster/br-{index}",
            "sr-ctl-02",
            level=1 + index % 4,
            controlled=True,
            running=index > 4,
        )


def _build_tango_test(backend: FakeBackend, device: DeviceName) -> None:
    scalars = (
        ("double_scalar", TangoType.DOUBLE, 12.8741, "mm", AttrWriteType.READ_WRITE),
        ("long_scalar", TangoType.LONG, 143, "counts", AttrWriteType.READ_WRITE),
        ("boolean_scalar", TangoType.BOOLEAN, True, "", AttrWriteType.READ_WRITE),
        ("string_scalar", TangoType.STRING, "idle", "", AttrWriteType.READ_WRITE),
        ("throughput", TangoType.DOUBLE, 418.2, "MB/s", AttrWriteType.READ),
    )
    for name, data_type, value, unit, writable in scalars:
        backend.register_attribute(
            device,
            AttributeSpec(
                name,
                data_type,
                AttrDataFormat.SCALAR,
                writable,
                label=name.replace("_", " "),
                unit=unit,
                alarms=AlarmConfig(max_alarm="900") if unit == "MB/s" else AlarmConfig(),
                events=EventConfig(change_abs="0.01"),
            ),
            value,
        )

    index = np.arange(1024, dtype=float)
    spectrum = 2.4 * np.exp(-0.5 * ((index - 512) / 90) ** 2) + 0.05 * np.sin(index / 12)
    backend.register_attribute(
        device,
        AttributeSpec(
            "double_spectrum",
            TangoType.DOUBLE,
            AttrDataFormat.SPECTRUM,
            unit="a.u.",
            max_dim_x=1024,
        ),
        spectrum,
    )

    rows, columns = np.mgrid[0:256, 0:256]
    image = 4096 * np.exp(-0.5 * (((rows - 120) / 21) ** 2 + ((columns - 112) / 17) ** 2))
    backend.register_attribute(
        device,
        AttributeSpec(
            "double_image",
            TangoType.DOUBLE,
            AttrDataFormat.IMAGE,
            unit="ADU",
            max_dim_x=256,
            max_dim_y=256,
            display_level=DisplayLevel.EXPERT,
        ),
        image,
    )

    backend.register_command(device, CommandSpec("DevVoid"))
    backend.register_command(
        device,
        CommandSpec("DevDouble", TangoType.DOUBLE, TangoType.DOUBLE, "echoed value"),
        lambda _backend, argin: argin,
    )
    backend.register_command(
        device,
        CommandSpec("DevString", TangoType.STRING, TangoType.STRING),
        lambda _backend, argin: str(argin).upper(),
    )


def _build_motor(backend: FakeBackend, device: DeviceName, *, position: float) -> None:
    backend.register_attribute(
        device,
        AttributeSpec(
            "position",
            TangoType.DOUBLE,
            AttrDataFormat.SCALAR,
            AttrWriteType.READ_WRITE,
            label="Position",
            unit="deg",
            min_value="-180",
            max_value="180",
            alarms=AlarmConfig(min_alarm="-179", max_alarm="179"),
        ),
        position,
    )
    backend.register_attribute(
        device,
        AttributeSpec(
            "velocity",
            TangoType.DOUBLE,
            AttrDataFormat.SCALAR,
            AttrWriteType.READ_WRITE,
            unit="deg/s",
        ),
        2.5,
    )
    backend.register_command(device, CommandSpec("Stop"))
    backend.register_command(device, CommandSpec("MoveRelative", TangoType.DOUBLE, TangoType.VOID))


DEMO_STARTER_HOSTS: tuple[str, ...] = (
    "id09-srv-01",
    "id09-srv-02",
    "id09-det-01",
    "id09-vac-01",
    "sr-ctl-01",
    "sr-ctl-02",
)

DEMO_SERVER: ServerName = ServerName("TangoTest", "test")
DEMO_DEVICE: DeviceName = DeviceName("sys", "tg_test", "1")
