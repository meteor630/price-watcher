"""Протокол worker по HTTP (DEVELOPMENT_ARCHITECTURE.md, раздел 8): /worker/v1/claim, /heartbeat, /result.

Только Authorization: Bearer с токеном worker; токены приложения и cookie сайта здесь не действуют.
В контракт приложения (openapi/v1.yaml) не входит: это внутренний протокол сервера и worker.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from pricewatch.api.app_auth import bearer_token
from pricewatch.api.context import get_context
from pricewatch.application.worker_protocol import (
    DURATION_MAX_MS,
    PRICE_MAX,
    SITE_NAME_MAX,
    TITLE_MAX,
    WorkerResult,
    authenticate_worker,
    claim_task,
    extend_lease,
    submit_result,
)

router = APIRouter(prefix="/worker/v1")

TaskId = Annotated[int, Field(ge=1, le=2**62)]
Price = Annotated[int, Field(ge=1, le=PRICE_MAX)]


def current_worker(request: Request) -> int:
    context = get_context(request)
    return authenticate_worker(context.unit_of_work(), context.secret_key, bearer_token(request), context.clock())


class TaskOut(BaseModel):
    task_id: int
    url: str
    region_code: str
    store_host: str
    parser_key: str
    fetch_method: str | None
    min_delay_ms: int
    lease_until: str


class ClaimOut(BaseModel):
    task: TaskOut | None


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: TaskId


class HeartbeatOut(BaseModel):
    lease_until: str


class ResultIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: TaskId
    status: Annotated[str, Field(min_length=2, max_length=40)]
    price_minor: Price | None = None
    card_price_minor: Price | None = None
    availability: Literal["in_stock", "out_of_stock", "unknown"] = "unknown"
    title: Annotated[str, Field(max_length=TITLE_MAX * 2)] | None = None
    source: Literal["site_data", "page"] | None = None
    site_name: Annotated[str, Field(max_length=SITE_NAME_MAX * 2)] | None = None
    fetch_method: Literal["http", "browser"] | None = None
    duration_ms: Annotated[int, Field(ge=0, le=DURATION_MAX_MS)] | None = None


@router.post("/claim")
def claim(request: Request, worker_id: int = Depends(current_worker)) -> ClaimOut:
    context = get_context(request)
    task = claim_task(context.unit_of_work(), worker_id, context.clock())
    if task is None:
        return ClaimOut(task=None)
    return ClaimOut(
        task=TaskOut(
            task_id=task.task_id, url=task.url, region_code=task.region_code, store_host=task.store_host,
            parser_key=task.parser_key, fetch_method=task.fetch_method, min_delay_ms=task.min_delay_ms,
            lease_until=_utc(task.lease_until),
        )
    )


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, request: Request, worker_id: int = Depends(current_worker)) -> HeartbeatOut:
    context = get_context(request)
    return HeartbeatOut(lease_until=_utc(extend_lease(context.unit_of_work(), worker_id, body.task_id, context.clock())))


@router.post("/result", status_code=204)
def result(body: ResultIn, request: Request, worker_id: int = Depends(current_worker)) -> Response:
    context = get_context(request)
    fields = body.model_dump(exclude={"task_id"})
    submit_result(context.unit_of_work(), worker_id, body.task_id, WorkerResult(**fields), context.clock())
    return Response(status_code=204)


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()
