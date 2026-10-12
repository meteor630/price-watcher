"""Запуск python -m pricewatch.bot: без настроек и с чужим токеном сервиса бот останавливается понятно, журнал — JSON,
токены не попадают в вывод. Сервер спрашивается первым: до Telegram дело не доходит."""

import os
import subprocess
import sys
from pathlib import Path

from test_worker_start import Rejecting, json_lines, rejecting_server  # noqa: F401 — фикстура модуля

TIMEOUT_S = 60
SERVICE_TOKEN = "s" * 43
TELEGRAM_TOKEN = "123456789:" + "B" * 35


def run_bot(cwd: Path, **variables: str) -> subprocess.CompletedProcess[str]:
    env = {name: value for name, value in os.environ.items() if not name.startswith("PRICEWATCH_")} | variables
    return subprocess.run(
        [sys.executable, "-m", "pricewatch.bot"], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT_S, check=False
    )


def test_without_settings_exits_with_config_code(tmp_path: Path) -> None:
    done = run_bot(tmp_path, PRICEWATCH_BOT_TELEGRAM_TOKEN="не токен")

    assert done.returncode == 2
    messages = " ".join(record["message"] for record in json_lines(done.stdout + done.stderr))
    assert "PRICEWATCH_BOT_SERVICE_TOKEN" in messages and "PRICEWATCH_BOT_TELEGRAM_TOKEN" in messages and "не токен" not in messages


def test_rejected_service_token_exits_and_tokens_not_logged(tmp_path: Path, rejecting_server: str) -> None:
    Rejecting.paths.clear()
    done = run_bot(
        tmp_path, PRICEWATCH_BOT_API_URL=rejecting_server, PRICEWATCH_BOT_SERVICE_TOKEN=SERVICE_TOKEN, PRICEWATCH_BOT_TELEGRAM_TOKEN=TELEGRAM_TOKEN
    )

    output = done.stdout + done.stderr
    assert done.returncode == 4, output
    assert Rejecting.paths == ["/internal/v1/outbox/claim"]
    assert any("rotate-service-token" in record["message"] for record in json_lines(output))
    assert SERVICE_TOKEN not in output and TELEGRAM_TOKEN not in output
