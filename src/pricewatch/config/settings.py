"""Настройки процесса.

Источники: переменные окружения с префиксом PRICEWATCH_, затем файл .env; окружение
важнее файла. Неверная или отсутствующая настройка останавливает старт с сообщением,
где названа переменная, но нет её значения: значения могут быть секретами.
"""

import os
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from pricewatch.observability.redaction import register_secret

ENV_PREFIX = "PRICEWATCH_"
# Свои переменные worker: сервер их пропускает, worker — переменные сервера (оба могут жить в одном окружении).
WORKER_PREFIX = "PRICEWATCH_WORKER_"
WORKER_TOKEN_MIN_CHARS = 40
LOCAL_ENVIRONMENTS = {"development", "test"}
SECRET_KEY_MIN_BYTES = 32


class ConfigError(Exception):
    """Конфигурация неполна или неверна. Текст не содержит значений настроек."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    env: Literal["development", "test", "production"]
    public_origin: str
    database_url: SecretStr
    secret_key: SecretStr
    trusted_proxies: Annotated[tuple[IPv4Network | IPv6Network, ...], NoDecode] = ()
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    listen_host: str = "127.0.0.1"
    listen_port: int = Field(default=8000, ge=1, le=65535)
    media_dir: Path = Path("media")
    # Имя Telegram-бота без @: страница профиля даёт ссылку t.me/<имя>?start=<код>; не задано — только команда /start <код>.
    telegram_bot_username: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]{5,32}$")] | None = None

    @field_validator("telegram_bot_username", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("public_origin")
    @classmethod
    def _origin_only(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("нужен адрес вида https://домен")
        if parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}:
            raise ValueError("только схема, домен и порт, без пути, параметров и логина")
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname}{port}"

    @field_validator("database_url")
    @classmethod
    def _postgres_url(cls, value: SecretStr) -> SecretStr:
        parts = urlsplit(value.get_secret_value())
        if parts.scheme not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError("нужен адрес вида postgresql://пользователь:пароль@хост:порт/база")
        if not parts.hostname or parts.path.strip("/") == "":
            raise ValueError("в адресе нужны хост и имя базы")
        return value

    @field_validator("secret_key")
    @classmethod
    def _long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().encode("utf-8")) < SECRET_KEY_MIN_BYTES:
            raise ValueError(f"нужно не меньше {SECRET_KEY_MIN_BYTES} байт; сгенерируйте ключ командой из .env.example")
        return value

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _split_networks(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return tuple(ip_network(item.strip()) for item in value.split(",") if item.strip())
        except ValueError:
            raise ValueError("нужен список сетей через запятую, например 10.0.0.2/32") from None

    @model_validator(mode="after")
    def _https_outside_development(self) -> "Settings":
        if self.env not in LOCAL_ENVIRONMENTS and not self.public_origin.startswith("https://"):
            raise ValueError("public_origin: в рабочем режиме нужен https")
        return self


def load_settings(env_file: Path | None = Path(".env")) -> Settings:
    """Прочитать настройки или остановиться с понятной ошибкой."""
    unknown = sorted(
        name for name in os.environ if name.startswith(ENV_PREFIX) and not name.startswith(WORKER_PREFIX) and not _is_field(name)
    )
    if unknown:
        raise ConfigError("Неизвестные переменные окружения: " + ", ".join(unknown))
    try:
        settings = Settings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as error:
        raise ConfigError(_describe(error)) from None
    _register_secrets(settings)
    return settings


class WorkerSettings(BaseSettings):
    """Настройки процесса worker: адрес сервера, токен worker, папка профилей браузера."""

    model_config = SettingsConfigDict(env_prefix=WORKER_PREFIX, env_file_encoding="utf-8", extra="forbid", frozen=True)

    api_url: str
    token: SecretStr
    profiles_dir: Path = Path(".browser-profiles")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Пауза между вопросами «есть ли задание», когда очередь пуста.
    poll_seconds: int = Field(default=10, ge=1, le=600)

    @field_validator("api_url")
    @classmethod
    def _api_origin(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("нужен адрес сервера вида http://api:8000 или https://домен")
        if parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}:
            raise ValueError("только схема, хост и порт, без пути, параметров и логина")
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname}{port}"

    @field_validator("token")
    @classmethod
    def _token_length(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < WORKER_TOKEN_MIN_CHARS:
            raise ValueError("нужен токен из команды create-worker")
        return value


def load_worker_settings(env_file: Path | None = Path("worker.env")) -> WorkerSettings:
    """Настройки worker или понятная ошибка; переменные сервера (PRICEWATCH_…) worker не смотрит."""
    unknown = sorted(
        name for name in os.environ
        if name.startswith(WORKER_PREFIX) and name.removeprefix(WORKER_PREFIX).lower() not in WorkerSettings.model_fields
    )
    if unknown:
        raise ConfigError("Неизвестные переменные окружения: " + ", ".join(unknown))
    try:
        settings = WorkerSettings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as error:
        raise ConfigError(_describe(error, WORKER_PREFIX)) from None
    register_secret(settings.token.get_secret_value())
    return settings


def _register_secrets(settings: Settings) -> None:
    """Значения секретов и пароли внутри адресов вырезаются из журнала дословно."""
    for name in Settings.model_fields:
        value = getattr(settings, name)
        if isinstance(value, SecretStr):
            secret = value.get_secret_value()
            register_secret(secret)
            password = urlsplit(secret).password
            if password:
                register_secret(unquote(password))


def _is_field(variable: str) -> bool:
    return variable.removeprefix(ENV_PREFIX).lower() in Settings.model_fields


def _describe(error: ValidationError, prefix: str = ENV_PREFIX) -> str:
    lines = []
    for issue in error.errors(include_input=False, include_url=False, include_context=False):
        location = [str(part) for part in issue["loc"]]
        field = location[0] if location else ""
        variable = f"{prefix}{field.upper()}" if field else "конфигурация"
        message = "переменная не задана" if issue["type"] == "missing" else _plain(issue)
        lines.append(f"- {variable}: {message}")
    return "Конфигурация неверна:\n" + "\n".join(lines)


def _plain(issue: dict) -> str:
    if issue["type"] == "extra_forbidden":
        return "неизвестная настройка в .env"
    if issue["type"] == "value_error":
        return str(issue["msg"]).removeprefix("Value error, ")
    if issue["type"] == "literal_error":
        return "значение не из допустимого списка"
    return "неверное значение"
