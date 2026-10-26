"""Secrets file path resolution and bootstrap for dbt."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .models import RawSecrets
from .secrets import parse_secrets_text

_DEFAULT_CONFIG_DIR = "Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
_ICLOUD_LEGACY = "Library/Mobile Documents/com~apple~CloudDocs/Backups/dbt_secrets"


class ConfigError(Exception):
    """A user or environment configuration could not be used."""


@dataclass(frozen=True, slots=True)
class ResolvedSecrets:
    path: Path
    notices: tuple[str, ...] = field(default=())


def resolve_secrets_file(
    env: Mapping[str, str],
    *,
    home: Path,
    repo_root: Path,
    example: Path,
) -> ResolvedSecrets:
    """Return the secrets file path plus any notice about how it was found.

    An existing ``DBT_SECRETS_FILE`` wins. Otherwise the file is
    ``$CLOUD_UTILS_CONFIG_DIR/dbt/secrets`` (default: the iCloud
    ``cloud-utils`` backup directory), bootstrapped on first use from the
    iCloud legacy file, the repo ``dbt_secrets``, or ``secrets.example``.
    """
    notices: list[str] = []
    override = env.get("DBT_SECRETS_FILE", "")
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return ResolvedSecrets(path)
        notices.append(f"DBT_SECRETS_FILE={override} does not exist; using the default location")

    config_text = env.get("CLOUD_UTILS_CONFIG_DIR", "")
    config_dir = Path(config_text).expanduser() if config_text else home / _DEFAULT_CONFIG_DIR
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"cannot create config directory {config_dir}: {exc}") from exc

    destination = config_dir / "dbt" / "secrets"
    if destination.is_file():
        return ResolvedSecrets(destination, tuple(notices))

    source = example
    for candidate in (home / _ICLOUD_LEGACY, repo_root / "dbt_secrets"):
        if candidate.is_file():
            source = candidate
            break

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    except OSError as exc:
        raise ConfigError(f"cannot bootstrap secrets file {destination}: {exc}") from exc
    if source == example:
        notices.append(f"created an empty secrets file at {destination}; add your entries there")
    else:
        notices.append(f"migrated secrets from {source} to {destination}")
    return ResolvedSecrets(destination, tuple(notices))


def load_secrets(path: Path) -> RawSecrets:
    """Parse the secrets file; raise ConfigError when it cannot be read."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read secrets file {path}: {exc}") from exc
    return parse_secrets_text(text)
