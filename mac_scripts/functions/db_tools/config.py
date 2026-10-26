"""Secrets file path resolution, first-run migration, and loading for dbt."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .legacy import LegacySecretsError, convert_legacy_secrets
from .models import Secrets
from .secrets import parse_secrets_text

_DEFAULT_CONFIG_DIR = "Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
_ICLOUD_LEGACY = "Library/Mobile Documents/com~apple~CloudDocs/Backups/dbt_secrets"
SECRETS_NAME = "secrets.toml"
_LEGACY_NAME = "secrets"
_LEGACY_BACKUP = "secrets.zsh.bak"


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
    ``$CLOUD_UTILS_CONFIG_DIR/dbt/secrets.toml`` (default: the iCloud
    ``cloud-utils`` backup directory). On first use it is converted from the
    old zsh-array file (``dbt/secrets`` next to it, the iCloud legacy file,
    or the repo ``dbt_secrets``) or copied from ``secrets.example.toml``.
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

    destination = config_dir / "dbt" / SECRETS_NAME
    if destination.is_file():
        return ResolvedSecrets(destination, tuple(notices))

    legacy_candidates = (
        destination.with_name(_LEGACY_NAME),
        home / _ICLOUD_LEGACY,
        repo_root / "dbt_secrets",
    )
    legacy = next((candidate for candidate in legacy_candidates if candidate.is_file()), None)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if legacy is None:
            shutil.copyfile(example, destination)
            notices.append(
                f"created an empty secrets file at {destination}; add your entries there"
            )
        else:
            notices.extend(_migrate(legacy, destination))
    except OSError as exc:
        raise ConfigError(f"cannot bootstrap secrets file {destination}: {exc}") from exc
    return ResolvedSecrets(destination, tuple(notices))


def _migrate(legacy: Path, destination: Path) -> list[str]:
    """Write `destination` from the zsh-array file; the old file is kept."""
    try:
        conversion = convert_legacy_secrets(legacy.read_text(encoding="utf-8"))
    except LegacySecretsError as exc:
        raise ConfigError(
            f"cannot migrate {legacy}: {exc}; fix that line, or write {destination} by hand"
        ) from exc
    destination.write_text(conversion.toml, encoding="utf-8")
    destination.chmod(0o600)
    notices = [f"migrated secrets from {legacy} to {destination}"]
    if legacy.parent == destination.parent:
        backup = legacy.with_name(_LEGACY_BACKUP)
        legacy.rename(backup)
        notices[0] += f" (old file kept as {backup.name})"
    if conversion.skipped:
        count = len(conversion.skipped)
        rows = "row was" if count == 1 else "rows were"
        notices.append(f"{count} invalid {rows} not converted; see the comments in {destination}")
    return notices


def load_secrets(path: Path) -> Secrets:
    """Parse the secrets file; raise ConfigError when it cannot be read."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read secrets file {path}: {exc}") from exc
    return parse_secrets_text(text)
