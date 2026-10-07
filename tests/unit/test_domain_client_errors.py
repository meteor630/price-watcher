"""pricewatch.domain.client_errors: из текста отчёта вырезаются секреты и управляющие символы."""

import pytest

from pricewatch.domain.client_errors import HIDDEN, clean_text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Нет сети", "Нет сети"),
        ("GET https://prices.example.ru/api/v1/items/7?token=abc#frag упал", "GET https://prices.example.ru/api/v1/items/7 упал"),
        ("Authorization: Bearer eyJhbGciOi.payload.sig", f"Authorization: Bearer {HIDDEN}"),
        ("refresh_token=Zm9vYmFy password: hunter2 pairing_code=ABCD2345", f"refresh_token={HIDDEN} password={HIDDEN} pairing_code={HIDDEN}"),
        ("id " + "a1B2" * 10, f"id {HIDDEN}"),
        ("строка\x00с\x1bуправляющими‮символами\nи переводом", "строкасуправляющимисимволами\nи переводом"),
        ("  пробелы по краям  ", "пробелы по краям"),
    ],
)
def test_clean_text(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected


def test_ordinary_words_stay() -> None:
    text = "Ошибка декодирования ItemDetail: поле price_minor отсутствует (keyNotFound)"

    assert clean_text(text) == text
