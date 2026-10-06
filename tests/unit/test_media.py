"""Картинки: перекодирование в WebP до 800×800 без метаданных, отказ на чужих форматах, «бомбах» и мусоре; хранилище по хешу."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.media.images import MAX_INPUT_BYTES, ImageRejected, to_webp
from pricewatch.media.store import MediaStore


def encoded(image: Image.Image, fmt: str, **options: object) -> bytes:
    out = BytesIO()
    image.save(out, fmt, **options)
    return out.getvalue()


def opened(data: bytes) -> Image.Image:
    image = Image.open(BytesIO(data))
    image.load()
    return image


def test_large_jpeg_becomes_small_webp_without_metadata() -> None:
    exif = Image.Exif()
    exif[0x010F] = "Камера продавца"  # производитель
    source = encoded(Image.new("RGB", (2000, 1000), "red"), "JPEG", exif=exif.tobytes())

    result = opened(to_webp(source))

    assert (result.format, result.size, result.mode) == ("WEBP", (800, 400), "RGB")
    assert "exif" not in result.info and not result.getexif()


def test_transparent_png_keeps_transparency() -> None:
    result = opened(to_webp(encoded(Image.new("RGBA", (100, 50), (0, 0, 0, 0)), "PNG")))

    assert (result.size, result.mode) == ((100, 50), "RGBA")  # маленькая не растягивается


def test_palette_png_with_transparency() -> None:
    image = Image.new("P", (10, 10), 0)
    image.info["transparency"] = 0

    assert opened(to_webp(encoded(image, "PNG", transparency=0))).mode == "RGBA"


def test_webp_is_accepted() -> None:
    assert opened(to_webp(encoded(Image.new("RGB", (900, 900)), "WEBP"))).size == (800, 800)


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (encoded(Image.new("RGB", (10, 10)), "GIF"), "unreadable"),
        (encoded(Image.new("RGB", (10, 10)), "BMP"), "unreadable"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "unreadable"),
        (b"\x00" * 100, "unreadable"),
        (encoded(Image.new("RGB", (300, 300), "blue"), "JPEG")[:200], "unreadable"),  # оборванный файл
        (b"\xff" * (MAX_INPUT_BYTES + 1), "too_large"),
        (encoded(Image.new("1", (9000, 5000)), "PNG"), "too_many_pixels"),  # маленький файл, огромная картинка
    ],
    ids=["gif", "bmp", "svg", "мусор", "оборван", "больше 2 МБ", "бомба"],
)
def test_rejected(data: bytes, reason: str) -> None:
    with pytest.raises(ImageRejected) as caught:
        to_webp(data)

    assert caught.value.reason == reason


def test_only_listed_formats() -> None:
    with pytest.raises(ImageRejected):
        to_webp(encoded(Image.new("RGB", (10, 10)), "JPEG"), formats=("WEBP",))


def test_store_names_file_by_content_hash(tmp_path: Path) -> None:
    store = MediaStore(tmp_path)
    data = to_webp(encoded(Image.new("RGB", (50, 50), "green"), "PNG"))

    first, second = store.save(data), store.save(data)

    assert first == second
    folder, name = first.split("/")
    assert len(folder) == 2 and name.startswith(folder) and name.endswith(".webp") and len(name) == 64 + 5
    assert opened((tmp_path / first).read_bytes()).format == "WEBP"
    assert [path.name for path in tmp_path.rglob("*")] == [folder, name]  # без временных файлов


def test_store_accepts_only_webp(tmp_path: Path) -> None:
    with pytest.raises(PriceWatcherError) as caught:
        MediaStore(tmp_path).save(encoded(Image.new("RGB", (10, 10)), "JPEG"))

    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    assert caught.value.details == {"fields": {"image": "unreadable"}}
    assert list(tmp_path.iterdir()) == []
