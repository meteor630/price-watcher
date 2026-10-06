"""Фото товара для сервера: скачать по адресу из карточки и перекодировать.

Скачивание — тем же безопасным загрузчиком, что и страницы (только публичные адреса, ручные
перенаправления), но с пределом 2 МБ. Ответ не 200 или файл не картинка нужного формата — ImageRejected.
"""

from pricewatch.media.images import MAX_INPUT_BYTES, ImageRejected, to_webp
from pricewatch.parsers.fetch import SafeFetcher
from pricewatch.parsers.urls import normalize_url

ACCEPT = "image/webp,image/png,image/jpeg"


class ImageLoader:
    def __init__(self, fetcher: SafeFetcher | None = None) -> None:
        self._fetcher = fetcher or SafeFetcher(max_bytes=MAX_INPUT_BYTES)

    def close(self) -> None:
        self._fetcher.close()

    def load(self, image_url: str) -> bytes:
        response = self._fetcher.fetch(normalize_url(image_url), accept=ACCEPT)
        if response.status != 200:
            raise ImageRejected(f"http_{response.status}")
        return to_webp(response.body)
