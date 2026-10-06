"""pricewatch.config.settings: чтение, проверка, ошибки без значений."""

from ipaddress import ip_network
from pathlib import Path

import pytest

from pricewatch.config.settings import ConfigError, Settings, load_settings

ROOT = Path(__file__).resolve().parents[2]
SECRETISH = "s3cr3t-VALUE-must-not-leak"
TEST_SECRET_KEY = "test-secret-key-" + "k" * 32


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in list(os.environ):
        if name.startswith("PRICEWATCH_"):
            monkeypatch.delenv(name)


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("PRICEWATCH_ENV", "production")
    monkeypatch.setenv("PRICEWATCH_PUBLIC_ORIGIN", "https://prices.example.ru")
    monkeypatch.setenv("PRICEWATCH_DATABASE_URL", "postgresql://pricewatch@localhost:5432/pricewatch")
    monkeypatch.setenv("PRICEWATCH_SECRET_KEY", TEST_SECRET_KEY)
    return monkeypatch


def load(tmp_path: Path, env_file_text: str | None = None) -> Settings:
    env_file = tmp_path / ".env"
    if env_file_text is not None:
        env_file.write_text(env_file_text, encoding="utf-8")
    return load_settings(env_file)


def config_error(tmp_path: Path, env_file_text: str | None = None) -> str:
    with pytest.raises(ConfigError) as caught:
        load(tmp_path, env_file_text)
    return str(caught.value)


def test_reads_environment_with_defaults(base_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = load(tmp_path)

    assert settings.env == "production"
    assert settings.public_origin == "https://prices.example.ru"
    assert settings.trusted_proxies == ()
    assert settings.log_level == "INFO"


def test_reads_env_file_and_environment_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PRICEWATCH_LOG_LEVEL", "ERROR")

    settings = load(
        tmp_path,
        "PRICEWATCH_ENV=development\nPRICEWATCH_PUBLIC_ORIGIN=http://localhost:8000\n"
        "PRICEWATCH_DATABASE_URL=postgresql://pricewatch@localhost:5432/pricewatch\nPRICEWATCH_LOG_LEVEL=DEBUG\n"
        f"PRICEWATCH_SECRET_KEY={TEST_SECRET_KEY}\n",
    )

    assert settings.env == "development"
    assert settings.log_level == "ERROR"


def test_env_example_is_complete_and_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    variables = {line.split("=", 1)[0] for line in example.splitlines() if line and not line.startswith("#")}

    # Серверного ключа в примере нет намеренно: секреты только в .env.
    monkeypatch.setenv("PRICEWATCH_SECRET_KEY", TEST_SECRET_KEY)
    settings = load(tmp_path, example)

    assert variables == {f"PRICEWATCH_{name.upper()}" for name in Settings.model_fields}
    assert settings.env == "development"


def test_settings_are_frozen(base_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = load(tmp_path)

    with pytest.raises(Exception, match="frozen"):
        settings.log_level = "DEBUG"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("https://Prices.Example.ru/", "https://prices.example.ru"),
        ("https://prices.example.ru:8443", "https://prices.example.ru:8443"),
        ("  https://prices.example.ru  ", "https://prices.example.ru"),
    ],
)
def test_public_origin_is_normalized(base_env: pytest.MonkeyPatch, tmp_path: Path, raw: str, normalized: str) -> None:
    base_env.setenv("PRICEWATCH_PUBLIC_ORIGIN", raw)

    assert load(tmp_path).public_origin == normalized


@pytest.mark.parametrize(
    "origin",
    [
        "prices.example.ru",
        "ftp://prices.example.ru",
        "https://prices.example.ru/app",
        "https://prices.example.ru/?next=1",
        "https://prices.example.ru/#top",
        f"https://admin:{SECRETISH}@prices.example.ru",
    ],
)
def test_public_origin_must_be_bare_origin(base_env: pytest.MonkeyPatch, tmp_path: Path, origin: str) -> None:
    base_env.setenv("PRICEWATCH_PUBLIC_ORIGIN", origin)

    message = config_error(tmp_path)

    assert "PRICEWATCH_PUBLIC_ORIGIN" in message
    assert origin.strip() not in message
    assert SECRETISH not in message


def test_http_allowed_only_locally(base_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base_env.setenv("PRICEWATCH_PUBLIC_ORIGIN", "http://prices.example.ru")

    assert "https" in config_error(tmp_path)

    base_env.setenv("PRICEWATCH_ENV", "test")
    assert load(tmp_path).public_origin == "http://prices.example.ru"


def test_trusted_proxies_parsed_from_comma_list(base_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base_env.setenv("PRICEWATCH_TRUSTED_PROXIES", "10.8.0.2/32, fd00::/64,")

    assert load(tmp_path).trusted_proxies == (ip_network("10.8.0.2/32"), ip_network("fd00::/64"))


@pytest.mark.parametrize("value", ["not-an-ip", "10.0.0.1/24", f"10.0.0.1,{SECRETISH}"])
def test_bad_trusted_proxies_rejected_without_value(base_env: pytest.MonkeyPatch, tmp_path: Path, value: str) -> None:
    base_env.setenv("PRICEWATCH_TRUSTED_PROXIES", value)

    message = config_error(tmp_path)

    assert "PRICEWATCH_TRUSTED_PROXIES" in message
    assert value not in message
    assert SECRETISH not in message


def test_missing_required_variables_are_named(tmp_path: Path) -> None:
    message = config_error(tmp_path)

    assert "PRICEWATCH_ENV: переменная не задана" in message
    assert "PRICEWATCH_PUBLIC_ORIGIN: переменная не задана" in message


def test_literal_error_hides_value(base_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base_env.setenv("PRICEWATCH_ENV", SECRETISH)

    message = config_error(tmp_path)

    assert "PRICEWATCH_ENV: значение не из допустимого списка" in message
    assert SECRETISH not in message


def test_unknown_environment_variable_is_rejected(base_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base_env.setenv("PRICEWATCH_PUBLIC_ORIGN", SECRETISH)

    message = config_error(tmp_path)

    assert "PRICEWATCH_PUBLIC_ORIGN" in message
    assert SECRETISH not in message


def test_unknown_env_file_entry_is_rejected(base_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    message = config_error(tmp_path, f"PRICEWATCH_LOG_LEVL={SECRETISH}\n")

    assert "неизвестная настройка в .env" in message
    assert SECRETISH not in message


def test_error_has_no_validation_chain(base_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base_env.setenv("PRICEWATCH_ENV", SECRETISH)

    with pytest.raises(ConfigError) as caught:
        load(tmp_path)

    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "url",
    [
        f"mysql://user:{SECRETISH}@db/pricewatch",
        f"postgresql://user:{SECRETISH}@db",
        f"postgresql://user:{SECRETISH}@/pricewatch",
        "not a url",
    ],
)
def test_bad_database_url_rejected_without_value(base_env: pytest.MonkeyPatch, tmp_path: Path, url: str) -> None:
    base_env.setenv("PRICEWATCH_DATABASE_URL", url)

    message = config_error(tmp_path)

    assert "PRICEWATCH_DATABASE_URL" in message
    assert SECRETISH not in message


def test_database_url_is_secret_and_registered_for_redaction(base_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from urllib.parse import quote

    from pricewatch.observability.redaction import redact_text

    password = "Pa55:/@" + SECRETISH
    base_env.setenv("PRICEWATCH_DATABASE_URL", f"postgresql://pricewatch:{quote(password, safe='')}@db:5432/pricewatch")

    settings = load(tmp_path)

    assert SECRETISH not in repr(settings)
    assert settings.database_url.get_secret_value().startswith("postgresql://pricewatch:")
    assert SECRETISH not in redact_text(f"connection failed: password {password} rejected")
    assert SECRETISH not in redact_text(f"dsn={settings.database_url.get_secret_value()}")


def test_missing_database_url_is_named(base_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base_env.delenv("PRICEWATCH_DATABASE_URL")

    assert "PRICEWATCH_DATABASE_URL: переменная не задана" in config_error(tmp_path)


@pytest.mark.parametrize("key", ["", "short", "я" * 15])
def test_short_secret_key_rejected_without_value(base_env: pytest.MonkeyPatch, tmp_path: Path, key: str) -> None:
    base_env.setenv("PRICEWATCH_SECRET_KEY", key)

    message = config_error(tmp_path)

    assert "PRICEWATCH_SECRET_KEY" in message
    if key:
        assert key not in message


def test_secret_key_of_32_bytes_accepted_and_hidden(base_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pricewatch.observability.redaction import redact_text

    key = "я" * 16  # 32 байта в UTF-8
    base_env.setenv("PRICEWATCH_SECRET_KEY", key)

    settings = load(tmp_path)

    assert settings.secret_key.get_secret_value() == key
    assert key not in repr(settings)
    assert key not in redact_text(f"ключ {key} в тексте")


# --- настройки worker ---

from pricewatch.config.settings import WorkerSettings, load_worker_settings  # noqa: E402

WORKER_TOKEN = "t" * 43


def worker_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name in [name for name in __import__("os").environ if name.startswith("PRICEWATCH_")]:
        monkeypatch.delenv(name)
    for name, value in values.items():
        monkeypatch.setenv(f"PRICEWATCH_WORKER_{name}", value)


def test_worker_settings_normal(monkeypatch: pytest.MonkeyPatch) -> None:
    worker_env(monkeypatch, API_URL="http://api:8000/", TOKEN=WORKER_TOKEN)

    settings = load_worker_settings(None)

    assert (settings.api_url, settings.token.get_secret_value(), settings.poll_seconds) == ("http://api:8000", WORKER_TOKEN, 10)


@pytest.mark.parametrize(
    ("values", "variable"),
    [
        ({"TOKEN": WORKER_TOKEN}, "PRICEWATCH_WORKER_API_URL"),
        ({"API_URL": "http://api:8000"}, "PRICEWATCH_WORKER_TOKEN"),
        ({"API_URL": "http://api:8000", "TOKEN": "short"}, "PRICEWATCH_WORKER_TOKEN"),
        ({"API_URL": "ftp://api", "TOKEN": WORKER_TOKEN}, "PRICEWATCH_WORKER_API_URL"),
        ({"API_URL": "http://api:8000/worker", "TOKEN": WORKER_TOKEN}, "PRICEWATCH_WORKER_API_URL"),
        ({"API_URL": "http://api:8000", "TOKEN": WORKER_TOKEN, "POLL_SECONDS": "0"}, "PRICEWATCH_WORKER_POLL_SECONDS"),
    ],
)
def test_worker_settings_errors_name_variable_without_value(monkeypatch: pytest.MonkeyPatch, values: dict, variable: str) -> None:
    worker_env(monkeypatch, **values)

    with pytest.raises(ConfigError) as caught:
        load_worker_settings(None)

    assert variable in str(caught.value)
    assert WORKER_TOKEN not in str(caught.value)


def test_worker_rejects_unknown_worker_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    worker_env(monkeypatch, API_URL="http://api:8000", TOKEN=WORKER_TOKEN, TOKN="typo")

    with pytest.raises(ConfigError, match="PRICEWATCH_WORKER_TOKN"):
        load_worker_settings(None)


def test_server_and_worker_ignore_each_others_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    worker_env(monkeypatch, API_URL="http://api:8000", TOKEN=WORKER_TOKEN)
    monkeypatch.setenv("PRICEWATCH_ENV", "test")

    assert load_worker_settings(None).api_url == "http://api:8000"
    with pytest.raises(ConfigError) as caught:
        load_settings(None)  # сервер жалуется на свои пропущенные настройки, а не на переменные worker
    assert "Неизвестные" not in str(caught.value)


def test_worker_env_example_is_complete_and_loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    example = (ROOT / "worker.env.example").read_text(encoding="utf-8")
    variables = {line.split("=", 1)[0] for line in example.splitlines() if line and not line.startswith("#")}
    env_file = tmp_path / "worker.env"
    env_file.write_text(example, encoding="utf-8")
    # Токена в примере нет намеренно: секреты только в worker.env.
    worker_env(monkeypatch, TOKEN=WORKER_TOKEN)

    settings = load_worker_settings(env_file)

    assert variables == {f"PRICEWATCH_WORKER_{name.upper()}" for name in WorkerSettings.model_fields}
    assert settings.api_url == "http://localhost:8000"
