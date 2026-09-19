from datetime import datetime, timedelta

from milonga.core.backend.fake import FakeBackend
from milonga.core.names import ServerName
from milonga.core.services.diagnostics import Diagnostics, format_uptime

RUNNING = ServerName.parse("TangoTest/test")
STOPPED = ServerName.parse("Vacuum/id09-front")


async def test_a_running_server_reports_pid_and_version(backend: FakeBackend) -> None:
    detail = await Diagnostics(backend).server_detail(RUNNING)
    assert detail.running
    assert detail.pid is not None
    assert detail.started_at is not None
    assert detail.version is not None and detail.version.idl_version == 5
    assert detail.uptime(datetime.now()) is not None


async def test_a_stopped_server_reports_nothing_live(backend: FakeBackend) -> None:
    detail = await Diagnostics(backend).server_detail(STOPPED)
    assert not detail.running
    assert detail.pid is None
    assert detail.version is None
    assert detail.uptime() is None


async def test_an_unknown_server_carries_the_error(backend: FakeBackend) -> None:
    detail = await Diagnostics(backend).server_detail(ServerName("Nope", "1"))
    assert detail.error is not None
    assert not detail.running


async def test_details_load_concurrently(backend: FakeBackend) -> None:
    servers = await backend.get_host_server_list("id09-srv-02")
    details = await Diagnostics(backend).server_details(servers)
    assert len(details) == len(servers)
    assert any(detail.running for detail in details)


def test_uptime_reads_in_human_units() -> None:
    assert format_uptime(None) == "—"
    assert format_uptime(timedelta(seconds=45)) == "45 s"
    assert format_uptime(timedelta(minutes=5)) == "5 min"
    assert format_uptime(timedelta(hours=3, minutes=20)) == "3 h 20 min"
    assert format_uptime(timedelta(days=2, hours=5)) == "2 d 5 h"
