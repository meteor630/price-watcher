"""Папка копий на домашнем узле (DEVELOPMENT_ARCHITECTURE.md, раздел 11).

Копия — tar с дампом базы (pg_dump, формат custom) и папкой картинок, зашифрованный открытым ключом age.
Расшифровать копию на узле нельзя: закрытый ключ хранится у владельца отдельно.

- Пароль базы — только в окружении pg_dump (PGPASSWORD), не в аргументах: аргументы видны в списке процессов.
- Незашифрованный дамп лежит только во временной папке (в контейнере /tmp — в памяти) и удаляется сразу после копии;
  в age tar идёт потоком, на диск попадает только зашифрованное.
- Копия пишется под временным именем и переименовывается целиком: оборванная запись не выглядит копией.
  Временные файлы оборванных попыток удаляются перед новой копией.
- На арендный сервер копии уходят rsync по SSH к пользователю, который может только дописывать новые файлы
  (deploy/vps/backup-target.sh): уже лежащие там не отправляются и не перезаписываются. Ключ сервера сверяется
  строго по заранее записанному known_hosts; свой ключ — копией с правами только владельца (на диске Windows,
  подключённом в контейнер, права шире, и ssh такой ключ не взял бы).
"""

import logging
import os
import subprocess
import tarfile
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from pricewatch.application.ports import BackupError
from pricewatch.domain.backups import created_at

logger = logging.getLogger(__name__)

DATABASE_DUMP_FAILED = "DATABASE_DUMP_FAILED"
ENCRYPTION_FAILED = "ENCRYPTION_FAILED"
STORAGE_FAILED = "STORAGE_FAILED"
MEDIA_UNAVAILABLE = "MEDIA_UNAVAILABLE"
UPLOAD_FAILED = "UPLOAD_FAILED"
UPLOAD_TIMEOUT_S = 1800
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
class RemoteFolder:
    """Папка копий на арендном сервере: адрес вида pwbackup@tunnel.example.ru, порт SSH, свой ключ и ключ сервера."""

    target: str
    port: int
    key_file: Path
    known_hosts: Path


@dataclass(frozen=True, slots=True)
class Programs:
    """Команды запуска pg_dump, age и rsync (в тестах — подставные программы)."""

    pg_dump: Sequence[str] = ("pg_dump",)
    age: Sequence[str] = ("age",)
    rsync: Sequence[str] = ("rsync",)


class AgeArchive:
    def __init__(
        self, folder: Path, media_dir: Path, recipient: str, database: DatabaseAddress, remote: RemoteFolder, *,
        programs: Programs = Programs(), work_root: Path | None = None,
    ) -> None:
        self.folder = folder
        self.media_dir = media_dir
        self.recipient = recipient
        self.database = database
        self.remote = remote
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

    def remove(self, name: str) -> None:
        try:
            (self.folder / name).unlink()
        except OSError as error:
            logger.error("Старая копия не удалена", extra={"file": name}, exc_info=True)
            raise BackupError(STORAGE_FAILED) from error

    def upload(self) -> None:
        names = sorted(name for name in self.names() if created_at(name) is not None)
        if not names:
            return
        try:
            key = self.remote.key_file.read_bytes()
        except OSError as error:
            logger.error("Нет ключа SSH для отправки копий", extra={"path": str(self.remote.key_file)}, exc_info=True)
            raise BackupError(UPLOAD_FAILED) from error
        with tempfile.TemporaryDirectory(dir=self.work_root) as work:
            key_copy = Path(work) / "ssh_key"
            key_copy.write_bytes(key)
            key_copy.chmod(0o600)
            ssh = " ".join([
                "ssh", "-F", "/dev/null", "-i", str(key_copy), "-p", str(self.remote.port), "-o", "BatchMode=yes",
                "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={self.remote.known_hosts}",
                "-o", "ConnectTimeout=30",
            ])
            command = [*self.programs.rsync, "--ignore-existing", "--chmod=F600", "-e", ssh, *(str(self.folder / name) for name in names), f"{self.remote.target}:"]
            try:
                result = subprocess.run(command, capture_output=True, check=False, timeout=UPLOAD_TIMEOUT_S)
            except subprocess.TimeoutExpired as error:
                logger.error("Отправка копий на сервер не уложилась во время", extra={"timeout_s": UPLOAD_TIMEOUT_S})
                raise BackupError(UPLOAD_FAILED) from error
        if result.returncode != 0:
            logger.error("Копии не отправлены на сервер", extra={"exit_code": result.returncode, "stderr": _tail(result.stderr)})
            raise BackupError(UPLOAD_FAILED)

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


def _tail(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")[-STDERR_TAIL:]
