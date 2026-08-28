from importlib import import_module
from pathlib import Path


def test_python_packages_are_installed_from_repo() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("shared", "twofa", "git_tools"):
        module = import_module(name)
        assert root in Path(module.__file__).resolve().parents


def test_skills_manager_is_installed_from_repo() -> None:
    root = Path(__file__).resolve().parents[1]
    module = import_module("skills_manager")
    assert root in Path(module.__file__).resolve().parents
