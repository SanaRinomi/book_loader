"""The layering rule (REFACTOR_PLAN §4): each new package imports only what lies below it.

- ``domain/`` imports only the standard library and itself.
- ``infra/`` imports ``domain/`` and itself from book_loader.
- Nothing below ``cli/`` imports click, rich, questionary or prompt_toolkit.

Packages are added here as they are built.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

import book_loader

PACKAGE_ROOT = Path(book_loader.__file__).parent
UI_LIBRARIES = {"click", "rich", "rich_click", "questionary", "prompt_toolkit"}

# package -> the book_loader packages it may import (itself included)
ALLOWED = {
    "domain": {"domain"},
    "infra": {"domain", "infra"},
}

# Imports allowed for now, each with the task that removes it.
TEMPORARY = {
    ("infra/logging.py", "book_loader.utils.redact"),  # T2.12 moves redact.py into infra
}


def imports_of(path: Path) -> set[str]:
    """Absolute names of the modules ``path`` imports."""
    module = ".".join(path.relative_to(PACKAGE_ROOT.parent).with_suffix("").parts)
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            relative = "." * node.level + (node.module or "")
            names.add(importlib.util.resolve_name(relative, package) if node.level else relative)
    return names


def files_of(package: str) -> list[Path]:
    return sorted((PACKAGE_ROOT / package).rglob("*.py"))


@pytest.mark.parametrize("package", ALLOWED)
def test_package_imports_only_lower_layers(package):
    for path in files_of(package):
        where = path.relative_to(PACKAGE_ROOT).as_posix()
        for name in imports_of(path):
            parts = name.split(".")
            if (where, name) in TEMPORARY:
                continue
            if parts[0] == "book_loader":
                assert (
                    len(parts) > 1 and parts[1] in ALLOWED[package]
                ), f"{path.name} imports {name}"
            assert parts[0] not in UI_LIBRARIES, f"{path.name} imports {name}"


def test_domain_imports_only_the_standard_library():
    for path in files_of("domain"):
        for name in imports_of(path):
            top = name.split(".")[0]
            assert top in sys.stdlib_module_names or top == "book_loader", f"{path.name}: {name}"


def test_temporary_exceptions_are_still_needed():
    # Once the task behind an exception is done, remove the exception too.
    for where, name in TEMPORARY:
        assert name in imports_of(PACKAGE_ROOT / where), f"{where} no longer imports {name}"


def test_relative_imports_are_resolved():
    assert "book_loader.infra.known_dirs" in imports_of(PACKAGE_ROOT / "infra" / "paths.py")
    assert "book_loader.domain.errors" in imports_of(PACKAGE_ROOT / "domain" / "conflicts.py")
