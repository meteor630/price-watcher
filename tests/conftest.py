"""Общие фикстуры: временная база PostgreSQL на прогон тестов.

TEST_DATABASE_URL — адрес сервера, где можно создавать базы, например
postgresql://localhost/postgres. Без него тесты базы падают с объяснением:
пропуск проверки был бы её ослаблением.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, make_url, text

TEST_DATABASE_ENV = "TEST_DATABASE_URL"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Адрес новой пустой базы; она удаляется после прогона."""
    admin_url = os.environ.get(TEST_DATABASE_ENV)
    if not admin_url:
        pytest.fail(
            f"Для тестов базы задайте {TEST_DATABASE_ENV}, например "
            f"{TEST_DATABASE_ENV}=postgresql://localhost/postgres uv run pytest"
        )
    server = make_url(admin_url).set(drivername="postgresql+psycopg")
    name = f"pricewatch_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(server, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield server.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()
