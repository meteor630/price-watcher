"""Реализованные маршруты /api/v1 существуют в утверждённом openapi/v1.yaml с тем же методом."""

from pathlib import Path

import yaml

from pricewatch.api.app import create_app
from pricewatch.config.settings import Settings

CONTRACT = yaml.safe_load((Path(__file__).resolve().parents[2] / "openapi" / "v1.yaml").read_text(encoding="utf-8"))
METHODS = {"get", "post", "put", "patch", "delete"}


def implemented() -> set[tuple[str, str]]:
    settings = Settings(
        env="test", public_origin="https://x.ru", database_url="postgresql://u@h/d", secret_key="k" * 40, _env_file=None
    )  # type: ignore[call-arg]
    paths = create_app(settings).openapi()["paths"]
    return {(method, path.removeprefix("/api/v1")) for path, ops in paths.items() if path.startswith("/api/v1") for method in ops}


def contracted() -> set[tuple[str, str]]:
    return {(method, path) for path, ops in CONTRACT["paths"].items() for method in ops if method in METHODS}


def test_every_implemented_route_is_in_contract() -> None:
    routes = implemented()

    assert routes, "маршрутов /api/v1 не найдено"
    assert routes - contracted() == set()


def test_auth_routes_are_implemented() -> None:
    assert {("post", "/auth/pair"), ("post", "/auth/refresh"), ("post", "/auth/logout"), ("get", "/me")} <= implemented()
