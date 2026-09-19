"""Column definitions shared by the read-only tables."""

from datetime import datetime

from milonga.core.enums import StateCategory
from milonga.core.model import (
    AttributeSpec,
    CommandSpec,
    DeviceSnapshot,
    PollingEntry,
    ServerSnapshot,
)
from milonga.core.services.diagnostics import ServerDetail, format_uptime
from milonga.ui.models.tables import Column
from milonga.ui.theme import run_state_category


def device_columns() -> list[Column[DeviceSnapshot]]:
    return [
        Column("Device", lambda row: str(row.name), mono=True, stretch=2),
        Column("Class", lambda row: row.info.class_name),
        Column(
            "Exported",
            lambda row: "exported" if row.info.exported else "not exported",
            category=lambda row: (
                StateCategory.NOMINAL if row.info.exported else StateCategory.INACTIVE
            ),
        ),
        Column("PID", lambda row: str(row.info.pid or "—"), align_right=True),
        Column("Alias", lambda row: row.info.alias or "—"),
    ]


def attribute_columns() -> list[Column[AttributeSpec]]:
    return [
        Column("Attribute", lambda spec: spec.name, mono=True, stretch=2),
        Column("Type", lambda spec: spec.data_type.value),
        Column("Format", lambda spec: spec.data_format.value),
        Column("Access", lambda spec: spec.writable.value),
        Column("Unit", lambda spec: spec.unit or "—"),
        Column("Label", lambda spec: spec.label or "—", tooltip=lambda spec: spec.description),
        Column("Level", lambda spec: spec.display_level.value),
    ]


def command_columns() -> list[Column[CommandSpec]]:
    return [
        Column("Command", lambda spec: spec.name, mono=True, stretch=2),
        Column("Argin", lambda spec: spec.in_type.value),
        Column("Argout", lambda spec: spec.out_type.value),
        Column(
            "Description",
            lambda spec: spec.in_description or spec.out_description or "—",
            stretch=2,
        ),
    ]


def server_columns() -> list[Column[ServerSnapshot]]:
    return [
        Column("Server", lambda row: str(row.name), mono=True, stretch=2),
        Column(
            "State",
            lambda row: row.run_state.value,
            category=lambda row: run_state_category(row.run_state),
        ),
        Column(
            "Level",
            lambda row: str(row.info.level) if row.info.level else "—",
            align_right=True,
        ),
        Column("Controlled", lambda row: "yes" if row.info.controlled else "no"),
    ]


def polling_columns() -> list[Column[PollingEntry]]:
    return [
        Column("Name", lambda row: row.name, mono=True, stretch=2),
        Column("Kind", lambda row: row.kind.value),
        Column("Period", lambda row: f"{row.period_ms} ms", align_right=True),
        Column(
            "Polled",
            lambda row: "yes" if row.polled else "no",
            category=lambda row: (
                StateCategory.NOMINAL if row.polled else StateCategory.INACTIVE
            ),
        ),
    ]


def detail_columns() -> list[Column[ServerDetail]]:
    return [
        Column("Server", lambda row: str(row.name), mono=True, stretch=2),
        Column(
            "State",
            lambda row: "running" if row.running else "stopped",
            category=lambda row: (
                StateCategory.NOMINAL if row.running else StateCategory.FAULT
            ),
        ),
        Column("PID", lambda row: str(row.pid or "—"), align_right=True),
        Column("Started", lambda row: _stamp(row.started_at)),
        Column("Uptime", lambda row: format_uptime(row.uptime()), align_right=True),
        Column("IDL", lambda row: str(row.version.idl_version) if row.version else "—"),
        Column("Release", lambda row: row.version.tango_release if row.version else "—"),
    ]


def _stamp(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"
