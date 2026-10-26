"""SSH tunnels for dbt: connect, disconnect, and list.

Every external command is an argv list; no shell and no `timeout` binary.
Port liveness is checked per port with `lsof` and `ps`, never by a callback.
"""

from __future__ import annotations

import signal
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, TextIO

from .models import VpsTunnel
from .runtime import Runtime

_SSH_TIMEOUT = 10


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


def _select(tunnels: Sequence[VpsTunnel], service: str | None) -> list[VpsTunnel]:
    """Every tunnel, or those whose service name or local port matches `service`."""
    return [t for t in tunnels if service is None or _matches_service(t, service)]


def _no_match(service: str, stderr: TextIO) -> int:
    stderr.write(f"No service found matching: {service}\n")
    return 1


def _nothing_configured(stdout: TextIO) -> int:
    stdout.write("No tunnels configured\n")
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


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def connection_hint(
    tunnels: Sequence[VpsTunnel], host: str, port: int, *, runtime: Runtime
) -> str:
    """One line on why a local `host:port` refused, judged by the dbt tunnel on that port.

    Empty when the host is not local or no tunnel uses the port.
    """
    if host not in _LOCAL_HOSTS:
        return ""
    tunnel = next((t for t in tunnels if t.local_port == port), None)
    if tunnel is None:
        return ""
    state = _Listeners(runtime).state(port)
    label = f"tunnel {tunnel.service} ({tunnel.name})"
    if not state.listens:
        return f"{label} is DOWN; run: dbt connect {tunnel.service}"
    if state.ours:
        return (
            f"{label} is UP, so {tunnel.host} refused the forward to"
            f" {tunnel.target_host}:{tunnel.target_port}; check that the service"
            " there listens on that address"
        )
    return (
        f"port {port} is used by another process, not the {tunnel.service} tunnel:"
        f" {state.command}"
    )


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
        "StrictHostKeyChecking=accept-new",
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
    configured: Sequence[VpsTunnel],
    *,
    service: str | None,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Open tunnels for unmatched ports, grouped per (name, user, host)."""
    tunnels = _select(configured, service)
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
    configured: Sequence[VpsTunnel],
    *,
    service: str | None,
    runtime: Runtime,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    """Stop our tunnel PIDs, each signalled once for all of its ports."""
    tunnels = _select(configured, service)
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


@dataclass(frozen=True, slots=True)
class TunnelState:
    """One tunnel row with what currently listens on its local port."""

    tunnel: VpsTunnel
    status: Literal["UP", "DOWN", "BUSY"]
    uptime: str = ""  # `ps etime` of our ssh, UP only
    ssh: str = ""  # "ok" / "failed" from the SSH preflight, DOWN rows with `ssh_check` only


def tunnel_states(
    configured: Sequence[VpsTunnel], *, runtime: Runtime, ssh_check: bool = False
) -> list[TunnelState]:
    """Probe every tunnel row locally (one `lsof` snapshot, one `ps` per pid).

    Without `ssh_check` no SSH call is made. With it, every DOWN row also gets
    the SSH preflight result for its host (once per user@host), so a tunnel
    that is merely closed can be told apart from a broken SSH login.
    """
    listeners = _Listeners(runtime)
    preflights: dict[tuple[str, str], str] = {}

    def ssh_result(tunnel: VpsTunnel) -> str:
        key = (tunnel.user, tunnel.host)
        if key not in preflights:
            result = runtime.run_command(_preflight_argv(tunnel.user, tunnel.host))
            preflights[key] = "ok" if result.returncode == 0 else "failed"
        return preflights[key]

    states: list[TunnelState] = []
    for tunnel in _select(configured, None):
        state = listeners.state(tunnel.local_port)
        if state.listens and state.ours and state.pid is not None:
            states.append(TunnelState(tunnel, "UP", uptime=_uptime(runtime, state.pid)))
        elif state.listens:
            states.append(TunnelState(tunnel, "BUSY"))
        else:
            ssh = ssh_result(tunnel) if ssh_check else ""
            states.append(TunnelState(tunnel, "DOWN", ssh=ssh))
    return states


def _uptime(runtime: Runtime, pid: int) -> str:
    result = runtime.run_command(["ps", "-p", str(pid), "-o", "etime="])
    return result.stdout.strip() or "-"
