"""Коды ошибок и базовое исключение проекта.

Список кодов — DEVELOPMENT_ARCHITECTURE.md, раздел 10. Сообщение для человека берётся
только из каталога ниже: технические подробности в него не попадают, они остаются
в цепочке исключений (`raise ... from`) и журнале.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class ErrorCode(StrEnum):
    # Сбор цен и общие сбои
    INVALID_URL = "INVALID_URL"
    STORE_NOT_ALLOWED = "STORE_NOT_ALLOWED"
    BLOCKED = "BLOCKED"
    CAPTCHA = "CAPTCHA"
    TIMEOUT = "TIMEOUT"
    LAYOUT_CHANGED = "LAYOUT_CHANGED"
    PRICE_NOT_FOUND = "PRICE_NOT_FOUND"
    REGION_MISMATCH = "REGION_MISMATCH"
    UNSUPPORTED_CURRENCY = "UNSUPPORTED_CURRENCY"
    WORKER_OFFLINE = "WORKER_OFFLINE"
    RUN_IN_PROGRESS = "RUN_IN_PROGRESS"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    DB_ERROR = "DB_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    # API и вход
    AUTH_REQUIRED = "AUTH_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    PAIRING_CODE_INVALID = "PAIRING_CODE_INVALID"
    PAIRING_CODE_EXPIRED = "PAIRING_CODE_EXPIRED"
    REFRESH_TOKEN_INVALID = "REFRESH_TOKEN_INVALID"
    REFRESH_TOKEN_REUSED = "REFRESH_TOKEN_REUSED"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"


SAFE_MESSAGES: Mapping[ErrorCode, str] = MappingProxyType(
    {
        ErrorCode.INVALID_URL: "Ссылка не похожа на адрес страницы товара. Проверьте её.",
        ErrorCode.STORE_NOT_ALLOWED: "Этот магазин пока не подключён. Попросите администратора добавить его.",
        ErrorCode.BLOCKED: "Магазин временно ограничил доступ. Проверка повторится позже.",
        ErrorCode.CAPTCHA: "Магазин запросил проверку «я не робот». Проверка повторится позже.",
        ErrorCode.TIMEOUT: "Магазин не ответил вовремя. Проверка повторится позже.",
        ErrorCode.LAYOUT_CHANGED: "Страница магазина изменилась, цену прочитать не удалось.",
        ErrorCode.PRICE_NOT_FOUND: "На странице не нашлось цены.",
        ErrorCode.REGION_MISMATCH: "Не удалось подтвердить цену для Санкт-Петербурга.",
        ErrorCode.UNSUPPORTED_CURRENCY: "Цена указана не в рублях.",
        ErrorCode.WORKER_OFFLINE: "Проверка цен сейчас недоступна. Цена появится при ближайшей проверке.",
        ErrorCode.RUN_IN_PROGRESS: "Проверка уже идёт.",
        ErrorCode.VERSION_CONFLICT: "Запись уже изменили в другом месте. Показаны актуальные данные.",
        ErrorCode.DB_ERROR: "Не удалось сохранить данные. Попробуйте ещё раз.",
        ErrorCode.INTERNAL_ERROR: "Что-то пошло не так. Мы уже знаем об ошибке.",
        ErrorCode.AUTH_REQUIRED: "Нужно войти заново.",
        ErrorCode.FORBIDDEN: "Это действие доступно только администратору.",
        ErrorCode.NOT_FOUND: "Не нашлось. Возможно, запись удалили.",
        ErrorCode.VALIDATION_ERROR: "Проверьте заполненные поля.",
        ErrorCode.RATE_LIMITED: "Слишком много попыток. Подождите и попробуйте снова.",
        ErrorCode.PAIRING_CODE_INVALID: "Код не подошёл. Проверьте его или получите новый.",
        ErrorCode.PAIRING_CODE_EXPIRED: "Срок действия кода истёк. Получите новый.",
        ErrorCode.REFRESH_TOKEN_INVALID: "Нужно войти заново.",
        ErrorCode.REFRESH_TOKEN_REUSED: "Сессия завершена из соображений безопасности. Войдите заново.",
        ErrorCode.IDEMPOTENCY_KEY_REUSED: "Повторный запрос отличается от исходного. Обновите страницу и повторите.",
    }
)


class PriceWatcherError(Exception):
    """Ожидаемая ошибка с кодом из каталога.

    `details` — структурированные данные для клиента (например, поля с ошибками),
    без технических подробностей и секретов.
    """

    def __init__(self, code: ErrorCode, details: Mapping[str, object] | None = None) -> None:
        if not isinstance(code, ErrorCode):
            raise TypeError(f"Код ошибки должен быть ErrorCode, получено {type(code).__name__}")
        self.code = code
        self.safe_message = SAFE_MESSAGES[code]
        self.details: Mapping[str, object] = MappingProxyType(dict(details or {}))
        super().__init__(f"{code.value}: {self.safe_message}")
