"""Команды create-worker, rotate-worker-token, create-service и rotate-service-token: токен печатается один раз, в базе — только HMAC."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text

TEST_SECRET_KEY = "test-secret-key-" + "k" * 32


def run(database_url: str, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {name: value for name, value in os.environ.items() if not name.startswith("PRICEWATCH_")} | {
        "PRICEWATCH_ENV": "test",
        "PRICEWATCH_PUBLIC_ORIGIN": "http://localhost:8000",
        "PRICEWATCH_DATABASE_URL": database_url,
        "PRICEWATCH_SECRET_KEY": TEST_SECRET_KEY,
        "PYTHONIOENCODING": "cp1252",  # вывод команды обязан быть UTF-8 и на Windows
    }
    return subprocess.run(
        [sys.executable, "-m", "pricewatch.bootstrap", *args], cwd=tmp_path, env=env, capture_output=True, encoding="utf-8", timeout=60, check=False
    )


def stored_hashes(database_url: str, name: str) -> tuple[str, str | None]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as db:
            return tuple(db.execute(text("SELECT token_hash, next_token_hash FROM workers WHERE name = :n"), {"n": name}).one())  # type: ignore[return-value]
    finally:
        engine.dispose()


def test_create_and_rotate_worker_token(migrated_database_url: str, tmp_path: Path) -> None:
    name = f"w-{uuid.uuid4().hex[:8]}"

    created = run(migrated_database_url, tmp_path, "create-worker", "--name", name)
    rotated = run(migrated_database_url, tmp_path, "rotate-worker-token", "--name", name)

    assert created.returncode == 0 and rotated.returncode == 0, created.stdout + rotated.stdout
    first, second = created.stdout.splitlines()[-1], rotated.stdout.splitlines()[-1]
    assert len(first) == len(second) == 43 and first != second
    token_hash, next_hash = stored_hashes(migrated_database_url, name)
    assert first not in (token_hash, next_hash) and second not in (token_hash, next_hash)
    assert next_hash is not None and len(token_hash) == 64


def test_same_name_twice_and_unknown_worker(migrated_database_url: str, tmp_path: Path) -> None:
    name = f"w-{uuid.uuid4().hex[:8]}"
    run(migrated_database_url, tmp_path, "create-worker", "--name", name)

    again = run(migrated_database_url, tmp_path, "create-worker", "--name", name)
    unknown = run(migrated_database_url, tmp_path, "rotate-worker-token", "--name", f"no-{uuid.uuid4().hex[:6]}")

    assert again.returncode == 1 and "Токен не выпущен" in again.stdout
    assert unknown.returncode == 1 and "Токен не выпущен" in unknown.stdout


def test_create_and_rotate_service_token(migrated_database_url: str, tmp_path: Path) -> None:
    name = f"bot-{uuid.uuid4().hex[:8]}"

    created = run(migrated_database_url, tmp_path, "create-service", "--name", name)
    rotated = run(migrated_database_url, tmp_path, "rotate-service-token", "--name", name)
    unknown = run(migrated_database_url, tmp_path, "rotate-service-token", "--name", f"no-{uuid.uuid4().hex[:6]}")

    assert created.returncode == 0 and rotated.returncode == 0, created.stdout + rotated.stdout
    assert "PRICEWATCH_BOT_SERVICE_TOKEN" in created.stdout
    first, second = created.stdout.splitlines()[-1], rotated.stdout.splitlines()[-1]
    assert len(first) == len(second) == 43 and first != second
    engine = create_engine(migrated_database_url)
    try:
        with engine.connect() as db:
            token_hash, next_hash = db.execute(text("SELECT token_hash, next_token_hash FROM services WHERE name = :n"), {"n": name}).one()
            assert db.execute(text("SELECT count(*) FROM workers WHERE name = :n"), {"n": name}).scalar_one() == 0
    finally:
        engine.dispose()
    assert first not in (token_hash, next_hash) and second not in (token_hash, next_hash) and next_hash is not None
    assert unknown.returncode == 1 and "Токен не выпущен" in unknown.stdout
