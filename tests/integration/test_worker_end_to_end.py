"""Worker целиком против настоящего сервера и базы: ссылка добавлена → worker проверил → цена в истории.

Сервер — приложение FastAPI через тестовый HTTP-клиент; worker говорит с ним по протоколу /worker/v1.
Парсер подменён: магазины в тестах не открываются.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import Engine, text

from pricewatch.api.app import create_app
from pricewatch.api.context import ApiContext
from pricewatch.application.links import add_link
from pricewatch.application.passwords import PasswordHasher
from pricewatch.application.worker_protocol import create_worker
from pricewatch.config.settings import Settings
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.pricing import Availability
from pricewatch.media.images import to_webp
from pricewatch.media.store import MediaStore
from pricewatch.parsers.catalog import LinkCatalog
from pricewatch.parsers.contract import ParsedProduct, PriceSource
from pricewatch.persistence.database import create_db_engine
from pricewatch.persistence.unit_of_work import SqlUnitOfWork
from pricewatch.worker.client import ApiClient, WorkerRejected
from pricewatch.worker.parsing import Parsed
from pricewatch.worker.runner import Runner

KEY = "worker-e2e-key-" + "k" * 30
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine(migrated_database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(migrated_database_url)
    with db_engine.begin() as db:
        db.execute(text("UPDATE check_tasks SET status = 'cancelled', lease_until = NULL, finished_at = now() WHERE status IN ('queued', 'leased')"))
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def server(engine: Engine, tmp_path: Path) -> TestClient:
    settings = Settings(env="test", public_origin="https://prices.example.ru", database_url="postgresql://u@h/d", secret_key=KEY, _env_file=None)  # type: ignore[call-arg]
    context = ApiContext(lambda: SqlUnitOfWork(engine), KEY.encode(), PasswordHasher(time_cost=1, memory_cost=8192), lambda: NOW, MediaStore(tmp_path))
    return TestClient(create_app(settings, context), base_url="https://testserver")


class FakeParsers:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.urls: list[str] = []

    def parse(self, url: str, region_code: str, parser_key: str, store_host: str, fetch_method: str | None) -> Parsed:
        self.urls.append(url)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome  # type: ignore[return-value]


def added_link(engine: Engine) -> tuple[int, str]:
    with engine.begin() as db:
        user_id = db.execute(
            text("INSERT INTO users (login, password_hash, role, display_name) VALUES (:l, 'h', 'user', 'U') RETURNING id"), {"l": f"u{uuid.uuid4().hex[:10]}"}
        ).scalar_one()
        category = db.execute(text("INSERT INTO categories (user_id, name, name_normalized, position) VALUES (:u, 'Дом', 'дом', 0) RETURNING id"), {"u": user_id}).scalar_one()
        item = db.execute(text("INSERT INTO items (user_id, category_id, name, position) VALUES (:u, :c, 'Лампа', 0) RETURNING id"), {"u": user_id, "c": category}).scalar_one()
    url = f"https://shop-{uuid.uuid4().hex[:8]}.example/p/lamp"
    added = add_link(SqlUnitOfWork(engine), LinkCatalog(), user_id, item, url, "spb", NOW)
    return added.tracked_link_id, url


class ShopImages:
    """Фото «из магазина»: настоящий JPEG, перекодированный так же, как у worker."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def load(self, image_url: str) -> bytes:
        self.urls.append(image_url)
        out = BytesIO()
        Image.new("RGB", (1200, 900), "navy").save(out, "JPEG")
        return to_webp(out.getvalue())


def worker(server: TestClient, engine: Engine, parsers: FakeParsers, images: ShopImages | None = None) -> Runner:
    token = create_worker(SqlUnitOfWork(engine), KEY.encode(), f"w-{uuid.uuid4().hex[:8]}", NOW)
    images = images or ShopImages()
    return Runner(ApiClient(server, token), parsers, images, poll_seconds=1, wait=lambda seconds: False)


def test_added_link_is_checked_and_price_lands_in_history(server: TestClient, engine: Engine) -> None:
    link_id, url = added_link(engine)
    product = ParsedProduct("Лампа настольная", 129_900, None, Availability.IN_STOCK, None, "spb", PriceSource.SITE_DATA, "Магазин Ламп")
    parsers = FakeParsers(Parsed(product, "http"))

    assert worker(server, engine, parsers).step() is True

    assert parsers.urls == [url]
    with engine.connect() as db:
        row = db.execute(text("SELECT status, price_minor, confirmation, source FROM price_observations WHERE tracked_link_id = :l"), {"l": link_id}).one()
        store = db.execute(text("SELECT s.fetch_method, s.display_name FROM stores s JOIN tracked_links l ON l.store_id = s.id WHERE l.id = :l"), {"l": link_id}).one()
    assert tuple(row) == ("ok", 129_900, "confirmed", "site_data")
    assert tuple(store) == ("http", "Магазин Ламп")


def test_photo_of_new_link_is_stored_once(server: TestClient, engine: Engine, tmp_path: Path) -> None:
    link_id, _ = added_link(engine)
    product = ParsedProduct("Лампа", 129_900, None, Availability.IN_STOCK, "https://img.example/lamp.jpg", "spb")
    images = ShopImages()

    worker(server, engine, FakeParsers(Parsed(product, "http")), images).step()

    with engine.connect() as db:
        stored = db.execute(text("SELECT image_path FROM tracked_links WHERE id = :l"), {"l": link_id}).scalar_one()
    assert images.urls == ["https://img.example/lamp.jpg"]
    with Image.open(tmp_path / stored) as saved:
        assert (saved.format, saved.size) == ("WEBP", (800, 600))

    with SqlUnitOfWork(engine) as work:  # следующая проверка этой ссылки фото уже не скачивает
        work.queue.enqueue_on_add(link_id, NOW)
        work.commit()
    worker(server, engine, FakeParsers(Parsed(product, "http")), images).step()
    assert images.urls == ["https://img.example/lamp.jpg"]


def test_store_error_lands_in_history_with_its_code(server: TestClient, engine: Engine) -> None:
    link_id, _ = added_link(engine)

    worker(server, engine, FakeParsers(PriceWatcherError(ErrorCode.PRICE_NOT_FOUND))).step()

    with engine.connect() as db:
        assert db.execute(text("SELECT status FROM price_observations WHERE tracked_link_id = :l"), {"l": link_id}).scalar_one() == "PRICE_NOT_FOUND"


def test_captcha_pauses_store_and_task_waits(server: TestClient, engine: Engine) -> None:
    link_id, _ = added_link(engine)

    worker(server, engine, FakeParsers(PriceWatcherError(ErrorCode.CAPTCHA))).step()

    with engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM price_observations WHERE tracked_link_id = :l"), {"l": link_id}).scalar_one() == 0
        assert db.execute(text("SELECT status, attempt FROM check_tasks WHERE tracked_link_id = :l"), {"l": link_id}).one() == ("queued", 0)
        paused = db.execute(
            text("SELECT s.pause_reason, s.pause_level, s.paused_until FROM stores s JOIN tracked_links l ON l.store_id = s.id WHERE l.id = :l"), {"l": link_id}
        ).one()
    assert (paused.pause_reason, paused.pause_level) == ("CAPTCHA", 1)
    assert paused.paused_until == NOW + timedelta(seconds=15)


def test_empty_queue(server: TestClient, engine: Engine) -> None:
    with engine.begin() as db:
        db.execute(text("UPDATE check_tasks SET status = 'cancelled', lease_until = NULL, finished_at = now() WHERE status IN ('queued', 'leased')"))
    parsers = FakeParsers(RuntimeError("пустая очередь не должна доходить до парсера"))

    assert worker(server, engine, parsers).step() is False
    assert parsers.urls == []


def test_wrong_token_stops_worker(server: TestClient) -> None:
    work = Runner(ApiClient(server, "w" * 43), FakeParsers(None), ShopImages(), poll_seconds=1, wait=lambda seconds: False)

    with pytest.raises(WorkerRejected):
        work.step()
