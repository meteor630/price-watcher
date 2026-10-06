"""Запуск python -m pricewatch.worker: без настроек и с чужим токеном worker останавливается понятно, журнал — JSON."""

import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

TIMEOUT_S = 60
TOKEN = "w" * 43


def run_worker(cwd: Path, **variables: str) -> subprocess.CompletedProcess[str]:
    env = {name: value for name, value in os.environ.items() if not name.startswith("PRICEWATCH_")} | variables
    return subprocess.run(
        [sys.executable, "-m", "pricewatch.worker"], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_S, check=False
    )


def json_lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class Rejecting(BaseHTTPRequestHandler):
    """Сервер, который не принимает токен: на любой запрос — 401."""

    paths: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 — имя задаёт http.server
        Rejecting.paths.append(self.path)
        body = b'{"error": {"code": "AUTH_REQUIRED"}}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — сигнатура http.server
        return None


@pytest.fixture
def rejecting_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Rejecting)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_without_settings_exits_with_config_code(tmp_path: Path) -> None:
    done = run_worker(tmp_path)

    assert done.returncode == 2
    records = json_lines(done.stdout + done.stderr)
    assert any("PRICEWATCH_WORKER_TOKEN" in record["message"] for record in records)


def test_rejected_token_exits_and_token_not_logged(tmp_path: Path, rejecting_server: str) -> None:
    done = run_worker(
        tmp_path, PRICEWATCH_WORKER_API_URL=rejecting_server, PRICEWATCH_WORKER_TOKEN=TOKEN, PRICEWATCH_WORKER_PROFILES_DIR=str(tmp_path / "profiles")
    )

    output = done.stdout + done.stderr
    assert done.returncode == 4, output
    assert Rejecting.paths == ["/worker/v1/claim"]
    assert any("rotate-worker-token" in record["message"] for record in json_lines(output))
    assert TOKEN not in output
