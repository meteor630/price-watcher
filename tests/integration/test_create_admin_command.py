"""Команда create-admin: создаёт администратора, пароль нигде не виден, ошибки понятны."""

import io
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from pricewatch.application.passwords import PasswordHasher
from pricewatch.bootstrap.commands import create_admin_command

TEST_SECRET_KEY = "test-secret-key-" + "k" * 32
PASSWORD = "Очень-длинный пароль " + uuid.uuid4().hex[:6]


def env_for(database_url: str) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if not name.startswith("PRICEWATCH_")}
    return env | {
        "PRICEWATCH_ENV": "test",
        "PRICEWATCH_PUBLIC_ORIGIN": "http://localhost:8000",
        "PRICEWATCH_DATABASE_URL": database_url,
        "PRICEWATCH_SECRET_KEY": TEST_SECRET_KEY,
    }


def run_command(database_url: str, tmp_path: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pricewatch.bootstrap", "create-admin", *args],
        cwd=tmp_path,
        env=env_for(database_url) | {"PYTHONIOENCODING": "cp1252"},
        input=stdin,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def stored(database_url: str, login: str) -> tuple[str, str, str] | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT role, password_hash, display_name FROM users WHERE lower(login) = lower(:l)"), {"l": login}
            ).one_or_none()
            return None if row is None else tuple(row)  # type: ignore[return-value]
    finally:
        engine.dispose()


def test_creates_admin_from_stdin(migrated_database_url: str, tmp_path: Path) -> None:
    login = f"admin{uuid.uuid4().hex[:8]}"

    result = run_command(migrated_database_url, tmp_path, "--login", login, "--name", "Владелец", "--password-stdin", stdin=PASSWORD + "\n")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Администратор «{login}» создан" in result.stdout
    role, password_hash, name = stored(migrated_database_url, login)  # type: ignore[misc]
    assert (role, name) == ("admin", "Владелец")
    assert password_hash.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert PASSWORD not in result.stdout + result.stderr


def test_login_taken(migrated_database_url: str, tmp_path: Path) -> None:
    login = f"admin{uuid.uuid4().hex[:8]}"
    run_command(migrated_database_url, tmp_path, "--login", login, "--password-stdin", stdin=PASSWORD)

    result = run_command(migrated_database_url, tmp_path, "--login", login.upper(), "--password-stdin", stdin=PASSWORD)

    assert result.returncode == 1
    assert "Такой логин уже занят" in result.stdout



def test_cyrillic_password_from_stdin_survives_legacy_encoding(migrated_database_url: str, tmp_path: Path) -> None:
    # run_command задаёт процессу кодировку cp1252, как у Windows: пароль не должен исказиться.
    login = f"cyr{uuid.uuid4().hex[:8]}"

    result = run_command(migrated_database_url, tmp_path, "--login", login, "--password-stdin", stdin=PASSWORD + "\n")

    assert result.returncode == 0, result.stdout
    row = stored(migrated_database_url, login)
    assert row is not None and PasswordHasher().verify(row[1], PASSWORD)

@pytest.mark.parametrize(
    ("login", "password", "expected"),
    [
        ("ok_login", "короткий", "пароль: не короче 10 символов"),
        ("same.as.login", "same.as.login", "пароль: не должен совпадать с логином"),
        ("ab", PASSWORD, "логин: 3–64 символа"),
    ],
)
def test_invalid_input_explained(
    migrated_database_url: str, tmp_path: Path, login: str, password: str, expected: str
) -> None:
    result = run_command(migrated_database_url, tmp_path, "--login", login, "--password-stdin", stdin=password)

    assert result.returncode == 1
    assert expected in result.stdout
    assert password not in result.stdout + result.stderr
    assert stored(migrated_database_url, login) is None


def test_password_is_not_an_argument() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pricewatch.bootstrap", "create-admin", "--login", "x", "--password", "secret"],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )

    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr


def test_interactive_mismatch(migrated_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in env_for(migrated_database_url).items():
        if name.startswith("PRICEWATCH_"):
            monkeypatch.setenv(name, value)
    answers = iter([PASSWORD, PASSWORD + "!"])
    out = io.StringIO()
    login = f"admin{uuid.uuid4().hex[:8]}"

    code = create_admin_command(login, login, False, env_file=tmp_path / ".env", prompt=lambda _: next(answers), out=out)

    assert code == 1
    assert "Пароли не совпадают" in out.getvalue()
    assert stored(migrated_database_url, login) is None


def test_interactive_success(migrated_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in env_for(migrated_database_url).items():
        if name.startswith("PRICEWATCH_"):
            monkeypatch.setenv(name, value)
    out = io.StringIO()
    login = f"admin{uuid.uuid4().hex[:8]}"

    code = create_admin_command(login, "Имя", False, env_file=tmp_path / ".env", prompt=lambda _: PASSWORD, out=out)

    assert code == 0
    assert stored(migrated_database_url, login)[0] == "admin"  # type: ignore[index]


def test_missing_configuration(tmp_path: Path) -> None:
    env = {name: value for name, value in os.environ.items() if not name.startswith("PRICEWATCH_")}
    result = subprocess.run(
        [sys.executable, "-m", "pricewatch.bootstrap", "create-admin", "--login", "admin", "--password-stdin"],
        cwd=tmp_path, env=env, input=PASSWORD, capture_output=True, encoding="utf-8", timeout=60, check=False,
    )

    assert result.returncode == 2
    assert "PRICEWATCH_DATABASE_URL: переменная не задана" in result.stdout


def test_unreachable_database(tmp_path: Path) -> None:
    result = run_command("postgresql://pricewatch:x@127.0.0.1:1/pricewatch", tmp_path, "--login", "admin", "--password-stdin", stdin=PASSWORD)

    assert result.returncode == 3
    assert PASSWORD not in result.stdout + result.stderr
