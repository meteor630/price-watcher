"""Сборка HTTP-приложения: обработчики ошибок, маршруты, статика сайта, CORS и внешний слой."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from pricewatch.api.context import ApiContext
from pricewatch.api.edge import REQUEST_ID_HEADER, EdgeMiddleware
from pricewatch.api.errors import handle_http_exception, handle_project_error, handle_validation_error
from pricewatch.api.v1_auth import router as v1_auth_router
from pricewatch.api.web_pages import router as web_pages_router
from pricewatch.api.worker_v1 import router as worker_v1_router
from pricewatch.config.settings import Settings
from pricewatch.domain.errors import PriceWatcherError
from pricewatch.web.templates import STATIC_DIR


def create_app(settings: Settings, context: ApiContext | None = None) -> FastAPI:
    # Схема API — утверждённый openapi/v1.yaml; автоматическая документация не публикуется.
    app = FastAPI(title="Price Watcher", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.context = context
    app.state.public_origin = settings.public_origin
    app.add_exception_handler(PriceWatcherError, handle_project_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(HTTPException, handle_http_exception)
    app.include_router(v1_auth_router)
    app.include_router(worker_v1_router)
    app.include_router(web_pages_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-None-Match", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "ETag"],
        max_age=600,
    )
    # Добавленный последним слой — внешний: он видит ответы CORS и все исключения.
    app.add_middleware(EdgeMiddleware, trusted_proxies=settings.trusted_proxies)
    return app
