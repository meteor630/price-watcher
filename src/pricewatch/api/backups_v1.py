"""Выдача копий домашнему приёмнику (DEVELOPMENT_ARCHITECTURE.md, раздел 11): /backups/v1, вне контракта приложения.

Только Authorization: Bearer с токеном приёмника копий; токены worker, бота, приложения и cookie сайта здесь не действуют.
Неверный токен — неудача по IP, после 10 за 15 минут — 429. Папка копий подключена к сайту только на чтение.
- GET  /files         — копии с размером и sha256 (копия без файла суммы ещё не готова и не показывается);
- GET  /files/{name}  — скачать копию; имя — только формата pricewatch-<время>Z.tar.age;
- POST /received      — приёмник получил копию и сверил сумму (отметка для сторожа «дом не забирал копии»).
Сервер в дом не ходит и копий по запросу приёмника не удаляет.
"""

import re
from pathlib import Path as FsPath
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from pricewatch.api.app_auth import bearer_token
from pricewatch.api.context import client_ip, get_context
from pricewatch.application.backup_readers import authenticate_backup_reader, confirm_received
from pricewatch.domain.backups import NAME, checksum_name, created_at
from pricewatch.domain.errors import ErrorCode, PriceWatcherError

router = APIRouter(prefix="/backups/v1")

SHA256 = re.compile(r"^[0-9a-f]{64}$")
BackupName = Annotated[str, Field(min_length=1, max_length=64, pattern=NAME.pattern)]


class ReceivedIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: BackupName


def current_reader(request: Request) -> int:
    context = get_context(request)
    return authenticate_backup_reader(context.unit_of_work(), context.secret_key, bearer_token(request), client_ip(request), context.clock())


def _folder(request: Request) -> FsPath:
    folder: FsPath | None = request.app.state.backups_dir
    if folder is None:
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    return folder


def _checksum(folder: FsPath, name: str) -> str | None:
    try:
        digest = (folder / checksum_name(name)).read_text(encoding="ascii").split(maxsplit=1)[0]
    except (OSError, UnicodeDecodeError, IndexError):
        return None
    return digest if SHA256.match(digest) else None


def _ready(folder: FsPath) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for entry in sorted(folder.iterdir()):
        if created_at(entry.name) is None or not entry.is_file():
            continue
        digest = _checksum(folder, entry.name)
        if digest is not None:
            items.append({"name": entry.name, "size_bytes": entry.stat().st_size, "sha256": digest})
    return items


@router.get("/files")
async def list_backups(request: Request, _reader: int = Depends(current_reader)) -> dict[str, object]:
    return {"items": await run_in_threadpool(_ready, _folder(request))}


@router.get("/files/{name}")
async def download_backup(
    request: Request, name: Annotated[str, Path(max_length=64, pattern=NAME.pattern)], _reader: int = Depends(current_reader)
) -> FileResponse:
    folder = _folder(request)
    target = folder / name
    if not await run_in_threadpool(lambda: target.is_file() and _checksum(folder, name) is not None):
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    return FileResponse(target, media_type="application/octet-stream", filename=name)


@router.post("/received", status_code=204)
async def backup_received(body: ReceivedIn, request: Request, reader: int = Depends(current_reader)) -> Response:
    folder = _folder(request)
    if not await run_in_threadpool((folder / body.name).is_file):
        raise PriceWatcherError(ErrorCode.NOT_FOUND)
    context = get_context(request)
    await run_in_threadpool(confirm_received, context.unit_of_work(), reader, context.clock())
    return Response(status_code=204)
