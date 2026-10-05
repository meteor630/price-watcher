"""Хеширование паролей: argon2id через argon2-cffi (решение по разделу 8 AGENTS.md).

Параметры по умолчанию — профиль RFC 9106 с малым расходом памяти (64 МиБ, t=3, p=4).
Пароль нормализуется (NFKC) и при хешировании, и при проверке — забыть это негде.
"""

from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import VerifyMismatchError

from pricewatch.domain.accounts import normalize_password
from pricewatch.domain.tokens import new_token


class PasswordHasher:
    def __init__(self, *, time_cost: int | None = None, memory_cost: int | None = None) -> None:
        """Без параметров — значения argon2-cffi по умолчанию; меньшие значения только для тестов."""
        options = {name: value for name, value in (("time_cost", time_cost), ("memory_cost", memory_cost)) if value}
        self._argon2 = Argon2Hasher(**options)
        # Хеш случайной строки: проверка по нему занимает столько же, сколько настоящая.
        self._dummy_hash = self._argon2.hash(new_token())

    def hash(self, password: str) -> str:
        return self._argon2.hash(normalize_password(password))

    def verify(self, password_hash: str, password: str) -> bool:
        """Неверный пароль — False. Испорченный хеш в базе — исключение: это ошибка данных, а не входа."""
        try:
            return self._argon2.verify(password_hash, normalize_password(password))
        except VerifyMismatchError:
            return False

    def verify_dummy(self, password: str) -> None:
        """Проверка с той же ценой для несуществующего логина: время ответа не выдаёт, есть ли пользователь."""
        self.verify(self._dummy_hash, password)

    def needs_rehash(self, password_hash: str) -> bool:
        return self._argon2.check_needs_rehash(password_hash)
