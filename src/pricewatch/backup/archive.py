"""Папка копий на сервере (DEVELOPMENT_ARCHITECTURE.md, раздел 11).

Копия — tar с дампом базы (pg_dump, формат custom) и папкой картинок, зашифрованный открытым ключом age.
Расшифровать копию на сервере нельзя: закрытый ключ хранится у владельца отдельно.

- Пароль базы — только в окружении pg_dump (PGPASSWORD), не в аргументах: аргументы видны в списке процессов.
- Незашифрованный дамп лежит только во временной папке (в контейнере /tmp — в памяти) и удаляется сразу после копии;
  в age tar идёт потоком, на диск попадает только зашифрованное.
- Копия пишется под временным именем и переименовывается целиком: оборванная запись не выглядит копией.
  Временные файлы оборванных попыток удаляются перед новой копией.
- Готовая копия получает файл <копия>.sha256; он пишется до переименования копии, так что копия без суммы — только
  оборванная запись. Сервер в дом не ходит: домашний приёмник сам скачивает копии и сверяет сумму (backup.pull).
"""

import hashlib
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from pricewatch.application.ports import BackupError
from pricewatch.domain.backups import checksum_name

logger = logging.getLogger(__name__)

DATABASE_DUMP_FAILED = "DATABASE_DUMP_FAILED"
ENCRYPTION_FAILED = "ENCRYPTION_FAILED"
STORAGE_FAILED = "STORAGE_FAILED"
MEDIA_UNAVAILABLE = "MEDIA_UNAVAILABLE"
CHUNK = 1024 * 1024
PARTIAL_PATTERN = ".pricewatch-*.partial"
STDERR_TAIL = 2000


@dataclass(frozen=True, slots=True)
class DatabaseAddress:
    host: str
    port: int
    user: str
    name: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Programs:
    """Команды запуска pg_dump и age (в тестах — подставные программы)."""

    pg_dump: Sequence[str] = ("pg_dump",)
    age: Sequence[str] = ("age",)


class AgeArchive:
    def __init__(
        self, folder: Path, media_dir: Path, recipient: str, database: DatabaseAddress, *,
        programs: Programs = Programs(), work_root: Path | None = None,
    ) -> None:
        self.folder = folder
        self.media_dir = media_dir
        self.recipient = recipient
        self.database = database
        self.programs = programs
        self.work_root = work_root

    def create(self, name: str) -> int:
        if not self.media_dir.is_dir():
            logger.error("Нет папки картинок", extra={"path": str(self.media_dir)})
            raise BackupError(MEDIA_UNAVAILABLE)
        self._remove_partials()
        partial = self.folder / f".{name}.partial"
        target = self.folder / name
        with tempfile.TemporaryDirectory(dir=self.work_root) as work:
            dump = Path(work) / "database.dump"
            self._dump(dump)
            self._encrypt(dump, partial)
        try:
            digest = _sha256(partial)
            (self.folder / checksum_name(name)).write_text(f"{digest}  {name}\n", encoding="ascii")
            os.replace(partial, target)
            return target.stat().st_size
        except OSError as error:
            logger.error("Копия не переименована", extra={"file": name}, exc_info=True)
            raise BackupError(STORAGE_FAILED) from error

    def names(self) -> list[str]:
        try:
            return [entry.name for entry in self.folder.iterdir() if entry.is_file()]
        except OSError as error:
            logger.error("Папка копий недоступна", extra={"path": str(self.folder)}, exc_info=True)
            raise BackupError(STORAGE_FAILED) from error

    def size(self, name: str) -> int:
        try:
            return (self.folder / name).stat().st_size
        except OSError as error:
            logger.error("Нет копии", extra={"file": name}, exc_info=True)
            raise BackupError(STORAGE_FAILED) from error

    def free_bytes(self) -> int:
        try:
            return shutil.disk_usage(self.folder).free
        except OSError as error:
            logger.error("Папка копий недоступна", extra={"path": str(self.folder)}, exc_info=True)
            raise BackupError(STORAGE_FAILED) from error

    def remove(self, name: str) -> None:
        try:
            (self.folder / name).unlink()
            (self.folder / checksum_name(name)).unlink(missing_ok=True)
        except OSError as error:
            logger.error("Старая копия не удалена", extra={"file": name}, exc_info=True)
            raise BackupError(STORAGE_FAILED) from error

    def _remove_partials(self) -> None:
        try:
            for leftover in self.folder.glob(PARTIAL_PATTERN):
                leftover.unlink()
        except OSError as error:
            logger.error("Папка копий недоступна", extra={"path": str(self.folder)}, exc_info=True)
            raise BackupError(STORAGE_FAILED) from error

    def _dump(self, target: Path) -> None:
        database = self.database
        command = [
            *self.programs.pg_dump, "--format=custom", "--no-password", f"--file={target}",
            f"--host={database.host}", f"--port={database.port}", f"--username={database.user}", f"--dbname={database.name}",
        ]
        result = subprocess.run(command, env={**os.environ, "PGPASSWORD": database.password}, capture_output=True, check=False)
        if result.returncode != 0:
            logger.error("pg_dump завершился с ошибкой", extra={"exit_code": result.returncode, "stderr": _tail(result.stderr)})
            raise BackupError(DATABASE_DUMP_FAILED)

    def _encrypt(self, dump: Path, partial: Path) -> None:
        try:
            output = partial.open("xb")
        except OSError as error:
            logger.error("Нельзя записать в папку копий", extra={"path": str(self.folder)}, exc_info=True)
            raise BackupError(STORAGE_FAILED) from error
        with output, tempfile.TemporaryFile(dir=self.work_root) as errors:
            process = subprocess.Popen([*self.programs.age, "--encrypt", "--recipient", self.recipient], stdin=subprocess.PIPE, stdout=output, stderr=errors)
            written = self._write_tar(process.stdin, dump)
            exit_code = process.wait()
            if not written or exit_code != 0:
                errors.seek(0)
                logger.error("age завершился с ошибкой", extra={"exit_code": exit_code, "stderr": _tail(errors.read())})
        if not written or exit_code != 0:
            partial.unlink(missing_ok=True)
            raise BackupError(ENCRYPTION_FAILED)

    def _write_tar(self, stream: IO[bytes] | None, dump: Path) -> bool:
        """Tar с дампом и картинками — потоком в age; False — tar не передан целиком (age закрыл ввод, файл не прочитан)."""
        if stream is None:
            raise RuntimeError("у age нет потока ввода")
        try:
            with stream, tarfile.open(fileobj=stream, mode="w|") as tar:
                tar.add(dump, arcname="database.dump")
                tar.add(self.media_dir, arcname="media")
        except OSError:
            # На Windows оборванный канал — OSError (EINVAL), а не BrokenPipeError.
            logger.error("Tar не передан age целиком", exc_info=True)
            return False
        return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _tail(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")[-STDERR_TAIL:]
