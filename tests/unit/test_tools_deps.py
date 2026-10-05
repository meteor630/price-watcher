"""tools/deps.py: прямые зависимые модуля."""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_deps(root: Path, module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "tools" / "deps.py"), module, "--root", str(root)],
        # Кодировка вывода как у Windows при записи в канал: инструмент обязан писать UTF-8 сам.
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def add_line(root: Path, module_file: str, line: str) -> None:
    path = root / "src" / "pricewatch" / module_file
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing + line + "\n", encoding="utf-8")


def test_lists_packages_that_import_module(skeleton_copy: Path) -> None:
    add_line(skeleton_copy, "api/__init__.py", "import pricewatch.application")
    add_line(skeleton_copy, "bootstrap/__init__.py", "from pricewatch import application")

    result = run_deps(skeleton_copy, "application")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["pricewatch.api", "pricewatch.bootstrap"]


def test_full_module_name_gives_same_result(skeleton_copy: Path) -> None:
    add_line(skeleton_copy, "api/__init__.py", "import pricewatch.application")

    short = run_deps(skeleton_copy, "application")
    full = run_deps(skeleton_copy, "pricewatch.application")

    assert short.stdout == full.stdout == "pricewatch.api\n"


def test_imports_inside_package_are_not_dependents(skeleton_copy: Path) -> None:
    add_line(skeleton_copy, "domain/money.py", "import decimal")
    add_line(skeleton_copy, "domain/totals.py", "from pricewatch.domain import money")

    result = run_deps(skeleton_copy, "domain")

    assert result.returncode == 0
    assert result.stdout == "У pricewatch.domain нет прямых зависимых.\n"


def test_submodule_dependents_include_siblings(skeleton_copy: Path) -> None:
    add_line(skeleton_copy, "domain/money.py", "import decimal")
    add_line(skeleton_copy, "domain/totals.py", "from pricewatch.domain import money")
    add_line(skeleton_copy, "application/__init__.py", "import pricewatch.domain.money")

    result = run_deps(skeleton_copy, "domain.money")

    assert result.stdout.splitlines() == ["pricewatch.application", "pricewatch.domain.totals"]


def test_import_of_submodule_counts_for_package(skeleton_copy: Path) -> None:
    add_line(skeleton_copy, "domain/money.py", "import decimal")
    add_line(skeleton_copy, "application/__init__.py", "from pricewatch.domain.money import decimal")

    result = run_deps(skeleton_copy, "domain")

    assert result.stdout.splitlines() == ["pricewatch.application"]


def test_unknown_module_fails(skeleton_copy: Path) -> None:
    result = run_deps(skeleton_copy, "nonexistent")

    assert result.returncode == 1
    assert "pricewatch.nonexistent не найден" in result.stderr
    assert result.stdout == ""


def test_runs_against_current_project() -> None:
    result = run_deps(PROJECT_ROOT, "domain")

    assert result.returncode == 0, result.stderr
