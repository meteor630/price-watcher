"""tools/deps.py: прямые зависимые модуля."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_deps(root: Path, module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "tools" / "deps.py"), module, "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def add_line(root: Path, module_file: str, line: str) -> None:
    path = root / "src" / "pricewatch" / module_file
    existing = path.read_text() if path.exists() else ""
    path.write_text(existing + line + "\n")


def test_lists_packages_that_import_module(project_copy: Path) -> None:
    add_line(project_copy, "api/__init__.py", "import pricewatch.application")
    add_line(project_copy, "bootstrap/__init__.py", "from pricewatch import application")

    result = run_deps(project_copy, "application")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["pricewatch.api", "pricewatch.bootstrap"]


def test_full_module_name_gives_same_result(project_copy: Path) -> None:
    add_line(project_copy, "api/__init__.py", "import pricewatch.application")

    short = run_deps(project_copy, "application")
    full = run_deps(project_copy, "pricewatch.application")

    assert short.stdout == full.stdout == "pricewatch.api\n"


def test_imports_inside_package_are_not_dependents(project_copy: Path) -> None:
    add_line(project_copy, "domain/money.py", "import decimal")
    add_line(project_copy, "domain/totals.py", "from pricewatch.domain import money")

    result = run_deps(project_copy, "domain")

    assert result.returncode == 0
    assert result.stdout == "У pricewatch.domain нет прямых зависимых.\n"


def test_submodule_dependents_include_siblings(project_copy: Path) -> None:
    add_line(project_copy, "domain/money.py", "import decimal")
    add_line(project_copy, "domain/totals.py", "from pricewatch.domain import money")
    add_line(project_copy, "application/__init__.py", "import pricewatch.domain.money")

    result = run_deps(project_copy, "domain.money")

    assert result.stdout.splitlines() == ["pricewatch.application", "pricewatch.domain.totals"]


def test_import_of_submodule_counts_for_package(project_copy: Path) -> None:
    add_line(project_copy, "domain/money.py", "import decimal")
    add_line(project_copy, "application/__init__.py", "from pricewatch.domain.money import decimal")

    result = run_deps(project_copy, "domain")

    assert result.stdout.splitlines() == ["pricewatch.application"]


def test_unknown_module_fails(project_copy: Path) -> None:
    result = run_deps(project_copy, "nonexistent")

    assert result.returncode == 1
    assert "pricewatch.nonexistent не найден" in result.stderr
    assert result.stdout == ""


def test_runs_against_current_project() -> None:
    result = run_deps(PROJECT_ROOT, "domain")

    assert result.returncode == 0, result.stderr
