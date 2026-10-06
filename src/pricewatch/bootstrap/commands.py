"""Команды сервера: python -m pricewatch.bootstrap [serve | create-admin | create-worker | rotate-worker-token].

Пароль никогда не передаётся аргументом (он попал бы в историю оболочки и список
процессов): только скрытый ввод дважды или одна строка из stdin (--password-stdin).
Коды выхода: 0 — успех, 1 — неверные данные, 2 — конфигурация, 3 — база.
"""

import argparse
from datetime import UTC, datetime
import getpass
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from sqlalchemy import Engine

from pricewatch.application.accounts import create_admin
from pricewatch.application.worker_protocol import create_worker, rotate_worker_token
from pricewatch.application.passwords import PasswordHasher
from pricewatch.bootstrap.server import EXIT_CONFIG, EXIT_DATABASE, check_database, serve
from pricewatch.config.settings import ConfigError, load_settings
from pricewatch.domain.accounts import PASSWORD_MAX, PASSWORD_MIN
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.observability.json_log import configure_logging
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

EXIT_INVALID = 1

FIELD_PROBLEMS = {
    ("login", "format"): "логин: 3–64 символа — латиница, цифры, точка, дефис, подчёркивание",
    ("password", "too_short"): f"пароль: не короче {PASSWORD_MIN} символов",
    ("password", "too_long"): f"пароль: не длиннее {PASSWORD_MAX} символов",
    ("password", "same_as_login"): "пароль: не должен совпадать с логином",
    ("display_name", "empty"): "имя: не может быть пустым",
    ("display_name", "too_long"): "имя: не длиннее 100 символов",
    ("display_name", "invalid_characters"): "имя: недопустимые символы",
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pricewatch.bootstrap")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("serve", help="запустить сервер (по умолчанию)")
    admin = commands.add_parser("create-admin", help="создать администратора")
    admin.add_argument("--login", required=True)
    admin.add_argument("--name", help="отображаемое имя; по умолчанию — логин")
    admin.add_argument("--password-stdin", action="store_true", help="прочитать пароль одной строкой из stdin")
    worker = commands.add_parser("create-worker", help="зарегистрировать worker и выпустить его токен")
    worker.add_argument("--name", required=True)
    rotate = commands.add_parser("rotate-worker-token", help="выпустить новый токен worker; старый действует до первого входа с новым")
    rotate.add_argument("--name", required=True)
    args = parser.parse_args(argv)

    if args.command in (None, "serve"):
        return serve()
    if args.command == "create-worker":
        return worker_token_command(args.name, rotate=False)
    if args.command == "rotate-worker-token":
        return worker_token_command(args.name, rotate=True)
    return create_admin_command(args.login, args.name or args.login, args.password_stdin)


def create_admin_command(
    login: str,
    display_name: str,
    password_stdin: bool,
    *,
    env_file: Path = Path(".env"),
    prompt: Callable[[str], str] = getpass.getpass,
    stdin: TextIO | None = None,
    out: TextIO | None = None,
) -> int:
    out = out or sys.stdout
    try:
        settings = load_settings(env_file)
    except ConfigError as error:
        print(error, file=out)
        return EXIT_CONFIG
    configure_logging("WARNING")
    engine = create_db_engine(settings.database_url.get_secret_value())
    try:
        return _create_admin(engine, login, display_name, password_stdin, prompt, stdin, out)
    finally:
        engine.dispose()


def _create_admin(
    engine: Engine,
    login: str,
    display_name: str,
    password_stdin: bool,
    prompt: Callable[[str], str],
    stdin: TextIO | None,
    out: TextIO,
) -> int:
    if not check_database(engine):
        return EXIT_DATABASE

    if password_stdin:
        password = (stdin or sys.stdin).readline().rstrip("\r\n")
    else:
        password = prompt("Пароль администратора: ")
        if prompt("Повторите пароль: ") != password:
            print("Пароли не совпадают. Администратор не создан.", file=out)
            return EXIT_INVALID

    try:
        user = create_admin(SqlUnitOfWork(engine), PasswordHasher(), login, password, display_name)
    except PriceWatcherError as error:
        print(_explain(error), file=out)
        return EXIT_INVALID
    print(f"Администратор «{user.login}» создан (id {user.id}).", file=out)
    return 0


def worker_token_command(name: str, *, rotate: bool, env_file: Path = Path(".env"), out: TextIO | None = None) -> int:
    """Токен worker печатается один раз: его нужно сразу записать в настройки worker, в базе — только HMAC."""
    out = out or sys.stdout
    try:
        settings = load_settings(env_file)
    except ConfigError as error:
        print(error, file=out)
        return EXIT_CONFIG
    configure_logging("WARNING")
    engine = create_db_engine(settings.database_url.get_secret_value())
    try:
        if not check_database(engine):
            return EXIT_DATABASE
        key = settings.secret_key.get_secret_value().encode()
        work = SqlUnitOfWork(engine)
        try:
            if rotate:
                token = rotate_worker_token(work, key, name)
            else:
                token = create_worker(work, key, name, datetime.now(UTC))
        except PriceWatcherError as error:
            print(f"Токен не выпущен: {error.safe_message}", file=out)
            return EXIT_INVALID
    finally:
        engine.dispose()
    print(f"Токен worker «{name}» (показывается один раз, сохраните в PRICEWATCH_WORKER_TOKEN worker):", file=out)
    print(token, file=out)
    return 0


def _explain(error: PriceWatcherError) -> str:
    fields = error.details.get("fields")
    if error.code is ErrorCode.VALIDATION_ERROR and isinstance(fields, Mapping):
        problems = [FIELD_PROBLEMS.get((field, reason), f"{field}: неверное значение") for field, reason in fields.items()]
        return "Администратор не создан: " + "; ".join(problems) + "."
    return f"Администратор не создан: {error.safe_message}"
