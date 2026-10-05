"""Запуск python -m pricewatch.bootstrap: сервер отвечает, журнал только JSON, без настроек не стартует."""

import json
import os
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


class ServerOutput:
    """Вывод процесса сервера: строки добавляются по мере появления; можно дождаться нужной."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.ended = False
        self._changed = threading.Condition()

    def add(self, line: str) -> None:
        with self._changed:
            self.lines.append(line)
            self._changed.notify_all()

    def finish(self) -> None:
        with self._changed:
            self.ended = True
            self._changed.notify_all()

    def wait_for(self, text: str, timeout: float = STARTUP_TIMEOUT_S) -> bool:
        """Дождаться строки с text; False — если вывод закончился или время вышло."""
        with self._changed:
            self._changed.wait_for(lambda: self.ended or any(text in line for line in self.lines), timeout)
            return any(text in line for line in self.lines)

    def wait_ended(self, timeout: float = STARTUP_TIMEOUT_S) -> None:
        with self._changed:
            self._changed.wait_for(lambda: self.ended, timeout)

    def text(self) -> str:
        with self._changed:
            return "".join(self.lines)


def collect(process: subprocess.Popen[str]) -> ServerOutput:
    output = ServerOutput()

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.add(line)
        output.finish()

    threading.Thread(target=pump, daemon=True).start()
    return output


@pytest.fixture
def server(tmp_path: Path, migrated_database_url: str) -> Iterator[tuple[str, ServerOutput]]:
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
    output = collect(process)
    try:
        assert output.wait_for("Application startup complete"), "сервер не стартовал:\n" + output.text()
        yield f"http://127.0.0.1:{port}", output
    finally:
        process.terminate()
        process.wait(timeout=STARTUP_TIMEOUT_S)
        output.wait_ended()
        assert process.stdout is not None
        process.stdout.close()


def test_server_answers_with_headers_and_json_error(server: tuple[str, ServerOutput]) -> None:
    base_url, _ = server

    response = httpx2.get(f"{base_url}/absent?token=s3cret")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "server" not in response.headers


def test_server_log_is_json_only(server: tuple[str, ServerOutput]) -> None:
    base_url, output = server
    httpx2.get(f"{base_url}/absent?token=s3cret")

    # Ждём запись о самом запросе: без неё проверка смотрела бы только на строки старта.
    assert output.wait_for('"path": "/absent"')
    entries = [json.loads(line) for line in output.lines]  # падает на любой не-JSON строке
    assert any(entry["logger"].startswith("uvicorn") for entry in entries)
    assert any(entry.get("path") == "/absent" and entry.get("status") == 404 for entry in entries)
    assert "s3cret" not in output.text()


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


def test_server_started_log_reports_schema(server: tuple[str, ServerOutput]) -> None:
    _, output = server

    (ready,) = [entry for entry in map(json.loads, output.lines) if entry["message"] == "База данных готова"]
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


def test_app_login_end_to_end_on_real_server(server: tuple[str, ServerOutput], migrated_database_url: str) -> None:
    """Настоящий процесс сервера с настоящей базой: код → токены → /me → ротация → выход."""
    import uuid
    from datetime import UTC, datetime

    from pricewatch.application.app_auth import issue_pairing_code
    from pricewatch.persistence.database import create_db_engine
    from pricewatch.persistence.unit_of_work import SqlUnitOfWork

    base_url, output = server
    login = f"e2e{uuid.uuid4().hex[:8]}"
    engine = create_db_engine(migrated_database_url)
    try:
        with engine.begin() as connection:
            user_id = connection.execute(
                text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'E2E') RETURNING id"),
                {"l": login},
            ).scalar_one()
        code = issue_pairing_code(SqlUnitOfWork(engine), TEST_SECRET_KEY.encode(), user_id, datetime.now(UTC)).code
    finally:
        engine.dispose()

    paired = httpx2.post(f"{base_url}/api/v1/auth/pair", json={"code": code, "device_name": "E2E"})
    assert paired.status_code == 200, paired.text
    tokens = paired.json()
    me = httpx2.get(f"{base_url}/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    rotated = httpx2.post(f"{base_url}/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    out = httpx2.post(f"{base_url}/api/v1/auth/logout", headers={"Authorization": f"Bearer {rotated['access_token']}"})
    after = httpx2.get(f"{base_url}/api/v1/me", headers={"Authorization": f"Bearer {rotated['access_token']}"})

    assert me.json() == {"id": user_id, "login": login, "display_name": "E2E", "role": "user"}
    assert rotated["refresh_token"] != tokens["refresh_token"]
    assert (out.status_code, after.status_code) == (204, 401)
    assert output.wait_for('"path": "/api/v1/me", "status": 401')
    logged = output.text()
    assert '"path": "/api/v1/auth/pair"' in logged  # журнал запросов действительно собран
    for secret in (code, tokens["access_token"], tokens["refresh_token"], rotated["access_token"]):
        assert secret not in logged
