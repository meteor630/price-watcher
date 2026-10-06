"""Перекодирование фото товара (DEVELOPMENT_ARCHITECTURE.md, раздел 7, «Картинки»; решение 06.10.2026).

Файл из магазина и файл от worker — недоверенный ввод. Принимаются только JPEG, PNG и WebP
не больше 2 МБ; размер картинки проверяется по заголовку до распаковки (защита от «бомб»).
Результат — новый WebP не больше 800×800 без метаданных: из исходного файла переносятся только
пиксели первого кадра. Перекодирование одинаково у worker и сервера.
"""

from io import BytesIO

from PIL import Image, UnidentifiedImageError

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_SIDE = 800
# 40 Мп — с запасом для фото товара; больше — подозрение на «бомбу» распаковки.
MAX_SOURCE_PIXELS = 40_000_000
SHOP_FORMATS = ("JPEG", "PNG", "WEBP")
WEBP_QUALITY = 82


class ImageRejected(ValueError):
    """Файл не принят; `reason` — короткий код причины для журнала и ответа."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def to_webp(data: bytes, formats: tuple[str, ...] = SHOP_FORMATS) -> bytes:
    if len(data) > MAX_INPUT_BYTES:
        raise ImageRejected("too_large")
    try:
        with Image.open(BytesIO(data), formats=list(formats)) as image:
            if image.width * image.height > MAX_SOURCE_PIXELS:
                raise ImageRejected("too_many_pixels")
            # JPEG можно сразу распаковать в уменьшенном масштабе: меньше памяти и времени.
            image.draft("RGB", (MAX_SIDE, MAX_SIDE))
            image.load()
            frame = image.convert("RGBA" if _has_alpha(image) else "RGB")
    except (UnidentifiedImageError, OSError, SyntaxError, EOFError, Image.DecompressionBombError) as error:
        raise ImageRejected("unreadable") from error
    frame.thumbnail((MAX_SIDE, MAX_SIDE))
    out = BytesIO()
    frame.save(out, "WEBP", quality=WEBP_QUALITY)
    return out.getvalue()


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info
