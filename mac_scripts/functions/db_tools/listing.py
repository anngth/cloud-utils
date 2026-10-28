"""One rendering of the secrets file for `list` and `status`.

Both show the same five sections with the same 1-based indexes (the ones
`--from`/`--to` accept); `status` adds the probe results.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import TextIO

from .models import Secrets
from .mongo import mongo_host, mongo_status
from .postgres import parse_postgres_url, postgres_status
from .runtime import Runtime
from .tunnels import TunnelState, tunnel_states

ICON_UP = "🟢"
ICON_DOWN = "🔴"
ICON_BUSY = "🟡"
ICON_UNKNOWN = "⚪"
PROBE_TIMEOUT_S = 3  # a status probe answers fast or not at all
_MAX_PROBES = 8
_TUNNEL_ICONS = {"UP": ICON_UP, "DOWN": ICON_DOWN, "BUSY": ICON_BUSY}


@dataclass(frozen=True, slots=True)
class Row:
    label: str
    detail: str
    icon: str | None = None


@dataclass(frozen=True, slots=True)
class Section:
    title: str
    rows: tuple[Row, ...]


# --- sections ---------------------------------------------------------------------


def tunnel_section(secrets: Secrets, states: Sequence[TunnelState] | None) -> Section:
    """Tunnel rows; with `states` each row carries UP/DOWN/BUSY and an icon."""
    rows: list[Row] = []
    for index, tunnel in enumerate(secrets.tunnels):
        state = states[index] if states is not None else None
        detail = (
            f":{tunnel.local_port} -> {tunnel.target_host}:{tunnel.target_port}"
            f"  {tunnel.user}@{tunnel.host}"
        )
        if state is not None:
            detail += f" | {_tunnel_status(state)}"
        rows.append(
            Row(
                f"{tunnel.name}: {tunnel.service}",
                detail,
                _TUNNEL_ICONS[state.status] if state is not None else None,
            )
        )
    return Section("🔀 Tunnels", tuple(rows))


def _tunnel_status(state: TunnelState) -> str:
    if state.status == "UP":
        return f"UP {state.uptime}".rstrip()
    if state.status == "DOWN" and state.ssh:
        return f"DOWN (ssh {state.ssh})"
    return state.status


def database_sections(
    secrets: Secrets, *, probes: Sequence[bool | None] | None = None
) -> list[Section]:
    """The four database sections; `probes` (from `probe_databases`) adds icons."""
    results = iter(probes) if probes is not None else None

    def icon() -> str | None:
        return _status_icon(next(results)) if results is not None else None

    def row(label: str, host: str) -> Row:
        return Row(label, host, icon())

    sources = [
        row(f"{s.name} → {s.database}", parse_postgres_url(s.url).host)
        for s in secrets.postgres_sources
    ]
    targets = []
    for t in secrets.postgres_targets:
        url = parse_postgres_url(t.url)
        ssl = "require" if url.sslmode_require else "disable"
        targets.append(row(t.name, f"{url.host} [SSL: {ssl}]"))
    mongo_sources = [
        row(f"{s.name} → {','.join(s.databases)}", mongo_host(s.uri))
        for s in secrets.mongo_sources
    ]
    mongo_targets = [row(t.name, mongo_host(t.uri)) for t in secrets.mongo_targets]
    return [
        Section("🐘 PostgreSQL Sources", tuple(sources)),
        Section("🎯 PostgreSQL Targets", tuple(targets)),
        Section("🍃 MongoDB Sources", tuple(mongo_sources)),
        Section("🎯 MongoDB Targets", tuple(mongo_targets)),
    ]


def _status_icon(status: bool | None) -> str:
    if status is None:
        return ICON_UNKNOWN
    return ICON_UP if status else ICON_DOWN


# --- probes -------------------------------------------------------------------------


def probe_count(secrets: Secrets) -> int:
    return (
        len(secrets.postgres_sources)
        + len(secrets.postgres_targets)
        + len(secrets.mongo_sources)
        + len(secrets.mongo_targets)
    )


def probe_databases(secrets: Secrets, *, runtime: Runtime) -> list[bool | None]:
    """Probe every source and target concurrently; results keep section order."""
    probes: list[Callable[[], bool | None]] = []
    probes += [
        partial(
            postgres_status,
            s.url,
            runtime=runtime,
            database=s.database,
            connect_timeout=PROBE_TIMEOUT_S,
        )
        for s in secrets.postgres_sources
    ]
    probes += [
        partial(postgres_status, t.url, runtime=runtime, connect_timeout=PROBE_TIMEOUT_S)
        for t in secrets.postgres_targets
    ]
    probes += [
        partial(mongo_status, s.uri, runtime=runtime, timeout_ms=PROBE_TIMEOUT_S * 1000)
        for s in secrets.mongo_sources
    ]
    probes += [
        partial(mongo_status, t.uri, runtime=runtime, timeout_ms=PROBE_TIMEOUT_S * 1000)
        for t in secrets.mongo_targets
    ]
    if not probes:
        return []
    with ThreadPoolExecutor(max_workers=min(len(probes), _MAX_PROBES)) as pool:
        return list(pool.map(lambda probe: probe(), probes))


def probe_summary(results: Sequence[bool | None]) -> str:
    connected = sum(1 for r in results if r is True)
    disconnected = sum(1 for r in results if r is False)
    unknown = sum(1 for r in results if r is None)
    summary = (
        f"Summary: {ICON_UP} {connected} connected  |  {ICON_DOWN} {disconnected} disconnected"
    )
    if unknown:
        summary += f"  |  {ICON_UNKNOWN} {unknown} unknown (client not installed)"
    return summary


# --- rendering ----------------------------------------------------------------------


def render(sections: Sequence[Section], stdout: TextIO) -> None:
    """Sections separated by a blank line; an empty section says `(none)`."""
    for position, section in enumerate(sections):
        if position:
            stdout.write("\n")
        stdout.write(f"{section.title} ({len(section.rows)}):\n")
        for index, row in enumerate(section.rows, start=1):
            icon = f"{row.icon} " if row.icon else ""
            stdout.write(f"  {icon}{index}) {row.label} | {row.detail}\n")
        if not section.rows:
            stdout.write("  (none)\n")


def render_listing(
    secrets: Secrets,
    *,
    runtime: Runtime,
    stdout: TextIO,
    tunnel_status: bool,
    probe: bool,
    ssh_check: bool = False,
) -> int:
    """Render all five sections.

    `tunnel_status` adds the local UP/DOWN/BUSY check (cheap, no network);
    `probe` connects to every database with a short timeout and ends with a
    summary; `ssh_check` adds the SSH preflight to DOWN tunnel rows.
    """
    states = (
        tunnel_states(secrets.tunnels, runtime=runtime, ssh_check=ssh_check)
        if tunnel_status
        else None
    )
    results: list[bool | None] | None = None
    if probe and (count := probe_count(secrets)):
        plural = "connection" if count == 1 else "connections"
        stdout.write(f"⏳ Probing {count} {plural} (up to {PROBE_TIMEOUT_S}s each)...\n\n")
        results = probe_databases(secrets, runtime=runtime)
    sections = [
        tunnel_section(secrets, states),
        *database_sections(secrets, probes=results),
    ]
    render(sections, stdout)
    if results:
        stdout.write("\n" + probe_summary(results) + "\n")
    return 0
