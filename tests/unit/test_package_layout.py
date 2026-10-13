import importlib
import pkgutil
from pathlib import Path

import pytest

import pricewatch

# Пакеты из DEVELOPMENT_ARCHITECTURE.md, раздел 3.
ARCHITECTURE_PACKAGES = {
    "domain",
    "application",
    "persistence",
    "api",
    "web",
    "parsers",
    "worker",
    "media",
    "scheduler",
    "bot",
    "backup",
    "observability",
    "bootstrap",
    "config",
}


@pytest.mark.parametrize("name", sorted(ARCHITECTURE_PACKAGES))
def test_package_imports(name: str) -> None:
    module = importlib.import_module(f"pricewatch.{name}")

    assert module.__doc__, f"pricewatch.{name} должен описывать свою ответственность"


def test_packages_match_architecture() -> None:
    found = {info.name for info in pkgutil.iter_modules(pricewatch.__path__) if info.ispkg}

    assert found == ARCHITECTURE_PACKAGES


def test_package_installed_from_src() -> None:
    # Сравнение по частям пути: на Windows разделитель — обратная косая черта.
    assert any(Path(path).parts[-2:] == ("src", "pricewatch") for path in pricewatch.__path__)
