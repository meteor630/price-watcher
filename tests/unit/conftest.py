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
