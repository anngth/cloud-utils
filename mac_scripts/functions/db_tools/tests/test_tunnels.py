from __future__ import annotations

import io
import signal
from dataclasses import dataclass, field
from datetime import datetime

import pytest
from shared.process import CommandResult

from db_tools.cli import run_cli
from db_tools.runtime import Runtime, SignalHooks
from db_tools.secrets import parse_secrets_text
from db_tools.tests.secrets_toml import toml_secrets, write_secrets
from db_tools.tunnels import connection_hint

ROWS = (
    "a|me|10.0.0.1|postgres|5432|db.internal|5432",
    "a|me|10.0.0.1|mongo|27017|db.internal|27017",
    "b|me|10.0.0.2|redis|6379|db.internal|6379",
)
REPLICA_ROW = "a|me|10.0.0.1|postgres-replica|5433|db.internal|5432"
TUNNELS = parse_secrets_text(toml_secrets(tunnels=ROWS)).tunnels

_PREFLIGHT = [
    "ssh",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=accept-new",
]


@dataclass
class FakeTunnels:
    """Records argv and answers lsof/ps/ssh without spawning anything."""

    listeners: dict[int, str] = field(default_factory=dict)
    commands: dict[int, str] = field(default_factory=dict)
    etimes: dict[int, str] = field(default_factory=dict)
    preflight_code: int = 0
    signal_result: bool = True
    die_on: frozenset[int] = field(
        default_factory=lambda: frozenset({signal.SIGTERM, signal.SIGKILL})
    )
    calls: list[list[str]] = field(default_factory=list)
    signals_sent: list[tuple[int, int]] = field(default_factory=list)
    slept: list[float] = field(default_factory=list)

    def run_command(self, argv, **_kwargs):
        self.calls.append(list(argv))
        head = argv[0]
        if head == "lsof":
            assert argv == ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"]
            lines = [f"p{pid}\nn*:{port}" for port, pid in self.listeners.items() if pid]
            return CommandResult(0, "\n".join(lines) + "\n", "")
        if head == "ps":
            pid = int(argv[2])
            option = argv[4]
            if option == "command=":
                return CommandResult(0, self.commands.get(pid, ""), "")
            if option == "etime=":
                return CommandResult(0, self.etimes.get(pid, ""), "")
            raise AssertionError(f"unexpected ps option: {argv}")
        if head == "ssh":
            if "BatchMode=yes" in argv:
                return CommandResult(self.preflight_code, "", "")
            return CommandResult(0, "", "")
        raise AssertionError(f"unexpected command: {argv}")

    def run_pipe(self, _left, _right, **_kwargs):
        return CommandResult(0, "", "")

    def process_alive(self, pid: int) -> bool:
        killed = {p for p, signum in self.signals_sent if signum in self.die_on}
        return pid not in killed

    def send_signal(self, pid: int, signum: int) -> bool:
        self.signals_sent.append((pid, signum))
        return self.signal_result

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def preflights(self) -> list[list[str]]:
        return [argv for argv in self.calls if "BatchMode=yes" in argv]

    def tunnel_opens(self) -> list[list[str]]:
        return [argv for argv in self.calls if argv and argv[0] == "ssh" and "-f" in argv]

    def forwards(self, argv: list[str]) -> list[str]:
        return [argv[i + 1] for i, token in enumerate(argv) if token == "-L"]


class _StubSignals(SignalHooks):
    def bind(self, handler) -> None:
        self.handler = handler  # tests must not touch real signal handlers


def _runtime(fake: FakeTunnels) -> Runtime:
    return Runtime(
        run_command=fake.run_command,
        run_pipe=fake.run_pipe,
        which=lambda name: f"/usr/bin/{name}",
        path_exists=lambda _path: True,
        process_alive=fake.process_alive,
        signal=fake.send_signal,
        sleep=fake.sleep,
        now=datetime.now,
        signals=_StubSignals(),
    )


def _drive(tmp_path, rows, argv, **fake_args):
    path = write_secrets(tmp_path / "secrets.toml", tunnels=rows)
    fake = FakeTunnels(**fake_args)
    out = io.StringIO()
    err = io.StringIO()
    code = run_cli(
        argv,
        env={
            "HOME": str(tmp_path / "home"),
            "CLOUD_UTILS_CONFIG_DIR": str(tmp_path / "config"),
            "DBT_SECRETS_FILE": str(path),
        },
        stdout=out,
        stderr=err,
        dependencies=_runtime(fake),
    )
    return code, out.getvalue(), err.getvalue(), fake


# --- connect -----------------------------------------------------------------


def test_connect_groups_by_host_and_orders_forwards(tmp_path):
    code, _out, err, fake = _drive(tmp_path, ROWS, ["connect"])

    assert code == 0
    assert err == ""
    assert fake.preflights() == [
        [*_PREFLIGHT, "me@10.0.0.1", "exit"],
        [*_PREFLIGHT, "me@10.0.0.2", "exit"],
    ]
    opens = fake.tunnel_opens()
    assert len(opens) == 2
    first, second = opens
    assert first[:9] == [
        "ssh",
        "-f",
        "-N",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=60",
        "-o",
        "ServerAliveCountMax=3",
    ]
    assert fake.forwards(first) == ["5432:db.internal:5432", "27017:db.internal:27017"]
    assert first[-1] == "me@10.0.0.1"
    assert fake.forwards(second) == ["6379:db.internal:6379"]
    assert second[-1] == "me@10.0.0.2"
    assert all(argv[0] != "timeout" for argv in fake.calls)


def test_connect_failed_preflight_still_opens(tmp_path):
    code, _out, err, fake = _drive(tmp_path, ROWS, ["connect"], preflight_code=1)

    assert code == 0
    assert "preflight" in err
    assert len(fake.tunnel_opens()) == 2


def test_connect_service_filter_opens_one_forward(tmp_path):
    code, _out, _err, fake = _drive(tmp_path, ROWS, ["connect", "postgres"])

    assert code == 0
    assert len(fake.tunnel_opens()) == 1
    argv = fake.tunnel_opens()[0]
    assert fake.forwards(argv) == ["5432:db.internal:5432"]
    assert argv[-1] == "me@10.0.0.1"
    assert fake.preflights() == [[*_PREFLIGHT, "me@10.0.0.1", "exit"]]


def test_connect_port_filter_opens_one_forward(tmp_path):
    code, _out, _err, fake = _drive(tmp_path, ROWS, ["connect", "5432"])

    assert code == 0
    assert len(fake.tunnel_opens()) == 1
    argv = fake.tunnel_opens()[0]
    assert fake.forwards(argv) == ["5432:db.internal:5432"]


def test_connect_prefix_filter_matches_two_services(tmp_path):
    code, _out, _err, fake = _drive(tmp_path, [*ROWS, REPLICA_ROW], ["connect", "post"])

    assert code == 0
    assert len(fake.tunnel_opens()) == 1
    argv = fake.tunnel_opens()[0]
    assert fake.forwards(argv) == ["5432:db.internal:5432", "5433:db.internal:5432"]


def test_connect_port_filter_matches_local_port_only(tmp_path):
    filler = "a|me|10.0.0.1|filler|15432|db.internal|5432"
    code, _out, _err, fake = _drive(tmp_path, [*ROWS, filler], ["connect", "5432"])

    assert code == 0
    assert len(fake.tunnel_opens()) == 1
    argv = fake.tunnel_opens()[0]
    assert fake.forwards(argv) == ["5432:db.internal:5432"]


def test_connect_rejects_a_zero_local_port(tmp_path):
    rows = ("a|me|10.0.0.1|ghost|0|db.internal|5432", ROWS[0])
    code, _out, err, fake = _drive(tmp_path, rows, ["connect"])

    assert code == 1
    assert "tunnels[1] (a): 'local_port' must be between 1 and 65535" in err
    assert fake.tunnel_opens() == []


def test_connect_no_match_exits_one_without_ssh(tmp_path):
    code, _out, err, fake = _drive(tmp_path, ROWS, ["connect", "missing"])

    assert code == 1
    assert err != ""
    assert all(argv[0] != "ssh" for argv in fake.calls)


def test_disconnect_no_match_exits_one(tmp_path):
    code, _out, err, fake = _drive(tmp_path, ROWS, ["disconnect", "missing"])

    assert code == 1
    assert err != ""
    assert fake.signals_sent == []


def test_connect_busy_port_fails_without_ssh(tmp_path):
    code, _out, err, fake = _drive(
        tmp_path,
        ROWS[:1],
        ["connect"],
        listeners={5432: "42"},
        commands={42: "postgres"},
    )

    assert code == 1
    assert "5432" in err
    assert fake.tunnel_opens() == []
    assert all(argv[0] != "ssh" for argv in fake.calls)


def test_connect_our_tunnel_counts_as_already_on(tmp_path):
    code, out, _err, fake = _drive(
        tmp_path,
        ROWS[:1],
        ["connect"],
        listeners={5432: "42"},
        commands={42: "ssh -L 5432:db.internal:5432"},
    )

    assert code == 0
    assert "Already on" in out
    assert all(argv[0] != "ssh" for argv in fake.calls)


def test_connect_rejects_the_file_when_a_tunnel_entry_is_invalid(tmp_path):
    code, _out, err, fake = _drive(tmp_path, ["bad|row", ROWS[0]], ["connect"])

    assert code == 1
    assert "tunnels[1] (bad): missing field 'host'" in err
    assert fake.tunnel_opens() == []


def test_connect_empty_vps_list_exits_zero(tmp_path):
    code, out, _err, fake = _drive(tmp_path, (), ["connect"])

    assert code == 0
    assert out != ""
    assert all(argv[0] != "ssh" for argv in fake.calls)


# --- list / ls ----------------------------------------------------------------


def test_list_shows_rows_without_ssh(tmp_path):
    code, out, _err, fake = _drive(
        tmp_path,
        ROWS,
        ["list"],
        listeners={5432: "42", 27017: "42"},
        commands={42: "ssh -L 5432:db.internal:5432 -L 27017:db.internal:27017"},
        etimes={42: "01:02"},
    )

    assert code == 0
    assert {argv[0] for argv in fake.calls} <= {"lsof", "ps"}
    assert [argv[0] for argv in fake.calls].count("lsof") == 1  # one snapshot
    assert [argv for argv in fake.calls if argv[0] == "ps" and argv[4] == "command="] == [
        ["ps", "-p", "42", "-o", "command="]
    ]  # one ps per pid, not per port
    for token in ("UP", "DOWN", "postgres", "mongo", "5432", "db.internal:5432",
                  "a", "10.0.0.1", "01:02"):
        assert token in out


def test_list_busy_row(tmp_path):
    code, out, _err, _fake = _drive(
        tmp_path,
        ROWS[:1],
        ["ls"],
        listeners={5432: "42"},
        commands={42: "postgres"},
    )

    assert code == 0
    assert "BUSY" in out


def test_list_empty_vps_prints_message(tmp_path):
    code, out, _err, fake = _drive(tmp_path, (), ["ls"])

    assert code == 0
    assert out != ""
    assert all(argv[0] != "ssh" for argv in fake.calls)


def test_list_rejects_unknown_option(tmp_path):
    code, _out, err, _fake = _drive(tmp_path, ROWS, ["list", "--full"])

    assert code == 1
    assert err != ""


def test_list_rejects_the_status_flag(tmp_path):
    code, out, err, fake = _drive(tmp_path, ROWS, ["list", "--status"])

    assert code == 1
    assert out == ""
    assert fake.calls == []
    assert "--status" in err


def test_status_shows_tunnels_then_sync_entries(tmp_path):
    code, out, err, fake = _drive(
        tmp_path,
        ROWS,
        ["status"],
        listeners={5432: "42"},
        commands={42: "ssh -L 5432:db.internal:5432"},
        etimes={42: "01:02"},
    )

    assert (code, err) == (0, "")
    tunnels, _blank, entries = out.partition("\n\n")
    assert tunnels.splitlines() == [
        "🔀 Tunnels (3):",
        "  🟢 1) a: postgres | :5432 -> db.internal:5432  me@10.0.0.1 | UP 01:02",
        "  🔴 2) a: mongo | :27017 -> db.internal:27017  me@10.0.0.1 | DOWN (ssh ok)",
        "  🔴 3) b: redis | :6379 -> db.internal:6379  me@10.0.0.2 | DOWN (ssh ok)",
    ]
    # only DOWN rows get the SSH preflight, once per host; the UP row is skipped
    assert fake.preflights() == [
        [*_PREFLIGHT, "me@10.0.0.1", "exit"],
        [*_PREFLIGHT, "me@10.0.0.2", "exit"],
    ]
    assert fake.tunnel_opens() == []
    assert entries.startswith("🐘 PostgreSQL Sources (0):\n  (none)\n")
    assert "🎯 MongoDB Targets (0):" in entries
    assert "Probing" not in out  # nothing to probe, so no waiting notice


def test_status_reports_a_failed_ssh_login_once_per_host(tmp_path):
    code, out, _err, fake = _drive(
        tmp_path, [*ROWS, REPLICA_ROW], ["status"], preflight_code=1
    )

    assert code == 0
    rows = out.partition("\n\n")[0].splitlines()[1:]
    assert all(row.endswith("| DOWN (ssh failed)") for row in rows) and len(rows) == 4
    assert fake.preflights() == [
        [*_PREFLIGHT, "me@10.0.0.1", "exit"],
        [*_PREFLIGHT, "me@10.0.0.2", "exit"],
    ]


def test_list_never_runs_ssh(tmp_path):
    _code, out, _err, fake = _drive(tmp_path, ROWS, ["list"])

    assert all(argv[0] != "ssh" for argv in fake.calls)
    assert "ssh" not in out
    assert "| DOWN\n" in out


def test_listening_pids_parses_lsof_field_output():
    from db_tools.tunnels import _listening_pids

    class R:
        def run_command(self, argv, **_kwargs):
            return CommandResult(
                0,
                "p42\nf5\nn*:5432\nf6\nn[::1]:5432\np77\nn127.0.0.1:27017\nnjunk\n",
                "",
            )

    assert _listening_pids(R()) == {5432: 42, 27017: 77}


# --- test ---------------------------------------------------------------------


def test_disconnect_kills_shared_pid_once(tmp_path):
    code, _out, _err, fake = _drive(
        tmp_path,
        [*ROWS, REPLICA_ROW],
        ["disconnect", "postgres"],
        listeners={5432: "42", 5433: "42"},
        commands={42: "ssh -L 5432:db.internal:5432 -L 5433:db.internal:5432"},
    )

    assert code == 0
    assert fake.signals_sent == [(42, signal.SIGTERM)]
    assert fake.slept == [0.5]


def test_disconnect_escalates_to_sigkill(tmp_path):
    code, out, _err, fake = _drive(
        tmp_path,
        ROWS[:1],
        ["disconnect"],
        listeners={5432: "42"},
        commands={42: "ssh -L 5432:db.internal:5432"},
        die_on=frozenset({signal.SIGKILL}),
    )

    assert code == 0
    assert fake.signals_sent == [(42, signal.SIGTERM), (42, signal.SIGKILL)]
    assert fake.slept == [0.5, 0.5]
    assert "forced" in out


def test_disconnect_signal_failure_exits_one(tmp_path):
    code, _out, err, fake = _drive(
        tmp_path,
        ROWS[:1],
        ["disconnect"],
        listeners={5432: "42"},
        commands={42: "ssh -L 5432:db.internal:5432"},
        signal_result=False,
    )

    assert code == 1
    assert err != ""
    assert fake.signals_sent == [(42, signal.SIGTERM)]
    assert fake.slept == []


def test_disconnect_warns_for_non_ssh_listener_and_off_row(tmp_path):
    code, out, err, fake = _drive(
        tmp_path,
        ROWS[:2],
        ["disconnect"],
        listeners={5432: "42"},
        commands={42: "postgres"},
    )

    assert code == 0
    assert fake.signals_sent == []
    assert "non-SSH" in err
    assert "Already off" in out


def test_disconnect_off_when_port_not_listening(tmp_path):
    code, out, _err, fake = _drive(tmp_path, ROWS[:1], ["disconnect"])

    assert code == 0
    assert "Already off" in out
    assert fake.signals_sent == []


# --- unit helpers -------------------------------------------------------------


def test_is_our_tunnel():
    from db_tools.tunnels import is_our_tunnel

    assert is_our_tunnel("ssh -L 5432:db:5432", 5432) is True
    assert is_our_tunnel("/usr/bin/ssh -f -N -L 5432:db.internal:5432 me@host", 5432) is True
    assert is_our_tunnel("ssh -L5432:db:5432 me@host", 5432) is True  # glued form
    assert is_our_tunnel("ssh -L 127.0.0.1:5432:db:5432 me@host", 5432) is True  # bind addr
    assert is_our_tunnel("ssh -L 27017:db:27017 -L 5432:db:5432 me@host", 5432) is True
    assert is_our_tunnel("psql -L 5432:db:5432", 5432) is False
    assert is_our_tunnel("ssh -N me@host", 5432) is False
    assert is_our_tunnel("", 5432) is False


def test_is_our_tunnel_matches_the_local_port_exactly() -> None:
    from db_tools.tunnels import is_our_tunnel

    assert is_our_tunnel("ssh -L 15432:db:15432 me@host", 5432) is False
    assert is_our_tunnel("ssh -L 54321:db:5432 me@host", 5432) is False  # remote port
    assert is_our_tunnel("ssh -L 5433:db:5432 me@host", 5432) is False
    assert is_our_tunnel("ssh -L", 5432) is False
    assert is_our_tunnel("ssh -L garbage me@host", 5432) is False


# --- connection hints ---------------------------------------------------------


def test_hint_names_the_down_tunnel_and_the_connect_command() -> None:
    fake = FakeTunnels()
    text = connection_hint(TUNNELS, "localhost", 27017, runtime=_runtime(fake))
    assert text == "tunnel mongo (a) is DOWN; run: dbt connect mongo"


def test_hint_blames_the_remote_side_when_our_tunnel_is_up() -> None:
    fake = FakeTunnels(
        listeners={27017: "77"}, commands={77: "ssh -f -N -L 27017:db.internal:27017 me@10.0.0.1"}
    )
    text = connection_hint(TUNNELS, "127.0.0.1", 27017, runtime=_runtime(fake))
    assert text.startswith(
        "tunnel mongo (a) is UP, so 10.0.0.1 refused the forward to db.internal:27017"
    )


def test_hint_reports_a_foreign_listener() -> None:
    fake = FakeTunnels(listeners={27017: "88"}, commands={88: "/usr/local/bin/mongod --port 27017"})
    text = connection_hint(TUNNELS, "localhost", 27017, runtime=_runtime(fake))
    assert "used by another process" in text
    assert "mongod --port 27017" in text


@pytest.mark.parametrize(("host", "port"), [("db.example", 27017), ("localhost", 9999)])
def test_hint_is_empty_for_remote_hosts_and_ports_without_a_row(host: str, port: int) -> None:
    fake = FakeTunnels()
    assert connection_hint(TUNNELS, host, port, runtime=_runtime(fake)) == ""
    assert fake.calls == []  # nothing is probed when no tunnel row applies
