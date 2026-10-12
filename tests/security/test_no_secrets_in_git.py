"""В Git не попадают секреты: ни файлы .env и ключей, ни токены в тексте.

Проверяются файлы, которые попадут в коммит: отслеживаемые и новые, кроме игнорируемых.
Образцы секретов в этом тесте собираются из частей, чтобы сам файл не содержал секрета.
"""

import re
import subprocess
from pathlib import Path

import pytest

from pricewatch.observability.redaction import is_sensitive_name

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_NAMES = re.compile(r"(^|/)(\.env(\..+)?|worker\.env|bot\.env|\.browser-profiles/.*|id_(rsa|ed25519|ecdsa)|.+\.(pem|key|p12|pfx|keystore))$")
ALLOWED_NAMES = {".env.example", "worker.env.example", "bot.env.example"}
SECRET_PATTERNS = {
    "закрытый ключ": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "токен Telegram-бота": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "токен GitHub": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b"),
    "ключ AWS": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "токен Slack": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "ключ API Anthropic или OpenAI": re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{32,}\b"),
}


def files_to_commit(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [name for name in result.stdout.decode("utf-8").split("\0") if name]


def forbidden_files(names: list[str]) -> list[str]:
    return [name for name in names if FORBIDDEN_NAMES.search(name) and Path(name).name not in ALLOWED_NAMES]


def secrets_in_text(text: str) -> list[str]:
    return [kind for kind, pattern in SECRET_PATTERNS.items() if pattern.search(text)]


def read_text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def filled_sensitive_variables(env_text: str) -> list[str]:
    filled = []
    for line in env_text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = (part.strip() for part in line.split("=", 1))
        if is_sensitive_name(name) and value:
            filled.append(name)
    return filled


# Образцы для проверки самого сканера.
PRIVATE_KEY = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIEow...\n"
TELEGRAM = "1234567" + "890:" + "AAH" + "x" * 32
GITHUB = "gh" + "p_" + "a" * 36
AWS = "AK" + "IA" + "ABCDEFGHIJKLMNOP"


def test_repository_files_contain_no_forbidden_files() -> None:
    assert forbidden_files(files_to_commit(ROOT)) == []


def test_repository_files_contain_no_secrets() -> None:
    found = []
    for name in files_to_commit(ROOT):
        path = ROOT / name
        text = read_text(path) if path.is_file() else None
        if text is not None:
            found += [f"{name}: {kind}" for kind in secrets_in_text(text)]

    assert found == []


def is_ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False).returncode == 0


@pytest.mark.parametrize(
    "path", [".env", ".env.local", ".env.production", "deploy/.env", "worker.env", "bot.env", ".browser-profiles/avito/Default/Cookies"]
)
def test_env_files_are_ignored_by_git(path: str) -> None:
    assert is_ignored(path)


@pytest.mark.parametrize("name", [".env.example", "worker.env.example", "bot.env.example"])
def test_env_example_is_not_ignored(name: str) -> None:
    assert not is_ignored(name)


@pytest.mark.parametrize("name", [".env.example", "worker.env.example", "bot.env.example"])
def test_env_example_has_no_filled_secrets(name: str) -> None:
    assert filled_sensitive_variables((ROOT / name).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (PRIVATE_KEY, "закрытый ключ"),
        (f"TELEGRAM_BOT_TOKEN={TELEGRAM}", "токен Telegram-бота"),
        (f"token: {GITHUB}", "токен GitHub"),
        (f"aws = '{AWS}'", "ключ AWS"),
        ("x" + "oxb-" + "1234567890-abcdef", "токен Slack"),
        ("s" + "k-ant-" + "b" * 40, "ключ API Anthropic или OpenAI"),
    ],
)
def test_scanner_finds_secret(text: str, kind: str) -> None:
    assert secrets_in_text(text) == [kind]


@pytest.mark.parametrize(
    "text",
    [
        "Цена 12 990 ₽, время 09:00",
        "SECRET = \"tok_9f8e7d6c5b4a\"",
        "sha256:" + "0" * 64,
        "E-ABCDEFGHJK",
    ],
)
def test_scanner_ignores_ordinary_text(text: str) -> None:
    assert secrets_in_text(text) == []


@pytest.mark.parametrize(
    "name",
    [".env", ".env.production", "deploy/.env.local", "worker.env", "bot.env", ".browser-profiles/dns/Cookies", "id_rsa", "certs/server.key", "tls/site.pem", "ios/dist.p12"],
)
def test_forbidden_file_names_are_caught(name: str) -> None:
    assert forbidden_files([name]) == [name]


@pytest.mark.parametrize("name", [".env.example", "src/pricewatch/config/settings.py", "docs/keys.md", "environment.py"])
def test_allowed_file_names_pass(name: str) -> None:
    assert forbidden_files([name]) == []


def test_filled_sensitive_variable_in_env_example_is_caught() -> None:
    sample = "# комментарий\nPRICEWATCH_ENV=production\nPRICEWATCH_DB_PASSWORD=hunter2\nPRICEWATCH_BOT_TOKEN=\n"

    assert filled_sensitive_variables(sample) == ["PRICEWATCH_DB_PASSWORD"]


def test_new_untracked_secret_file_is_seen(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    (tmp_path / "server.key").write_text(PRIVATE_KEY, encoding="utf-8")
    (tmp_path / "notes.txt").write_text(f"bot {TELEGRAM}\n", encoding="utf-8")

    names = files_to_commit(tmp_path)

    assert ".env" not in names
    assert forbidden_files(names) == ["server.key"]
    assert secrets_in_text((tmp_path / "notes.txt").read_text(encoding="utf-8")) == ["токен Telegram-бота"]
