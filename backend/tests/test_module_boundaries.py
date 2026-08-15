from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

IMPURE_PACKAGES = ("app.providers", "app.db", "app.storage")


PURE_MODULES = (
    "app/domain",
    "app/services/parser/tei_mapper.py",
    "app/services/parser/postvalidator.py",
    "app/services/parser/segmenter.py",
    "app/services/citations/csl.py",
    "app/services/editor/tokens.py",
    "app/services/editor/delta_engine.py",
    "app/services/editor/candidate_revision.py",
    "app/services/exporter/render_set.py",
    "app/services/exporter/pandoc_ir.py",
)


def _python_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("*.py") if p.name != "__init__.py")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def test_pure_modules_import_nothing_impure() -> None:
    violations: list[str] = []
    for relative in PURE_MODULES:
        target = APP.parent / relative
        assert target.exists(), f"{relative} is listed as pure but does not exist"
        for path in _python_files(target):
            for module in _imported_modules(path):
                if module.startswith(IMPURE_PACKAGES):
                    violations.append(f"{path.relative_to(APP.parent)} imports {module}")
    assert not violations, "purity claim violated:\n" + "\n".join(violations)


def test_domain_never_imports_services_or_api() -> None:
    violations: list[str] = []
    for path in _python_files(APP / "domain"):
        for module in _imported_modules(path):
            if module.startswith(("app.services", "app.api")):
                violations.append(f"{path.relative_to(APP.parent)} imports {module}")
    assert not violations, "domain must not depend on services or api:\n" + "\n".join(violations)


def test_application_never_imports_test_doubles() -> None:
    violations: list[str] = []
    for path in _python_files(APP):
        for module in _imported_modules(path):
            if module == "tests" or module.startswith("tests."):
                violations.append(f"{path.relative_to(APP.parent)} imports {module}")
    assert not violations, "application code must not depend on tests:\n" + "\n".join(violations)


def test_api_contains_no_workflow_implementations() -> None:
    offenders: list[str] = []
    for path in _python_files(APP / "api"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                body_lines = (node.end_lineno or node.lineno) - node.lineno
                if body_lines > 60:
                    offenders.append(f"{path.relative_to(APP.parent)}::{node.name} ({body_lines})")
    assert not offenders, "api handlers should delegate, not implement:\n" + "\n".join(offenders)


def test_runtime_invariants_never_use_assert() -> None:
    offenders: list[str] = []
    for path in _python_files(APP):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}")
    assert not offenders, "assert is not a runtime guard:\n" + "\n".join(offenders)
