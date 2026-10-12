"""tools/chrome_seccomp.py и готовый deploy/home/chrome-seccomp.json: стандартный профиль Docker плюс одно правило для песочницы Chrome."""

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROFILE = PROJECT_ROOT / "deploy" / "home" / "chrome-seccomp.json"
CHROME_NAMES = ["clone", "unshare"]
DEFAULT = {
    "defaultAction": "SCMP_ACT_ERRNO",
    "syscalls": [
        {"names": ["read", "write"], "action": "SCMP_ACT_ALLOW"},
        {"names": ["clone3"], "action": "SCMP_ACT_ERRNO", "errnoRet": 38, "excludes": {"caps": ["CAP_SYS_ADMIN"]}},
    ],
}


def run(source: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "tools" / "chrome_seccomp.py"), str(source)],
        env={**os.environ, "PYTHONIOENCODING": "cp1252"}, capture_output=True, check=False,
    )


def test_adds_one_chrome_rule_and_keeps_docker_rules(tmp_path: Path) -> None:
    source = tmp_path / "default.json"
    source.write_text(json.dumps(DEFAULT), encoding="utf-8")

    done = run(source)

    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    profile = json.loads(done.stdout.decode("utf-8"))
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert profile["syscalls"][:2] == DEFAULT["syscalls"]
    assert [rule["names"] for rule in profile["syscalls"][2:]] == [CHROME_NAMES]
    assert "Песочница Chrome" in profile["syscalls"][2]["comment"]


def test_refuses_profile_that_already_has_chrome_rule(tmp_path: Path) -> None:
    source = tmp_path / "chrome.json"
    source.write_bytes(PROFILE.read_bytes())

    done = run(source)

    assert done.returncode != 0 and b"Chrome" in done.stderr


def test_committed_profile_is_docker_default_plus_chrome_rule() -> None:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    chrome = [rule for rule in profile["syscalls"] if rule["names"] == CHROME_NAMES and rule["action"] == "SCMP_ACT_ALLOW"]

    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert len(chrome) == 1 and profile["syscalls"][-1] == chrome[0]
    # Признаки стандартного профиля Docker: clone3 без CAP_SYS_ADMIN — «не поддерживается», остальное запрещено по умолчанию.
    assert any(rule["names"] == ["clone3"] and rule.get("errnoRet") == 38 for rule in profile["syscalls"])
    assert len(profile["syscalls"]) > 40
