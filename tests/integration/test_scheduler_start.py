"""Запуск python -m pricewatch.bootstrap scheduler: без настроек не стартует, с базой — работает и пишет JSON.

База своя на модуль: после старта планировщик может создать пропущенный плановый запуск, а он один на всю базу.
"""

import json
import subprocess
import sys
from pathlib import Path

from test_server_start import STARTUP_TIMEOUT_S, clean_env, collect


def test_missing_setting_stops_scheduler(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pricewatch.bootstrap", "scheduler"],
        cwd=tmp_path, env=clean_env(), capture_output=True, encoding="utf-8", timeout=STARTUP_TIMEOUT_S, check=False,
    )

    assert result.returncode == 2
    (entry,) = [json.loads(line) for line in result.stdout.splitlines()]
    assert "PRICEWATCH_ENV: переменная не задана" in entry["message"]


def test_scheduler_starts_and_logs_json(tmp_path: Path, module_database_url: str) -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "pricewatch.bootstrap", "scheduler"],
        cwd=tmp_path,
        env=clean_env(PRICEWATCH_ENV="test", PRICEWATCH_PUBLIC_ORIGIN="http://127.0.0.1", PRICEWATCH_DATABASE_URL=module_database_url),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
    )
    output = collect(process)
    try:
        assert output.wait_for("Планировщик запущен"), "планировщик не стартовал:\n" + output.text()
    finally:
        process.terminate()
        process.wait(timeout=STARTUP_TIMEOUT_S)
        output.wait_ended()
        assert process.stdout is not None
        process.stdout.close()

    entries = [json.loads(line) for line in output.lines]  # падает на любой не-JSON строке
    assert not [entry for entry in entries if entry["level"] == "ERROR"]
