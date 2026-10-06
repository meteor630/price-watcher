"""Контракты import-linter из pyproject.toml ловят нарушения границ модулей."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# which учитывает расширение .exe на Windows.
LINT_IMPORTS = shutil.which("lint-imports", path=str(Path(sys.executable).parent))

DOMAIN = "domain импортирует только стандартную библиотеку"
APPLICATION = "application зависит только от domain"
PERSISTENCE_OUTWARD = "persistence не импортирует api, web, parsers, worker, scheduler, bot, bootstrap"
PERSISTENCE_ONLY_BOOTSTRAP = "persistence импортирует только bootstrap"
BOOTSTRAP_ENTRY = "bootstrap никто не импортирует: обход сценариев через него невозможен"
WORKER_BOT = "worker и bot не импортируют persistence"
STORES = "адаптеры магазинов не импортируют друг друга"


def run_lint(root: Path) -> subprocess.CompletedProcess[str]:
    assert LINT_IMPORTS is not None, "lint-imports не установлен рядом с интерпретатором"
    return subprocess.run(
        [str(LINT_IMPORTS), "--no-cache", "--config", str(root / "pyproject.toml")],
        cwd=root,
        # Окружение целиком: без SYSTEMROOT Python на Windows не запускается. Вывод — UTF-8 и без
        # переноса строк: иначе длинное имя контракта при узком терминале разбивается на две строки.
        env={**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONIOENCODING": "utf-8", "COLUMNS": "1000"},
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def add_line(root: Path, package: str, line: str) -> None:
    init = root / "src" / "pricewatch" / package / "__init__.py"
    init.write_text(init.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")


def broken(result: subprocess.CompletedProcess[str]) -> set[str]:
    return {line.removesuffix(" BROKEN") for line in result.stdout.splitlines() if line.endswith(" BROKEN")}


def test_current_code_keeps_contracts() -> None:
    result = run_lint(PROJECT_ROOT)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("package", "line", "contract"),
    [
        ("domain", "import pricewatch.application", DOMAIN),
        ("domain", "import pricewatch.observability", DOMAIN),
        ("domain", "import pytest", DOMAIN),
        ("domain", "from fastapi import FastAPI", DOMAIN),
        ("domain", "import decimalx", DOMAIN),
        ("application", "import pricewatch.persistence", APPLICATION),
        ("application", "import pricewatch.api", APPLICATION),
        ("application", "import pricewatch.config", APPLICATION),
        ("domain", "from pricewatch.config import settings", DOMAIN),
        ("persistence", "import pricewatch.api", PERSISTENCE_OUTWARD),
        ("api", "import pricewatch.persistence", PERSISTENCE_ONLY_BOOTSTRAP),
        ("scheduler", "import pricewatch.persistence", PERSISTENCE_ONLY_BOOTSTRAP),
        ("observability", "import pricewatch.persistence", PERSISTENCE_ONLY_BOOTSTRAP),
        ("api", "import pricewatch.bootstrap", BOOTSTRAP_ENTRY),
        ("scheduler", "from pricewatch import bootstrap", BOOTSTRAP_ENTRY),
        ("worker", "import pricewatch.persistence", WORKER_BOT),
        ("bot", "import pricewatch.persistence", WORKER_BOT),
    ],
)
def test_violation_breaks_contract(project_copy: Path, package: str, line: str, contract: str) -> None:
    add_line(project_copy, package, line)

    result = run_lint(project_copy)

    assert result.returncode == 1
    assert contract in broken(result)


def test_indirect_path_to_persistence_is_broken(project_copy: Path) -> None:
    # worker -> parsers -> persistence: обход через промежуточный модуль тоже запрещён.
    add_line(project_copy, "worker", "import pricewatch.parsers")
    add_line(project_copy, "parsers", "import pricewatch.persistence")

    result = run_lint(project_copy)

    assert {WORKER_BOT, PERSISTENCE_ONLY_BOOTSTRAP} <= broken(result)


def test_store_adapter_importing_another_breaks_contract(project_copy: Path) -> None:
    stores = project_copy / "src" / "pricewatch" / "parsers" / "stores"
    (stores / "other_shop.py").write_text("from pricewatch.parsers.stores import wildberries\n", encoding="utf-8")

    result = run_lint(project_copy)

    assert STORES in broken(result)


def test_new_store_adapter_using_shared_parts_keeps_contracts(project_copy: Path) -> None:
    stores = project_copy / "src" / "pricewatch" / "parsers" / "stores"
    (stores / "other_shop.py").write_text(
        "from pricewatch.parsers import browser, contract, generic\nfrom pricewatch.domain import errors\n", encoding="utf-8"
    )

    result = run_lint(project_copy)

    assert result.returncode == 0, result.stdout + result.stderr


def test_package_with_domain_prefix_is_not_domain(project_copy: Path) -> None:
    lookalike = project_copy / "src" / "pricewatch" / "domainx"
    lookalike.mkdir()
    (lookalike / "__init__.py").write_text("", encoding="utf-8")
    add_line(project_copy, "domain", "import pricewatch.domainx")

    result = run_lint(project_copy)

    assert DOMAIN in broken(result)


def test_domain_violation_names_module_and_line(project_copy: Path) -> None:
    add_line(project_copy, "domain", "import pytest")

    result = run_lint(project_copy)

    assert "pricewatch.domain -> pytest (l.2)" in result.stdout


def test_allowed_dependencies_keep_contracts(project_copy: Path) -> None:
    (project_copy / "src" / "pricewatch" / "domain" / "money.py").write_text(
        "from __future__ import annotations\n\nfrom decimal import Decimal\n", encoding="utf-8"
    )
    add_line(project_copy, "domain", "import datetime")
    add_line(project_copy, "domain", "import os.path")
    add_line(project_copy, "domain", "from pricewatch.domain import money")
    add_line(project_copy, "application", "import pricewatch.domain")
    add_line(project_copy, "persistence", "import pricewatch.application")
    add_line(project_copy, "api", "import pricewatch.application")
    add_line(project_copy, "worker", "import pricewatch.parsers")
    add_line(project_copy, "bootstrap", "import pricewatch.persistence")
    add_line(project_copy, "bootstrap", "import pricewatch.api")
    add_line(project_copy, "bootstrap", "import pricewatch.scheduler")
    add_line(project_copy, "api", "import pricewatch.config")
    add_line(project_copy, "bootstrap", "import pricewatch.config")

    result = run_lint(project_copy)

    assert result.returncode == 0, result.stdout + result.stderr
