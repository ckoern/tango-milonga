"""Starting and stopping a real server through its Starter.

This acts on a real process, so it runs only when MILONGA_STARTER_SERVER names
the server to use (for example ``TangoTest/test``). The server is left in the
run state it was found in.
"""

import asyncio
import os

import pytest

from milonga.core.backend.pytango_backend import PyTangoBackend
from milonga.core.enums import ServerRunState
from milonga.core.names import ServerName
from milonga.core.services.control import StarterControl

pytestmark = pytest.mark.integration

SETTLE = 20.0


async def test_a_server_stops_starts_and_restarts_through_its_starter(
    tango_backend: PyTangoBackend,
) -> None:
    name = os.environ.get("MILONGA_STARTER_SERVER")
    if not name:
        pytest.skip("set MILONGA_STARTER_SERVER to the server this test may start and stop")
    server = ServerName.parse(name)
    control = StarterControl(tango_backend)
    hosts = await control.controlled_hosts()
    if not hosts:
        pytest.skip("no Starter in this control system")
    host = hosts[0]
    was_running = await control.is_running(server)

    try:
        if was_running:
            await control.stop_server(host, server)
            assert await control.wait_until(server, running=False, timeout=SETTLE)
        stopped = await control.probe_server(host, server)
        assert stopped.run_state is ServerRunState.STOPPED

        await control.start_and_confirm(host, server)
        await control.wait_for_report(host, server, ServerRunState.RUNNING, timeout=SETTLE)
        snapshot = await control.host_snapshot(host)
        (entry,) = [item for item in snapshot.servers if item.name == server]
        assert entry.run_state is ServerRunState.RUNNING
        first_pid = (await control.probe_server(host, server)).pid
        assert first_pid

        await control.restart_server(host, server)
        restarted = await control.probe_server(host, server)
        assert restarted.run_state is ServerRunState.RUNNING
        assert restarted.pid != first_pid
    finally:
        now_running = await control.is_running(server)
        if was_running and not now_running:
            await control.start_and_confirm(host, server)
        elif not was_running and now_running:
            await control.stop_server(host, server)


async def test_a_controlled_server_restarts_on_the_starters_word(
    tango_backend: PyTangoBackend,
) -> None:
    """The Starter reports when a controlled server's process has gone, so a
    restart waits for that instead of a measured grace."""
    name = os.environ.get("MILONGA_STARTER_SERVER")
    if not name:
        pytest.skip("set MILONGA_STARTER_SERVER to the server this test may start and stop")
    from tests.integration.conftest import _delete_record

    server = ServerName.parse(name)
    control = StarterControl(tango_backend, exit_grace=60.0)
    (host,) = (await control.controlled_hosts())[:1]
    record = await tango_backend.get_server_info(server)
    had_record = bool(record.host or record.level or record.controlled)
    was_running = await control.is_running(server)
    if not was_running:
        await control.start_and_confirm(host, server)
    try:
        await control.set_server_control(server, host, level=1)
        await control.wait_for_report(host, server, ServerRunState.RUNNING, timeout=SETTLE)
        snapshot = await control.host_snapshot(host)
        (entry,) = [item for item in snapshot.servers if item.name == server]
        assert entry.info.controlled and entry.info.level == 1
        assert entry.run_state is ServerRunState.RUNNING

        began = asyncio.get_running_loop().time()
        await control.restart_server(host, server)
        took = asyncio.get_running_loop().time() - began
        assert await control.is_running(server)
        assert took < 60.0, "waited on the grace, not on the Starter"
        await control.wait_for_report(host, server, ServerRunState.RUNNING, timeout=SETTLE)
    finally:
        if had_record:
            await tango_backend.put_server_info(record)
        else:
            await asyncio.to_thread(_delete_record, str(server))
        await control.update_servers_info(host)
        if not was_running and await control.is_running(server):
            await control.stop_server(host, server)
