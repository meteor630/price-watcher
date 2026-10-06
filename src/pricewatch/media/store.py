"""Каталог картинок на сервере: файл получает имя по SHA-256 содержимого.

Путь в базе — относительный, вида «ab/<хеш>.webp»: имя не зависит ни от магазина, ни от
присланного worker, поэтому выйти за пределы каталога нельзя. Одинаковые фото хранятся одним
файлом. Запись — через временный файл и переименование: оборванная запись не оставляет полфайла.
"""

import hashlib
import os
import tempfile
from pathlib import Path

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.media.images import ImageRejected, to_webp


class MediaStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def save(self, data: bytes) -> str:
        """Перекодировать присланный WebP и сохранить; неверный файл — VALIDATION_ERROR по полю image."""
        try:
            webp = to_webp(data, formats=("WEBP",))
        except ImageRejected as error:
            raise PriceWatcherError(ErrorCode.VALIDATION_ERROR, {"fields": {"image": error.reason}}) from error
        digest = hashlib.sha256(webp).hexdigest()
        relative = f"{digest[:2]}/{digest}.webp"
        target = self._root / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
            try:
                with os.fdopen(handle, "wb") as file:
                    file.write(webp)
                os.replace(temporary, target)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        return relative
