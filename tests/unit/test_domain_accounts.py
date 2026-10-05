"""pricewatch.domain.accounts: логин, пароль, имя, роли."""

import pytest

from pricewatch.domain.accounts import (
    Role,
    User,
    check_display_name,
    check_login,
    check_password,
    login_key,
    normalize_password,
)
from pricewatch.domain.errors import ErrorCode, PriceWatcherError


def reason(action, field: str) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(PriceWatcherError) as caught:
        action()
    assert caught.value.code is ErrorCode.VALIDATION_ERROR
    return caught.value.details["fields"][field]


@pytest.mark.parametrize("login", ["abc", "Alice", "ivan.petrov", "a-b_c", "x" * 64, "  alice  "])
def test_valid_logins(login: str) -> None:
    assert check_login(login) == login.strip()


@pytest.mark.parametrize("login", ["ab", "x" * 65, "иван", "a b", "alice@home", "", "a\nb"])
def test_invalid_logins(login: str) -> None:
    assert reason(lambda: check_login(login), "login") == "format"


def test_login_key_ignores_case() -> None:
    assert login_key("Alice") == login_key("ALICE") == "alice"


@pytest.mark.parametrize("password", ["1234567890", "п" * 10, "x" * 128, "пароль с пробелами"])
def test_valid_passwords(password: str) -> None:
    assert check_password(password, "alice") == password


@pytest.mark.parametrize(
    ("password", "problem"),
    [("123456789", "too_short"), ("x" * 129, "too_long"), ("AliceAlice", None), ("alice12345", None)],
)
def test_password_length(password: str, problem: str | None) -> None:
    if problem is None:
        assert check_password(password, "alice")
    else:
        assert reason(lambda: check_password(password, "alice"), "password") == problem


@pytest.mark.parametrize("password", ["ivan.petrov", "IVAN.PETROV"])
def test_password_equal_to_login_rejected(password: str) -> None:
    assert reason(lambda: check_password(password, "Ivan.Petrov"), "password") == "same_as_login"


def test_password_length_counted_after_normalization() -> None:
    decomposed = "й" * 10  # 20 кодовых точек, но 10 букв «й»
    assert len(normalize_password(decomposed)) == 10
    assert check_password(decomposed, "alice") == "й" * 10
    assert reason(lambda: check_password("й" * 9, "alice"), "password") == "too_short"


def test_display_name_rules() -> None:
    assert check_display_name("  Иван  Петров ") == "Иван Петров"
    assert reason(lambda: check_display_name("   "), "display_name") == "empty"


def test_user_admin_flag() -> None:
    admin = User(id=1, login="a", role=Role.ADMIN, display_name="A", disabled=False)
    user = User(id=2, login="u", role=Role.USER, display_name="U", disabled=False)

    assert admin.is_admin and not user.is_admin
