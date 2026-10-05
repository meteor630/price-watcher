"""Запуск python -m pricewatch.bootstrap: сервер отвечает, журнал только JSON, без настроек не стартует."""

import json
import os
import queue
import socket
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import create_engine, text

from pricewatch.persistence.schema import head_revision

STARTUP_TIMEOUT_S = 20


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


TEST_SECRET_KEY = "test-secret-key-" + "k" * 32


def clean_env(**variables: str) -> dict[str, str]:
    """Окружение без чужих PRICEWATCH_*; серверный ключ задан, если тест не убрал его явно."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("PRICEWATCH_")}
    return env | {"PRICEWATCH_SECRET_KEY": TEST_SECRET_KEY} | variables


def stream_lines(process: subprocess.Popen[str]) -> "queue.Queue[str | None]":
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return lines


@pytest.fixture
def server(tmp_path: Path, migrated_database_url: str) -> Iterator[tuple[str, list[str]]]:
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "pricewatch.bootstrap"],
        cwd=tmp_path,
        env=clean_env(
            PRICEWATCH_ENV="test",
            PRICEWATCH_PUBLIC_ORIGIN=f"http://127.0.0.1:{port}",
            PRICEWATCH_LISTEN_PORT=str(port),
            PRICEWATCH_DATABASE_URL=migrated_database_url,
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines = stream_lines(process)
    seen: list[str] = []
    output_ended = False
    try:
        while True:
            line = lines.get(timeout=STARTUP_TIMEOUT_S)
            if line is None:
                output_ended = True
                raise AssertionError("сервер завершился до старта:\n" + "".join(seen))
            seen.append(line)
            if "Application startup complete" in line:
                break
        yield f"http://127.0.0.1:{port}", seen
    finally:
        process.terminate()
        process.wait(timeout=STARTUP_TIMEOUT_S)
        # Маркер конца вывода читается один раз: если сервер упал при старте, он уже получен.
        while not output_ended:
            line = lines.get(timeout=STARTUP_TIMEOUT_S)
            if line is None:
                output_ended = True
            else:
                seen.append(line)
        assert process.stdout is not None
        process.stdout.close()


def test_server_answers_with_headers_and_json_error(server: tuple[str, list[str]]) -> None:
    base_url, _ = server

    response = httpx2.get(f"{base_url}/absent?token=s3cret")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "server" not in response.headers


def test_server_log_is_json_only(server: tuple[str, list[str]]) -> None:
    base_url, seen = server
    httpx2.get(f"{base_url}/absent?token=s3cret")

    entries = [json.loads(line) for line in seen]  # падает на любой не-JSON строке
    assert any(entry["logger"].startswith("uvicorn") for entry in entries)
    assert "s3cret" not in "".join(seen)


def start_once(tmp_path: Path, **variables: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pricewatch.bootstrap"],
        cwd=tmp_path,
        env=clean_env(**variables),
        capture_output=True,
        text=True,
        timeout=STARTUP_TIMEOUT_S,
    )


def error_entries(result: subprocess.CompletedProcess[str]) -> list[dict[str, object]]:
    entries = [json.loads(line) for line in result.stdout.splitlines()]
    return [entry for entry in entries if entry["level"] == "ERROR"]


def test_missing_setting_stops_start(tmp_path: Path) -> None:
    result = start_once(tmp_path, PRICEWATCH_PUBLIC_ORIGIN="https://prices.example.ru")

    assert result.returncode == 2
    (entry,) = [json.loads(line) for line in result.stdout.splitlines()]
    assert entry["level"] == "ERROR"
    assert "PRICEWATCH_ENV: переменная не задана" in entry["message"]
    assert result.stderr == ""


def test_server_started_log_reports_schema(server: tuple[str, list[str]]) -> None:
    _, seen = server

    (ready,) = [entry for entry in map(json.loads, seen) if entry["message"] == "База данных готова"]
    assert ready["schema_revision"] == head_revision()
    assert str(ready["server_version"]).startswith("18")


def test_unreachable_database_stops_start(tmp_path: Path) -> None:
    password = "Unreach" + "-pw-" + "77"
    result = start_once(
        tmp_path,
        PRICEWATCH_ENV="test",
        PRICEWATCH_PUBLIC_ORIGIN="http://127.0.0.1",
        PRICEWATCH_DATABASE_URL=f"postgresql://pricewatch:{password}@127.0.0.1:{free_port()}/pricewatch",
    )

    assert result.returncode == 3
    (entry,) = error_entries(result)
    assert entry["message"] == "База данных недоступна"
    assert "Traceback" in str(entry["exc"])
    assert password not in result.stdout + result.stderr


def test_empty_schema_stops_start(tmp_path: Path, empty_database_url: str) -> None:
    result = start_once(
        tmp_path,
        PRICEWATCH_ENV="test",
        PRICEWATCH_PUBLIC_ORIGIN="http://127.0.0.1",
        PRICEWATCH_DATABASE_URL=empty_database_url,
    )

    assert result.returncode == 3
    (entry,) = error_entries(result)
    assert entry["message"] == f"Схема базы пустая, код ожидает {head_revision()}. Выполните: uv run alembic upgrade head"


def test_unknown_schema_revision_stops_start(tmp_path: Path, empty_database_url: str) -> None:
    engine = create_engine(empty_database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('9999_future')"))
    engine.dispose()

    result = start_once(
        tmp_path,
        PRICEWATCH_ENV="test",
        PRICEWATCH_PUBLIC_ORIGIN="http://127.0.0.1",
        PRICEWATCH_DATABASE_URL=empty_database_url,
    )

    assert result.returncode == 3
    (entry,) = error_entries(result)
    assert "ревизии 9999_future, неизвестной этой версии кода" in str(entry["message"])
