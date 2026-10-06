"""Фото у worker: скачивание безопасным загрузчиком с пределом 2 МБ, перекодирование, отказы."""

import ipaddress
from io import BytesIO

import httpx2
import pytest
from PIL import Image

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.media.images import MAX_INPUT_BYTES, ImageRejected
from pricewatch.parsers.fetch import SafeFetcher
from pricewatch.worker.images import ImageLoader

PUBLIC = ipaddress.ip_address("93.184.216.34")


def loader(status: int = 200, body: bytes = b"", resolve: ipaddress.IPv4Address = PUBLIC) -> tuple[ImageLoader, list[httpx2.Request]]:
    seen: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(status, content=body, headers={"content-type": "image/jpeg"})

    fetcher = SafeFetcher(resolver=lambda host: [resolve], transport=httpx2.MockTransport(respond), max_bytes=MAX_INPUT_BYTES)
    return ImageLoader(fetcher), seen


def jpeg(size: tuple[int, int] = (1600, 1200)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, "teal").save(out, "JPEG")
    return out.getvalue()


def test_shop_photo_becomes_webp() -> None:
    images, seen = loader(body=jpeg())

    data = images.load("https://basket-01.wbbasket.ru/vol1/part1/1/images/big/1.jpg")

    with Image.open(BytesIO(data)) as result:
        assert (result.format, result.size) == ("WEBP", (800, 600))
    assert seen[0].headers["accept"].startswith("image/")


def test_not_found_is_rejected() -> None:
    images, _ = loader(status=404)

    with pytest.raises(ImageRejected) as caught:
        images.load("https://img.example/1.jpg")

    assert caught.value.reason == "http_404"


def test_bigger_than_two_megabytes_is_cut_off() -> None:
    images, _ = loader(body=b"\xff" * (MAX_INPUT_BYTES + 1))

    with pytest.raises(PriceWatcherError) as caught:
        images.load("https://img.example/1.jpg")

    assert caught.value.code is ErrorCode.LAYOUT_CHANGED


def test_private_address_is_refused() -> None:
    images, seen = loader(body=jpeg(), resolve=ipaddress.ip_address("192.168.1.10"))

    with pytest.raises(PriceWatcherError) as caught:
        images.load("https://img.example/1.jpg")

    assert caught.value.code is ErrorCode.INVALID_URL and seen == []


def test_plain_http_address_is_refused() -> None:
    images, seen = loader(body=jpeg())

    with pytest.raises(PriceWatcherError):
        images.load("http://img.example/1.jpg")

    assert seen == []
