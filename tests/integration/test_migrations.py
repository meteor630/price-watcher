"""Миграции: вверх, вниз, снова вверх; модели совпадают со схемой; команда alembic работает."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, make_url, text

from pricewatch.persistence.models import Base
from pricewatch.persistence.schema import alembic_config

ROOT = Path(__file__).resolve().parents[2]
AUTH_TABLES = {
    "users", "categories", "groups", "items",
    "invites", "web_sessions", "devices", "refresh_tokens", "access_tokens", "pairing_codes", "auth_failures",
}
COLLECTION_TABLES = AUTH_TABLES | {
    "stores", "tracked_links", "item_links", "parse_runs", "check_tasks", "workers", "price_observations", "settings",
}
TABLES = COLLECTION_TABLES | {"notifications", "idempotency_keys", "price_decisions", "audit_events", "client_error_reports", "services", "telegram_link_codes", "backup_runs"}


def tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def columns(url: str, table: str) -> set[str]:
    engine = create_engine(url)
    try:
        return {column["name"] for column in inspect(engine).get_columns(table)}
    finally:
        engine.dispose()


def revision(url: str) -> str | None:
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def test_upgrade_downgrade_upgrade(empty_database_url: str) -> None:
    config = alembic_config(empty_database_url)

    command.upgrade(config, "head")
    assert tables(empty_database_url) == TABLES
    assert revision(empty_database_url) == "0015"

    command.downgrade(config, "0014")
    assert "backup_runs" not in tables(empty_database_url)
    assert revision(empty_database_url) == "0014"

    command.downgrade(config, "0013")
    assert "summarized_at" not in columns(empty_database_url, "parse_runs")
    assert revision(empty_database_url) == "0013"

    command.downgrade(config, "0012")
    assert "silent_since" not in columns(empty_database_url, "workers") | columns(empty_database_url, "services")
    assert revision(empty_database_url) == "0012"

    command.downgrade(config, "0011")
    assert {"services", "telegram_link_codes"}.isdisjoint(tables(empty_database_url))
    assert "claimed_until" not in columns(empty_database_url, "notifications")
    assert revision(empty_database_url) == "0011"

    command.downgrade(config, "0010")
    assert "client_error_reports" not in tables(empty_database_url)
    assert revision(empty_database_url) == "0010"

    command.downgrade(config, "0009")
    assert "repeat_count" not in columns(empty_database_url, "notifications")
    assert revision(empty_database_url) == "0009"

    command.downgrade(config, "0008")
    assert "audit_events" not in tables(empty_database_url)
    assert revision(empty_database_url) == "0008"

    command.downgrade(config, "0007")
    assert "price_decisions" not in tables(empty_database_url)
    assert revision(empty_database_url) == "0007"

    command.downgrade(config, "0006")
    assert {"purchase_price_minor", "purchase_quantity", "purchase_store_id", "purchase_baseline_minor", "archived_at"}.isdisjoint(
        columns(empty_database_url, "items")
    )
    assert revision(empty_database_url) == "0006"

    command.downgrade(config, "0005")
    assert "idempotency_keys" not in tables(empty_database_url)
    assert "include_stale" in columns(empty_database_url, "items")

    command.downgrade(config, "0004")
    assert "include_stale" not in columns(empty_database_url, "items")
    assert revision(empty_database_url) == "0004"

    command.downgrade(config, "0003")
    assert tables(empty_database_url) == COLLECTION_TABLES
    assert revision(empty_database_url) == "0003"

    command.downgrade(config, "0002")
    assert tables(empty_database_url) == AUTH_TABLES
    assert {"state", "purchased_at"}.isdisjoint(columns(empty_database_url, "items"))
    assert revision(empty_database_url) == "0002"

    command.downgrade(config, "0001")
    assert tables(empty_database_url) == {"users", "categories", "groups", "items"}
    assert revision(empty_database_url) == "0001"

    command.downgrade(config, "base")
    assert tables(empty_database_url) == set()
    assert revision(empty_database_url) is None

    command.upgrade(config, "head")
    assert tables(empty_database_url) == TABLES


def test_models_match_migrated_schema(migrated_database_url: str) -> None:
    engine = create_engine(migrated_database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection, opts={"compare_type": True, "compare_server_default": True}
            )
            differences = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert differences == []


def test_alembic_command_uses_settings_and_logs_json(empty_database_url: str, tmp_path: Path) -> None:
    # Отдельная роль с настоящим паролем: проверка работает и там, где пароль проверяется (CI),
    # и там, где вход без пароля (локальный trust). Роль становится владельцем тестовой базы.
    role = f"cli_{uuid.uuid4().hex[:8]}"
    password = "cli-Pw%40:/@" + uuid.uuid4().hex[:6]
    admin = create_engine(empty_database_url, isolation_level="AUTOCOMMIT")
    database = make_url(empty_database_url).database
    with admin.connect() as connection:
        original_owner = connection.execute(
            text("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database()")
        ).scalar_one()
        escaped = password.replace("'", "''")
        connection.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{escaped}'"))
        connection.execute(text(f'ALTER DATABASE "{database}" OWNER TO {role}'))
    url = make_url(empty_database_url).set(username=role, password=password).render_as_string(hide_password=False)
    env = {name: value for name, value in os.environ.items() if not name.startswith("PRICEWATCH_")} | {
        "PRICEWATCH_ENV": "test",
        "PRICEWATCH_PUBLIC_ORIGIN": "http://localhost:8000",
        "PRICEWATCH_DATABASE_URL": url,
        "PRICEWATCH_SECRET_KEY": "test-secret-key-" + "k" * 32,
        # Кодировка вывода как у Windows при записи в канал: журнал миграций обязан писать UTF-8 сам.
        "PYTHONIOENCODING": "cp1252",
    }
    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ROOT / "alembic.ini"), "upgrade", "head"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        entries = [json.loads(line) for line in result.stdout.splitlines()]
        assert any("Running upgrade" in entry["message"] for entry in entries)
        assert password not in result.stdout + result.stderr
        assert quote(password, safe="") not in result.stdout + result.stderr
        assert tables(empty_database_url) == TABLES
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'ALTER DATABASE "{database}" OWNER TO "{original_owner}"'))
            connection.execute(text(f"REASSIGN OWNED BY {role} TO \"{original_owner}\""))
            connection.execute(text(f"DROP OWNED BY {role}"))
            connection.execute(text(f"DROP ROLE {role}"))
        admin.dispose()


def test_offline_mode_is_refused(empty_database_url: str) -> None:
    with pytest.raises(RuntimeError, match="офлайн"):
        command.upgrade(alembic_config(empty_database_url), "head", sql=True)



def test_alembic_ini_is_ascii() -> None:
    # Alembic читает alembic.ini в кодировке системы (encoding="locale"): на Windows кириллица
    # в комментарии ломает любую команду alembic.
    (ROOT / "alembic.ini").read_bytes().decode("ascii")
