"""Отчёт о неожиданной ошибке приложения: POST /api/v1/client_errors по openapi/v1.yaml.

Только с bearer-токеном устройства; лишние поля и превышенные длины — 422; сверх 20 отчётов в час — 429.
Ответ 201 с error_id, который приложение показывает пользователю; повтор с тем же ключом — тот же ответ.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from pricewatch.api.app_auth import app_user
from pricewatch.api.context import get_context
from pricewatch.api.idempotency import idempotent
from pricewatch.application.app_auth import DeviceUser
from pricewatch.application.client_errors import ClientReport, report_client_error
from pricewatch.observability.ids import new_error_id

router = APIRouter(prefix="/api/v1")

CREATED = 201


class ClientErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    app_version: Annotated[str, Field(max_length=40)]
    os_version: Annotated[str, Field(max_length=40)]
    screen: Annotated[str, Field(max_length=80)]
    error_code: Annotated[str, Field(max_length=80)]
    message: Annotated[str, Field(max_length=2000)]
    # Тело приходит уже разобранным JSON: дата — строка ISO 8601, её разбор нестрогий, но с часовым поясом.
    occurred_at: Annotated[AwareDatetime, Field(strict=False)]


@router.post("/client_errors")
def client_error_route(request: Request, body: ClientErrorBody, device: DeviceUser = Depends(app_user)) -> JSONResponse:
    context = get_context(request)

    def run() -> dict[str, Any]:
        error_id = new_error_id()
        report = ClientReport(body.app_version, body.os_version, body.screen, body.error_code, body.message, body.occurred_at)
        report_client_error(context.unit_of_work(), device, error_id, report, context.clock())
        return {"error_id": error_id}

    return idempotent(request, device.user.id, body.model_dump(mode="json"), run, status=CREATED)
