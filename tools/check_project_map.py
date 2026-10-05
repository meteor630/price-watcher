"""Проверка PROJECT_MAP.md против кода.

Запуск: uv run python tools/check_project_map.py

Формат, который понимает проверка:
- каждый пакет pricewatch описан разделом `### pricewatch.<имя>`
  с полями `- Путь:`, `- Отвечает:`, `- Зависит от:`, `- Тесты:`;
- `Зависит от:` перечисляет пакеты в обратных кавычках или содержит `—`;
- в строках `Путь:` и `Файлы:` каждое значение в обратных кавычках — путь от корня;
- в строках `Тесты:` и `Команда:` значение в обратных кавычках — команда,
  пути среди её аргументов тоже проверяются.
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import grimp
from deps import PROJECT_ROOT, ROOT_PACKAGE, build_graph

MAP_NAME = "PROJECT_MAP.md"
REQUIRED_FIELDS = ("Путь", "Отвечает", "Зависит от", "Тесты")
PATH_FIELDS = {"Путь", "Файлы"}
COMMAND_FIELDS = {"Тесты", "Команда"}
NOTHING = "—"
FILE_SUFFIXES = {".py", ".md", ".toml", ".lock", ".css", ".js", ".html", ".json", ".yaml", ".yml", ".png", ".swift"}

HEADING = re.compile(rf"^###\s+({ROOT_PACKAGE}\.\w+)\s*$")
FIELD = re.compile(r"^-\s+([^:]+):\s*(.*)$")
CODE = re.compile(r"`([^`]+)`")


def parse_packages(text: str) -> dict[str, dict[str, str]]:
    packages: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in text.splitlines():
        if line.startswith("#"):
            heading = HEADING.match(line)
            current = packages.setdefault(heading.group(1), {}) if heading else None
            continue
        field = FIELD.match(line)
        if current is not None and field:
            current[field.group(1).strip()] = field.group(2).strip()
    return packages


def referenced_paths(text: str) -> list[str]:
    paths = []
    for line in text.splitlines():
        field = FIELD.match(line)
        if not field:
            continue
        name, value = field.group(1).strip(), field.group(2)
        for code in CODE.findall(value):
            if name in PATH_FIELDS:
                paths.append(code)
            elif name in COMMAND_FIELDS:
                paths.extend(arg.split("::")[0] for arg in code.split() if _looks_like_path(arg))
    return paths


def _looks_like_path(arg: str) -> bool:
    if arg.startswith(("-", "/", "<")) or "://" in arg:
        return False
    return "/" in arg or Path(arg.split("::")[0]).suffix in FILE_SUFFIXES


def path_exists(root: Path, path: str) -> bool:
    if "*" in path:
        return any(root.glob(path))
    return (root / path).exists()


def package_dependencies(graph: grimp.ImportGraph) -> dict[str, set[str]]:
    dependencies: dict[str, set[str]] = defaultdict(set)
    for module in graph.modules:
        package = _top_package(module)
        for imported in graph.find_modules_directly_imported_by(module):
            if _top_package(imported) != package:
                dependencies[package].add(_top_package(imported))
    return dependencies


def _top_package(module: str) -> str:
    return ".".join(module.split(".")[:2])


def code_packages(root: Path) -> set[str]:
    source = root / "src" / ROOT_PACKAGE
    return {f"{ROOT_PACKAGE}.{init.parent.name}" for init in source.glob("*/__init__.py")}


def check(root: Path) -> list[str]:
    map_path = root / MAP_NAME
    if not map_path.is_file():
        return [f"Нет файла {MAP_NAME} в {root}"]
    text = map_path.read_text(encoding="utf-8")
    described = parse_packages(text)
    errors = []

    headings = [match.group(1) for line in text.splitlines() if (match := HEADING.match(line))]
    for package in sorted({name for name in headings if headings.count(name) > 1}):
        errors.append(f"{package}: пакет описан в карте несколько раз")

    existing = code_packages(root)
    for package in sorted(existing - described.keys()):
        errors.append(f"{package}: пакет есть в коде, но не описан в карте")
    for package in sorted(described.keys() - existing):
        errors.append(f"{package}: пакет описан в карте, но его нет в коде")

    actual = package_dependencies(build_graph(root))
    for package in sorted(described.keys() & existing):
        fields = described[package]
        for name in REQUIRED_FIELDS:
            if not fields.get(name):
                errors.append(f"{package}: нет поля «{name}»")
        expected_path = f"src/{package.replace('.', '/')}/"
        if "Путь" in fields and f"`{expected_path}`" not in fields["Путь"]:
            errors.append(f"{package}: «Путь» должен быть `{expected_path}`")
        if fields.get("Зависит от"):
            errors.extend(_check_dependencies(package, fields["Зависит от"], actual[package]))

    for path in referenced_paths(text):
        if not path_exists(root, path):
            errors.append(f"Путь не существует: {path}")
    return errors


def _check_dependencies(package: str, value: str, actual: set[str]) -> list[str]:
    listed = set(CODE.findall(value))
    if not listed and value != NOTHING:
        return [f"{package}: «Зависит от» — пакеты в обратных кавычках или {NOTHING}"]
    errors = []
    for missing in sorted(actual - listed):
        errors.append(f"{package}: импортирует {missing}, но в карте это не указано")
    for extra in sorted(listed - actual):
        errors.append(f"{package}: в карте указана зависимость от {extra}, но импорта нет")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)

    errors = check(args.root)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    print(f"{MAP_NAME} соответствует коду.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
