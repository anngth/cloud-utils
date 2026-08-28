from __future__ import annotations

import io
from pathlib import Path

import pytest

from shared.selector import SelectorResult, create_selector_state
from skills_manager.cli import CommandContext, Services
from skills_manager.config import Catalog, CatalogSource, ConfigPaths
from skills_manager.ui import SkmUi


@pytest.fixture
def context(tmp_path: Path) -> CommandContext:
    stdout, stderr = io.StringIO(), io.StringIO()
    paths = ConfigPaths.for_config_dir(tmp_path / "config")
    catalog = Catalog(
        version=1,
        sources=(
            CatalogSource(source="owner/catalog", skills=("demo",)),
        ),
    )
    return CommandContext(
        cwd=tmp_path,
        env={"HOME": str(tmp_path / "home")},
        stdin=io.StringIO(),
        stdout=stdout,
        stderr=stderr,
        paths=paths,
        catalog=catalog,
        ui=SkmUi(stdout, stderr),
        services=Services(),
        select_items=lambda *_args, **_kwargs: SelectorResult(
            "submit",
            create_selector_state(()),
            (),
        ),
        confirm=lambda _message: True,
        confirm_apply=lambda **_kwargs: True,
    )
