import pytest

from milonga.core.backend.fake import FakeBackend
from milonga.core.enums import HostState, ServerRunState
from milonga.core.names import ServerName
from milonga.core.services.control import StarterControl, build_host_snapshot
from milonga.core.services.starter_protocol import ServerLine

HOST = "id09-srv-02"
VACUUM = ServerName.parse("Vacuum/id09-front")
TEST = ServerName.parse("TangoTest/test")


async def test_snapshot_reports_mixed_state_and_levels(control: StarterControl) -> None:
    snapshot = await control.host_snapshot(HOST, group="Beamline")
    assert snapshot.state is HostState.MIXED
    assert snapshot.group == "Beamline"
    assert snapshot.levels == (1, 2, 3)
    assert snapshot.stopped_count == 1


async def test_snapshot_is_ordered_by_startup_level(control: StarterControl) -> None:
    snapshot = await control.host_snapshot(HOST)
    levels = [server.info.level for server in snapshot.servers]
    assert levels == sorted(levels)


async def test_starting_a_server_makes_the_host_nominal(control: StarterControl) -> None:
    await control.start_server(HOST, VACUUM)
    assert (await control.host_snapshot(HOST)).state is HostState.ALL_RUNNING


async def test_stopping_and_restarting_a_server(
    control: StarterControl, backend: FakeBackend
) -> None:
    await control.stop_server(HOST, TEST)
    assert backend.servers[TEST].run_state is ServerRunState.STOPPED
    await control.restart_server(HOST, TEST)
    assert backend.servers[TEST].run_state is ServerRunState.RUNNING


async def test_level_commands_act_on_one_level_only(control: StarterControl) -> None:
    await control.stop_level(HOST, 2)
    snapshot = await control.host_snapshot(HOST)
    by_level = {server.name: server for server in snapshot.servers}
    assert by_level[ServerName.parse("IcePAP/id09")].run_state is ServerRunState.STOPPED
    assert by_level[TEST].run_state is ServerRunState.RUNNING


async def test_hard_kill_and_log(control: StarterControl) -> None:
    await control.hard_kill_server(HOST, TEST)
    assert "killed" in await control.read_log(HOST, TEST)


async def test_running_and_stopped_server_lists(control: StarterControl) -> None:
    running = await control.running_servers(HOST)
    stopped = await control.stopped_servers(HOST)
    assert TEST in running
    assert VACUUM in stopped


async def test_unreachable_host_yields_an_error_snapshot(control: StarterControl) -> None:
    snapshot = await control.host_snapshot("id09-vac-01")
    assert snapshot.state is HostState.UNREACHABLE
    assert snapshot.error is not None
    assert snapshot.servers == ()


async def test_snapshots_load_every_host(control: StarterControl, backend: FakeBackend) -> None:
    hosts = await backend.get_host_list()
    snapshots = await control.host_snapshots(hosts, groups={"id09-srv-02": "Beamline"})
    assert len(snapshots) == len(hosts)
    assert {snapshot.name for snapshot in snapshots} == set(hosts)
    assert any(snapshot.state is HostState.UNREACHABLE for snapshot in snapshots)


async def test_set_server_control_writes_the_database(
    control: StarterControl, backend: FakeBackend
) -> None:
    await control.set_server_control(VACUUM, HOST, level=4)
    assert (await backend.get_server_info(VACUUM)).level == 4
    snapshot = await control.host_snapshot(HOST)
    assert {server.name: server.info.level for server in snapshot.servers}[VACUUM] == 4


async def test_a_new_host_in_the_record_does_not_move_the_server(
    control: StarterControl, backend: FakeBackend
) -> None:
    await control.set_server_control(TEST, "id09-srv-01", level=2)
    assert (await backend.get_server_info(TEST)).host == "id09-srv-01"
    still_here = {server.name for server in (await control.host_snapshot(HOST)).servers}
    assert TEST in still_here
    elsewhere = {server.name for server in (await control.host_snapshot("id09-srv-01")).servers}
    assert TEST not in elsewhere


async def test_starting_a_server_runs_it_on_the_starters_host(
    control: StarterControl, backend: FakeBackend
) -> None:
    await control.stop_server(HOST, TEST)
    await control.start_server("id09-srv-01", TEST)
    assert TEST in {server.name for server in (await control.host_snapshot("id09-srv-01")).servers}


async def test_a_missing_log_reads_as_empty(control: StarterControl) -> None:
    assert await control.read_log(HOST, TEST) == ""


def test_uncontrolled_servers_do_not_decide_the_host_state() -> None:
    lines = [
        ServerLine(ServerName("A", "1"), ServerRunState.RUNNING, True, 1),
        ServerLine(ServerName("B", "1"), ServerRunState.STOPPED, False, 0),
    ]
    assert build_host_snapshot("h", lines).state is HostState.ALL_RUNNING


def test_database_only_servers_are_kept_as_uncontrolled() -> None:
    snapshot = build_host_snapshot(
        "h",
        [ServerLine(ServerName("A", "1"), ServerRunState.RUNNING, True, 1)],
        known_servers=[ServerName("B", "1")],
    )
    assert len(snapshot.servers) == 2
    assert snapshot.servers[0].name == ServerName("B", "1")
    assert snapshot.servers[0].info.is_controlled is False


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        ([ServerRunState.RUNNING, ServerRunState.RUNNING], HostState.ALL_RUNNING),
        ([ServerRunState.STOPPED, ServerRunState.STOPPED], HostState.ALL_STOPPED),
        ([ServerRunState.RUNNING, ServerRunState.STOPPED], HostState.MIXED),
        ([ServerRunState.RUNNING, ServerRunState.STARTING], HostState.STARTING),
    ],
)
def test_host_state_aggregation(states: list[ServerRunState], expected: HostState) -> None:
    lines = [
        ServerLine(ServerName("S", str(index)), state, True, 1)
        for index, state in enumerate(states)
    ]
    assert build_host_snapshot("h", lines).state is expected


async def test_start_all_walks_the_levels_in_order(
    control: StarterControl, backend: FakeBackend
) -> None:
    await control.stop_all(await control.host_snapshot(HOST))
    stopped = await control.host_snapshot(HOST)
    assert stopped.state is HostState.ALL_STOPPED
    await control.start_all(stopped)
    assert (await control.host_snapshot(HOST)).state is HostState.ALL_RUNNING


def test_a_starter_with_nothing_to_control_is_idle() -> None:
    uncontrolled = [ServerLine(ServerName("A", "1"), ServerRunState.STOPPED, False, 0)]
    assert build_host_snapshot("h", uncontrolled).state is HostState.IDLE
    assert build_host_snapshot("h", []).state is HostState.IDLE
