"""tools/check_project_map.py: карта проекта сверяется с кодом."""

import subprocess
import sys
from pathlib import Path

import pytest

PACKAGES = [
    "api",
    "application",
    "bootstrap",
    "bot",
    "domain",
    "observability",
    "parsers",
    "persistence",
    "scheduler",
    "web",
    "worker",
]
LAYOUT_TESTS = "`uv run pytest tests/unit/test_package_layout.py`"


def package_section(name: str, **fields: str) -> str:
    values = {
        "Путь": f"`src/pricewatch/{name}/`",
        "Отвечает": "описание",
        "Зависит от": "—",
        "Тесты": LAYOUT_TESTS,
    } | fields
    lines = [f"### pricewatch.{name}"]
    lines += [f"- {field}: {value}" for field, value in values.items() if value is not None]
    return "\n".join(lines)


def write_map(root: Path, overrides: dict[str, dict[str, str]] | None = None, extra: str = "") -> None:
    overrides = overrides or {}
    sections = [package_section(name, **overrides.get(name, {})) for name in PACKAGES]
    (root / "PROJECT_MAP.md").write_text("# Карта\n\n## Пакеты\n\n" + "\n\n".join(sections) + "\n" + extra)


def run_check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "tools" / "check_project_map.py"), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def add_line(root: Path, package: str, line: str) -> None:
    init = root / "src" / "pricewatch" / package / "__init__.py"
    init.write_text(init.read_text() + line + "\n")


def test_valid_map_passes(project_copy: Path) -> None:
    write_map(project_copy)

    result = run_check(project_copy)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "PROJECT_MAP.md соответствует коду.\n"


def test_missing_map_fails(project_copy: Path) -> None:
    result = run_check(project_copy)

    assert result.returncode == 1
    assert "Нет файла PROJECT_MAP.md" in result.stderr


def test_undescribed_package_fails(project_copy: Path) -> None:
    write_map(project_copy)
    (project_copy / "src" / "pricewatch" / "extra").mkdir()
    (project_copy / "src" / "pricewatch" / "extra" / "__init__.py").write_text("")

    result = run_check(project_copy)

    assert result.returncode == 1
    assert "pricewatch.extra: пакет есть в коде, но не описан в карте" in result.stderr


def test_described_package_without_code_fails(project_copy: Path) -> None:
    write_map(project_copy, extra="\n" + package_section("ghost", Путь="`src/`"))

    result = run_check(project_copy)

    assert "pricewatch.ghost: пакет описан в карте, но его нет в коде" in result.stderr


def test_duplicate_section_fails(project_copy: Path) -> None:
    write_map(project_copy, extra="\n" + package_section("domain"))

    result = run_check(project_copy)

    assert "pricewatch.domain: пакет описан в карте несколько раз" in result.stderr


@pytest.mark.parametrize("field", ["Путь", "Отвечает", "Зависит от", "Тесты"])
def test_missing_field_fails(project_copy: Path, field: str) -> None:
    write_map(project_copy, {"domain": {field: None}})

    result = run_check(project_copy)

    assert result.returncode == 1
    assert f"pricewatch.domain: нет поля «{field}»" in result.stderr


def test_wrong_package_path_fails(project_copy: Path) -> None:
    write_map(project_copy, {"domain": {"Путь": "`src/pricewatch/application/`"}})

    result = run_check(project_copy)

    assert "pricewatch.domain: «Путь» должен быть `src/pricewatch/domain/`" in result.stderr


def test_unlisted_dependency_fails(project_copy: Path) -> None:
    add_line(project_copy, "application", "import pricewatch.domain")
    write_map(project_copy)

    result = run_check(project_copy)

    assert "pricewatch.application: импортирует pricewatch.domain, но в карте это не указано" in result.stderr


def test_listed_dependency_without_import_fails(project_copy: Path) -> None:
    write_map(project_copy, {"application": {"Зависит от": "`pricewatch.domain`"}})

    result = run_check(project_copy)

    assert (
        "pricewatch.application: в карте указана зависимость от pricewatch.domain, но импорта нет"
        in result.stderr
    )


def test_matching_dependencies_pass(project_copy: Path) -> None:
    add_line(project_copy, "application", "import pricewatch.domain")
    add_line(project_copy, "bootstrap", "import pricewatch.application")
    add_line(project_copy, "bootstrap", "from pricewatch.persistence import __doc__")
    write_map(
        project_copy,
        {
            "application": {"Зависит от": "`pricewatch.domain`"},
            "bootstrap": {"Зависит от": "`pricewatch.application`, `pricewatch.persistence`"},
        },
    )

    result = run_check(project_copy)

    assert result.returncode == 0, result.stderr


def test_dependency_in_free_text_fails(project_copy: Path) -> None:
    write_map(project_copy, {"domain": {"Зависит от": "ничего"}})

    result = run_check(project_copy)

    assert "pricewatch.domain: «Зависит от» — пакеты в обратных кавычках или —" in result.stderr


def test_missing_file_path_fails(project_copy: Path) -> None:
    write_map(project_copy, extra="\n## Инструменты\n\n- Файлы: `tools/deps.py`, `tools/absent.py`\n")

    result = run_check(project_copy)

    assert result.stderr == "Путь не существует: tools/absent.py\n"


def test_missing_path_in_command_fails(project_copy: Path) -> None:
    write_map(project_copy, {"domain": {"Тесты": "`uv run pytest tests/unit/test_absent.py::test_one -q`"}})

    result = run_check(project_copy)

    assert result.stderr == "Путь не существует: tests/unit/test_absent.py\n"


def test_glob_must_match_something(project_copy: Path) -> None:
    write_map(project_copy, extra="\n- Файлы: `src/pricewatch/*/__init__.py`, `src/pricewatch/*/absent_*.py`\n")

    result = run_check(project_copy)

    assert result.stderr == "Путь не существует: src/pricewatch/*/absent_*.py\n"


def test_non_path_values_are_ignored(project_copy: Path) -> None:
    (project_copy / "design_reference").mkdir()
    (project_copy / "design_reference" / "домашняя страница.png").write_bytes(b"")
    write_map(
        project_copy,
        extra=(
            "\n- Файлы: `design_reference/домашняя страница.png`\n"
            "- Команда: `uv run python tools/deps.py <модуль> --root .`\n"
            "- Маршруты: `/api/v1/items`, `pricewatch.domain`, `price_minor`\n"
        ),
    )

    result = run_check(project_copy)

    assert result.returncode == 0, result.stderr


def test_project_map_matches_current_code() -> None:
    project_root = Path(__file__).resolve().parents[2]

    result = run_check(project_root)

    assert result.returncode == 0, result.stderr
