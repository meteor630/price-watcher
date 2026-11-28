"""Список доменов домашнего worker: python -m pricewatch.worker.allow [--file allowlist.txt] list | add <домен> | remove <домен>.

Только владелец на домашнем узле меняет этот файл; сервер его не видит. Worker читает список на каждом задании.
Коды выхода: 0 — готово, 2 — неверный домен или нет файла (для list и remove).
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pricewatch.observability.json_log import use_utf8_streams
from pricewatch.worker.allowlist import AllowlistUnusable, clean_domain, read_domains

EXIT_INVALID = 2
HEADER = "# Домены, которые домашний worker может открывать (сам домен и его поддомены). Меняет только владелец.\n"


def _write(path: Path, domains: frozenset[str]) -> None:
    path.write_text(HEADER + "".join(f"{domain}\n" for domain in sorted(domains)), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pricewatch.worker.allow")
    parser.add_argument("--file", type=Path, default=Path("allowlist.txt"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="показать разрешённые домены")
    for name, text in (("add", "разрешить домен"), ("remove", "убрать домен")):
        command = commands.add_parser(name, help=text)
        command.add_argument("domain")
    args = parser.parse_args(argv)

    if not args.file.exists() and args.command == "add":
        domains: frozenset[str] = frozenset()
    else:
        try:
            domains = read_domains(args.file)
        except AllowlistUnusable as error:
            # Испорченный файл не перезаписывается: владелец правит его сам.
            print(f"Список не читается: {error}")
            return EXIT_INVALID
    if args.command == "list":
        print("\n".join(sorted(domains)) or "(список пуст)")
        return 0
    try:
        domain = clean_domain(args.domain)
    except ValueError as error:
        print(str(error))
        return EXIT_INVALID
    if args.command == "add":
        _write(args.file, domains | {domain})
        print(f"Разрешён: {domain}")
    else:
        _write(args.file, domains - {domain})
        print(f"Убран: {domain}")
    return 0


if __name__ == "__main__":
    use_utf8_streams()
    sys.exit(main())
