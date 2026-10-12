"""Бот целиком против настоящего сервера и базы: код с сайта → /start в Telegram → чат привязан → /code → код
подключает приложение; уведомление администратору уходит в Telegram и отмечается отправленным. Telegram подменён."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.application.notifications import report_unexpected
from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.services import create_service
from pricewatch.application.telegram import issue_link_code
from pricewatch.bot import commands
from pricewatch.bot.api import InternalApi, ServiceRejected
from pricewatch.bot.runner import Bot
from pricewatch.bot.telegram import Update
from pricewatch.config.settings import Settings
from pricewatch.domain.accounts import Role, User
from pricewatch.media.store import MediaStore
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = "bot-e2e-key-" + "k" * 30
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine(module_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(module_database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def server(engine: Engine, tmp_path: Path) -> TestClient:
    settings = Settings(env="test", public_origin="https://prices.example.ru", database_url="postgresql://u@h/d", secret_key=KEY, _env_file=None)  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), PasswordHasher(time_cost=1, memory_cost=8192), lambda: NOW, MediaStore(tmp_path), LinkCatalog())
    return TestClient(create_app(settings, context), base_url="https://testserver")


class FakeTelegram:
    """Telegram с заранее заданными обновлениями; отправленное запоминается."""

    def __init__(self, batches: list[list[Update]] | None = None) -> None:
        self.batches = list(batches or [])
        self.messages: list[tuple[int, str]] = []

    def updates(self, offset: int | None, poll_s: int) -> list[Update]:
        return self.batches.pop(0) if self.batches else []

    def send(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        return None


def say(text: str, update_id: int, chat: int) -> Update:
    return Update(update_id, chat, text)


def admin(engine: Engine) -> User:
    with engine.begin() as db:
        user_id = db.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'admin', 'Владелец') RETURNING id"), {"l": f"b{uuid.uuid4().hex[:10]}"}
        ).scalar_one()
    return User(user_id, "b", Role.ADMIN, "Владелец", False)


def bot(engine: Engine, server: TestClient, telegram: FakeTelegram) -> Bot:
    token = create_service(SqlUnitOfWork(engine), KEY.encode(), f"bot-{uuid.uuid4().hex[:8]}", NOW - timedelta(days=1))
    return Bot(InternalApi(server, token), telegram, wait=lambda seconds: False)  # type: ignore[arg-type]


def test_link_from_site_then_pairing_code_connects_app(engine: Engine, server: TestClient) -> None:
    owner = admin(engine)
    chat = 800_000_000 + owner.id
    code = issue_link_code(SqlUnitOfWork(engine), KEY.encode(), owner, NOW).code
    telegram = FakeTelegram([[say(f"/start {code}", 1, chat)], [say("/code", 2, chat)]])
    worker = bot(engine, server, telegram)

    worker.tick()
    worker.tick()

    linked, pairing = telegram.messages
    assert linked == (chat, commands.LINKED.format(name="Владелец"))
    assert pairing[0] == chat and pairing[1].startswith("Код для приложения: ")
    app_code = pairing[1].removeprefix("Код для приложения: ").split(".")[0].replace(" ", "")
    paired = server.post("/api/v1/auth/pair", json={"code": app_code, "device_name": "iPhone"})
    assert paired.status_code == 200


def test_admin_notification_goes_to_telegram_once(engine: Engine, server: TestClient) -> None:
    owner = admin(engine)
    chat = 810_000_000 + owner.id
    with engine.begin() as db:
        db.execute(text("UPDATE users SET telegram_chat_id = :c WHERE id = :u"), {"c": chat, "u": owner.id})
    report_unexpected(SqlUnitOfWork(engine), "E-BBBBBBBBBB", "GET", "/web/", "KeyError", NOW - timedelta(minutes=1))
    telegram = FakeTelegram()
    worker = bot(engine, server, telegram)

    worker.tick()
    worker.tick()

    mine = [text_ for chat_id, text_ in telegram.messages if chat_id == chat]
    assert mine == ["Неожиданная ошибка сервера: KeyError в GET /web/. Номер ошибки E-BBBBBBBBBB."]
    with engine.connect() as db:
        assert db.execute(text("SELECT sent_at FROM notifications WHERE user_id = :u AND channel = 'telegram'"), {"u": owner.id}).scalar_one() == NOW


def test_wrong_service_token_stops_bot(server: TestClient) -> None:
    worker = Bot(InternalApi(server, "s" * 43), FakeTelegram(), wait=lambda seconds: False)  # type: ignore[arg-type]

    with pytest.raises(ServiceRejected):
        worker.run()
