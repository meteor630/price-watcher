"""pricewatch.api: заголовки, request_id, ответы об ошибках по схеме OpenAPI, CORS, 500 без утечек."""

import io
import json
import logging
import re
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from pricewatch.api.app import create_app
from pricewatch.api.edge import SECURITY_HEADERS
from pricewatch.api.errors import HTTP_STATUS
from pricewatch.config.settings import Settings
from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.observability.ids import current_request_id
from pricewatch.observability.json_log import JsonFormatter

ROOT = Path(__file__).resolve().parents[2]
OPENAPI = yaml.safe_load((ROOT / "openapi" / "v1.yaml").read_text(encoding="utf-8"))
ERROR_SCHEMA = Draft202012Validator(OPENAPI["components"]["schemas"]["ErrorResponse"])
ORIGIN = "https://prices.example.ru"
SECRET = "pw_7c1f0e9d"


def make_settings() -> Settings:
    return Settings(env="test", public_origin=ORIGIN, database_url="postgresql://pricewatch@localhost:5432/pricewatch", _env_file=None)  # type: ignore[call-arg]


def make_app() -> FastAPI:
    app = create_app(make_settings())

    @app.get("/t/ok")
    def ok() -> dict[str, str | None]:
        logging.getLogger("pricewatch.test.route").info("внутри маршрута")
        return {"request_id": current_request_id()}

    @app.get("/t/boom")
    def boom() -> None:
        raise RuntimeError(f"подключение postgresql://u:{SECRET}@db не удалось password={SECRET}")

    @app.get("/t/conflict")
    def conflict() -> None:
        raise PriceWatcherError(ErrorCode.VERSION_CONFLICT, {"current": {"id": 1, "version": 3}})

    @app.get("/t/quantity")
    def quantity(value: int) -> dict[str, int]:
        return {"value": value}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(make_app(), raise_server_exceptions=False)


def assert_error_body(response, code: str) -> dict:  # type: ignore[no-untyped-def]
    body = response.json()
    ERROR_SCHEMA.validate(body)
    assert body["error"]["code"] == code
    return body["error"]


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("get", "/t/ok", 200),
        ("get", "/absent", 404),
        ("post", "/t/ok", 405),
        ("get", "/t/quantity?value=abc", 422),
        ("get", "/t/conflict", 409),
        ("get", "/t/boom", 500),
    ],
)
def test_security_headers_and_request_id_on_every_status(
    client: TestClient, method: str, path: str, status: int
) -> None:
    response = client.request(method, path)

    assert response.status_code == status
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])
    assert "server" not in response.headers


def test_request_id_is_available_inside_handler(client: TestClient) -> None:
    response = client.get("/t/ok")

    assert response.json() == {"request_id": response.headers["X-Request-ID"]}


def test_valid_incoming_request_id_is_kept(client: TestClient) -> None:
    response = client.get("/t/ok", headers={"X-Request-ID": "ios-3f2a9c1e-77"})

    assert response.headers["X-Request-ID"] == "ios-3f2a9c1e-77"


@pytest.mark.parametrize("incoming", ["short", "a" * 65, "id with spaces", "id;drop", "ид-кириллица-123"])
def test_suspicious_incoming_request_id_is_replaced(client: TestClient, incoming: str) -> None:
    # Байтами: так клиент может прислать и не-ASCII, как это сделал бы посторонний клиент.
    response = client.get("/t/ok", headers={"X-Request-ID": incoming.encode("utf-8")})

    assert response.headers["X-Request-ID"] != incoming
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


def test_not_found_matches_schema(client: TestClient) -> None:
    error = assert_error_body(client.get("/absent"), "NOT_FOUND")

    assert error["error_id"] is None


def test_method_not_allowed_keeps_allow_header(client: TestClient) -> None:
    response = client.post("/t/ok")

    assert_error_body(response, "NOT_FOUND")
    assert response.headers["Allow"] == "GET"


def test_validation_error_names_field_without_echoing_value(client: TestClient) -> None:
    response = client.get(f"/t/quantity?value={SECRET}")

    error = assert_error_body(response, "VALIDATION_ERROR")
    assert error["details"] == {"fields": {"query.value": "int_parsing"}}
    assert SECRET not in response.text


def test_project_error_uses_catalog_status_and_details(client: TestClient) -> None:
    response = client.get("/t/conflict")

    error = assert_error_body(response, "VERSION_CONFLICT")
    assert response.status_code == 409
    assert error["message"] == "Запись уже изменили в другом месте. Показаны актуальные данные."
    assert error["details"] == {"current": {"id": 1, "version": 3}}


def test_unexpected_error_is_logged_but_not_leaked(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        response = client.get("/t/boom")

    error = assert_error_body(response, "INTERNAL_ERROR")
    assert re.fullmatch(r"E-[A-Z2-9]{10}", error["error_id"])
    assert SECRET not in response.text
    assert "Traceback" not in response.text and "RuntimeError" not in response.text

    (record,) = [r for r in caplog.records if getattr(r, "error_id", None) == error["error_id"]]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert record.path == "/t/boom"  # type: ignore[attr-defined]


def test_access_log_has_path_without_query(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="pricewatch.api.edge"):
        client.get(f"/t/quantity?value=1&token={SECRET}")

    (record,) = [r for r in caplog.records if r.getMessage() == "HTTP-запрос"]
    assert (record.method, record.path, record.status) == ("GET", "/t/quantity", 200)  # type: ignore[attr-defined]
    assert record.duration_ms >= 0  # type: ignore[attr-defined]
    assert SECRET not in str(vars(record))


def test_log_inside_route_carries_request_id(client: TestClient) -> None:
    # Форматтер читает request_id в момент записи, поэтому нужен свой обработчик, а не caplog.
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    route_logger = logging.getLogger("pricewatch.test.route")
    route_logger.addHandler(handler)
    route_logger.setLevel(logging.INFO)
    try:
        response = client.get("/t/ok")
    finally:
        route_logger.removeHandler(handler)

    (entry,) = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert entry["message"] == "внутри маршрута"
    assert entry["request_id"] == response.headers["X-Request-ID"]


def test_cors_allows_only_own_origin(client: TestClient) -> None:
    preflight = {"Access-Control-Request-Method": "PATCH", "Access-Control-Request-Headers": "Idempotency-Key"}

    own = client.options("/t/ok", headers={"Origin": ORIGIN, **preflight})
    foreign = client.options("/t/ok", headers={"Origin": "https://evil.example", **preflight})
    simple_foreign = client.get("/t/ok", headers={"Origin": "https://evil.example"})

    assert own.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "Access-Control-Allow-Origin" not in foreign.headers
    assert "Access-Control-Allow-Origin" not in simple_foreign.headers
    assert foreign.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_generated_docs_are_not_published(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_every_error_code_has_http_status() -> None:
    assert set(HTTP_STATUS) == set(ErrorCode)
    assert all(400 <= status <= 599 for status in HTTP_STATUS.values())


def test_openapi_declares_statuses_used_for_its_codes() -> None:
    # Коды, которые контракт явно связывает со статусом, получают тот же статус в каталоге.
    assert HTTP_STATUS[ErrorCode.VERSION_CONFLICT] == 409
    assert HTTP_STATUS[ErrorCode.IDEMPOTENCY_KEY_REUSED] == 409
    assert HTTP_STATUS[ErrorCode.RUN_IN_PROGRESS] == 409
    assert HTTP_STATUS[ErrorCode.REFRESH_TOKEN_REUSED] == 401
    assert "409" in OPENAPI["paths"]["/runs"]["post"]["responses"]
