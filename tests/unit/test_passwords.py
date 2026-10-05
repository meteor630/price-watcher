"""pricewatch.application.passwords: argon2id, нормализация, перехеширование."""

import pytest
from argon2.exceptions import VerificationError

from pricewatch.application.passwords import PasswordHasher

# Дешёвые параметры ради скорости тестов; параметры по умолчанию проверяются отдельно.
FAST = {"time_cost": 1, "memory_cost": 8 * 1024}


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher(**FAST)


def test_hash_and_verify(hasher: PasswordHasher) -> None:
    password_hash = hasher.hash("правильный пароль")

    assert password_hash.startswith("$argon2id$")
    assert "правильный" not in password_hash
    assert hasher.verify(password_hash, "правильный пароль")
    assert not hasher.verify(password_hash, "неправильный пароль")


def test_same_password_gives_different_hashes(hasher: PasswordHasher) -> None:
    assert hasher.hash("одинаковый пароль") != hasher.hash("одинаковый пароль")


def test_password_normalized_on_hash_and_verify(hasher: PasswordHasher) -> None:
    password_hash = hasher.hash("пароль-йод-123")

    assert hasher.verify(password_hash, "пароль-йод-123")


@pytest.mark.parametrize("corrupted", ["not-a-hash", "$argon2id$v=19$m=8192", "не хеш"])
def test_corrupted_hash_is_an_error_not_a_wrong_password(hasher: PasswordHasher, corrupted: str) -> None:
    # argon2-cffi даёт InvalidHashError, VerificationError (обрезанный хеш) или UnicodeEncodeError
    # (не-ASCII); важно, что это исключение, а не False — то есть не «неверный пароль».
    with pytest.raises((ValueError, VerificationError)):
        hasher.verify(corrupted, "пароль")


def test_dummy_verification_runs_real_argon2(hasher: PasswordHasher) -> None:
    assert hasher._dummy_hash.startswith("$argon2id$")
    assert hasher.verify_dummy("любой пароль") is None


def test_default_parameters_are_rfc9106_low_memory() -> None:
    password_hash = PasswordHasher().hash("пароль по умолчанию")

    assert password_hash.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_weaker_hash_needs_rehash(hasher: PasswordHasher) -> None:
    weak = hasher.hash("пароль для перехеширования")

    assert PasswordHasher().needs_rehash(weak)
    assert not hasher.needs_rehash(weak)
