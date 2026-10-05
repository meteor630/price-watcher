"""pricewatch.domain.tokens: случайность, формат, HMAC с назначением, вывод следующего токена."""

import hashlib
import hmac
import re

import pytest

from pricewatch.domain.tokens import (
    PAIRING_CODE_ALPHABET,
    Purpose,
    derive_token,
    is_pairing_code,
    keyed_hash,
    new_pairing_code,
    new_token,
    same_secret,
)

KEY = b"k" * 32
OTHER_KEY = b"o" * 32


def test_tokens_are_long_urlsafe_and_unique() -> None:
    tokens = {new_token() for _ in range(2000)}

    assert len(tokens) == 2000
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{43}", token) for token in tokens)


def test_pairing_codes_match_contract_pattern() -> None:
    codes = [new_pairing_code() for _ in range(2000)]

    assert all(re.fullmatch(r"[A-Z2-7]{8}", code) for code in codes)
    assert len(set(codes)) > 1990  # 2^40 вариантов: совпадения практически невозможны
    assert set("".join(codes)) == set(PAIRING_CODE_ALPHABET)


@pytest.mark.parametrize(("value", "valid"), [("ABCD2345", True), ("abcd2345", False), ("ABCD234", False), ("ABCD2341", False), ("ABCD23456", False), ("АBCD2345", False)])
def test_pairing_code_format(value: str, valid: bool) -> None:
    assert is_pairing_code(value) is valid


def test_keyed_hash_is_hmac_sha256_with_purpose() -> None:
    expected = hmac.new(KEY, b"session\x00value", hashlib.sha256).hexdigest()

    assert keyed_hash(KEY, Purpose.SESSION, "value") == expected
    assert len(expected) == 64


def test_keyed_hash_depends_on_key_purpose_and_value() -> None:
    base = keyed_hash(KEY, Purpose.REFRESH, "token")

    assert keyed_hash(OTHER_KEY, Purpose.REFRESH, "token") != base
    assert keyed_hash(KEY, Purpose.ACCESS, "token") != base
    assert keyed_hash(KEY, Purpose.REFRESH, "token2") != base
    assert keyed_hash(KEY, Purpose.REFRESH, "token") == base


def test_purpose_separator_prevents_ambiguity() -> None:
    # «csrf» + «x» не должно совпасть с «cs» + «rfx»: назначение отделено нулевым байтом.
    assert keyed_hash(KEY, Purpose.CSRF, "x") != hmac.new(KEY, b"csrfx", hashlib.sha256).hexdigest()


def test_derived_token_is_deterministic_and_keyed() -> None:
    parent = new_token()

    first = derive_token(KEY, Purpose.REFRESH, parent)

    assert first == derive_token(KEY, Purpose.REFRESH, parent)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", first)
    assert first != derive_token(OTHER_KEY, Purpose.REFRESH, parent)
    assert first != derive_token(KEY, Purpose.ACCESS, parent)
    assert first != derive_token(KEY, Purpose.REFRESH, new_token())


def test_derived_token_differs_from_its_hash() -> None:
    parent = new_token()

    assert derive_token(KEY, Purpose.REFRESH, parent) != keyed_hash(KEY, Purpose.REFRESH, parent)


def test_empty_key_is_refused() -> None:
    with pytest.raises(ValueError):
        keyed_hash(b"", Purpose.SESSION, "x")


def test_same_secret() -> None:
    assert same_secret("абв", "абв")
    assert not same_secret("абв", "абг")
    assert not same_secret("абв", "абвг")
