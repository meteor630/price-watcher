"""deploy/windows: скрипты worker для Windows читаются Windows PowerShell 5.1.

Без метки BOM PowerShell 5.1 читает файл в кодировке системы (cp1251), и русский текст ломается. Разбор скриптов самим
PowerShell и запуск установки — в CI на Windows.
"""

from pathlib import Path

import pytest

SCRIPTS = sorted((Path(__file__).resolve().parents[2] / "deploy" / "windows").glob("*.ps1"))
BOM = b"\xef\xbb\xbf"


def test_all_worker_scripts_present() -> None:
    assert [script.name for script in SCRIPTS] == ["autostart.ps1", "install-worker.ps1", "restore-check.ps1", "start-worker.ps1"]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_script_is_utf8_with_bom(script: Path) -> None:
    raw = script.read_bytes()
    assert raw.startswith(BOM)
    raw.decode("utf-8")


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_script_stops_on_errors(script: Path) -> None:
    assert "$ErrorActionPreference = 'Stop'" in script.read_text(encoding="utf-8-sig")
