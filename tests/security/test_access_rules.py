"""Правила доступа, которые сами распространяются на будущий код.

- Каждый маршрут /api/v1 без bearer-токена отвечает 401; без токена открыты только
  маршруты из PUBLIC_API — по контракту. Маршруты перебираются из самого приложения,
  поэтому новый маршрут без защиты сразу уронит тест.
- Каждое действие администратора из ADMIN_ONLY от обычного пользователя даёт FORBIDDEN.
  Новое действие администратора добавляется в этот список.
"""

import tempfile
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.application.accounts import create_invite
from pricewatch.application.passwords import PasswordHasher
from pricewatch.config.settings import Settings
from pricewatch.domain.accounts import Role
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.media.store import MediaStore
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork

KEY = b"access-rules-key-" + b"k" * 20
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
PUBLIC_API = {("POST", "/api/v1/auth/pair"), ("POST", "/api/v1/auth/refresh")}

ADMIN_ONLY: dict[str, Callable[[SqlUnitOfWork, int], object]] = {
    "create_invite": lambda uow, actor: create_invite(uow, KEY, actor, Role.USER, NOW),
}


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    yield db_engine
    db_engine.dispose()


def make_client(engine: Engine) -> TestClient:
    settings = Settings(
        env="test", public_origin="https://prices.example.ru", database_url="postgresql://u@h/d", secret_key="k" * 40, _env_file=None
    )  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY, PasswordHasher(time_cost=1, memory_cost=8192), lambda: NOW, MediaStore(Path(tempfile.mkdtemp())), LinkCatalog())
    return TestClient(create_app(settings, context), base_url="https://testserver")


def api_routes(client: TestClient) -> list[tuple[str, str]]:
    # Публичный app.openapi(): в FastAPI 0.142 подключённые роутеры хранятся в app.routes одной обёрткой.
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/api/v1")
        for method in operations
    )


def test_public_routes_exist(engine: Engine) -> None:
    assert PUBLIC_API <= set(api_routes(make_client(engine)))


@pytest.mark.parametrize("bearer", [None, "Bearer made-up-token"])
def test_every_api_route_requires_bearer(engine: Engine, bearer: str | None) -> None:
    client = make_client(engine)
    headers = {"Authorization": bearer} if bearer else {}
    protected = [route for route in api_routes(client) if route not in PUBLIC_API]

    unprotected = [
        f"{method} {path}"
        for method, path in protected
        if client.request(method, path.replace("{", "").replace("}", ""), headers=headers).status_code != 401
    ]

    assert protected, "в /api/v1 нет защищённых маршрутов — проверять нечего"
    assert unprotected == []


def add_user(engine: Engine, role: str, disabled: bool = False) -> int:
    with engine.begin() as connection:
        return connection.execute(
            text(
                "INSERT INTO users (login, password_hash, role, display_name, disabled_at) "
                "VALUES (:l, 'h', :r, 'U', CASE WHEN :d THEN now() END) RETURNING id"
            ),
            {"l": f"r{uuid.uuid4().hex[:10]}", "r": role, "d": disabled},
        ).scalar_one()


@pytest.mark.parametrize("action", sorted(ADMIN_ONLY))
@pytest.mark.parametrize(("role", "disabled"), [("user", False), ("admin", True)])
def test_admin_actions_forbidden_for_others(engine: Engine, action: str, role: str, disabled: bool) -> None:
    actor = add_user(engine, role, disabled)

    with pytest.raises(PriceWatcherError) as caught:
        ADMIN_ONLY[action](SqlUnitOfWork(engine), actor)

    assert caught.value.code is ErrorCode.FORBIDDEN


@pytest.mark.parametrize("action", sorted(ADMIN_ONLY))
def test_admin_actions_allowed_for_active_admin(engine: Engine, action: str) -> None:
    ADMIN_ONLY[action](SqlUnitOfWork(engine), add_user(engine, "admin"))
