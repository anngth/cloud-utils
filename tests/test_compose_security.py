"""Rendered Compose must fail closed on secrets and not publish moving image tags."""

from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"

BASE_ENV = {
    "CF_DNS_API_TOKEN": "cf-token",
    "CF_API_EMAIL": "ops@example.com",
    "TRAEFIK_DOMAIN": "example.com",
    "TRAEFIK_EMAIL": "ops@example.com",
    "TRAEFIK_DASHBOARD_AUTH": "dash:hashed",
    "POSTGRES16_DB": "app16",
    "POSTGRES16_USER": "app16",
    "POSTGRES16_PASSWORD": "pg16-secret",
    "POSTGRES17_DB": "app17",
    "POSTGRES17_USER": "app17",
    "POSTGRES17_PASSWORD": "pg17-secret",
    "MONGO8_INITDB_ROOT_USERNAME": "mongo",
    "MONGO8_INITDB_ROOT_PASSWORD": "mongo-secret",
    "MONGO8_INITDB_DATABASE": "app",
    "REDIS7_PASSWORD": "redis-secret",
    "STIRLING_INITIAL_USERNAME": "pdf-admin",
    "STIRLING_INITIAL_PASSWORD": "pdf-secret",
    "STIRLING_EDGE_AUTH": "pdf:hashed",
}


def _render(tmp_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    target = tmp_path / "docker-compose.yml"
    target.write_text(COMPOSE.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / ".env").write_text(
        "".join(f"{key}={value}\n" for key, value in env.items()),
        encoding="utf-8",
    )
    return subprocess.run(
        ["docker", "compose", "config"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )


def test_compose_config_requires_postgres_and_redis_passwords(tmp_path: Path) -> None:
    for key in ("POSTGRES17_PASSWORD", "REDIS7_PASSWORD"):
        missing = dict(BASE_ENV)
        del missing[key]
        result = _render(tmp_path, missing)

        assert result.returncode != 0, result.stdout
        assert key in result.stderr
        assert "POSTGRES_PASSWORD: postgres" not in result.stdout


def test_rendered_stack_pins_images_and_authenticates_redis_and_pdf(tmp_path: Path) -> None:
    result = _render(tmp_path, BASE_ENV)

    assert result.returncode == 0, result.stderr
    rendered = result.stdout
    assert "POSTGRES_PASSWORD: pg17-secret" in rendered
    assert "POSTGRES_PASSWORD: postgres" not in rendered
    assert "REDISCLI_AUTH: redis-secret" in rendered
    assert "redis-cli" in rendered
    assert "ping" in rendered
    assert "DOCKER_ENABLE_SECURITY" not in rendered
    assert "SECURITY_ENABLELOGIN: 'true'" in rendered or 'SECURITY_ENABLELOGIN: "true"' in rendered
    assert "SECURITY_INITIALLOGIN_PASSWORD: pdf-secret" in rendered
    assert "pdf:hashed" in rendered
    assert "portainer/portainer-ce:2.45.3" in rendered
    assert "stirling-pdf:3.2.1" in rendered
    assert ":latest" not in rendered
