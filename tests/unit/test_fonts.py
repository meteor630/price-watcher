"""Шрифты: подключённые файлы существуют, лицензии на месте, нужные символы покрыты."""

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "src" / "pricewatch" / "web" / "static"
FONTS_CSS = (STATIC / "css" / "fonts.css").read_text(encoding="utf-8")
TOKENS_CSS = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")
FACES = [
    {
        "family": re.search(r'font-family:\s*"([^"]+)"', body).group(1),
        "url": re.search(r'url\("([^"]+)"\)', body).group(1),
        "range": re.search(r"unicode-range:\s*([^;]+);", body).group(1),
    }
    for body in re.findall(r"@font-face\s*\{([^}]*)\}", FONTS_CSS)
]
FAMILIES = sorted({face["family"] for face in FACES})


def covers(unicode_range: str, codepoint: int) -> bool:
    for part in unicode_range.split(","):
        bounds = part.strip().removeprefix("U+").split("-")
        low, high = int(bounds[0], 16), int(bounds[-1], 16)
        if low <= codepoint <= high:
            return True
    return False


def test_every_face_points_to_existing_file() -> None:
    missing = [face["url"] for face in FACES if not (STATIC / "css" / face["url"]).resolve().is_file()]

    assert FACES
    assert missing == []


def test_every_font_file_is_used() -> None:
    used = {(STATIC / "css" / face["url"]).resolve() for face in FACES}

    assert set((STATIC / "fonts").glob("*.woff2")) == used


def test_token_families_are_served_locally() -> None:
    for token in ("--font-display", "--font-body", "--font-mono"):
        first = re.search(rf'{token}:\s*"([^"]+)"', TOKENS_CSS).group(1)
        assert first in FAMILIES


@pytest.mark.parametrize("family", FAMILIES)
def test_family_has_ofl_license(family: str) -> None:
    slug = family.lower().replace(" ", "-")
    license_text = (STATIC / "fonts" / f"{slug}.LICENSE.txt").read_text(encoding="utf-8")

    assert "SIL Open Font License" in license_text


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize(
    ("char", "name"),
    [("₽", "знак рубля"), ("Ж", "кириллица"), ("ё", "ё"), ("A", "латиница"), ("№", "номер"), ("−", "минус")],
)
def test_family_covers_required_characters(family: str, char: str, name: str) -> None:
    ranges = [face["range"] for face in FACES if face["family"] == family]

    assert any(covers(r, ord(char)) for r in ranges), f"{family}: не покрыт символ «{name}»"


def test_covers_detects_gaps() -> None:
    assert covers("U+0400-045F", ord("Ж"))
    assert not covers("U+0000-00FF, U+20AC", ord("₽"))
