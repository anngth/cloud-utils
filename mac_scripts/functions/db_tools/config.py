"""Secrets file path resolution and bootstrap for dbt."""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path

from .models import RawSecrets
from .secrets import parse_secrets_text

_DEFAULT_CONFIG_DIR = "Library/Mobile Documents/com~apple~CloudDocs/Backups/cloud-utils"
_ICLOUD_LEGACY = "Library/Mobile Documents/com~apple~CloudDocs/Backups/dbt_secrets"


class ConfigError(Exception):
    """A user or environment configuration could not be used."""


def resolve_secrets_file(
    env: Mapping[str, str],
    *,
    home: Path,
    repo_root: Path,
    example: Path,
) -> Path:
    """Return the current secrets file path, bootstrapping it if needed.

    An existing ``DBT_SECRETS_FILE`` wins and never touches the config
    directory. Otherwise the destination is ``$CLOUD_UTILS_CONFIG_DIR/dbt/secrets``
    (defaulting to the iCloud ``cloud-utils`` backup directory); when the
    destination is missing it is copied from the iCloud legacy file, the repo
    ``dbt_secrets``, or the packaged ``secrets.example``, in that order.
    """
    override = env.get("DBT_SECRETS_FILE", "")
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path

    config_text = env.get("CLOUD_UTILS_CONFIG_DIR", "")
    config_dir = Path(config_text).expanduser() if config_text else home / _DEFAULT_CONFIG_DIR
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"cannot create config directory {config_dir}: {exc}") from exc

    destination = config_dir / "dbt" / "secrets"
    if destination.is_file():
        return destination

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
    return destination


class SecretsCache:
    """Parses a secrets file once per path, reloading when its mtime moves.

    Raises ``ConfigError`` if the file (or a parent component) is missing.
    """

    def __init__(self) -> None:
        self._path: Path | None = None
        self._mtime: float | None = None
        self._secrets: RawSecrets | None = None

    def load(self, path: Path) -> RawSecrets:
        try:
            stat = path.stat()
            text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ConfigError(f"secrets file not found: {path}") from exc
        if (
            self._secrets is not None
            and self._path == path
            and self._mtime == stat.st_mtime
        ):
            return self._secrets
        secrets = parse_secrets_text(text)
        self._path = path
        self._mtime = stat.st_mtime
        self._secrets = secrets
        return secrets
