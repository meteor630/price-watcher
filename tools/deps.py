"""Прямые зависимые модуля: кто импортирует его напрямую.

Запуск: uv run python tools/deps.py <модуль>
Модуль указывается как `application`, `pricewatch.application`
или `pricewatch.domain.money`.
"""

import argparse
import sys
from pathlib import Path

import grimp

ROOT_PACKAGE = "pricewatch"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def build_graph(root: Path) -> grimp.ImportGraph:
    # Граф строится по коду из root/src, а не по установленной копии пакета.
    sys.path.insert(0, str(root / "src"))
    return grimp.build_graph(ROOT_PACKAGE, cache_dir=None)


def normalize(module: str) -> str:
    if module == ROOT_PACKAGE or module.startswith(f"{ROOT_PACKAGE}."):
        return module
    return f"{ROOT_PACKAGE}.{module}"


def direct_dependents(graph: grimp.ImportGraph, module: str) -> list[str]:
    """Модули вне module и его потомков, которые напрямую импортируют module или его потомков."""
    if module not in graph.modules:
        raise LookupError(f"Модуль {module} не найден в {ROOT_PACKAGE}")
    target = {module} | graph.find_descendants(module)
    importers: set[str] = set()
    for imported in target:
        importers |= graph.find_modules_that_directly_import(imported)
    return sorted(importers - target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("module")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)

    module = normalize(args.module)
    try:
        dependents = direct_dependents(build_graph(args.root), module)
    except LookupError as error:
        print(error, file=sys.stderr)
        return 1

    if not dependents:
        print(f"У {module} нет прямых зависимых.")
    for dependent in dependents:
        print(dependent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
