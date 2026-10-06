"""Токен отмены удаления: 15 секунд, только своему пользователю, подделка и мусор — NOT_FOUND."""

from datetime import UTC, datetime, timedelta

import pytest

from pricewatch.domain.errors import ErrorCode, PriceWatcherError
from pricewatch.domain.undo import UNDO_WINDOW, UndoKind, issue_undo, read_undo

KEY = b"undo-test-key-" + b"k" * 30
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def test_round_trip_within_window() -> None:
    token = issue_undo(KEY, 7, UndoKind.CATEGORY, 42, NOW)

    assert read_undo(KEY, 7, token, NOW + UNDO_WINDOW) == (UndoKind.CATEGORY, 42)


@pytest.mark.parametrize(
    ("user", "moment", "mutate"),
    [
        (7, NOW + UNDO_WINDOW + timedelta(seconds=1), lambda token: token),  # просрочен
        (8, NOW, lambda token: token),  # чужой
        (7, NOW, lambda token: token.replace("category.42", "category.43")),  # подменён номер
        (7, NOW, lambda token: token.replace("category", "group")),  # подменён вид
        (7, NOW, lambda token: token[:-1] + ("0" if token[-1] != "0" else "1")),  # подпись
        (7, NOW, lambda token: "мусор"),
        (7, NOW, lambda token: "a.b.c.d"),
    ],
    ids=["просрочен", "чужой", "номер", "вид", "подпись", "мусор", "не числа"],
)
def test_bad_tokens_are_not_found(user: int, moment: datetime, mutate) -> None:  # type: ignore[no-untyped-def]
    token = mutate(issue_undo(KEY, 7, UndoKind.CATEGORY, 42, NOW))

    with pytest.raises(PriceWatcherError) as caught:
        read_undo(KEY, user, token, moment)

    assert caught.value.code is ErrorCode.NOT_FOUND


def test_other_key_does_not_verify() -> None:
    with pytest.raises(PriceWatcherError):
        read_undo(b"other-key-" + b"x" * 30, 7, issue_undo(KEY, 7, UndoKind.GROUP, 1, NOW), NOW)
