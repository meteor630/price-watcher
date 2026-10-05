import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def project_copy(tmp_path: Path) -> Path:
    """Копия кода, инструментов и конфигурации, которую тест может ломать."""
    shutil.copytree(PROJECT_ROOT / "src", tmp_path / "src")
    shutil.copytree(PROJECT_ROOT / "tools", tmp_path / "tools")
    shutil.copytree(PROJECT_ROOT / "tests", tmp_path / "tests")
    shutil.copy(PROJECT_ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    return tmp_path


@pytest.fixture
def skeleton_copy(project_copy: Path) -> Path:
    """Копия, где у пакетов pricewatch остались только пустые __init__.py.

    Для тестов инструментов: связи между пакетами создаёт только сам тест,
    а не настоящий код, который растёт от среза к срезу.
    """
    source = project_copy / "src" / "pricewatch"
    for package in [init.parent for init in source.glob("*/__init__.py")]:
        shutil.rmtree(package)
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
    return project_copy
