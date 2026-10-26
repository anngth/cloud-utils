"""Tests for the dbt secrets TOML loader."""

from __future__ import annotations

import pytest

from db_tools.models import MongoSource, PostgresSource, PostgresTarget, Secrets, VpsTunnel
from db_tools.secrets import SecretsError, parse_secrets_text, redact

FULL = """\
# a comment
[[tunnels]]
name = "oracle"
user = "me"
host = "10.0.0.1"
service = "postgres"
local_port = 5432
target_host = "localhost"
target_port = 5432

[[postgres.sources]]
name = "prod"
url = "postgresql://u:p$ss|w@h:5432/app"

[[postgres.sources]]
name = "other"
url = "postgresql://u:p@h:5432/postgres"
database = "reports"

[[postgres.targets]]
name = "cloud"
url = "postgres://u:p@h:5432/postgres?sslmode=require"

[[mongo.sources]]
name = "m"
uri = "mongodb+srv://u:p@cluster.example/?authSource=admin"
databases = ["app", "logs"]

[[mongo.targets]]
name = "mt"
uri = "mongodb://u:p@h:27017"
"""


def test_full_file_parses_into_typed_entries() -> None:
    secrets = parse_secrets_text(FULL)
    assert secrets.tunnels == (
        VpsTunnel("oracle", "me", "10.0.0.1", "postgres", 5432, "localhost", 5432),
    )
    assert secrets.postgres_sources == (
        PostgresSource("prod", "postgresql://u:p$ss|w@h:5432/app", "app"),
        PostgresSource("other", "postgresql://u:p@h:5432/postgres", "reports"),
    )
    assert secrets.postgres_targets == (
        PostgresTarget("cloud", "postgres://u:p@h:5432/postgres?sslmode=require"),
    )
    assert secrets.mongo_sources == (
        MongoSource("m", "mongodb+srv://u:p@cluster.example/?authSource=admin", ("app", "logs")),
    )
    assert [t.name for t in secrets.mongo_targets] == ["mt"]


def test_empty_and_comment_only_files_have_no_entries() -> None:
    assert parse_secrets_text("") == Secrets()
    assert parse_secrets_text("# nothing yet\n") == Secrets()


def test_literal_strings_need_no_escaping() -> None:
    text = "[[postgres.targets]]\nname = 'x'\nurl = 'postgresql://u:p\\$\"q@h:5432/db'\n"
    assert parse_secrets_text(text).postgres_targets[0].url == 'postgresql://u:p\\$"q@h:5432/db'


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h:5432/app?sslmode=require", "app"),
        ("postgresql://u:p@h/app/extra", "app"),
        ("postgresql://u:p%40@h:5432/my%20db", "my db"),
        ("postgresql://u:p@ss#x?y@h:5432/app", "app"),
    ],
)
def test_postgres_source_database_defaults_to_the_url_path(url: str, expected: str) -> None:
    text = f'[[postgres.sources]]\nname = "s"\nurl = "{url}"\n'
    assert parse_secrets_text(text).postgres_sources[0].database == expected


def test_postgres_source_without_a_database_anywhere_is_rejected() -> None:
    text = '[[postgres.sources]]\nname = "s"\nurl = "postgresql://u:p@h:5432"\n'
    with pytest.raises(SecretsError, match=r"postgres\.sources\[1\] \(s\): set 'database'"):
        parse_secrets_text(text)


def test_invalid_toml_reports_the_line() -> None:
    with pytest.raises(SecretsError, match=r"invalid TOML: .*line 2") as excinfo:
        parse_secrets_text('[[tunnels]]\nname = "x\n')
    assert "invalid TOML" in str(excinfo.value)


_TUNNEL = (
    '[[tunnels]]\nname = "x"\nuser = "u"\nhost = "h"\nservice = "s"\n'
    'target_host = "t"\ntarget_port = 1\n'
)
_PG_TARGET = '[[postgres.targets]]\nname = "{name}"\nurl = "postgresql://h/db"\n'
_MONGO_SOURCE = '[[mongo.sources]]\nname = "m"\nuri = "mongodb://h"\ndatabases = {databases}\n'


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[[vps]]\nname = "x"\n', "unknown key 'vps'"),
        ("[postgres]\nsource = []\n", "unknown key 'postgres.source'"),
        ('[[tunnels]]\nname = "x"\nhost = "h"\n', "tunnels[1] (x): missing field 'user'"),
        (
            _TUNNEL + 'local_port = "5432"\n',
            "tunnels[1] (x): 'local_port' must be an integer port, not a string",
        ),
        (_TUNNEL + "local_port = true\n", "'local_port' must be an integer port, not a boolean"),
        (_TUNNEL + "local_port = 70000\n", "'local_port' must be between 1 and 65535"),
        (_TUNNEL + "local_port = 0\n", "'local_port' must be between 1 and 65535"),
        (_PG_TARGET.format(name="x") + "extra = 1\n", "unknown field 'extra'"),
        (_PG_TARGET.format(name=""), "'name' must be a non-empty string"),
        (_PG_TARGET.format(name="x").replace('"x"', "1"), "'name' must be a non-empty string"),
        (_MONGO_SOURCE.format(databases='"app"'), "'databases' must be a non-empty list"),
        (_MONGO_SOURCE.format(databases="[]"), "'databases' must be a non-empty list"),
        (_MONGO_SOURCE.format(databases='["app", ""]'), "'databases' must be a non-empty list"),
        ('[[mongo.targets]]\nname = "m"\nuri = "mongo://h"\n', "'uri' must start with mongodb://"),
        ("tunnels = 1\n", "'tunnels' must be an array of tables"),
        ('postgres = "x"\n', "'postgres' must be a table"),
        (
            _PG_TARGET.format(name="a") * 2,
            "postgres.targets[2] (a): name 'a' is already used by postgres.targets[1]",
        ),
    ],
)
def test_layout_errors_name_the_entry_and_field(text: str, message: str) -> None:
    with pytest.raises(SecretsError) as excinfo:
        parse_secrets_text(text)
    assert message in str(excinfo.value)


def _tunnel(service: str, port: int) -> str:
    return (
        '[[tunnels]]\nname = "a"\nuser = "u"\nhost = "h"\ntarget_host = "t"\ntarget_port = 1\n'
        f'service = "{service}"\nlocal_port = {port}\n'
    )


def test_tunnels_of_one_vps_share_a_name() -> None:
    text = _tunnel("postgres", 5432) + _tunnel("mongo", 27017)
    assert [t.service for t in parse_secrets_text(text).tunnels] == ["postgres", "mongo"]


def test_two_tunnels_on_one_local_port_are_rejected() -> None:
    text = _tunnel("postgres", 5432) + _tunnel("replica", 5432)
    with pytest.raises(SecretsError) as excinfo:
        parse_secrets_text(text)
    assert str(excinfo.value) == "tunnels[2] (a): local_port 5432 is already used by tunnels[1]"


def test_wrong_scheme_error_masks_the_password() -> None:
    text = '[[postgres.sources]]\nname = "bad"\nurl = "http://svc:s3cr3t@h:5432/db"\n'
    with pytest.raises(SecretsError) as excinfo:
        parse_secrets_text(text)
    assert "postgres.sources[1] (bad): 'url' must start with postgresql:// or postgres://" in str(
        excinfo.value
    )
    assert "http://***@h:5432/db" in str(excinfo.value)
    assert "s3cr3t" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("name|postgresql://svc:s3cr3t@h:5432/db|db", "name|postgresql://***@h:5432/db|db"),
        ("mongodb+srv://u:p@ss@cluster.example/app", "mongodb+srv://***@cluster.example/app"),
        ("a|me|10.0.0.1|pg|5432|db|5432", "a|me|10.0.0.1|pg|5432|db|5432"),
        ("mongodb://cluster.example:27017", "mongodb://cluster.example:27017"),
    ],
)
def test_redact_masks_credentials_only(text: str, expected: str) -> None:
    assert redact(text) == expected
