"""Домашний приёмник копий (DEVELOPMENT_ARCHITECTURE.md, раздел 11): забирает копии с сервера, сервер в дом не ходит.

- Список копий (/backups/v1/files) разбирается строго: ровно поля name, size_bytes, sha256, правильные типы и
  формат; ответ больше 64 КБ, лишние поля, неверные имена — отказ, ничего не скачивается.
- Скачивается только то, чего дома нет. Копия больше лимита (max_bytes) не скачивается вовсе; при скачивании
  обрыв, лишние байты сверх заявленного размера или неверная sha256 — файл удаляется, копия не считается полученной.
  Запись — под временным именем и переименование целиком: оборванная загрузка не выглядит копией.
- Полученная копия подтверждается серверу (/received): по этой отметке сторож сообщает, что дом давно не забирал копии.
- Приёмник ничего не распаковывает и не запускает: зашифровать открытым ключом age может кто угодно, в том числе
  захваченный сервер, поэтому копия — только файл для ручного восстановления владельцем.
- Дома копии хранятся 30 суток; старше — удаляются вместе с файлом суммы. Копии на сервере приёмник не удаляет никогда.
"""

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx2

from pricewatch.domain.backups import NAME, checksum_name, created_at, expired

logger = logging.getLogger(__name__)

LIST_LIMIT = 64 * 1024
CHUNK = 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PARTIAL_PATTERN = ".pricewatch-*.partial"
ITEM_FIELDS = {"name", "size_bytes", "sha256"}


class PullError(Exception):
    """Сервер не ответил, отказал или прислал список не по схеме — забрать копии сейчас нельзя."""


@dataclass(frozen=True, slots=True)
class RemoteCopy:
    name: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PullReport:
    received: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


def parse_listing(raw: bytes, max_bytes: int) -> list[RemoteCopy]:
    """Список копий от сервера — недоверенный ввод: любое отклонение от схемы — PullError."""
    if len(raw) > LIST_LIMIT:
        raise PullError("список копий больше лимита")
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise PullError("список копий — не JSON") from error
    if not isinstance(data, dict) or set(data) != {"items"} or not isinstance(data["items"], list):
        raise PullError("список копий не по схеме")
    copies: list[RemoteCopy] = []
    for item in data["items"]:
        if not isinstance(item, dict) or set(item) != ITEM_FIELDS:
            raise PullError("копия в списке не по схеме")
        name, size, digest = item["name"], item["size_bytes"], item["sha256"]
        if not isinstance(name, str) or NAME.match(name) is None or created_at(name) is None:
            raise PullError("неверное имя копии")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise PullError("неверный размер копии")
        if not isinstance(digest, str) or SHA256.match(digest) is None:
            raise PullError("неверная сумма копии")
        if size > max_bytes:
            logger.error("Копия на сервере больше лимита — не скачивается", extra={"file": name, "size_bytes": size, "limit": max_bytes})
            continue
        copies.append(RemoteCopy(name, size, digest))
    return copies


class BackupPuller:
    def __init__(self, client: httpx2.Client, token: str, folder: Path, max_bytes: int, clock: Callable[[], datetime]) -> None:
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"}
        self._folder = folder
        self._max_bytes = max_bytes
        self._clock = clock

    def pull(self) -> PullReport:
        self._folder.mkdir(parents=True, exist_ok=True)
        for leftover in self._folder.glob(PARTIAL_PATTERN):
            leftover.unlink()
        have = {entry.name for entry in self._folder.iterdir() if entry.is_file()}
        received: list[str] = []
        rejected: list[str] = []
        for copy in self._listing():
            if copy.name in have:
                continue
            if self._download(copy):
                self._confirm(copy.name)
                received.append(copy.name)
            else:
                rejected.append(copy.name)
        removed = self._remove_expired()
        return PullReport(tuple(received), tuple(rejected), tuple(removed))

    def _listing(self) -> list[RemoteCopy]:
        try:
            with self._client.stream("GET", "/backups/v1/files", headers=self._headers) as response:
                if response.status_code != 200:
                    raise PullError(f"сервер ответил {response.status_code} на список копий")
                raw = b""
                for chunk in response.iter_bytes():
                    raw += chunk
                    if len(raw) > LIST_LIMIT:
                        raise PullError("список копий больше лимита")
        except httpx2.HTTPError as error:
            raise PullError("сервер не ответил на список копий") from error
        return parse_listing(raw, self._max_bytes)

    def _download(self, copy: RemoteCopy) -> bool:
        partial = self._folder / f".{copy.name}.partial"
        digest = hashlib.sha256()
        written = 0
        try:
            with self._client.stream("GET", f"/backups/v1/files/{copy.name}", headers=self._headers) as response:
                if response.status_code != 200:
                    logger.error("Копия не скачана", extra={"file": copy.name, "status": response.status_code})
                    return False
                with partial.open("xb") as output:
                    for chunk in response.iter_bytes(CHUNK):
                        written += len(chunk)
                        if written > copy.size_bytes:
                            break
                        digest.update(chunk)
                        output.write(chunk)
        except httpx2.HTTPError:
            logger.error("Скачивание копии оборвалось", extra={"file": copy.name}, exc_info=True)
            partial.unlink(missing_ok=True)
            return False
        if written != copy.size_bytes or digest.hexdigest() != copy.sha256:
            logger.error(
                "Копия не совпала с заявленной — удалена",
                extra={"file": copy.name, "expected_bytes": copy.size_bytes, "got_bytes": written, "checksum_ok": digest.hexdigest() == copy.sha256},
            )
            partial.unlink(missing_ok=True)
            return False
        (self._folder / checksum_name(copy.name)).write_text(f"{copy.sha256}  {copy.name}\n", encoding="ascii")
        os.replace(partial, self._folder / copy.name)
        logger.info("Копия получена", extra={"file": copy.name, "size_bytes": written})
        return True

    def _confirm(self, name: str) -> None:
        try:
            response = self._client.post("/backups/v1/received", json={"name": name}, headers=self._headers)
        except httpx2.HTTPError:
            logger.error("Получение копии не подтверждено серверу", extra={"file": name}, exc_info=True)
            return
        if response.status_code != 204:
            logger.error("Сервер не принял подтверждение копии", extra={"file": name, "status": response.status_code})

    def _remove_expired(self) -> list[str]:
        names = [entry.name for entry in self._folder.iterdir() if entry.is_file()]
        old = expired(names, self._clock())
        for name in old:
            (self._folder / name).unlink()
            (self._folder / checksum_name(name)).unlink(missing_ok=True)
            logger.info("Старая копия удалена", extra={"file": name})
        return old
