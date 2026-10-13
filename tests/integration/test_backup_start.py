"""Запуск python -m pricewatch.bootstrap backup: без настроек копий не стартует, служба с базой запускается и пишет JSON.

Сама копия на настоящих pg_dump и age проверяется в CI в образе backup (копия → расшифровка → pg_restore).
"""

import json
import subprocess
import sys
from pathlib import Path

from test_server_start import STARTUP_TIMEOUT_S, clean_env, collect

RECIPIENT = "age1" + "q" * 58


def test_missing_backup_setting_stops_service(tmp_path: Path, migrated_database_url: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pricewatch.bootstrap", "backup", "--once"],
        cwd=tmp_path,
        env=clean_env(PRICEWATCH_ENV="test", PRICEWATCH_PUBLIC_ORIGIN="http://127.0.0.1", PRICEWATCH_DATABASE_URL=migrated_database_url,
                      PRICEWATCH_BACKUP_DIR=str(tmp_path)),
        capture_output=True, encoding="utf-8", timeout=STARTUP_TIMEOUT_S, check=False,
    )

    assert result.returncode == 2
    (entry,) = [json.loads(line) for line in result.stdout.splitlines()]
    assert "PRICEWATCH_BACKUP_RECIPIENT: переменная не задана" in entry["message"]


def test_backup_service_starts_and_logs_json(tmp_path: Path, migrated_database_url: str) -> None:
    process = subprocess.Popen(
        [sys.executable, "-m", "pricewatch.bootstrap", "backup"],
        cwd=tmp_path,
        env=clean_env(PRICEWATCH_ENV="test", PRICEWATCH_PUBLIC_ORIGIN="http://127.0.0.1", PRICEWATCH_DATABASE_URL=migrated_database_url,
                      PRICEWATCH_BACKUP_DIR=str(tmp_path), PRICEWATCH_BACKUP_RECIPIENT=RECIPIENT,
                      PRICEWATCH_BACKUP_REMOTE="pwbackup@tunnel.example.ru", PRICEWATCH_BACKUP_SSH_KEY=str(tmp_path / "key"),
                      PRICEWATCH_BACKUP_KNOWN_HOSTS=str(tmp_path / "known_hosts")),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
    )
    output = collect(process)
    try:
        assert output.wait_for("Следующая резервная копия"), "служба копий не стартовала:\n" + output.text()
    finally:
        process.terminate()
        process.wait(timeout=STARTUP_TIMEOUT_S)
        output.wait_ended()
        assert process.stdout is not None
        process.stdout.close()

    entries = [json.loads(line) for line in output.lines]  # падает на любой не-JSON строке
    assert not [entry for entry in entries if entry["level"] == "ERROR"]
    assert "Служба резервных копий запущена" in [entry["message"] for entry in entries]
