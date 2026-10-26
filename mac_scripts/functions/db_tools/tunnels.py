"""SSH tunnels for dbt: connect, disconnect, list, and test.

Every external command is an argv list; no shell and no `timeout` binary.
Port liveness is checked per port with `lsof` and `ps`, never by a callback.
"""

from __future__ import annotations

import re
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO

from .models import VpsTunnel
from .runtime import Runtime

_SSH_TIMEOUT = 10
_MIN_PORT = 1
_MAX_PORT = 65535
_PORT_TEXT = re.compile(r"\d+")


def parse_vps_row(value: str) -> VpsTunnel | None:
    """Parse one `name|user|host|service|local|target_host|target` row.

    Returns None when the row has anything other than seven fields, a
    non-integer or out-of-range port, or a missing required field.
    """
    fields = value.split("|")
    if len(fields) != 7:
        return None
    name, user, host, service, local_text, target_host, target_text = fields
    local_port = _parse_port(local_text)
    target_port = _parse_port(target_text)
    if local_port is None or target_port is None:
        return None
    if not name or not user or not host or not service or not target_host:
        return None
    return VpsTunnel(
        name=name,
        user=user,
        host=host,
        service=service,
        local_port=local_port,
        target_host=target_host,
        target_port=target_port,
    )


def _parse_port(text: str) -> int | None:
    if _PORT_TEXT.fullmatch(text) is None:
        return None
    port = int(text)
    if not _MIN_PORT <= port <= _MAX_PORT:
        return None
    return port


def is_our_tunnel(command: str, port: int) -> bool:
    """True when an `ssh` command has a `-L` forward whose local port is `port`."""
    tokens = command.split()
    if not tokens or tokens[0].rsplit("/", 1)[-1] != "ssh":
        return False
    for index, token in enumerate(tokens[1:], start=1):
        if token == "-L":
            if index + 1 >= len(tokens):
                return False
            spec = tokens[index + 1]
        elif token.startswith("-L"):
            spec = token[2:]
        else:
            continue
        if _forward_local_port(spec) == port:
            return True
    return False


def _forward_local_port(spec: str) -> int | None:
    """Local port of a `[bind:]local:host:remote` forward spec, or None."""
    parts = spec.split(":")
    if len(parts) == 3:
        local = parts[0]
    elif len(parts) == 4:
        local = parts[1]
    else:
        return None
    return int(local) if local.isdigit() else None


@dataclass(frozen=True, slots=True)
class _PortState:
    """What currently listens on one local port."""

    listens: bool
    ours: bool
    pid: int | None = None
    command: str = ""


def _matches_service(tunnel: VpsTunnel, service: str) -> bool:
    return service in tunnel.service or service == str(tunnel.local_port)


def _select_rows(
    rows: Sequence[str], service: str | None, stderr: TextIO
) -> list[VpsTunnel]:
    """Parse rows, warn on invalid ones, and filter by service if given."""
    tunnels: list[VpsTunnel] = []
    for row in rows:
        tunnel = parse_vps_row(row)
        if tunnel is None:
            stderr.write(f"skipped invalid row: {row}\n")
        elif service is None or _matches_service(tunnel, service):
            tunnels.append(tunnel)
    return tunnels


def _no_match(service: str, stderr: TextIO) -> int:
    stderr.write(f"No service found matching: {service}\n")
    return 1


def _nothing_configured(stdout: TextIO) -> int:
    stdout.write("No VPS tunnel rows configured\n")
    return 0


class _Listeners:
    """One `lsof` snapshot of every listening TCP port, plus `ps` per pid."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._pids = _listening_pids(runtime)
        self._commands: dict[int, str] = {}

    def state(self, port: int) -> _PortState:
        pid = self._pids.get(port)
        if pid is None:
            return _PortState(listens=False, ours=False)
        if pid not in self._commands:
            result = self._runtime.run_command(["ps", "-p", str(pid), "-o", "command="])
            self._commands[pid] = result.stdout.strip()
        command = self._commands[pid]
        return _PortState(listens=True, ours=is_our_tunnel(command, port), pid=pid, command=command)


def _listening_pids(runtime: Runtime) -> dict[int, int]:
    """Parse `lsof -F pn` output (`p<pid>` then `n<addr>:<port>` lines)."""
    result = runtime.run_command(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpn"])
    pids: dict[int, int] = {}
    pid: int | None = None
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            port_text = line.rsplit(":", 1)[-1]
            if port_text.isdigit():
                pids.setdefault(int(port_text), pid)
    return pids


def _forward(tunnel: VpsTunnel) -> str:
    return f"{tunnel.local_port}:{tunnel.target_host}:{tunnel.target_port}"


def _service_line(tunnel: VpsTunnel) -> str:
    return f"{tunnel.service}: local :{tunnel.local_port} -> remote :{tunnel.target_port}"


def _preflight_argv(user: str, host: str) -> list[str]:
    return [
        "ssh",
        "-o",
        f"ConnectTimeout={_SSH_TIMEOUT}",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        f"{user}@{host}",
        "exit",
    ]


def _tunnel_argv(tunnels: Sequence[VpsTunnel]) -> list[str]:
    first = tunnels[0]
    argv = [
        "ssh",
        "-f",
        "-N",
        "-o",
        f"ConnectTimeout={_SSH_TIMEOUT}",
        "-o",
        "ServerAliveInterval=60",
        "-o",
        "ServerAliveCountMax=3",
    ]
    for tunnel in tunnels:
        argv += ["-L", _forward(tunnel)]
    argv.append(f"{first.user}@{first.host}")
    return argv


def connect_tunnels(
    rows: Sequence[str],
    *,
    service: str | None,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Open tunnels for unmatched ports, grouped per (name, user, host)."""
    tunnels = _select_rows(rows, service, stderr)
    if service is not None and not tunnels:
        return _no_match(service, stderr)
    if not tunnels:
        return _nothing_configured(stdout)

    pending: dict[tuple[str, str, str], list[VpsTunnel]] = {}
    already: dict[tuple[str, str, str], list[VpsTunnel]] = {}
    failed = 0
    listeners = _Listeners(runtime)
    for tunnel in tunnels:
        state = listeners.state(tunnel.local_port)
        key = (tunnel.name, tunnel.user, tunnel.host)
        if not state.listens:
            pending.setdefault(key, []).append(tunnel)
        elif state.ours:
            already.setdefault(key, []).append(tunnel)
        else:
            stderr.write(
                f"{tunnel.service}: port {tunnel.local_port} is in use by "
                f"another process: {state.command}\n"
            )
            failed += 1

    for key, tunnels in already.items():
        name, _user, host = key
        stdout.write(f"{name} {host}:\n  Already on:\n")
        for tunnel in tunnels:
            stdout.write(f"    - {_service_line(tunnel)}\n")
        stdout.write("\n")

    opened = 0
    for key, tunnels in pending.items():
        name, user, host = key
        if runtime.run_command(_preflight_argv(user, host)).returncode != 0:
            stderr.write(
                f"SSH preflight to {name} ({host}) failed; "
                f"trying the tunnel anyway\n"
            )
        result = runtime.run_command(_tunnel_argv(tunnels))
        if result.returncode == 0:
            stdout.write(f"{name} {host}:\n  Opened:\n")
            for tunnel in tunnels:
                stdout.write(f"    - {_service_line(tunnel)}\n")
            opened += len(tunnels)
        else:
            stderr.write(f"Failed to open tunnels for {name} ({host})\n")
            if result.stderr:
                stderr.write(result.stderr)
            failed += len(tunnels)
        stdout.write("\n")

    on = sum(len(tunnels) for tunnels in already.values())
    total = opened + on + failed
    stdout.write(
        f"Connect: {opened} opened | {on} already on | "
        f"{failed} failed ({total} total)\n"
    )
    return 1 if failed else 0


def disconnect_tunnels(
    rows: Sequence[str],
    *,
    service: str | None,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Stop our tunnel PIDs, each signalled once for all of its ports."""
    tunnels = _select_rows(rows, service, stderr)
    if service is not None and not tunnels:
        stderr.write("disconnect matches a service name or a local port\n")
        return _no_match(service, stderr)
    if not tunnels:
        return _nothing_configured(stdout)

    off: dict[tuple[str, str, str], list[VpsTunnel]] = {}
    by_pid: dict[int, list[VpsTunnel]] = {}
    left_alone = 0
    listeners = _Listeners(runtime)
    for tunnel in tunnels:
        state = listeners.state(tunnel.local_port)
        key = (tunnel.name, tunnel.user, tunnel.host)
        if not state.listens:
            off.setdefault(key, []).append(tunnel)
        elif not state.ours:
            stderr.write(
                f"{tunnel.service}: port {tunnel.local_port} is in use by a "
                f"non-SSH process, leaving it alone: {state.command}\n"
            )
            left_alone += 1
        elif state.pid is not None:
            by_pid.setdefault(state.pid, []).append(tunnel)

    for key, tunnels in off.items():
        name, _user, host = key
        stdout.write(f"{name} {host}:\n  Already off:\n")
        for tunnel in tunnels:
            stdout.write(f"    - {_service_line(tunnel)}\n")
        stdout.write("\n")

    closed = 0
    failed = 0
    for pid, tunnels in by_pid.items():
        label = f"{tunnels[0].name} {tunnels[0].host}"
        if not runtime.signal(pid, signal.SIGTERM):
            stderr.write(f"Failed to signal pid {pid} ({label})\n")
            failed += len(tunnels)
            continue
        runtime.sleep(0.5)
        header = "  Closed:"
        if runtime.process_alive(pid):
            if not runtime.signal(pid, signal.SIGKILL):
                stderr.write(f"Failed to signal pid {pid} ({label})\n")
                failed += len(tunnels)
                continue
            runtime.sleep(0.5)
            if runtime.process_alive(pid):
                stderr.write(f"Pid {pid} ({label}) is still running\n")
                failed += len(tunnels)
                continue
            header = "  Closed (forced):"
        stdout.write(f"{label}:\n{header}\n")
        for tunnel in tunnels:
            stdout.write(f"    - {_service_line(tunnel)}\n")
        closed += len(tunnels)

    off_count = sum(len(tunnels) for tunnels in off.values())
    total = closed + off_count + left_alone + failed
    stdout.write(
        f"Disconnect: {closed} closed | {off_count} already off | "
        f"{left_alone} busy | {failed} failed ({total} total)\n"
    )
    return 1 if failed else 0


def list_tunnels(
    rows: Sequence[str],
    *,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Show a status table for every tunnel row; makes no SSH call."""
    tunnels = _select_rows(rows, None, stderr)
    if not tunnels:
        return _nothing_configured(stdout)
    listeners = _Listeners(runtime)

    stdout.write(
        f"{'STATUS':<8}{'SERVICE':<20}{'LOCAL':<8}{'TARGET':<26}"
        f"{'VIA':<10}{'IP':<18}UPTIME\n"
    )
    for tunnel in tunnels:
        state = listeners.state(tunnel.local_port)
        if state.listens and state.ours and state.pid is not None:
            status = "UP"
            uptime = _uptime(runtime, state.pid)
        elif state.listens:
            status = "BUSY"
            uptime = "-"
        else:
            status = "DOWN"
            uptime = "-"
        target = f"{tunnel.target_host}:{tunnel.target_port}"
        stdout.write(
            f"{status:<8}{tunnel.service:<20}{f':{tunnel.local_port}':<8}"
            f"{target:<26}{tunnel.name:<10}{tunnel.host:<18}{uptime}\n"
        )
    return 0


def _uptime(runtime: Runtime, pid: int) -> str:
    result = runtime.run_command(["ps", "-p", str(pid), "-o", "etime="])
    return result.stdout.strip() or "-"


def test_tunnels(
    rows: Sequence[str],
    *,
    service: str | None,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Run the SSH preflight once per distinct (name, user, host)."""
    tunnels = _select_rows(rows, service, stderr)
    if service is not None and not tunnels:
        return _no_match(service, stderr)
    if not tunnels:
        return _nothing_configured(stdout)

    hosts: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for tunnel in tunnels:
        key = (tunnel.name, tunnel.user, tunnel.host)
        if key not in seen:
            seen.add(key)
            hosts.append(key)

    failed = 0
    for name, user, host in hosts:
        stdout.write(f"Testing {name} {host}... ")
        if runtime.run_command(_preflight_argv(user, host)).returncode == 0:
            stdout.write("ok\n")
        else:
            stdout.write("failed\n")
            failed += 1
    if failed:
        stdout.write(f"SSH test: {failed}/{len(hosts)} hosts failed\n")
    else:
        stdout.write(f"SSH test: all {len(hosts)} hosts reachable\n")
    return 1 if failed else 0
