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

STARTUP_TIMEOUT_S = 20


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def clean_env(**variables: str) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if not name.startswith("PRICEWATCH_")}
    return env | variables


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
def server(tmp_path: Path) -> Iterator[tuple[str, list[str]]]:
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, "-m", "pricewatch.bootstrap"],
        cwd=tmp_path,
        env=clean_env(
            PRICEWATCH_ENV="test",
            PRICEWATCH_PUBLIC_ORIGIN=f"http://127.0.0.1:{port}",
            PRICEWATCH_LISTEN_PORT=str(port),
            PRICEWATCH_DATABASE_URL="postgresql://pricewatch@localhost:5432/pricewatch",
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


def test_missing_setting_stops_start(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pricewatch.bootstrap"],
        cwd=tmp_path,
        env=clean_env(PRICEWATCH_PUBLIC_ORIGIN="https://prices.example.ru"),
        capture_output=True,
        text=True,
        timeout=STARTUP_TIMEOUT_S,
    )

    assert result.returncode == 2
    (entry,) = [json.loads(line) for line in result.stdout.splitlines()]
    assert entry["level"] == "ERROR"
    assert "PRICEWATCH_ENV: переменная не задана" in entry["message"]
    assert result.stderr == ""
