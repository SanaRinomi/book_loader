"""``domain/`` is the bottom layer: it imports only the standard library and itself."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import book_loader.domain

DOMAIN = Path(book_loader.domain.__file__).parent


def imported_modules(path: Path) -> set[str]:
    modules = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                modules.add("book_loader.domain" if node.level == 1 else "book_loader")
            elif node.module:
                modules.add(node.module)
    return modules


def test_domain_imports_only_the_standard_library():
    for path in sorted(DOMAIN.glob("*.py")):
        for module in imported_modules(path):
            top = module.split(".")[0]
            allowed = top in sys.stdlib_module_names or module.startswith("book_loader.domain")
            assert allowed, f"{path.name} imports {module}"
